#!/usr/bin/env python3
"""Mirror leucaena.earth ALS status into articulacao GPKG lidar_proc.

Reads GET /api/v1/als/export/registry.csv and sets lidar_proc on matching
rows (NOMENC_2K / NOMENC_5K / NOMENC_10K).

Usage:
  set ALS_MACHINE_TOKEN=...
  python python/scripts/sync_lidar_proc_from_api.py
  python python/scripts/sync_lidar_proc_from_api.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_GPKG = (
    REPO
    / "data"
    / "articulacao_igc_sp"
    / "articulacao-igc-merged-spatialindex-sirgas2000.gpkg"
)
LAYER = "articulacao-igc-merged-spatialindex-sirgas2000"
DEFAULT_API = os.environ.get("ALS_API_URL", "https://leucaena.earth").rstrip("/")


def log(msg: str) -> None:
    print(msg, flush=True)


def http_get(url: str, token: str, timeout: float = 180.0) -> bytes:
    last = "request failed"
    for attempt in range(1, 5):
        req = urllib.request.Request(
            url,
            headers={
                "X-ALS-Token": token,
                "Accept": "text/csv, application/json, */*",
                "User-Agent": "leucaena-earth-utils/sync_lidar_proc_from_api",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400].strip()
            last = f"HTTP {exc.code} {detail}".strip()
            if exc.code not in (408, 429) and exc.code < 500:
                break
        except urllib.error.URLError as exc:
            last = f"network error: {exc.reason}"
        if attempt < 4:
            time.sleep(min(2**attempt, 20))
            log(f"retry {attempt}/3 after: {last}")
    raise RuntimeError(last)


def load_status_map(api: str, token: str) -> dict[str, int]:
    url = api + "/api/v1/als/export/registry.csv?" + urllib.parse.urlencode(
        {"track": "lidar"}
    )
    raw = http_get(url, token)
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "tile_id" not in reader.fieldnames:
        raise RuntimeError("registry CSV missing tile_id")
    out: dict[str, int] = {}
    for row in reader:
        tile = (row.get("tile_id") or "").strip().upper()
        if not tile:
            continue
        code = (row.get("status_code") or "").strip()
        status = (row.get("status") or "").strip().lower()
        try:
            val = int(float(code))
        except ValueError:
            if status == "processed":
                val = 1
            elif status in ("erro", "error", "failed"):
                val = 2
            elif status in ("em_andamento", "claimed"):
                val = 3
            elif status in ("pulado", "skipped"):
                val = 4
            else:
                val = 0
        out[tile] = val
    return out


def ensure_columns(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute(f'PRAGMA table_info("{LAYER}")')}
    if "lidar_proc" not in cols:
        con.execute(f'ALTER TABLE "{LAYER}" ADD COLUMN lidar_proc INTEGER DEFAULT 0')
    if "rgbnir_proc" not in cols:
        con.execute(f'ALTER TABLE "{LAYER}" ADD COLUMN rgbnir_proc INTEGER DEFAULT 0')


def counts(con: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for code, n in con.execute(
        f'SELECT COALESCE(lidar_proc, 0), COUNT(*) FROM "{LAYER}" GROUP BY COALESCE(lidar_proc, 0)'
    ):
        out[str(int(code))] = int(n)
    out["total"] = con.execute(f'SELECT COUNT(*) FROM "{LAYER}"').fetchone()[0]
    return out


def _rtree_update_any_triggers(con: sqlite3.Connection) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for name, sql in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
        (LAYER,),
    ):
        if name.endswith("_geom_update4") or name.endswith("_geom_update5"):
            out.append((name, sql))
    return out


def sync(gpkg: Path, status_map: dict[str, int], dry_run: bool) -> dict[str, int]:
    con = sqlite3.connect(str(gpkg))
    dropped: list[tuple[str, str]] = []
    try:
        ensure_columns(con)
        before = counts(con)
        log(f"GPKG before lidar_proc: {before}")

        rows = con.execute(
            f'''
            SELECT fid,
                   UPPER(TRIM(COALESCE(NOMENC_2K, ""))),
                   UPPER(TRIM(COALESCE(NOMENC_5K, ""))),
                   UPPER(TRIM(COALESCE(NOMENC_10K, ""))),
                   COALESCE(lidar_proc, 0)
            FROM "{LAYER}"
            '''
        ).fetchall()

        updates: list[tuple[int, int]] = []
        matched_ids: set[str] = set()
        for fid, n2, n5, n10, cur in rows:
            hit = None
            if n2 and n2 in status_map:
                hit = n2
            elif n5 and n5 in status_map:
                hit = n5
            elif n10 and n10 in status_map:
                hit = n10
            if not hit:
                continue
            matched_ids.add(hit)
            new_code = int(status_map[hit])
            if int(cur) != new_code:
                updates.append((new_code, int(fid)))

        log(f"API tile_ids: {len(status_map)}")
        log(f"GPKG rows with API match: {len(matched_ids)} unique ids")
        log(f"Rows to update: {len(updates)}")
        log(f"API ids with no GPKG match: {len(status_map) - len(matched_ids)}")

        if dry_run:
            log("Dry-run — no write.")
            return {
                "api_tiles": len(status_map),
                "matched_ids": len(matched_ids),
                "rows_to_update": len(updates),
            }

        dropped = _rtree_update_any_triggers(con)
        for name, _sql in dropped:
            con.execute(f'DROP TRIGGER IF EXISTS "{name}"')
            log(f"temporarily dropped trigger {name}")

        con.execute("BEGIN")
        con.executemany(
            f'UPDATE "{LAYER}" SET lidar_proc = ? WHERE fid = ?',
            updates,
        )
        con.commit()

        for name, sql in dropped:
            con.execute(sql)
            log(f"restored trigger {name}")
        con.commit()

        after = counts(con)
        log(f"GPKG after lidar_proc: {after}")
        return {
            "api_tiles": len(status_map),
            "matched_ids": len(matched_ids),
            "rows_updated": len(updates),
            "gpkg_before": before,
            "gpkg_after": after,
        }
    except Exception:
        for name, sql in dropped:
            try:
                con.execute(sql)
            except Exception:
                pass
        raise
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gpkg", type=Path, default=DEFAULT_GPKG)
    ap.add_argument("--api-url", default=DEFAULT_API)
    ap.add_argument("--token", default=os.environ.get("ALS_MACHINE_TOKEN", ""))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = (args.token or "").strip()
    if not token:
        log("ALS_MACHINE_TOKEN required (env or --token).")
        return 1
    if not args.gpkg.is_file():
        log(f"GPKG not found: {args.gpkg}")
        return 1

    log(f"API: {args.api_url}")
    log(f"GPKG: {args.gpkg}")
    status_map = load_status_map(args.api_url, token)
    by = {}
    for v in status_map.values():
        by[v] = by.get(v, 0) + 1
    log(f"API status counts: {by}")
    stats = sync(args.gpkg, status_map, args.dry_run)
    log(f"Done: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

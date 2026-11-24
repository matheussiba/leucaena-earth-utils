#!/usr/bin/env python3
"""Mirror leucaena.earth lidar_proc=1 into the IGC articulation GPKG.

Reads GET /api/v1/als/export/registry.csv (track=lidar) and sets
``lidar_proc = 1`` on matching rows in
``data/articulacao_igc_sp/articulacao-igc-merged-spatialindex-sirgas2000.gpkg``.

Match keys (uppercase): NOMENC_2K, NOMENC_5K, NOMENC_10K, and tile_id-like
values already stored in those columns.

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


def load_processed_ids(api: str, token: str) -> set[str]:
    url = api + "/api/v1/als/export/registry.csv?" + urllib.parse.urlencode(
        {"track": "lidar"}
    )
    raw = http_get(url, token)
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "tile_id" not in reader.fieldnames:
        raise RuntimeError("registry CSV missing tile_id")
    ids: set[str] = set()
    for row in reader:
        code = (row.get("status_code") or "").strip()
        status = (row.get("status") or "").strip().lower()
        try:
            done = int(float(code)) == 1
        except ValueError:
            done = status == "processed"
        if not done:
            continue
        tile = (row.get("tile_id") or "").strip().upper()
        if tile:
            ids.add(tile)
    return ids


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
    """Triggers that fire on ANY UPDATE and call ST_* (break plain sqlite3)."""
    out: list[tuple[str, str]] = []
    for name, sql in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
        "AND tbl_name=? AND sql LIKE '%AFTER UPDATE ON%'",
        (LAYER,),
    ):
        if sql and "UPDATE OF" not in sql.upper().split("AFTER UPDATE", 1)[-1][:40]:
            out.append((name, sql))
    # Known offenders from this GPKG (fid-change rtree maintainers)
    for name, sql in con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
        (LAYER,),
    ):
        if name.endswith("_geom_update4") or name.endswith("_geom_update5"):
            if (name, sql) not in out:
                out.append((name, sql))
    # de-dupe by name
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for name, sql in out:
        if name in seen:
            continue
        seen.add(name)
        uniq.append((name, sql))
    return uniq


def sync(
    gpkg: Path,
    processed: set[str],
    dry_run: bool,
    out_sp: bool = False,
) -> dict[str, int]:
    con = sqlite3.connect(str(gpkg))
    dropped: list[tuple[str, str]] = []
    try:
        ensure_columns(con)
        before = counts(con)
        log(f"GPKG before lidar_proc: {before}")
        log(f"OUT_SP={out_sp}  (false=only sp_inside=1, true=SP+outside)")

        # Collect fids that match any nomenclature column
        cols = {r[1] for r in con.execute(f'PRAGMA table_info("{LAYER}")')}
        has_sp = "sp_inside" in cols
        if has_sp:
            rows = con.execute(
                f'''
                SELECT fid,
                       UPPER(TRIM(COALESCE(NOMENC_2K, ""))),
                       UPPER(TRIM(COALESCE(NOMENC_5K, ""))),
                       UPPER(TRIM(COALESCE(NOMENC_10K, ""))),
                       sp_inside
                FROM "{LAYER}"
                '''
            ).fetchall()
        else:
            rows = [
                (*r, None)
                for r in con.execute(
                    f'''
                    SELECT fid,
                           UPPER(TRIM(COALESCE(NOMENC_2K, ""))),
                           UPPER(TRIM(COALESCE(NOMENC_5K, ""))),
                           UPPER(TRIM(COALESCE(NOMENC_10K, "")))
                    FROM "{LAYER}"
                    '''
                ).fetchall()
            ]

        def sp_ok(val) -> bool:
            if val is None:
                return False
            if isinstance(val, (int, float)):
                return int(val) == 1
            return str(val).strip().lower() in ("1", "true", "t", "yes", "sim")

        match_fids: list[int] = []
        matched_ids: set[str] = set()
        skipped_out_sp = 0
        for fid, n2, n5, n10, sp in rows:
            hit = None
            if n2 and n2 in processed:
                hit = n2
            elif n5 and n5 in processed:
                hit = n5
            elif n10 and n10 in processed:
                hit = n10
            if not hit:
                continue
            if not out_sp and has_sp and not sp_ok(sp):
                skipped_out_sp += 1
                continue
            match_fids.append(int(fid))
            matched_ids.add(hit)

        already_ok = con.execute(
            f'SELECT COUNT(*) FROM "{LAYER}" WHERE COALESCE(lidar_proc, 0) = 1'
        ).fetchone()[0]

        log(f"API processed tile_ids: {len(processed)}")
        log(f"GPKG rows matching those ids: {len(match_fids)}")
        log(f"Skipped outside SP (OUT_SP=false): {skipped_out_sp}")
        log(f"Unique API ids that hit at least one GPKG row: {len(matched_ids)}")
        log(f"API ids with no GPKG match: {len(processed) - len(matched_ids)}")
        log(f"GPKG rows already lidar_proc=1: {already_ok}")

        if dry_run:
            log("Dry-run — no write.")
            return {
                "api_ok": len(processed),
                "gpkg_matched_rows": len(match_fids),
                "skipped_out_sp": skipped_out_sp,
                "api_ids_matched": len(matched_ids),
                "api_ids_unmatched": len(processed) - len(matched_ids),
            }

        # Plain sqlite3 cannot run GPKG rtree triggers that call ST_IsEmpty.
        # Drop the "AFTER UPDATE ON" (any-column) triggers, patch attrs, restore.
        dropped = _rtree_update_any_triggers(con)
        for name, _sql in dropped:
            con.execute(f'DROP TRIGGER IF EXISTS "{name}"')
            log(f"temporarily dropped trigger {name}")

        con.execute("BEGIN")
        con.executemany(
            f'UPDATE "{LAYER}" SET lidar_proc = 1 WHERE fid = ?',
            [(fid,) for fid in match_fids],
        )
        con.commit()

        for name, sql in dropped:
            con.execute(sql)
            log(f"restored trigger {name}")
        con.commit()

        after = counts(con)
        log(f"GPKG after lidar_proc: {after}")
        return {
            "api_ok": len(processed),
            "gpkg_matched_rows": len(match_fids),
            "skipped_out_sp": skipped_out_sp,
            "api_ids_matched": len(matched_ids),
            "api_ids_unmatched": len(processed) - len(matched_ids),
            "gpkg_ok_before": int(before.get("1", 0)),
            "gpkg_ok_after": int(after.get("1", 0)),
        }
    except Exception:
        # Best-effort restore if we dropped triggers mid-flight
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
    ap.add_argument(
        "--out-sp",
        default="false",
        help="false=only sp_inside=1 (inside SP); true=SP + outside SP",
    )
    args = ap.parse_args()

    token = (args.token or "").strip()
    if not token:
        log("ALS_MACHINE_TOKEN required (env or --token).")
        return 1
    if not args.gpkg.is_file():
        log(f"GPKG not found: {args.gpkg}")
        return 1

    out_sp = str(args.out_sp).strip().lower() in ("1", "true", "t", "yes", "y", "sim")

    log(f"API: {args.api_url}")
    log(f"GPKG: {args.gpkg}")
    processed = load_processed_ids(args.api_url, token)
    log(f"Loaded {len(processed)} processed tile_ids from API")
    stats = sync(args.gpkg, processed, args.dry_run, out_sp=out_sp)
    log(f"Done: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Stage a limited batch of LAZ and optical GeoTIFF tiles from an external disk.

The external disk holds the full archive (tens of thousands of ``.laz`` and
``.tif`` files). This script copies a slice of that archive into a local
folder so a processing run has something to work on, without copying tiles
that are already there or already finished.

Quota
-----
Pass one limit, the other, or neither:

- ``--max-files N`` copies N files that are not already in the destination.
- ``--max-gb N`` copies until the next file would pass N gigabytes
  (1 GB = 1024^3 bytes). A file that does not fit is left behind and a
  later, smaller file may still be taken, so the budget is actually used.
- Both limits: copying stops when either one is reached.
- Neither limit: every file that passes the filters below is copied.

Filters, applied before the quota
----------------------------------
1. The destination is scanned by file name (case-insensitive). A name that
   is already there with a non-zero size is not copied again. The quota
   counts only new files: asking for 100 when 50 are already in
   ``C:\\OUTDIR`` copies 100 other files, not 50.
2. leucaena.earth is queried for tiles whose ``lidar_proc`` is ``1``
   (track ``lidar``, the same registry the GeoTrees LAStools batch uses).
   Those tiles are finished and are not staged again. Optical names such
   as ``ORTM_RGB_<tile>_V1.tif`` are reduced to the same tile id the R
   batch stores.

The list endpoint ``GET /api/v1/als/tiles`` caps at 5 000 rows and has no
page offset, so a catalogue of this size would silently drop finished
tiles. This script therefore reads ``GET /api/v1/als/export/registry.csv``
(full table, same host, same ``X-ALS-Token`` header as
``r/scripts/geotrees/als_registry_api.R``) and checks the count against
``GET /api/v1/als/summary``.

Authentication is ``ALS_MACHINE_TOKEN`` (or ``--als-api-token``). The URL
defaults to ``https://leucaena.earth`` (``ALS_API_URL`` / ``--als-api-url``).
Without a token the run stops, unless ``--ignore-api`` is set.

Copy rules
----------
Files are considered in sorted relative-path order, so the next run
continues where this one left off. Relative folders are preserved.
Each file is written to ``<name>.partial`` and renamed only after the
byte count matches the source. A second window is refused while
``.stage_external_laz_tif.lock`` is held (exit code 3).

Examples
--------
::

    python stage_external_laz_tif.py --source E:\\tiles --dest C:\\OUTDIR --max-files 100
    python stage_external_laz_tif.py --source E:\\tiles --dest C:\\OUTDIR --max-gb 40
    python stage_external_laz_tif.py --source E:\\tiles --dest C:\\OUTDIR --dry-run

The companion ``stage_external_laz_tif-sample.bat`` is the tracked
template. Copy it to ``stage_external_laz_tif.bat`` (gitignored) and edit
the paths and the token there.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import io
import json
import os
import re
import shutil
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_NAME = "stage_external_laz_tif.py"
DEFAULT_API_URL = "https://leucaena.earth"
EXTENSIONS = {".laz", ".tif", ".tiff"}
GB = 1024**3
COPY_BUFFER = 16 * 1024 * 1024
PROGRESS_EVERY = 256 * 1024 * 1024
PARTIAL_SUFFIX = ".partial"
LOCK_NAME = ".stage_external_laz_tif.lock"
MANIFEST_NAME = "stage_external_laz_tif_manifest.csv"
SKIP_DIRS = {
    "$recycle.bin",
    "system volume information",
    ".git",
    "__pycache__",
}
MANIFEST_COLUMNS = (
    "copied_utc",
    "hostname",
    "user",
    "rel_path",
    "file_name",
    "tile_id",
    "bytes",
    "dest_path",
)

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_AWAYMODE_REQUIRED = 0x00000040


def log(message: str) -> None:
    print(message, flush=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def human_bytes(n: int) -> str:
    value = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{n} B"


def format_seconds(seconds: float) -> str:
    total = int(max(0.0, seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes:02d}m {secs:02d}s"


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def positive_gb(text: str) -> float:
    try:
        value = float(text.strip().replace(",", "."))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a number of gigabytes") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def optical_tile_key(filename: str) -> str:
    """Strip IGC RGB/IR wrappers so an optical name matches the LAZ tile id.

    ``ORTM_RGB_<ID>_V1.tif`` and ``<ID>_ORTO_IR.tif`` both reduce to ``<ID>``.
    LAZ names are not passed through here: the R batch uses the stem as-is.
    """
    stem = Path(filename).stem
    stem = re.sub(r"(?i)^ORTM_(?:RGB|IR|RGBNIR)_", "", stem)
    stem = re.sub(r"(?i)^ORTO[_-](?:RGB|IR|RGBNIR)_", "", stem)
    previous = None
    while previous != stem:
        previous = stem
        stem = re.sub(r"(?i)_ORTO[_-](?:RGB|IR|RGBNIR)$", "", stem)
        stem = re.sub(r"(?i)_V\d+$", "", stem)
        stem = re.sub(r"(?i)_R\d+$", "", stem)
    return stem.strip()


def tile_ids_for(filename: str) -> frozenset[str]:
    stem = Path(filename).stem.strip()
    ids = {stem.upper()} if stem else set()
    ext = Path(filename).suffix.casefold()
    if ext in {".tif", ".tiff"}:
        cleaned = optical_tile_key(filename).strip()
        if cleaned:
            ids.add(cleaned.upper())
    return frozenset(ids)


def extended_path(path: str) -> str:
    """Win32 long-path form. No effect on other platforms."""
    absolute = os.path.abspath(path)
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def strip_extended(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def same_or_inside(child: str, parent: str) -> bool:
    child_n = os.path.normcase(os.path.normpath(strip_extended(child)))
    parent_n = os.path.normcase(os.path.normpath(strip_extended(parent)))
    return child_n == parent_n or child_n.startswith(parent_n + os.sep)


@dataclass(frozen=True)
class SourceFile:
    rel: str
    name: str
    size: int
    tile_ids: frozenset[str]
    name_key: str
    src_extended: str

    @property
    def primary_tile_id(self) -> str:
        if not self.tile_ids:
            return ""
        return sorted(self.tile_ids, key=len)[0]


@dataclass
class Partition:
    eligible: list[SourceFile]
    seen: int = 0
    unreadable: int = 0
    empty: int = 0
    already_in_dest: int = 0
    lidar_done: int = 0
    source_duplicate: int = 0


def partition_files(
    files: list[SourceFile],
    dest_blocking: set[str],
    processed_ids: set[str],
    processed_names: set[str],
) -> Partition:
    """Split an already-sorted inventory into 'copy' vs skip reasons.

    Destination and ``lidar_proc=1`` are decided before the duplicate-name
    rule, so two copies of a finished tile both count as finished.
    """
    out = Partition(eligible=[])
    seen_names: set[str] = set()
    out.seen = len(files)
    for item in files:
        if item.size <= 0:
            out.empty += 1
            continue
        if item.name_key in dest_blocking:
            out.already_in_dest += 1
            continue
        if item.tile_ids & processed_ids or item.name_key in processed_names:
            out.lidar_done += 1
            continue
        if item.name_key in seen_names:
            out.source_duplicate += 1
            continue
        seen_names.add(item.name_key)
        out.eligible.append(item)
    return out


def select_for_quota(
    files: list[SourceFile],
    max_files: int | None,
    max_bytes: int | None,
) -> tuple[list[SourceFile], int]:
    """Pick files until a file cap, a byte cap, or both, run out.

    Returns ``(chosen, n_that_did_not_fit)``. A file larger than the
    remaining byte budget is skipped and later smaller files are still
    considered. With both caps unset, every file is returned.
    """
    if max_files is None and max_bytes is None:
        return list(files), 0
    chosen: list[SourceFile] = []
    total = 0
    did_not_fit = 0
    for item in files:
        if max_files is not None and len(chosen) >= max_files:
            break
        if max_bytes is not None and total + item.size > max_bytes:
            did_not_fit += 1
            continue
        chosen.append(item)
        total += item.size
    return chosen, did_not_fit


def processed_from_csv(payload: bytes) -> tuple[set[str], set[str], int]:
    """Return ``(tile_ids, laz basenames, n_status_1)`` from a registry export.

    ``status_code`` on a ``track=lidar`` export is ``lidar_proc``.
    """
    text = payload.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    if "tile_id" not in fields:
        raise RuntimeError("registry CSV has no tile_id column")
    ids: set[str] = set()
    names: set[str] = set()
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
        laz = (row.get("laz_nome") or "").strip()
        if laz:
            names.add(Path(laz).name.casefold())
    return ids, names, len(ids)


def summary_counts(payload: bytes) -> dict[str, int]:
    data = json.loads(payload.decode("utf-8"))
    by = data.get("by_status") or {}
    counts: dict[str, int] = {}
    for key, value in by.items():
        counts[str(key)] = int(value)
    counts["total"] = int(data.get("total") or 0)
    return counts


class AlsClient:
    """Minimal leucaena.earth ALS client.

    Same contract as ``als_http_json`` in ``als_registry_api.R``:
    ``X-ALS-Token``, JSON in, JSON or CSV out. Reads only.
    """

    def __init__(self, base_url: str, token: str, timeout: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def get(self, path: str, query: dict[str, str]) -> bytes:
        url = self.base_url + path
        if query:
            url = url + "?" + urllib.parse.urlencode(query)
        last_error = "request failed"
        for attempt in range(1, 5):
            request = urllib.request.Request(
                url,
                headers={
                    "X-ALS-Token": self.token,
                    "Accept": "application/json, text/csv, */*",
                    "User-Agent": "leucaena-earth-utils/stage_external_laz_tif",
                },
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:400].strip()
                last_error = f"HTTP {exc.code} {detail}".strip()
                retry = exc.code in (408, 429) or exc.code >= 500
                if not retry:
                    break
            except urllib.error.URLError as exc:
                last_error = f"network error: {exc.reason}"
            if attempt < 4:
                time.sleep(min(2**attempt, 20))
                log(f"API retry {attempt}/3 after: {last_error}")
        raise RuntimeError(last_error)


def load_processed(client: AlsClient, track: str) -> tuple[set[str], set[str]]:
    column = "lidar_proc" if track == "lidar" else "rgbnir_proc"
    log(f"Reading {client.base_url}  track={track}  ({column}=1 means finished).")
    summary_raw = client.get("/api/v1/als/summary", {"track": track})
    counts = summary_counts(summary_raw)
    log(
        "Registry summary: "
        f"total={counts.get('total', 0)}  "
        f"pending={counts.get('0', 0)}  "
        f"ok={counts.get('1', 0)}  "
        f"fail={counts.get('2', 0)}  "
        f"claim={counts.get('3', 0)}  "
        f"skipped={counts.get('4', 0)}"
    )
    csv_raw = client.get("/api/v1/als/export/registry.csv", {"track": track})
    ids, names, n_done = processed_from_csv(csv_raw)
    reported = counts.get("1")
    log(
        f"Registry export: {n_done} tile(s) with {column}=1 "
        f"({len(names)} laz file name(s)). "
        "The CSV export is used because GET /api/v1/als/tiles stops at 5000 rows."
    )
    if reported is not None and reported != n_done:
        log(
            f"WARNING: summary ok={reported} but the CSV lists {n_done} finished tiles. "
            "The CSV is the skip list."
        )
        if reported > 0 and n_done == 0:
            raise RuntimeError(
                "summary reports finished tiles but the CSV export parsed none; "
                "refusing to copy without a trustworthy skip list"
            )
    return ids, names


def _pid_is_running(pid: int) -> bool:
    if pid <= 0 or os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return ctypes.get_last_error() == 5


class RunLock:
    """One copier at a time. A lock left by a dead process is removed."""

    def __init__(self, folder: Path) -> None:
        self.path = folder / LOCK_NAME
        self._held = False
        self.blocked = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            pid = _read_lock_pid(self.path)
            if pid and _pid_is_running(pid):
                self.blocked = True
                log(f"Another copy is already running (PID {pid}). Lock: {self.path}")
                return False
            log(f"Removing a stale lock (PID {pid or '?'} is not running).")
            try:
                self.path.unlink()
            except OSError as exc:
                log(f"Could not remove the stale lock {self.path}: {exc}")
                return False
        try:
            self.path.write_text(
                "\n".join(
                    [
                        f"pid={os.getpid()}",
                        f"hostname={socket.gethostname()}",
                        f"started_utc={utc_now()}",
                        f"script={SCRIPT_NAME}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            log(f"Could not create the lock {self.path}: {exc}")
            return False
        self._held = True
        return True

    def release(self) -> None:
        if not self._held:
            return
        try:
            self.path.unlink()
        except OSError as exc:
            log(f"WARNING: could not remove the lock {self.path}: {exc}")
        self._held = False


def _read_lock_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("pid="):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                return None
    return None


class KeepAwake:
    """Ask Windows not to sleep during a long copy off an external disk."""

    def __enter__(self) -> "KeepAwake":
        self._on = False
        if os.name != "nt":
            return self
        try:
            flags = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_AWAYMODE_REQUIRED
            self._on = ctypes.windll.kernel32.SetThreadExecutionState(flags) != 0
        except Exception:  # noqa: BLE001
            self._on = False
        return self

    def __exit__(self, *_) -> None:
        if not self._on:
            return
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        except Exception:  # noqa: BLE001
            pass


def scan_destination(dest: Path) -> tuple[set[str], list[str]]:
    """Return blocking basenames and a short list of empty files to replace.

    A name blocks when at least one copy in the destination has size > 0.
    """
    present: dict[str, int] = {}
    empty: list[str] = []
    if not dest.exists():
        return set(), empty
    root = extended_path(os.fspath(dest))
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name.casefold() not in SKIP_DIRS]
        for name in filenames:
            if name.casefold() in {LOCK_NAME.casefold(), MANIFEST_NAME.casefold()}:
                continue
            if name.endswith(PARTIAL_SUFFIX):
                continue
            ext = os.path.splitext(name)[1].casefold()
            if ext not in EXTENSIONS:
                continue
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            key = name.casefold()
            if size <= 0:
                empty.append(strip_extended(full))
                continue
            present[key] = present.get(key, 0) + size
    return set(present), empty


def remove_empty_named(empty_paths: list[str], name_key: str) -> int:
    removed = 0
    for path in empty_paths:
        if Path(path).name.casefold() != name_key:
            continue
        try:
            os.remove(extended_path(path))
            removed += 1
        except OSError as exc:
            log(f"WARNING: could not remove empty file {path}: {exc}")
    return removed


def _is_reparse_point(entry: os.DirEntry) -> bool:
    """Skip junctions and mount points so a walk cannot loop on Windows."""
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attrs & 0x400)


def iter_source_files(source: Path, dest: Path):
    root = extended_path(os.fspath(source))
    stack = [root]
    seen_dirs: set[str] = set()
    while stack:
        current = stack.pop()
        current_plain = strip_extended(current)
        marker = os.path.normcase(current_plain)
        if marker in seen_dirs:
            continue
        seen_dirs.add(marker)
        try:
            iterator = os.scandir(current)
        except OSError as exc:
            log(f"WARNING: cannot read {current_plain}: {exc}")
            continue
        with iterator:
            for entry in iterator:
                try:
                    if entry.is_symlink():
                        continue
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    if entry.name.casefold() in SKIP_DIRS:
                        continue
                    if _is_reparse_point(entry):
                        continue
                    if same_or_inside(entry.path, os.fspath(dest)):
                        continue
                    stack.append(entry.path)
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                ext = os.path.splitext(entry.name)[1].casefold()
                if ext not in EXTENSIONS:
                    continue
                yield entry


def inventory_source(source: Path, dest: Path) -> tuple[list[SourceFile], int]:
    files: list[SourceFile] = []
    unreadable = 0
    root_abs = os.path.abspath(os.fspath(source))
    indexed = 0
    for entry in iter_source_files(source, dest):
        indexed += 1
        plain = strip_extended(entry.path)
        try:
            size = entry.stat(follow_symlinks=False).st_size
        except OSError as exc:
            unreadable += 1
            log(f"WARNING: cannot stat {plain}: {exc}")
            continue
        rel = os.path.relpath(plain, root_abs)
        files.append(
            SourceFile(
                rel=rel,
                name=entry.name,
                size=size,
                tile_ids=tile_ids_for(entry.name),
                name_key=entry.name.casefold(),
                src_extended=entry.path,
            )
        )
        if indexed % 5000 == 0:
            log(f"Indexed {indexed} tile file(s) on the source disk...")
    files.sort(key=lambda item: item.rel.casefold())
    return files, unreadable


def copy_verified(src_extended: str, dest_file: Path) -> None:
    os.makedirs(extended_path(os.fspath(dest_file.parent)), exist_ok=True)
    partial = Path(str(dest_file) + PARTIAL_SUFFIX)
    partial_ext = extended_path(os.fspath(partial))
    dest_ext = extended_path(os.fspath(dest_file))
    if os.path.exists(partial_ext):
        os.remove(partial_ext)
    expected = os.path.getsize(src_extended)
    copied = 0
    last_report = 0
    label = dest_file.name
    show_progress = expected >= PROGRESS_EVERY
    try:
        with open(src_extended, "rb") as source, open(partial_ext, "wb") as target:
            while True:
                chunk = source.read(COPY_BUFFER)
                if not chunk:
                    break
                target.write(chunk)
                copied += len(chunk)
                if show_progress and (copied - last_report >= PROGRESS_EVERY or copied == expected):
                    print(
                        f"\r    {label}  {human_bytes(copied)} / {human_bytes(expected)}   ",
                        end="",
                        flush=True,
                    )
                    last_report = copied
            target.flush()
            os.fsync(target.fileno())
        if show_progress:
            print(flush=True)
        if os.path.getsize(partial_ext) != expected:
            raise OSError(f"size mismatch after copy ({copied} vs {expected})")
        os.replace(partial_ext, dest_ext)
        try:
            shutil.copystat(strip_extended(src_extended), strip_extended(dest_ext))
        except OSError:
            pass
    except Exception:
        if show_progress:
            print(flush=True)
        if os.path.exists(partial_ext):
            try:
                os.remove(partial_ext)
            except OSError:
                pass
        raise


def append_manifest(dest: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    path = dest / MANIFEST_NAME
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


def quota_text(max_files: int | None, max_gb: float | None) -> str:
    parts = []
    if max_files is not None:
        parts.append(f"{max_files} file(s)")
    if max_gb is not None:
        parts.append(f"{max_gb:g} GB")
    if not parts:
        return "no cap (every file that is still pending)"
    if len(parts) == 2:
        return parts[0] + " or " + parts[1] + ", whichever comes first"
    return parts[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Copy a quota of .laz and .tif tiles from an external disk into a "
            "local folder, skipping names already there and tiles with "
            "lidar_proc=1 on leucaena.earth."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--source", help="Folder on the external disk to read.")
    parser.add_argument("--dest", help="Local folder to receive the new files.")
    parser.add_argument(
        "--max-files",
        type=positive_int,
        default=None,
        help="How many new files to copy. Omit to leave this cap unset.",
    )
    parser.add_argument(
        "--max-gb",
        type=positive_gb,
        default=None,
        help="How many GB to copy (1024^3 bytes). Omit to leave this cap unset.",
    )
    parser.add_argument(
        "--als-api-url",
        default=os.environ.get("ALS_API_URL", DEFAULT_API_URL).strip() or DEFAULT_API_URL,
        help="Registry base URL. Env: ALS_API_URL.",
    )
    parser.add_argument(
        "--als-api-token",
        default="",
        help="Machine token. Prefer the ALS_MACHINE_TOKEN environment variable.",
    )
    parser.add_argument(
        "--track",
        choices=("lidar", "rgbnir"),
        default="lidar",
        help="Which registry column status_code=1 refers to. lidar means lidar_proc.",
    )
    parser.add_argument(
        "--ignore-api",
        action="store_true",
        help="Do not call leucaena.earth. Finished tiles are not filtered out.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan. Do not create the destination and do not copy.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run the built-in checks and exit.",
    )
    return parser


def run_self_test() -> int:
    assert optical_tile_key("ORTM_RGB_SF-22-V-B-III-3-SE-A-I_V1.tif") == "SF-22-V-B-III-3-SE-A-I"
    assert optical_tile_key("SF-22-V-B-III-3-SE-A-I_ORTO_IR.tif") == "SF-22-V-B-III-3-SE-A-I"
    laz_ids = tile_ids_for("SF-22-V-B-III-3-SE-A-I.laz")
    assert laz_ids == frozenset({"SF-22-V-B-III-3-SE-A-I"})
    tif_ids = tile_ids_for("ORTM_RGB_SF-22-V-B-III-3-SE-A-I_V1.tif")
    assert "SF-22-V-B-III-3-SE-A-I" in tif_ids

    def fake(name: str, size: int) -> SourceFile:
        return SourceFile(name, name, size, tile_ids_for(name), name.casefold(), name)

    files = [
        fake("a.laz", 10),
        fake("b.tif", 50),
        fake("c.laz", 5),
        fake("a.laz", 10),
        fake("done.laz", 7),
        fake("empty.laz", 0),
        fake("ORTM_RGB_done_V1.tif", 3),
    ]
    part = partition_files(
        files,
        dest_blocking={"b.tif"},
        processed_ids={"DONE"},
        processed_names=set(),
    )
    assert [item.name for item in part.eligible] == ["a.laz", "c.laz"]
    assert part.already_in_dest == 1
    assert part.lidar_done == 2
    assert part.source_duplicate == 1
    assert part.empty == 1

    chosen, skipped = select_for_quota(part.eligible, None, None)
    assert len(chosen) == 2 and skipped == 0
    chosen, skipped = select_for_quota(part.eligible, 1, None)
    assert [item.name for item in chosen] == ["a.laz"] and skipped == 0
    pool = [fake("big.laz", 100), fake("small.laz", 40), fake("tiny.laz", 30)]
    chosen, skipped = select_for_quota(pool, None, 80)
    assert [item.name for item in chosen] == ["small.laz", "tiny.laz"] and skipped == 1
    chosen, skipped = select_for_quota(pool, 1, 80)
    assert [item.name for item in chosen] == ["small.laz"]

    payload = (
        "tile_id,laz_nome,status,status_code\n"
        "ABC,ABC.laz,processed,1\n"
        "DEF,DEF.laz,erro,2\n"
    ).encode("utf-8")
    ids, names, n_done = processed_from_csv(payload)
    assert ids == {"ABC"} and names == {"abc.laz"} and n_done == 1
    counts = summary_counts(b'{"total": 3, "by_status": {"0": 1, "1": 2}}')
    assert counts["1"] == 2 and counts["total"] == 3
    log("self-test ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return run_self_test()
    if not args.source or not args.dest:
        log("Pass --source and --dest (or run with --self-test).")
        return 2

    source = Path(args.source)
    dest = Path(args.dest)
    if not source.is_dir():
        log(f"Source folder does not exist: {source}")
        return 2
    source_abs = os.path.abspath(os.fspath(source))
    dest_abs = os.path.abspath(os.fspath(dest))
    if os.path.normcase(source_abs) == os.path.normcase(dest_abs) or same_or_inside(
        source_abs, dest_abs
    ):
        log("The source folder is the destination, or is inside it. Pick two different folders.")
        return 2

    token = (args.als_api_token or os.environ.get("ALS_MACHINE_TOKEN", "")).strip()
    if not args.ignore_api and not token:
        log(
            "ALS_MACHINE_TOKEN is empty. Paste the token in the .bat "
            "(set \"ALS_MACHINE_TOKEN=...\") or pass --ignore-api."
        )
        return 2

    max_bytes = None if args.max_gb is None else int(args.max_gb * GB)
    log("=" * 60)
    log("Stage LAZ + optical GeoTIFF from external disk")
    log(f"Source : {source_abs}")
    log(f"Dest   : {dest_abs}")
    log(f"Quota  : {quota_text(args.max_files, args.max_gb)}")
    log(f"API    : {'off (--ignore-api)' if args.ignore_api else args.als_api_url}")
    log(f"Track  : {args.track}")
    log(f"Mode   : {'dry-run' if args.dry_run else 'copy'}")
    log("=" * 60)

    if args.dry_run and not dest.exists():
        log("Destination does not exist yet. A real run would create it.")

    processed_ids: set[str] = set()
    processed_names: set[str] = set()
    if not args.ignore_api:
        client = AlsClient(args.als_api_url.strip(), token)
        try:
            processed_ids, processed_names = load_processed(client, args.track)
        except (RuntimeError, json.JSONDecodeError, OSError, UnicodeError) as exc:
            log(f"Registry request failed: {exc}")
            log("Nothing was copied. Fix the token, the URL, or the network, then run again.")
            return 2

    lock: RunLock | None = None
    try:
        if not args.dry_run:
            dest.mkdir(parents=True, exist_ok=True)
            lock = RunLock(dest)
            if not lock.acquire():
                return 3 if lock.blocked else 2

        log("Scanning the destination for names already copied...")
        dest_blocking, empty_paths = scan_destination(dest) if dest.exists() else (set(), [])
        log(
            f"Destination already has {len(dest_blocking)} non-empty tile file name(s). "
            f"Empty leftovers: {len(empty_paths)}."
        )

        log("Indexing .laz / .tif / .tiff on the source. This walks the whole tree once.")
        started = time.monotonic()
        files, unreadable = inventory_source(source, dest)
        part = partition_files(files, dest_blocking, processed_ids, processed_names)
        part.unreadable = unreadable
        chosen, did_not_fit = select_for_quota(part.eligible, args.max_files, max_bytes)
        chosen_bytes = sum(item.size for item in chosen)
        log(
            f"Source tile files: {part.seen}  |  "
            f"already in dest: {part.already_in_dest}  |  "
            f"{args.track} finished: {part.lidar_done}  |  "
            f"duplicate name: {part.source_duplicate}  |  "
            f"empty: {part.empty}  |  "
            f"unreadable: {part.unreadable}"
        )
        log(
            f"Eligible: {len(part.eligible)}  |  "
            f"this run: {len(chosen)} file(s), {human_bytes(chosen_bytes)}"
        )
        if did_not_fit:
            log(f"{did_not_fit} eligible file(s) did not fit in the remaining GB budget.")
        if not chosen:
            log("Nothing to copy.")
            return 0

        if args.dry_run:
            for index, item in enumerate(chosen, start=1):
                log(f"[dry-run {index}/{len(chosen)}] {human_bytes(item.size):>10}  {item.rel}")
            log("Dry-run finished. No file was written.")
            return 0

        hostname = socket.gethostname()
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        copied = 0
        copied_bytes = 0
        failed = 0
        manifest_rows: list[dict[str, str]] = []
        copy_started = time.monotonic()
        try:
            with KeepAwake():
                for index, item in enumerate(chosen, start=1):
                    target = dest / item.rel
                    removed = remove_empty_named(empty_paths, item.name_key)
                    if removed:
                        log(f"Removed {removed} empty leftover(s) named {item.name}.")
                    log(
                        f"[{index}/{len(chosen)}] {human_bytes(copied_bytes)} copied so far  "
                        f"-> {item.rel} ({human_bytes(item.size)})"
                    )
                    try:
                        copy_verified(item.src_extended, target)
                    except OSError as exc:
                        failed += 1
                        log(f"FAILED {item.rel}: {exc}")
                        continue
                    copied += 1
                    copied_bytes += item.size
                    manifest_rows.append(
                        {
                            "copied_utc": utc_now(),
                            "hostname": hostname,
                            "user": user,
                            "rel_path": item.rel,
                            "file_name": item.name,
                            "tile_id": item.primary_tile_id,
                            "bytes": str(item.size),
                            "dest_path": os.fspath(target),
                        }
                    )
                    if len(manifest_rows) >= 25:
                        append_manifest(dest, manifest_rows)
                        manifest_rows.clear()
            append_manifest(dest, manifest_rows)
        except KeyboardInterrupt:
            append_manifest(dest, manifest_rows)
            log("Stopped by the user. Files already renamed into the destination stay there.")
            return 130

        elapsed = time.monotonic() - copy_started
        rate = copied_bytes / elapsed if elapsed > 0 else 0.0
        log("-" * 60)
        log(
            f"Copied {copied} file(s), {human_bytes(copied_bytes)} "
            f"in {format_seconds(elapsed)} ({human_bytes(int(rate))}/s)."
        )
        log(
            f"Failed: {failed}. "
            f"Index: {format_seconds(copy_started - started)}. "
            f"Copy: {format_seconds(elapsed)}."
        )
        log(f"Manifest: {dest / MANIFEST_NAME}")
        if failed:
            log("Re-run the same command. Names already copied are skipped, and the quota fills up again.")
            return 1
        return 0
    except KeyboardInterrupt:
        log("Stopped by the user before the copy finished.")
        return 130
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    sys.exit(main())

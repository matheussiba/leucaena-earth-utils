"""
Build 4-band RGB+IR GeoTIFFs per AOI tile by fusing two co-registered single-tile
rasters: an RGB raster (3 bands: R, G, B) and an IR raster stored as a "false
color" 3-band image whose **band 1 is the actual near-infrared channel**.

Why this exists
---------------
Storing both rasters keeps the same area on disk twice (RGB plus a 3-band IR
where only one band is meaningful). This script writes a single 4-band GeoTIFF
per tile with band assignments::

    band 1 = Red       (from RGB band 1)
    band 2 = Green     (from RGB band 2)
    band 3 = Blue      (from RGB band 3)
    band 4 = NIR       (from IR  band 1)

Tile selection (AOI)
--------------------
The list of tiles to fuse comes from a GeoPackage layer (the training AOI). The
attribute column ``TILE_ID_COLUMN`` (default ``NOMENC_10K``) provides the tile
identifier shared by the RGB and IR filenames.

Inputs / outputs
----------------
- Original RGB and IR folders are **never touched**. The 4-band output is
  written to a separate folder ``OUTPUT_FOLDER`` (created if missing).
- Output filename: ``<TILE_ID>_rgbir.tif``.
- The script verifies that the two source rasters cover the **same area at the
  same resolution and CRS**; if not, the tile is skipped with a clear reason.

Dependencies (see repo root ``requirements.txt``)
-------------------------------------------------
    pip install rasterio geopandas

Then::

    python build_4band_rgbir_geotiff_from_rgb_and_ir_false_color_per_aoi_tile.py
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Iterable

import geopandas as gpd
import rasterio
from rasterio.errors import RasterioIOError

# =============================================================================
# USER CONFIGURATION — edit only this block
# =============================================================================

# AOI GeoPackage and layer name. The layer must contain the tile-id column.
AOI_GPKG_PATH = r"G:\My Drive\PHD\02-Tese\02-data\adote-uma-leucena\v1-LEUCENA MAPPING\gdb-leucena_v2.gpkg"
AOI_LAYER_NAME = "articulacao_laser_voo22_AOI_treino"
TILE_ID_COLUMN = "NOMENC_10K"

# Source folders with original tiles
SOURCE_RGB_FOLDER = r"D:\rgb"
SOURCE_IR_FOLDER = r"D:\ir"

# Output folder for fused 4-band rasters (created if missing)
OUTPUT_FOLDER = r"D:\rgbir"

# Which band of the IR raster carries the actual NIR signal (1-based)
IR_NIR_BAND_INDEX = 1

# If True, fall back to shorter prefixes of the tile id when no exact match
# is found (mirrors the cascade used in the IGC tile copy script).
USE_SUFFIX_CASCADE = True

# Skip output that already exists (resume-safe). Set to True to redo files.
OVERWRITE_EXISTING_FILES = False

# GeoTIFF write options
COMPRESS = "DEFLATE"    # DEFLATE / LZW / ZSTD / None
PREDICTOR = 2           # 2 for integer bands (uint8/uint16), 3 for float
TILED = True
BLOCKXSIZE = 512
BLOCKYSIZE = 512

# =============================================================================
# Implementation
# =============================================================================

RASTER_EXTENSIONS = (".tif", ".tiff")


@dataclass
class TileSummary:
    tile_id: str
    rgb_name: str | None
    ir_name: str | None
    status: str
    detail: str = ""


def _list_rasters(folder: str) -> list[str]:
    if not os.path.isdir(folder):
        return []
    return [f for f in os.listdir(folder) if f.lower().endswith(RASTER_EXTENSIONS)]


def _match_for_token(filenames: Iterable[str], token: str) -> list[str]:
    """Return files whose name contains ``token`` as a whole alphanumeric token."""
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    return [f for f in filenames if pattern.search(f)]


def _find_tile_file(filenames: list[str], tile_id: str) -> str | None:
    """Find a single raster for ``tile_id`` (exact, then optional suffix cascade)."""
    hits = _match_for_token(filenames, tile_id)
    if hits:
        return sorted(hits)[0]
    if not USE_SUFFIX_CASCADE:
        return None
    parts = tile_id.split("-")
    while len(parts) > 1:
        parts.pop()
        token = "-".join(parts)
        hits = _match_for_token(filenames, token)
        if hits:
            return sorted(hits)[0]
    return None


def _approx_equal_transform(a, b, tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(tuple(a)[:6], tuple(b)[:6]))


def _fuse_one_tile(rgb_path: str, ir_path: str, out_path: str) -> tuple[str, str]:
    """Fuse one RGB + IR pair into a 4-band GeoTIFF. Returns (status, detail)."""
    try:
        with rasterio.open(rgb_path) as rgb_src, rasterio.open(ir_path) as ir_src:
            if rgb_src.count < 3:
                return ("rgb_band_count", f"RGB has {rgb_src.count} bands (<3)")
            if ir_src.count < IR_NIR_BAND_INDEX:
                return ("ir_band_count", f"IR has {ir_src.count} bands; NIR index {IR_NIR_BAND_INDEX} unavailable")
            if (rgb_src.width, rgb_src.height) != (ir_src.width, ir_src.height):
                return ("shape_mismatch", f"RGB {rgb_src.width}x{rgb_src.height} vs IR {ir_src.width}x{ir_src.height}")
            if rgb_src.crs != ir_src.crs:
                return ("crs_mismatch", f"RGB={rgb_src.crs} IR={ir_src.crs}")
            if not _approx_equal_transform(rgb_src.transform, ir_src.transform):
                return ("transform_mismatch", "RGB and IR pixel grids differ")

            profile = rgb_src.profile.copy()
            profile.update(
                count=4,
                driver="GTiff",
                tiled=TILED,
                blockxsize=BLOCKXSIZE,
                blockysize=BLOCKYSIZE,
            )
            if COMPRESS:
                profile.update(compress=COMPRESS)
                if PREDICTOR:
                    profile.update(predictor=PREDICTOR)

            # Use band 1 of RGB to keep dtype + nodata aligned with the source.
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with rasterio.open(out_path, "w", **profile) as dst:
                dst.write(rgb_src.read(1), 1)
                dst.write(rgb_src.read(2), 2)
                dst.write(rgb_src.read(3), 3)
                dst.write(ir_src.read(IR_NIR_BAND_INDEX), 4)
                dst.descriptions = ("Red", "Green", "Blue", "NIR")
                dst.update_tags(
                    PROCESSING_SCRIPT="build_4band_rgbir_geotiff_from_rgb_and_ir_false_color_per_aoi_tile.py",
                    SOURCE_RGB=os.path.basename(rgb_path),
                    SOURCE_IR=os.path.basename(ir_path),
                    IR_NIR_BAND_INDEX=str(IR_NIR_BAND_INDEX),
                )
        return ("ok", "")
    except RasterioIOError as exc:
        return ("io_error", str(exc))


def _load_aoi_tile_ids() -> list[str]:
    print(f"Reading AOI layer: {AOI_GPKG_PATH} | layer={AOI_LAYER_NAME}")
    gdf = gpd.read_file(AOI_GPKG_PATH, layer=AOI_LAYER_NAME)
    if TILE_ID_COLUMN not in gdf.columns:
        raise SystemExit(
            f"[ABORT] Column {TILE_ID_COLUMN!r} not found in layer. "
            f"Columns available: {list(gdf.columns)}"
        )
    ids = (
        gdf[TILE_ID_COLUMN]
        .dropna()
        .astype(str)
        .str.strip()
        .replace("", None)
        .dropna()
        .unique()
        .tolist()
    )
    print(f"  AOI tiles: {len(ids)}")
    return ids


def main() -> None:
    t_start = time.time()

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)

    tile_ids = _load_aoi_tile_ids()
    if not tile_ids:
        print("[ABORT] No tile ids in AOI.")
        return

    rgb_names = _list_rasters(SOURCE_RGB_FOLDER)
    ir_names = _list_rasters(SOURCE_IR_FOLDER)
    if not rgb_names:
        print(f"[ABORT] No rasters in {SOURCE_RGB_FOLDER}")
        return
    if not ir_names:
        print(f"[ABORT] No rasters in {SOURCE_IR_FOLDER}")
        return
    print(f"  RGB files available: {len(rgb_names)} | IR files available: {len(ir_names)}")

    summaries: list[TileSummary] = []
    written = 0
    skipped_existing = 0

    for i, tile_id in enumerate(tile_ids, 1):
        rgb_name = _find_tile_file(rgb_names, tile_id)
        ir_name = _find_tile_file(ir_names, tile_id)

        if not rgb_name and not ir_name:
            summaries.append(TileSummary(tile_id, None, None, "missing_both"))
            continue
        if not rgb_name:
            summaries.append(TileSummary(tile_id, None, ir_name, "missing_rgb"))
            continue
        if not ir_name:
            summaries.append(TileSummary(tile_id, rgb_name, None, "missing_ir"))
            continue

        out_name = f"{tile_id}_rgbir.tif"
        out_path = os.path.join(OUTPUT_FOLDER, out_name)
        if os.path.exists(out_path) and not OVERWRITE_EXISTING_FILES:
            skipped_existing += 1
            summaries.append(TileSummary(tile_id, rgb_name, ir_name, "skipped_existing"))
            continue

        rgb_path = os.path.join(SOURCE_RGB_FOLDER, rgb_name)
        ir_path = os.path.join(SOURCE_IR_FOLDER, ir_name)
        status, detail = _fuse_one_tile(rgb_path, ir_path, out_path)
        summaries.append(TileSummary(tile_id, rgb_name, ir_name, status, detail))

        if status == "ok":
            written += 1
            print(f"[{i}/{len(tile_ids)}] OK     {tile_id} -> {out_name}")
        else:
            print(f"[{i}/{len(tile_ids)}] FAIL   {tile_id} ({status}) {detail}")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  AOI tiles:        {len(tile_ids)}")
    print(f"  Written:          {written}")
    print(f"  Skipped existing: {skipped_existing}")
    by_status: dict[str, int] = {}
    for s in summaries:
        by_status[s.status] = by_status.get(s.status, 0) + 1
    for status, count in sorted(by_status.items()):
        print(f"  {status:18s} {count}")
    print(f"\nElapsed: {time.time() - t_start:.2f} s")


if __name__ == "__main__":
    main()

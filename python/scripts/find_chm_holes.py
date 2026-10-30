"""
find_chm_holes.py
=================
Encontra buracos sem cobertura LiDAR em um raster CHM (+ intensidade opcional).

Criterio "hard miss" (padrao):
  CHM == 0 (ou NaN)  E  INTENSITY == 0 (ou NaN), se a banda 2 existir.
  Se so houver 1 banda, usa so CHM == 0 / NaN.

Saidas (mesma pasta do --out-dir, default = Desktop):
  - <nome>_missing_overview.png
  - <nome>_missing_holes.geojson  (buracos grandes, WGS84)

Uso (PowerShell):
  python find_chm_holes.py
  python find_chm_holes.py --chm "E:/dados-msb/00_DATASETS_AI/.../lidar/rasters/TILE.tif"
  python find_chm_holes.py --chm ... --min-area-m2 500 --block 512
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
import rasterio
from rasterio import features
from shapely.geometry import shape

try:
    import geopandas as gpd
except ImportError as e:
    raise SystemExit("Precisa geopandas: pip install geopandas") from e


def desktop() -> Path:
    for p in (
        Path(os.path.expanduser(r"~\OneDrive\Desktop")),
        Path(os.path.expanduser(r"~\Desktop")),
    ):
        if p.is_dir():
            return p
    return Path.cwd()


def parse_args():
    p = argparse.ArgumentParser(description="Localiza buracos sem cobertura no CHM/LiDAR.")
    p.add_argument(
        "--chm",
        default=r"C:\00_DATASETS_AI\261002-riodaspedras-aoi\lidar\rasters\SF-23-Y-A-IV-4-SO-A.tif",
        help="GeoTIFF CHM (banda 1) e opcionalmente intensidade (banda 2).",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        help="Pasta de saida (default: Desktop).",
    )
    p.add_argument("--block", type=int, default=512, help="Tamanho do bloco para listar vazios.")
    p.add_argument(
        "--empty-frac",
        type=float,
        default=0.90,
        help="Fracao minima de miss no bloco para reportar (0-1).",
    )
    p.add_argument(
        "--min-area-m2",
        type=float,
        default=500.0,
        help="Area minima (m2) para exportar poligono no GeoJSON.",
    )
    p.add_argument(
        "--poly-step",
        type=int,
        default=4,
        help="Downsample da mascara antes de polygonizar (mais rapido / GeoJSON menor).",
    )
    p.add_argument(
        "--chm-only",
        action="store_true",
        help="Ignora intensidade; buraco = so CHM==0/NaN.",
    )
    return p.parse_args()


def main():
    a = parse_args()
    chm_path = Path(a.chm)
    if not chm_path.is_file():
        raise SystemExit(f"CHM nao encontrado: {chm_path}")

    out_dir = Path(a.out_dir) if a.out_dir else desktop()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = chm_path.stem

    print(f"CHM: {chm_path}")
    with rasterio.open(chm_path) as s:
        print(f"  size={s.width}x{s.height} res={s.res} crs={s.crs} bands={s.count}")
        ch = s.read(1).astype(np.float32)
        miss_chm = ~np.isfinite(ch) | (ch == 0)

        if (not a.chm_only) and s.count >= 2:
            inten = s.read(2).astype(np.float32)
            miss_int = ~np.isfinite(inten) | (inten == 0)
            miss = miss_chm & miss_int
            mode = "CHM==0 AND INT==0 (sem retorno util)"
        else:
            miss = miss_chm
            mode = "CHM==0/NaN apenas"

        pct = 100.0 * float(miss.mean())
        print(f"  criterio: {mode}")
        print(f"  pixels missing: {int(miss.sum()):,} / {miss.size:,} ({pct:.2f}%)")
        print(f"  CHM min/max (finite): {float(np.nanmin(ch)):.2f} / {float(np.nanmax(ch)):.2f}")

        # blocos vazios
        h, w = miss.shape
        empty = []
        for r0 in range(0, h, a.block):
            for c0 in range(0, w, a.block):
                r1 = min(r0 + a.block, h)
                c1 = min(c0 + a.block, w)
                frac = float(miss[r0:r1, c0:c1].mean())
                if frac >= a.empty_frac:
                    x = s.transform.c + ((c0 + c1) / 2.0) * s.transform.a
                    y = s.transform.f + ((r0 + r1) / 2.0) * s.transform.e
                    empty.append((frac, r0, c0, r1, c1, x, y))
        empty.sort(reverse=True)
        print(f"  blocos {a.block}px com miss>={a.empty_frac:.0%}: {len(empty)}")
        for frac, r0, c0, r1, c1, x, y in empty[:15]:
            print(f"    {frac:.2f} rows[{r0}:{r1}] cols[{c0}:{c1}] xy=({x:.0f},{y:.0f})")

        # overview PNG
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            step = max(8, min(h, w) // 800)
            fig, ax = plt.subplots(figsize=(8, 6))
            ax.imshow(miss[::step, ::step], cmap="magma", vmin=0, vmax=1)
            ax.set_title(f"Missing LiDAR ({mode})\n{pct:.1f}% of scene")
            ax.axis("off")
            png = out_dir / f"{stem}_missing_overview.png"
            fig.tight_layout()
            fig.savefig(png, dpi=150)
            plt.close()
            print(f"  overview: {png}")
        except Exception as e:
            print(f"  overview PNG falhou: {e}")

        # GeoJSON dos buracos
        step = max(1, int(a.poly_step))
        m = miss[::step, ::step].astype(np.uint8)
        t = s.transform
        transform = rasterio.Affine(t.a * step, t.b, t.c, t.d, t.e * step, t.f)
        geoms = [
            shape(g)
            for g, v in features.shapes(m, mask=m.astype(bool), transform=transform)
            if v == 1
        ]
        gdf = gpd.GeoDataFrame({"missing": [1] * len(geoms)}, geometry=geoms, crs=s.crs)
        if len(gdf):
            gdf["area_m2"] = gdf.geometry.area
            gdf = gdf[gdf["area_m2"] >= float(a.min_area_m2)].copy()
            n = len(gdf)
            largest_ha = float(gdf["area_m2"].max()) / 1e4 if n else 0.0
            print(f"  poligonos >= {a.min_area_m2} m2: {n} | maior={largest_ha:.2f} ha")
            gj = out_dir / f"{stem}_missing_holes.geojson"
            gdf.to_crs(4326).drop(columns=["area_m2"], errors="ignore").to_file(
                gj, driver="GeoJSON"
            )
            print(f"  geojson: {gj}")
        else:
            print("  nenhum poligono missing encontrado.")

    print("Pronto.")


if __name__ == "__main__":
    main()

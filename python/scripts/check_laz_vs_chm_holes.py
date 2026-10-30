"""
check_laz_vs_chm_holes.py
=========================
Confere nos .LAZ brutos se areas sem CHM realmente nao tem pontos.

1) Acha buracos no raster CHM (CHM==0 e INT==0, se banda 2 existir)
2) Conta pontos dos .laz DENTRO dessas bboxes
3) Compara com uma bbox controle ao lado

Uso:
  python check_laz_vs_chm_holes.py
  python check_laz_vs_chm_holes.py --laz-dir "E:/.../lidar/raw" --chm "E:/.../rasters/TILE.tif"
"""
from __future__ import annotations

import argparse
from pathlib import Path

import laspy
import numpy as np
import rasterio


def parse_args():
    p = argparse.ArgumentParser(description="Conta pontos LAZ dentro dos buracos do CHM.")
    p.add_argument(
        "--laz-dir",
        default=r"C:\00_DATASETS_AI\261002-riodaspedras-aoi\lidar\raw",
    )
    p.add_argument(
        "--chm",
        default=r"C:\00_DATASETS_AI\261002-riodaspedras-aoi\lidar\rasters\SF-23-Y-A-IV-4-SO-A.tif",
    )
    p.add_argument("--block", type=int, default=512)
    p.add_argument("--top", type=int, default=5, help="Quantos buracos (blocos) testar.")
    p.add_argument("--pad-m", type=float, default=20.0)
    p.add_argument("--chunk", type=int, default=2_000_000)
    p.add_argument("--shift-m", type=float, default=400.0, help="Deslocamento da bbox controle.")
    return p.parse_args()


def find_empty_blocks(chm_path: Path, block: int, top: int, pad_m: float):
    with rasterio.open(chm_path) as s:
        ch = s.read(1)
        inten = s.read(2) if s.count >= 2 else None
        miss = (ch == 0) | ~np.isfinite(ch)
        if inten is not None:
            miss &= (inten == 0) | ~np.isfinite(inten)

        h, w = miss.shape
        empty = []
        for r0 in range(0, h, block):
            for c0 in range(0, w, block):
                r1, c1 = min(r0 + block, h), min(c0 + block, w)
                frac = float(miss[r0:r1, c0:c1].mean())
                if frac < 0.90:
                    continue
                xs = [s.transform.c + c0 * s.transform.a, s.transform.c + c1 * s.transform.a]
                ys = [s.transform.f + r0 * s.transform.e, s.transform.f + r1 * s.transform.e]
                xmin, xmax = min(xs) - pad_m, max(xs) + pad_m
                ymin, ymax = min(ys) - pad_m, max(ys) + pad_m
                empty.append((frac, r0, c0, r1, c1, xmin, xmax, ymin, ymax))
        empty.sort(reverse=True)
        print(f"CHM: {chm_path}")
        print(f"  crs={s.crs} size={s.width}x{s.height}")
        print(f"  missing (CHM0&INT0) = {100*miss.mean():.2f}% da cena")
        print(f"  blocos {block}px com miss>=90%: {len(empty)} (testando top {top})")
        return empty[:top], s.crs


def count_in_bbox(laz_files, xmin, xmax, ymin, ymax, chunk):
    total_inside = 0
    per_file = []
    for laz in laz_files:
        with laspy.open(laz) as reader:
            mins, maxs = reader.header.mins, reader.header.maxs
            hx0, hy0 = float(mins[0]), float(mins[1])
            hx1, hy1 = float(maxs[0]), float(maxs[1])
            if hx1 < xmin or hx0 > xmax or hy1 < ymin or hy0 > ymax:
                per_file.append((laz.name, 0, False))
                continue
            inside = 0
            for pts in reader.chunk_iterator(chunk):
                x = np.asarray(pts.x)
                y = np.asarray(pts.y)
                inside += int(((x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)).sum())
            per_file.append((laz.name, inside, True))
            total_inside += inside
    return total_inside, per_file


def main():
    a = parse_args()
    laz_dir = Path(a.laz_dir)
    files = sorted(laz_dir.glob("*.laz"))
    if not files:
        raise SystemExit(f"Nenhum .laz em {laz_dir}")
    print(f"LAZ dir: {laz_dir} ({len(files)} arquivos)")
    for f in files:
        print(f"  {f.name}  {f.stat().st_size/1e9:.2f} GB")

    holes, crs = find_empty_blocks(Path(a.chm), a.block, a.top, a.pad_m)
    print(f"CRS: {crs}\n")

    for i, (frac, r0, c0, r1, c1, xmin, xmax, ymin, ymax) in enumerate(holes, 1):
        area = (xmax - xmin) * (ymax - ymin)
        print("=" * 60)
        print(f"BURACO #{i}  miss_frac={frac:.2f}  rows[{r0}:{r1}] cols[{c0}:{c1}]")
        print(f"  bbox X[{xmin:.1f},{xmax:.1f}] Y[{ymin:.1f},{ymax:.1f}]  ~{area/1e4:.2f} ha")

        inside, per = count_in_bbox(files, xmin, xmax, ymin, ymax, a.chunk)
        for name, n, ov in per:
            if ov or n:
                print(f"  {name}: {n:,} pts no buraco")
        dens = inside / area if area else 0
        print(f"  TOTAL no buraco: {inside:,} pts | dens={dens:.3f} pts/m2")

        # controle
        w = xmax - xmin
        cx0, cx1 = xmin - a.shift_m - w, xmin - a.shift_m
        cy0, cy1 = ymin, ymax
        ctrl, _ = count_in_bbox(files, cx0, cx1, cy0, cy1, a.chunk)
        cdens = ctrl / area if area else 0
        print(f"  CONTROLE (shift -{a.shift_m}m X): {ctrl:,} pts | dens={cdens:.3f} pts/m2")

        if inside == 0:
            print("  => Sem pontos no LAZ: gap real de cobertura.")
        elif dens < 0.05 and cdens > 1:
            print("  => Quase sem pontos (esparso demais p/ CHM util).")
        else:
            print("  => Ha pontos; se CHM=0, investigar prep/filtro.")

    print("\nPronto.")


if __name__ == "__main__":
    main()

"""
batch_check_laz_holes.py
========================
Vistoria rápida de buracos (gaps de cobertura) em todos os .LAZ IGC de uma pasta.

Ideia (em espírito do check_laz_vs_chm_holes.py, sem precisar de CHM):
  1) Lê X/Y do LAZ em chunks
  2) Marca ocupação numa grade grossa (cell_m)
  3) Células vazias no INTERIOR (borda ignorada) viram candidatos a buraco
  4) Componentes conectados com área >= min_hole_m2 = hole

Saídas (padrão no disco D:):
  D:\\laz_holes_igc.gpkg
      layer laz_tiles  -> um polígono por arquivo (articulação IGC se casar o id)
      layer laz_holes  -> polígonos dos buracos (CRS do LAZ / UTM local)
  D:\\laz_holes_summary.csv
  D:\\laz_holes_registry.csv   (retomável: reexecução pula o que já terminou)
  D:\\laz_holes_relatorio.md   (texto para reunião / professor)

Uso (PowerShell, máquina do lab com D:\\laz):
  python batch_check_laz_holes.py
  python batch_check_laz_holes.py --deadline-min 25
  python batch_check_laz_holes.py --laz-dir "D:\\laz" --workers 8 --cell-m 50
  python batch_check_laz_holes.py --fast --deadline-min 20
  python batch_check_laz_holes.py --limit 20   # fumaça
  python batch_check_laz_holes.py --report-only   # só regenera CSV/.md do registry (não reprocessa)

Dependências: laspy, numpy, geopandas, shapely  (+ pyogrio recomendado)
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    import laspy
except ImportError as e:
    raise SystemExit("Precisa laspy: pip install laspy lazrs") from e

try:
    import geopandas as gpd
    from shapely.geometry import box, Polygon
except ImportError as e:
    raise SystemExit("Precisa geopandas/shapely: pip install geopandas") from e


# -----------------------------------------------------------------------------
# Defaults (lab / IGC)
# -----------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent.parent
_BUNDLED_ARTIC = (
    _REPO_ROOT
    / "data"
    / "articulacao_igc_sp"
    / "articulacao-igc-merged-spatialindex-sirgas2000.gpkg"
)
_LOCAL_ARTIC = Path(r"D:\_articulacao_igc_sp\articulacao-igc-merged-spatialindex-sirgas2000.gpkg")

DEFAULT_LAZ_DIR = r"D:\laz"
DEFAULT_OUT_GPKG = r"D:\laz_holes_igc.gpkg"
DEFAULT_OUT_DIR = r"D:\\"
DEFAULT_ARTIC_LAYER = "articulacao-igc-merged-spatialindex-sirgas2000"
VERSION = "1.1.0"

AUTO_ID_COLUMNS = ("NOMENC_2K", "NOMENC_5K", "NOMENC_10K")

TILES_LAYER = "laz_tiles"
HOLES_LAYER = "laz_holes"
REGISTRY_NAME = "laz_holes_registry.csv"
SUMMARY_NAME = "laz_holes_summary.csv"
REPORT_NAME = "laz_holes_relatorio.md"


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Batch: acha buracos de cobertura em todos os LAZ de uma pasta."
    )
    p.add_argument("--laz-dir", default=DEFAULT_LAZ_DIR, help="Pasta raiz dos .laz (recursivo).")
    p.add_argument(
        "--out-gpkg",
        default=DEFAULT_OUT_GPKG,
        help="GeoPackage de saida (tiles + buracos).",
    )
    p.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help="Pasta dos CSVs (summary + registry). Default: D:\\",
    )
    p.add_argument(
        "--articulacao",
        default=str(_BUNDLED_ARTIC if _BUNDLED_ARTIC.is_file() else _LOCAL_ARTIC),
        help="GPKG da articulacao IGC (opcional; so para geometria do tile).",
    )
    p.add_argument("--articulacao-layer", default=DEFAULT_ARTIC_LAYER)
    p.add_argument("--cell-m", type=float, default=50.0, help="Tamanho da celula da grade (m).")
    p.add_argument(
        "--border-cells",
        type=int,
        default=2,
        help="Anel de celulas na borda ignorado (nao conta como buraco).",
    )
    p.add_argument(
        "--min-hole-m2",
        type=float,
        default=2500.0,
        help="Area minima (m2) de um componente vazio interior para reportar.",
    )
    p.add_argument("--chunk", type=int, default=2_000_000, help="Pontos por chunk laspy.")
    p.add_argument(
        "--subsample",
        type=int,
        default=2,
        help="Usa 1 a cada N pontos (1=todos). Acelera; 2-5 costuma bastar em cell_m>=50.",
    )
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    p.add_argument("--limit", type=int, default=0, help="Processa so os N primeiros (0=todos).")
    p.add_argument(
        "--deadline-min",
        type=float,
        default=0.0,
        help="Para apos N minutos e grava o que ja tiver (0=sem limite). Ideal p/ reuniao.",
    )
    p.add_argument(
        "--fast",
        action="store_true",
        help="Atalho: cell_m=100, subsample=5, border=1 (vistoria bem mais rapida).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Reprocessa mesmo se o arquivo ja estiver no registry como ok/has_hole.",
    )
    p.add_argument(
        "--no-artic",
        action="store_true",
        help="Nao tenta casar com articulacao; usa so bbox do header.",
    )
    p.add_argument(
        "--report-only",
        action="store_true",
        help="Nao processa LAZ: so le o registry ja gravado e regenera summary + .md da reuniao.",
    )
    return p.parse_args()


# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_tempo(segundos: float) -> str:
    m, s = divmod(int(max(0.0, segundos)), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m:02d}m {s:02d}s"


def list_laz(root: Path) -> list[Path]:
    files = sorted({p.resolve() for p in root.rglob("*.laz")} | {p.resolve() for p in root.rglob("*.las")})
    return files


def atomic_to_file(path: Path, write_fn) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    write_fn(tmp)
    os.replace(tmp, path)


# -----------------------------------------------------------------------------
# Occupancy / holes (worker)
# -----------------------------------------------------------------------------

@dataclass
class LazHoleResult:
    laz_path: str
    laz_nome: str
    status: str
    has_hole: int
    n_points_used: int
    n_points_header: int
    size_gb: float
    crs_wkt: str
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    cell_m: float
    n_cols: int
    n_rows: int
    n_empty_interior: int
    empty_frac_interior: float
    n_holes: int
    largest_hole_m2: float
    holes_area_m2: float
    elapsed_s: float
    erro: str
    hole_polys_xy: list  # list of (area_m2, [(x,y), ...]) exterior rings


def _components_4(mask: np.ndarray) -> list[np.ndarray]:
    """Connected components (4-neigh) of True cells. Returns list of (row,col) arrays."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    comps: list[np.ndarray] = []
    for r in range(h):
        for c in range(w):
            if not mask[r, c] or seen[r, c]:
                continue
            stack = [(r, c)]
            seen[r, c] = True
            cells: list[tuple[int, int]] = []
            while stack:
                cr, cc = stack.pop()
                cells.append((cr, cc))
                for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    nr, nc = cr + dr, cc + dc
                    if 0 <= nr < h and 0 <= nc < w and mask[nr, nc] and not seen[nr, nc]:
                        seen[nr, nc] = True
                        stack.append((nr, nc))
            comps.append(np.asarray(cells, dtype=np.int32))
    return comps


def _cells_to_poly(cells: np.ndarray, xmin: float, ymax: float, cell: float) -> Polygon | None:
    """Union of cell squares via discrete outline -> shapely (axis-aligned merge by buffer0)."""
    from shapely.ops import unary_union

    squares = []
    for r, c in cells:
        x0 = xmin + c * cell
        y1 = ymax - r * cell
        x1 = x0 + cell
        y0 = y1 - cell
        squares.append(box(x0, y0, x1, y1))
    if not squares:
        return None
    geom = unary_union(squares)
    if geom.is_empty:
        return None
    if geom.geom_type == "MultiPolygon":
        # keep largest part for the ring export; caller may split later
        geom = max(geom.geoms, key=lambda g: g.area)
    if geom.geom_type != "Polygon":
        return None
    return geom


def analyze_one_laz(
    path_str: str,
    cell_m: float,
    chunk: int,
    subsample: int,
    border_cells: int,
    min_hole_m2: float,
) -> dict:
    t0 = time.perf_counter()
    path = Path(path_str)
    out = LazHoleResult(
        laz_path=str(path),
        laz_nome=path.name,
        status="erro",
        has_hole=0,
        n_points_used=0,
        n_points_header=0,
        size_gb=path.stat().st_size / 1e9 if path.is_file() else 0.0,
        crs_wkt="",
        xmin=0.0,
        xmax=0.0,
        ymin=0.0,
        ymax=0.0,
        cell_m=float(cell_m),
        n_cols=0,
        n_rows=0,
        n_empty_interior=0,
        empty_frac_interior=0.0,
        n_holes=0,
        largest_hole_m2=0.0,
        holes_area_m2=0.0,
        elapsed_s=0.0,
        erro="",
        hole_polys_xy=[],
    )
    try:
        with laspy.open(path) as reader:
            hdr = reader.header
            out.n_points_header = int(hdr.point_count)
            mins, maxs = hdr.mins, hdr.maxs
            xmin, ymin = float(mins[0]), float(mins[1])
            xmax, ymax = float(maxs[0]), float(maxs[1])
            # pad half cell so borda do header nao vira “falso buraco” por arredondamento
            pad = cell_m * 0.5
            xmin -= pad
            ymin -= pad
            xmax += pad
            ymax += pad
            out.xmin, out.xmax, out.ymin, out.ymax = xmin, xmax, ymin, ymax
            try:
                crs = hdr.parse_crs()
                if crs is not None:
                    out.crs_wkt = crs.to_wkt() if hasattr(crs, "to_wkt") else str(crs)
            except Exception:
                out.crs_wkt = ""

            width = max(xmax - xmin, cell_m)
            height = max(ymax - ymin, cell_m)
            ncols = max(1, int(np.ceil(width / cell_m)))
            nrows = max(1, int(np.ceil(height / cell_m)))
            # guarda de memoria: grade absurda = bbox ruim
            if ncols * nrows > 5_000_000:
                out.erro = f"grade grande demais ({ncols}x{nrows}); confira CRS/bbox"
                out.elapsed_s = time.perf_counter() - t0
                return out.__dict__

            out.n_cols, out.n_rows = ncols, nrows
            occ = np.zeros((nrows, ncols), dtype=bool)
            step = max(1, int(subsample))
            used = 0

            for pts in reader.chunk_iterator(chunk):
                x = np.asarray(pts.x)
                y = np.asarray(pts.y)
                if step > 1:
                    x = x[::step]
                    y = y[::step]
                if x.size == 0:
                    continue
                cols = np.floor((x - xmin) / cell_m).astype(np.int32)
                rows = np.floor((ymax - y) / cell_m).astype(np.int32)
                m = (cols >= 0) & (cols < ncols) & (rows >= 0) & (rows < nrows)
                if not np.any(m):
                    continue
                occ[rows[m], cols[m]] = True
                used += int(m.sum())

            out.n_points_used = used

        # mascara interior
        interior = np.ones_like(occ, dtype=bool)
        b = max(0, int(border_cells))
        if b > 0 and nrows > 2 * b and ncols > 2 * b:
            interior[:b, :] = False
            interior[-b:, :] = False
            interior[:, :b] = False
            interior[:, -b:] = False
        else:
            # tile pequeno demais: nao da para falar de buraco interior
            out.status = "ok"
            out.has_hole = 0
            out.elapsed_s = time.perf_counter() - t0
            return out.__dict__

        empty_int = interior & ~occ
        n_int = int(interior.sum())
        n_empty = int(empty_int.sum())
        out.n_empty_interior = n_empty
        out.empty_frac_interior = float(n_empty / n_int) if n_int else 0.0

        comps = _components_4(empty_int)
        cell_area = float(cell_m) * float(cell_m)
        holes_xy: list = []
        areas: list[float] = []
        for cells in comps:
            area = float(len(cells)) * cell_area
            if area < float(min_hole_m2):
                continue
            poly = _cells_to_poly(cells, xmin, ymax, float(cell_m))
            if poly is None or poly.is_empty:
                continue
            ring = list(poly.exterior.coords)
            holes_xy.append((area, ring))
            areas.append(area)

        out.n_holes = len(holes_xy)
        out.holes_area_m2 = float(sum(areas)) if areas else 0.0
        out.largest_hole_m2 = float(max(areas)) if areas else 0.0
        out.hole_polys_xy = holes_xy
        out.has_hole = 1 if out.n_holes > 0 else 0
        out.status = "has_hole" if out.has_hole else "ok"
    except Exception as exc:
        out.status = "erro"
        out.erro = f"{type(exc).__name__}: {exc}"
        out.hole_polys_xy = []
    out.elapsed_s = time.perf_counter() - t0
    # dataclasses -> dict already; hole_polys_xy stays list
    return out.__dict__


# -----------------------------------------------------------------------------
# Articulacao
# -----------------------------------------------------------------------------

def load_artic_index(path: Path, layer: str) -> dict[str, object]:
    """Mapa id_upper -> (geometry, attrs dict)."""
    if not path.is_file():
        return {}
    try:
        gdf = gpd.read_file(path, layer=layer)
    except Exception as exc:
        print(f"[AVISO] Nao li articulacao {path}: {exc}")
        return {}
    index: dict[str, object] = {}
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        attrs = {
            "layer": row["layer"] if "layer" in gdf.columns else "",
            "NOMENC_2K": str(row["NOMENC_2K"]) if "NOMENC_2K" in gdf.columns and row["NOMENC_2K"] is not None else "",
            "NOMENC_5K": str(row["NOMENC_5K"]) if "NOMENC_5K" in gdf.columns and row["NOMENC_5K"] is not None else "",
            "NOMENC_10K": str(row["NOMENC_10K"]) if "NOMENC_10K" in gdf.columns and row["NOMENC_10K"] is not None else "",
        }
        for col in AUTO_ID_COLUMNS:
            val = attrs.get(col, "")
            if val and val.lower() != "nan":
                index.setdefault(val.strip().upper(), (geom, attrs))
    print(f"Articulacao: {path.name} | ids indexados: {len(index)}")
    return index


def _bbox_to_4674(xmin, xmax, ymin, ymax, crs_wkt: str):
    """Reprojeta bbox do LAZ para SIRGAS 2000 geografico (EPSG:4674)."""
    if not crs_wkt:
        return None
    try:
        import pyproj
        from shapely.ops import transform as shp_transform

        src = pyproj.CRS.from_user_input(crs_wkt)
        dst = pyproj.CRS.from_epsg(4674)
        if src == dst:
            return box(xmin, ymin, xmax, ymax)
        tfm = pyproj.Transformer.from_crs(src, dst, always_xy=True).transform
        return shp_transform(tfm, box(xmin, ymin, xmax, ymax))
    except Exception:
        return None


def tile_geom_for_laz(nome: str, artic: dict, xmin, xmax, ymin, ymax, crs_wkt: str):
    stem = Path(nome).stem.upper()
    hit = artic.get(stem)
    empty_attrs = {
        "layer": "",
        "NOMENC_2K": "",
        "NOMENC_5K": "",
        "NOMENC_10K": "",
    }
    if hit is not None:
        geom, attrs = hit
        return geom, attrs, "articulacao"
    geom = _bbox_to_4674(xmin, xmax, ymin, ymax, crs_wkt)
    if geom is not None:
        return geom, empty_attrs, "header_bbox_4674"
    # sem articulacao e sem CRS: ainda grava atributos; sem geometria evita misturar UTM no layer 4674
    return None, empty_attrs, "sem_geometria"


# -----------------------------------------------------------------------------
# Registry / IO
# -----------------------------------------------------------------------------

REGISTRY_FIELDS = [
    "laz_nome",
    "laz_path",
    "status",
    "has_hole",
    "n_holes",
    "largest_hole_m2",
    "holes_area_m2",
    "empty_frac_interior",
    "n_points_header",
    "n_points_used",
    "size_gb",
    "cell_m",
    "subsample",
    "elapsed_s",
    "erro",
    "atualizado_utc",
]


def load_registry(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    out: dict[str, dict] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = (row.get("laz_path") or "").strip()
            if key:
                out[key] = row
    return out


def save_registry(path: Path, registry: dict[str, dict]) -> None:
    rows = sorted(registry.values(), key=lambda r: (r.get("status", ""), r.get("laz_nome", "")))

    def _write(tmp: Path) -> None:
        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=REGISTRY_FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in REGISTRY_FIELDS})

    atomic_to_file(path, _write)


def save_summary(path: Path, stats) -> None:
    def _write(tmp: Path) -> None:
        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["metrica", "valor", "nota"])
            for k, v, note in stats:
                w.writerow([k, v, note])

    atomic_to_file(path, _write)


def _as_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_relatorio_md(
    *,
    laz_dir: Path,
    out_gpkg: Path,
    n_listed: int,
    rows: list[dict],
    cell_m: float,
    subsample: int,
    min_hole_m2: float,
    skipped: int,
    processed: int,
    stopped_deadline: bool,
    elapsed_rodada: str,
    report_only: bool,
) -> str:
    n_reg = len(rows)
    n_hole = sum(1 for r in rows if _as_int(r.get("has_hole")) == 1)
    n_ok = sum(1 for r in rows if (r.get("status") or "") == "ok")
    n_err = sum(1 for r in rows if (r.get("status") or "") == "erro")
    pct_hole = (100.0 * n_hole / n_reg) if n_reg else 0.0
    pct_done = (100.0 * n_reg / n_listed) if n_listed else 0.0

    top = sorted(
        (r for r in rows if _as_int(r.get("has_hole")) == 1),
        key=lambda r: _as_float(r.get("largest_hole_m2")),
        reverse=True,
    )[:15]

    status_parcial = stopped_deadline or (n_listed > 0 and n_reg < n_listed)
    titulo_status = "PARCIAL" if status_parcial else "COMPLETO (registry atual)"

    lines: list[str] = [
        "# Relatório — buracos de cobertura nos LAZ IGC",
        "",
        f"**Status:** {titulo_status}  ",
        f"**Atualizado (UTC):** {utc_now()}  ",
        f"**Script:** `batch_check_laz_holes.py {VERSION}`  ",
        f"**Pasta LAZ:** `{laz_dir}`  ",
        "",
        "## Em uma frase",
        "",
        (
            f"Vistoria automatizada de gaps interiores de pontos nos tiles LiDAR IGC: "
            f"**{n_hole}** com buraco / **{n_reg}** vistoriados"
            + (f" ({pct_done:.0f}% da lista desta run)." if n_listed else ".")
        ),
        "",
        "## Números gerais (10 segundos)",
        "",
        "| Métrica | Valor |",
        "|---|---:|",
        f"| LAZ listados (esta run) | {n_listed} |",
        f"| Já vistoriados (registry acumulado) | {n_reg} |",
        f"| Com buraco (`has_hole=1`) | **{n_hole}** |",
        f"| Sem buraco (`ok`) | {n_ok} |",
        f"| Erro | {n_err} |",
        f"| % com buraco (entre vistoriados) | {pct_hole:.1f}% |",
        f"| Pulados nesta rodada (já no registry) | {skipped} |",
        f"| Processados nesta rodada | {processed} |",
        "",
    ]

    if report_only:
        lines += [
            "> Gerado com `--report-only`: **não reprocessou** LAZ; leu só o registry já gravado.",
            "",
        ]
    if stopped_deadline:
        lines += [
            "> Parou por `--deadline-min` — resultado **parcial**. Rode de novo para continuar "
            "(os já vistoriados são pulados).",
            "",
        ]
    elif status_parcial and n_listed:
        lines += [
            f"> Ainda faltam ~{max(0, n_listed - n_reg)} arquivos da lista para fechar 100%.",
            "",
        ]

    lines += [
        "## Método (breve)",
        "",
        "- Fonte: nuvens `.laz` / `.las` brutas (sem CHM).",
        f"- Grade de ocupação: célula **{cell_m:g} m**, subsample **1/{subsample}**.",
        "- Borda do tile ignorada; buraco = componente vazio **interior**.",
        f"- Área mínima reportada: **{min_hole_m2:g} m²** "
        f"({min_hole_m2 / 1e4:.2f} ha).",
        "- Retomável via `laz_holes_registry.csv` (mesmos parâmetros → não reprocessa).",
        "",
        "## Top buracos (maior polígono interior)",
        "",
    ]

    if not top:
        lines += [
            "_Nenhum tile com `has_hole=1` no registry até agora._",
            "",
        ]
    else:
        lines += [
            "| # | Tile (arquivo) | Maior buraco (ha) | N° buracos | Status |",
            "|---:|---|---:|---:|---|",
        ]
        for i, r in enumerate(top, 1):
            ha = _as_float(r.get("largest_hole_m2")) / 1e4
            lines.append(
                f"| {i} | `{r.get('laz_nome')}` | {ha:.2f} | "
                f"{_as_int(r.get('n_holes'))} | {r.get('status')} |"
            )
        lines.append("")
        lines.append("Exemplos para citar na reunião (3–5 primeiros da tabela acima).")
        lines.append("")

    lines += [
        "## O que abrir além deste .md",
        "",
        f"- Planilha rápida: `D:\\\\laz_holes_summary.csv`",
        f"- Registro linha a linha: `D:\\\\laz_holes_registry.csv`",
        f"- Mapa (mostrar à parte no QGIS): `{out_gpkg}`",
        "  - layer `laz_tiles` → filtrar `has_hole = 1`",
        "  - layer `laz_holes` → polígonos dos gaps",
        "",
        f"_Tempo desta rodada: {elapsed_rodada}_",
        "",
    ]
    return "\n".join(lines)


def save_relatorio_md(path: Path, text: str) -> None:
    def _write(tmp: Path) -> None:
        tmp.write_text(text, encoding="utf-8")

    atomic_to_file(path, _write)


def write_gpkg(path: Path, tile_rows: list[dict], hole_rows: list[dict], tile_crs, holes_crs) -> None:
    def _write(tmp: Path) -> None:
        if tmp.exists():
            tmp.unlink()
        if tile_rows:
            gdf_t = gpd.GeoDataFrame(tile_rows, geometry="geometry", crs=tile_crs)
            gdf_t.to_file(tmp, layer=TILES_LAYER, driver="GPKG")
        else:
            gpd.GeoDataFrame(
                {"laz_nome": []}, geometry=[], crs=tile_crs or "EPSG:4674"
            ).to_file(tmp, layer=TILES_LAYER, driver="GPKG")
        if hole_rows:
            gdf_h = gpd.GeoDataFrame(hole_rows, geometry="geometry", crs=holes_crs)
            gdf_h.to_file(tmp, layer=HOLES_LAYER, driver="GPKG")

    atomic_to_file(path, _write)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    a = parse_args()
    if a.fast:
        a.cell_m = 100.0
        a.subsample = 5
        a.border_cells = 1
        print("[fast] cell_m=100 subsample=5 border_cells=1")

    laz_dir = Path(a.laz_dir)
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_gpkg = Path(a.out_gpkg)
    reg_path = out_dir / REGISTRY_NAME
    sum_path = out_dir / SUMMARY_NAME
    md_path = out_dir / REPORT_NAME

    # --report-only: só regenera CSV/.md a partir do registry (não reprocessa).
    if a.report_only:
        registry = load_registry(reg_path)
        if not registry:
            print(f"[ERRO] Registry vazio/ausente: {reg_path}")
            print("Rode o batch pelo menos uma vez antes de --report-only.")
            return 2
        files = list_laz(laz_dir) if laz_dir.is_dir() else []
        n_listed = len(files) if files else len(registry)
        rows = list(registry.values())
        n_reg = len(rows)
        n_hole = sum(1 for r in rows if _as_int(r.get("has_hole")) == 1)
        n_ok = sum(1 for r in rows if (r.get("status") or "") == "ok")
        n_err = sum(1 for r in rows if (r.get("status") or "") == "erro")
        top = sorted(
            (r for r in rows if _as_int(r.get("has_hole")) == 1),
            key=lambda r: _as_float(r.get("largest_hole_m2")),
            reverse=True,
        )[:15]
        # parametros: prefere o que esta no registry (moda simples pelo 1o ok/has_hole)
        cell_m = a.cell_m
        subsample = a.subsample
        for r in rows:
            if r.get("status") in {"ok", "has_hole"}:
                cell_m = _as_float(r.get("cell_m"), cell_m)
                subsample = _as_int(r.get("subsample"), subsample)
                break
        stats = [
            ("script", f"batch_check_laz_holes.py {VERSION}", "Versao"),
            ("atualizado_utc", utc_now(), "report-only"),
            ("laz_dir", str(laz_dir), ""),
            ("arquivos_listados", n_listed, "Lista em disco se existir; senao = registry"),
            ("arquivos_no_registry", n_reg, "Ja vistoriados (acumulado)"),
            ("com_buraco", n_hole, "has_hole=1"),
            ("sem_buraco", n_ok, "status=ok"),
            ("erro", n_err, ""),
            ("pulados_nesta_rodada", n_reg, "report-only"),
            ("processados_nesta_rodada", 0, ""),
            ("deadline_atingido", 0, ""),
            ("cell_m", cell_m, "do registry"),
            ("subsample", subsample, "do registry"),
            ("min_hole_m2", a.min_hole_m2, ""),
            ("gpkg", str(out_gpkg), f"layers: {TILES_LAYER}, {HOLES_LAYER}"),
            ("elapsed_rodada", "0s", "report-only"),
        ]
        save_summary(sum_path, stats)
        md = build_relatorio_md(
            laz_dir=laz_dir,
            out_gpkg=out_gpkg,
            n_listed=n_listed,
            rows=rows,
            cell_m=cell_m,
            subsample=subsample,
            min_hole_m2=float(a.min_hole_m2),
            skipped=n_reg,
            processed=0,
            stopped_deadline=False,
            elapsed_rodada="0s",
            report_only=True,
        )
        save_relatorio_md(md_path, md)
        print("=" * 64)
        print(f"report-only | registry={n_reg} | buracos={n_hole} | ok={n_ok} | erro={n_err}")
        if top:
            print("Top buracos:")
            for r in top[:5]:
                print(f"  {r.get('laz_nome')}: {_as_float(r.get('largest_hole_m2'))/1e4:.2f} ha")
        print(f"MD  : {md_path}")
        print(f"CSV : {sum_path}")
        print("Pronto (sem reprocessar LAZ).")
        return 0

    if not laz_dir.is_dir():
        print(f"[ERRO] Pasta LAZ nao existe: {laz_dir}")
        print("Este script espera o disco do lab (ex.: D:\\laz).")
        return 2

    files = list_laz(laz_dir)
    if not files:
        print(f"[ERRO] Nenhum .laz/.las em {laz_dir}")
        return 2
    if a.limit and a.limit > 0:
        files = files[: a.limit]

    print("=" * 64)
    print(f"batch_check_laz_holes.py {VERSION}")
    print(f"LAZ dir : {laz_dir}  ({len(files)} arquivos)")
    print(f"GPKG    : {out_gpkg}")
    print(f"CSV     : {reg_path}")
    print(f"MD      : {md_path}")
    print(
        f"grade   : cell={a.cell_m}m  subsample=1/{a.subsample}  "
        f"border={a.border_cells}  min_hole={a.min_hole_m2} m2"
    )
    print(f"workers : {a.workers}  deadline_min={a.deadline_min or 'sem'}")
    print("=" * 64)

    artic: dict = {}
    if not a.no_artic:
        artic = load_artic_index(Path(a.articulacao), a.articulacao_layer)

    registry = {} if a.force else load_registry(reg_path)
    done_ok = {
        k
        for k, r in registry.items()
        if r.get("status") in {"ok", "has_hole"}
        and float(r.get("cell_m") or 0) == float(a.cell_m)
        and int(float(r.get("subsample") or 0)) == int(a.subsample)
    }

    pending = [p for p in files if str(p) not in done_ok]
    skipped = len(files) - len(pending)
    print(f"Ja no registry (mesmos parametros): {skipped}")
    print(f"A processar agora: {len(pending)}")

    # Carrega GPKG existente (se houver) para merge
    tile_by_path: dict[str, dict] = {}
    holes_all: list[dict] = []
    tile_crs = "EPSG:4674"
    holes_crs = None

    if out_gpkg.is_file() and not a.force:
        try:
            gdf_t = gpd.read_file(out_gpkg, layer=TILES_LAYER)
            tile_crs = gdf_t.crs or tile_crs
            for _, row in gdf_t.iterrows():
                key = str(row.get("laz_path") or "")
                if key:
                    d = row.drop(labels="geometry").to_dict()
                    d["geometry"] = row.geometry
                    tile_by_path[key] = d
            try:
                gdf_h = gpd.read_file(out_gpkg, layer=HOLES_LAYER)
                holes_crs = gdf_h.crs
                for _, row in gdf_h.iterrows():
                    d = row.drop(labels="geometry").to_dict()
                    d["geometry"] = row.geometry
                    # so mantem buracos de arquivos ainda “done”
                    if str(d.get("laz_path") or "") in done_ok or str(d.get("laz_path") or "") in tile_by_path:
                        holes_all.append(d)
            except Exception:
                pass
            print(f"GPKG existente: {len(tile_by_path)} tiles carregados")
        except Exception as exc:
            print(f"[AVISO] Nao reaproveitei GPKG antigo: {exc}")

    deadline = time.perf_counter() + a.deadline_min * 60.0 if a.deadline_min and a.deadline_min > 0 else None
    t_run = time.perf_counter()
    processed = 0
    stopped_deadline = False

    def ingest(result: dict) -> None:
        nonlocal holes_crs
        path = result["laz_path"]
        nome = result["laz_nome"]
        geom, attrs, geom_src = tile_geom_for_laz(
            nome, artic, result["xmin"], result["xmax"], result["ymin"], result["ymax"], result["crs_wkt"]
        )
        tile_row = {
            "laz_nome": nome,
            "laz_path": path,
            "status": result["status"],
            "has_hole": int(result["has_hole"]),
            "n_holes": int(result["n_holes"]),
            "largest_hole_m2": float(result["largest_hole_m2"]),
            "holes_area_m2": float(result["holes_area_m2"]),
            "empty_frac_interior": float(result["empty_frac_interior"]),
            "n_points_header": int(result["n_points_header"]),
            "n_points_used": int(result["n_points_used"]),
            "size_gb": float(result["size_gb"]),
            "cell_m": float(result["cell_m"]),
            "crs_wkt": (result.get("crs_wkt") or "")[:200],
            "geom_src": geom_src,
            "layer": attrs.get("layer", ""),
            "NOMENC_2K": attrs.get("NOMENC_2K", ""),
            "NOMENC_5K": attrs.get("NOMENC_5K", ""),
            "NOMENC_10K": attrs.get("NOMENC_10K", ""),
            "elapsed_s": float(result["elapsed_s"]),
            "erro": result.get("erro") or "",
            "atualizado_utc": utc_now(),
            "geometry": geom,
        }
        tile_by_path[path] = tile_row

        # remove buracos antigos deste arquivo e reinsere
        holes_all[:] = [h for h in holes_all if h.get("laz_path") != path]
        for i, (area, ring) in enumerate(result.get("hole_polys_xy") or [], 1):
            poly = Polygon(ring)
            holes_all.append(
                {
                    "laz_nome": nome,
                    "laz_path": path,
                    "hole_id": i,
                    "area_m2": float(area),
                    "area_ha": float(area) / 1e4,
                    "cell_m": float(result["cell_m"]),
                    "geometry": poly,
                }
            )
        if holes_crs is None and result.get("crs_wkt"):
            holes_crs = result["crs_wkt"]

        registry[path] = {
            "laz_nome": nome,
            "laz_path": path,
            "status": result["status"],
            "has_hole": int(result["has_hole"]),
            "n_holes": int(result["n_holes"]),
            "largest_hole_m2": float(result["largest_hole_m2"]),
            "holes_area_m2": float(result["holes_area_m2"]),
            "empty_frac_interior": float(result["empty_frac_interior"]),
            "n_points_header": int(result["n_points_header"]),
            "n_points_used": int(result["n_points_used"]),
            "size_gb": float(result["size_gb"]),
            "cell_m": float(a.cell_m),
            "subsample": int(a.subsample),
            "elapsed_s": round(float(result["elapsed_s"]), 3),
            "erro": result.get("erro") or "",
            "atualizado_utc": utc_now(),
        }

    def flush() -> None:
        save_registry(reg_path, registry)
        # layer de tiles sempre em SIRGAS 2000 geografico (articulacao / bbox reprojetado)
        write_gpkg(
            out_gpkg,
            list(tile_by_path.values()),
            holes_all,
            "EPSG:4674",
            holes_crs or "EPSG:4674",
        )

    if not pending:
        print("Nada novo a processar. Atualizando summary…")
    else:
        # ProcessPool: um arquivo por worker
        with ProcessPoolExecutor(max_workers=max(1, int(a.workers))) as pool:
            futures = {
                pool.submit(
                    analyze_one_laz,
                    str(p),
                    float(a.cell_m),
                    int(a.chunk),
                    int(a.subsample),
                    int(a.border_cells),
                    float(a.min_hole_m2),
                ): p
                for p in pending
            }
            try:
                for fut in as_completed(futures):
                    if deadline is not None and time.perf_counter() >= deadline:
                        stopped_deadline = True
                        print("\n[deadline] Tempo esgotado — cancelando o restante e gravando parcial…")
                        for other in futures:
                            other.cancel()
                        break
                    p = futures[fut]
                    try:
                        result = fut.result()
                    except Exception as exc:
                        result = {
                            "laz_path": str(p),
                            "laz_nome": p.name,
                            "status": "erro",
                            "has_hole": 0,
                            "n_points_used": 0,
                            "n_points_header": 0,
                            "size_gb": p.stat().st_size / 1e9 if p.is_file() else 0.0,
                            "crs_wkt": "",
                            "xmin": 0.0,
                            "xmax": 0.0,
                            "ymin": 0.0,
                            "ymax": 0.0,
                            "cell_m": float(a.cell_m),
                            "n_cols": 0,
                            "n_rows": 0,
                            "n_empty_interior": 0,
                            "empty_frac_interior": 0.0,
                            "n_holes": 0,
                            "largest_hole_m2": 0.0,
                            "holes_area_m2": 0.0,
                            "elapsed_s": 0.0,
                            "erro": f"{type(exc).__name__}: {exc}",
                            "hole_polys_xy": [],
                        }
                        print(f"  ERRO worker {p.name}: {exc}")
                    ingest(result)
                    processed += 1
                    flag = "HOLE" if result.get("has_hole") else result.get("status", "").upper()
                    print(
                        f"[{processed}/{len(pending)}] {flag:8}  {p.name}  "
                        f"holes={result.get('n_holes', 0)}  "
                        f"maior={float(result.get('largest_hole_m2') or 0)/1e4:.2f}ha  "
                        f"{float(result.get('elapsed_s') or 0):.1f}s"
                    )
                    if processed % 5 == 0:
                        flush()
            except KeyboardInterrupt:
                print("\n[Ctrl+C] Gravando progresso…")
            finally:
                flush()

    # Summary para o professor
    rows = list(registry.values()) if registry else [
        {
            "status": r.get("status"),
            "has_hole": r.get("has_hole"),
            "largest_hole_m2": r.get("largest_hole_m2"),
            "laz_nome": r.get("laz_nome"),
        }
        for r in tile_by_path.values()
    ]
    # se registry vazio mas tile_by_path cheio (force edge), monta das tiles
    if not rows and tile_by_path:
        rows = list(tile_by_path.values())

    n_total_disk = len(files)
    n_reg = len(rows)
    n_hole = sum(1 for r in rows if int(float(r.get("has_hole") or 0)) == 1)
    n_ok = sum(1 for r in rows if r.get("status") == "ok")
    n_err = sum(1 for r in rows if r.get("status") == "erro")
    top = sorted(
        (r for r in rows if int(float(r.get("has_hole") or 0)) == 1),
        key=lambda r: float(r.get("largest_hole_m2") or 0),
        reverse=True,
    )[:15]

    stats = [
        ("script", f"batch_check_laz_holes.py {VERSION}", "Versao"),
        ("atualizado_utc", utc_now(), ""),
        ("laz_dir", str(laz_dir), ""),
        ("arquivos_listados", n_total_disk, "Nesta rodada (apos --limit)"),
        ("arquivos_no_registry", n_reg, "Ja vistoriados (acumulado)"),
        ("com_buraco", n_hole, "has_hole=1"),
        ("sem_buraco", n_ok, "status=ok"),
        ("erro", n_err, ""),
        ("pulados_nesta_rodada", skipped, "Mesmos parametros no registry"),
        ("processados_nesta_rodada", processed, ""),
        ("deadline_atingido", int(stopped_deadline), ""),
        ("cell_m", a.cell_m, ""),
        ("subsample", a.subsample, ""),
        ("min_hole_m2", a.min_hole_m2, ""),
        ("gpkg", str(out_gpkg), f"layers: {TILES_LAYER}, {HOLES_LAYER}"),
        ("elapsed_rodada", format_tempo(time.perf_counter() - t_run), ""),
    ]
    save_summary(sum_path, stats)
    md = build_relatorio_md(
        laz_dir=laz_dir,
        out_gpkg=out_gpkg,
        n_listed=n_total_disk,
        rows=rows,
        cell_m=float(a.cell_m),
        subsample=int(a.subsample),
        min_hole_m2=float(a.min_hole_m2),
        skipped=skipped,
        processed=processed,
        stopped_deadline=stopped_deadline,
        elapsed_rodada=format_tempo(time.perf_counter() - t_run),
        report_only=False,
    )
    save_relatorio_md(md_path, md)
    flush()

    print("\n" + "=" * 64)
    print("RELATORIO RAPIDO (professor)")
    print("=" * 64)
    print(f"Vistoriados: {n_reg} / listados nesta run: {n_total_disk}")
    print(f"Com buraco : {n_hole}")
    print(f"Sem buraco : {n_ok}")
    print(f"Erro       : {n_err}")
    if stopped_deadline:
        print("Parou por --deadline-min (resultado PARCIAL; rode de novo para continuar).")
    if top:
        print("\nTop buracos (maior poligono interior):")
        for r in top:
            ha = float(r.get("largest_hole_m2") or 0) / 1e4
            print(f"  {r.get('laz_nome')}: {ha:.2f} ha  (n_holes={r.get('n_holes')})")
    print(f"\nMD   : {md_path}")
    print(f"GPKG : {out_gpkg}")
    print(f"CSV  : {sum_path}")
    print(f"Reg  : {reg_path}")
    print("Pronto.")
    return 0


if __name__ == "__main__":
    # Windows spawn: guarda contra reentrada
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)

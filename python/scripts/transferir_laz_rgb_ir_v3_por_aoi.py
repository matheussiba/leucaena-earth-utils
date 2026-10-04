r"""
===============================================================================
TRANSFERIR_LAZ_RGB_IR_v3  —  Transferência de tiles IGC/SP por ÁREA DE INTERESSE
===============================================================================

O que mudou em relação ao v2.0
-------------------------------
- NÃO precisa mais exportar os shapefiles de articulação com sufixo "_selecao".
- Você passa UM arquivo espacial de ÁREA DE INTERESSE (AOI) — ponto, linha ou
  polígono — em qualquer formato (.shp / .geojson / .gpkg / .kml / .kmz).
- O script cruza (intersecta) essa AOI com a articulação IGC já mesclada
  (as 3 articulações: Fehidro, Lote4 e Voo22 em um único GeoPackage) e
  descobre automaticamente quais tiles (grids) baixar.
- Todos os caminhos e opções entram por ARGUMENTOS DE LINHA DE COMANDO
  (não é mais preciso editar o código).
- Opcionalmente, ao final gera (ou incrementa) um arquivo espacial de
  COBERTURA com os grids efetivamente disponíveis/copiados.

Articulação mesclada (padrão)
-----------------------------
  Bundled in this repo (preferred)::

    data/articulacao_igc_sp/articulacao-igc-merged-spatialindex-sirgas2000.gpkg

  Local mirror (fallback if the bundled file is missing)::

    D:\\_articulacao_igc_sp\\articulacao-igc-merged-spatialindex-sirgas2000.gpkg

  Campos relevantes:
    layer       -> de qual articulação o grid veio
                   (Articulacao_Laser_Fehidro / _Lote4 / _Voo22)
    NOMENC_2K   -> id do tile para Fehidro
    NOMENC_5K   -> id do tile para Lote4 e Voo22
    NOMENC_10K  -> folha 1:10.000 (usada pelos nomes de RGB/IR)
  CRS: SIRGAS 2000 (EPSG:4674).

  Você NÃO precisa passar ``--articulacao`` se o arquivo padrão existir.
  Só use ``--articulacao`` / ``--articulacao-layer`` se quiser outro GPKG.

Como o id do tile vira nome de arquivo
--------------------------------------
- LAZ : nome bate exatamente com NOMENC_2K / NOMENC_5K.
- RGB / IR : o nome do arquivo usa a folha 1:10.000 (mais "grossa"); a busca
  usa "cascata" (vai encurtando o id por '-') até encontrar — igual ao v2.0.

Como rodar (PowerShell)
-----------------------
Use o backtick `` ` `` no final de cada linha no **PowerShell**.
No CMD use ``^`` ou coloque tudo em uma linha só.

  # Teste: AOI = polígono, fontes = D:\\laz|rgb|ir, destino = C:\\deleteme
  # + gera cobertura do que foi transferido
  python transferir_laz_rgb_ir_v3_por_aoi.py `
    --aoi "C:\\Users\\Public\\Desktop\\deleteme.shp" `
    --source-laz "D:\\laz" `
    --source-rgb "D:\\rgb" `
    --source-ir  "D:\\ir" `
    --dest "C:\\deleteme" `
    --cobertura-out "C:\\deleteme\\cobertura_baixados.gpkg"

  # Mesmo comando em UMA linha (funciona no CMD e no PowerShell):
  python transferir_laz_rgb_ir_v3_por_aoi.py --aoi "C:\\caminho\\area.shp" --source-laz "D:\\laz" --source-rgb "D:\\rgb" --source-ir "D:\\ir" --dest "C:\\deleteme" --cobertura-out "C:\\deleteme\\cobertura_baixados.gpkg"

  # Transferência padrão no PC de origem (D/E/F -> G), só passando a AOI
  python transferir_laz_rgb_ir_v3_por_aoi.py --aoi "C:\\minha_area.gpkg"

  # Só vistoria (não copia), desligando IR
  python transferir_laz_rgb_ir_v3_por_aoi.py --aoi area.kml --dry-run --no-ir

  # Articulação fora do padrão (opcional)
  python transferir_laz_rgb_ir_v3_por_aoi.py `
    --aoi "C:\\area.shp" `
    --articulacao "D:\\_articulacao_igc_sp\\articulacao-igc-merged-spatialindex-sirgas2000.gpkg" `
    --dest "G:\\"

  # Incrementando (append) uma cobertura já existente
  python transferir_laz_rgb_ir_v3_por_aoi.py `
    --aoi area2.gpkg `
    --cobertura-out G:\\cobertura_baixados.gpkg `
    --cobertura-append

Dependências
------------
  pip install geopandas shapely pyogrio   (mesmas do v2.0)
===============================================================================
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
import time
import zipfile

import geopandas as gpd
import pandas as pd


# =============================================================================
# PADRÕES (sobrescreva por linha de comando)
# =============================================================================
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
_BUNDLED_ARTICULACAO = os.path.join(
    _REPO_ROOT,
    "data",
    "articulacao_igc_sp",
    "articulacao-igc-merged-spatialindex-sirgas2000.gpkg",
)
_LOCAL_ARTICULACAO = (
    r"D:\_articulacao_igc_sp\articulacao-igc-merged-spatialindex-sirgas2000.gpkg"
)
DEFAULT_ARTICULACAO = (
    _BUNDLED_ARTICULACAO
    if os.path.exists(_BUNDLED_ARTICULACAO)
    else _LOCAL_ARTICULACAO
)
DEFAULT_ARTIC_LAYER = "articulacao-igc-merged-spatialindex-sirgas2000"

DEFAULT_SRC_LAZ = r"D:\laz"
DEFAULT_SRC_RGB = r"E:\RGB"
DEFAULT_SRC_IR = r"F:\IR"
DEFAULT_DEST = r"G:"

# Colunas de id por articulação (mesma regra do v2.0)
ID_COLUMN_BY_LAYER = {
    "Articulacao_Laser_Fehidro": "NOMENC_2K",
    "Articulacao_Laser_Lote4": "NOMENC_5K",
    "Articulacao_Laser_Voo22": "NOMENC_5K",
}
# Ordem de fallback caso 'layer' não esteja no mapa acima
AUTO_ID_COLUMNS = ("NOMENC_2K", "NOMENC_5K", "NOMENC_10K")

RASTER_PC_EXTS = r"(?:tif|tiff|laz|las|copc)"


# =============================================================================
# Utilitários de formatação
# =============================================================================
def formatar_tempo(segundos: float) -> str:
    m, s = divmod(int(max(0.0, segundos)), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m:02d}m {s:02d}s"


def formatar_tamanho(bytes_size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} PB"


# =============================================================================
# Busca de arquivos (idêntica em espírito ao v2.0)
# =============================================================================
def buscar_arquivo_preciso(arquivos_origem, termo_busca: str):
    padrao = re.compile(
        rf"(?<![a-zA-Z0-9]){re.escape(termo_busca)}(?![a-zA-Z0-9]).*\.{RASTER_PC_EXTS}$",
        re.IGNORECASE,
    )
    return [f for f in arquivos_origem if padrao.search(f)]


def buscar_cascata(arquivos_origem, tile_original: str):
    partes = tile_original.split("-")
    while len(partes) > 0:
        termo_atual = "-".join(partes)
        encontrados = buscar_arquivo_preciso(arquivos_origem, termo_atual)
        if encontrados:
            return encontrados
        partes.pop()
    return []


# =============================================================================
# Leitura da AOI (aceita .shp/.geojson/.gpkg/.kml/.kmz)
# =============================================================================
def ler_aoi(path: str, layer: str | None = None) -> gpd.GeoDataFrame:
    ext = os.path.splitext(path)[1].lower()

    if ext == ".kmz":
        # KMZ é um ZIP com um .kml dentro — extrai e lê o .kml
        with zipfile.ZipFile(path) as zf:
            kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_names:
                raise ValueError(f"KMZ sem .kml interno: {path}")
            tmpdir = tempfile.mkdtemp(prefix="aoi_kmz_")
            kml_path = zf.extract(kml_names[0], tmpdir)
        return _read_vector(kml_path, None)

    return _read_vector(path, layer)


def _read_vector(path: str, layer: str | None) -> gpd.GeoDataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"AOI não encontrado: {path}\n"
            "  Confira o caminho (aspas se tiver espaço) e a extensão "
            "(.shp precisa dos sidecars .shx/.dbf/.prj)."
        )

    ext = os.path.splitext(path)[1].lower()
    try:
        if layer:
            gdf = gpd.read_file(path, layer=layer)
        else:
            gdf = gpd.read_file(path)
    except Exception as first_err:
        # KML às vezes precisa do driver explícito; não mascarar .shp/.gpkg
        if ext in (".kml", ".kmz"):
            try:
                gdf = gpd.read_file(path, driver="KML")
            except Exception as kml_err:
                raise RuntimeError(
                    f"Falha ao ler AOI KML/KMZ {path}: {kml_err}"
                ) from kml_err
        else:
            raise RuntimeError(
                f"Falha ao ler AOI {path}: {first_err}"
            ) from first_err

    gdf = gdf[gdf.geometry.notna()].copy()
    if gdf.empty:
        raise ValueError(f"AOI sem geometrias válidas: {path}")
    return gdf


# =============================================================================
# Seleção de tiles por interseção
# =============================================================================
def id_do_grid(row) -> str | None:
    layer = row.get("layer")
    col = ID_COLUMN_BY_LAYER.get(layer)
    if col and row.get(col):
        return str(row[col]).strip()
    for c in AUTO_ID_COLUMNS:
        v = row.get(c)
        if v:
            return str(v).strip()
    return None


def selecionar_grids(articulacao_path, artic_layer, aoi_path, aoi_layer):
    print("Lendo articulação mesclada...")
    artic = gpd.read_file(articulacao_path, layer=artic_layer)
    print(f"  Feições na articulação: {len(artic)}  | CRS: {artic.crs}")

    print(f"Lendo AOI: {aoi_path}")
    aoi = ler_aoi(aoi_path, aoi_layer)
    print(f"  Feições na AOI: {len(aoi)}  | CRS: {aoi.crs}")

    # Reprojeta a AOI para o CRS da articulação
    if aoi.crs is None:
        raise ValueError(
            "A AOI não tem CRS definido. Defina o CRS do arquivo e tente de novo."
        )
    if artic.crs is not None and aoi.crs != artic.crs:
        print(f"  Reprojetando AOI {aoi.crs} -> {artic.crs}")
        aoi = aoi.to_crs(artic.crs)

    # Interseção espacial (ponto/linha/polígono)
    print("Cruzando AOI x articulação (intersects)...")
    sel = gpd.sjoin(artic, aoi[["geometry"]], predicate="intersects", how="inner")
    sel = sel[~sel.index.duplicated(keep="first")].copy()
    print(f"  Grids intersectados: {len(sel)}")

    # id do tile por feição
    sel["__tile_id"] = sel.apply(id_do_grid, axis=1)
    sel = sel[sel["__tile_id"].notna()].copy()

    # dedup por (tile_id) mantendo a 1ª (geometria preservada p/ cobertura)
    tiles = []
    seen = set()
    for _, r in sel.iterrows():
        tid = r["__tile_id"]
        if tid in seen:
            continue
        seen.add(tid)
        tiles.append({"tile": tid, "origem": r.get("layer", "?")})
    print(f"  Tiles únicos: {len(tiles)}")
    return tiles, sel


# =============================================================================
# Transferência
# =============================================================================
def transferir(args) -> None:
    tipos = []
    if args.laz:
        tipos.append("LAZ")
    if args.rgb:
        tipos.append("RGB")
    if args.ir:
        tipos.append("IR")
    if not tipos:
        print("[ABORTA] Nenhum tipo selecionado (não use --no-laz/--no-rgb/--no-ir juntos).")
        return

    sources = {"LAZ": args.source_laz, "RGB": args.source_rgb, "IR": args.source_ir}
    dests = {
        "LAZ": os.path.join(args.dest, "laz"),
        "RGB": os.path.join(args.dest, "rgb"),
        "IR": os.path.join(args.dest, "ir"),
    }

    tiles, sel = selecionar_grids(
        args.articulacao, args.articulacao_layer, args.aoi, args.aoi_layer
    )
    if not tiles:
        print("\n[ABORTA] Nenhum tile encontrado na interseção. Encerrando.")
        return

    if not args.dry_run:
        for t in tipos:
            os.makedirs(dests[t], exist_ok=True)

    # Listagens de origem e destino
    arq_origem = {}
    arq_dest = {}
    for t in tipos:
        src = sources[t]
        arq_origem[t] = os.listdir(src) if os.path.exists(src) else []
        if not arq_origem[t]:
            print(f"[AVISO] Origem {t} vazia ou inexistente: {src}")
        arq_dest[t] = set(os.listdir(dests[t])) if os.path.isdir(dests[t]) else set()

    # ---- Vistoria ----
    print("\n" + "=" * 60)
    print("VISTORIA")
    print("=" * 60)

    fila = []
    faltantes = {t: {} for t in tipos}
    # cobertura: tile -> {LAZ:bool, RGB:bool, IR:bool} (disponível na origem OU já no destino)
    cobertura_flags = {}
    tamanho_total = 0

    for item in tiles:
        tile = item["tile"]
        origem = item["origem"]
        arquivos_tile = {t: [] for t in tipos}
        flags = {"LAZ": False, "RGB": False, "IR": False}

        for t in tipos:
            # LAZ = match preciso; RGB/IR = cascata
            if t == "LAZ":
                enc = buscar_arquivo_preciso(arq_origem[t], tile)
            else:
                enc = buscar_cascata(arq_origem[t], tile)

            if not enc:
                faltantes[t].setdefault(origem, []).append(tile)
                continue

            # existe na origem -> coberto
            flags[t] = True
            for a in enc:
                ja_no_destino = a in arq_dest[t]
                if (not ja_no_destino) or args.overwrite:
                    arquivos_tile[t].append(a)
                    p = os.path.join(sources[t], a)
                    if os.path.exists(p):
                        tamanho_total += os.path.getsize(p)

        cobertura_flags[tile] = flags
        if any(arquivos_tile[t] for t in tipos):
            fila.append({"tile": tile, "origem": origem, "arquivos": arquivos_tile})

    total = len(fila)
    print(f"Tiles com arquivos pendentes de cópia: {total}")
    if total > 0:
        print(f"Espaço estimado a copiar: {formatar_tamanho(tamanho_total)}")

    # ---- Cópia ----
    if args.dry_run:
        print("\n[DRY-RUN] Nada foi copiado.")
    elif total == 0:
        print("\nTudo já está no destino (nada a copiar).")
    else:
        print("\nIniciando cópia...")
        t0 = time.time()
        copiados = {t: 0 for t in tipos}
        for i, it in enumerate(fila, 1):
            tile = it["tile"]
            arq = it["arquivos"]
            feitos = i - 1
            if feitos > 0:
                por_tile = (time.time() - t0) / feitos
                restam = total - feitos
                eta = f" | Restam: {formatar_tempo(por_tile * restam)} | Faltam: {restam}"
            else:
                eta = " | Calculando tempo..."
            pct = (i / total) * 100
            print(f"\n[{pct:6.2f}%] {i}/{total} Conjunto: {tile}{eta}")

            for t in tipos:
                if not arq[t]:
                    continue
                for a in arq[t]:
                    try:
                        print(f"   -> {t}: {a}")
                        shutil.copy2(
                            os.path.join(sources[t], a), os.path.join(dests[t], a)
                        )
                        copiados[t] += 1
                    except Exception as exc:
                        print(f"   !! ERRO ao copiar {a}: {exc}")

        print("\nCopiados nesta sessão:")
        for t in tipos:
            print(f"  {t}: {copiados[t]}")

    # ---- Relatório de faltantes ----
    print("\n" + "#" * 60)
    print("RELATÓRIO")
    print("#" * 60)
    print(f"Tiles verificados: {len(tiles)}")
    for t in tipos:
        if faltantes[t]:
            n = sum(len(set(v)) for v in faltantes[t].values())
            print(f"\n[{t}] NÃO localizados na origem ({n}):")
            for origem, lista in faltantes[t].items():
                for x in sorted(set(lista)):
                    print(f"   - {x}  ({origem})")
        else:
            print(f"\n[{t}] Todos localizados.")

    # ---- Cobertura (opcional) ----
    if args.cobertura_out:
        escrever_cobertura(sel, cobertura_flags, tipos, args)


def escrever_cobertura(sel, cobertura_flags, tipos, args) -> None:
    print("\n" + "=" * 60)
    print(f"COBERTURA -> {args.cobertura_out}")
    print("=" * 60)

    cov = sel.copy()
    cov["has_laz"] = 0
    cov["has_rgb"] = 0
    cov["has_ir"] = 0
    for idx, r in cov.iterrows():
        flags = cobertura_flags.get(r["__tile_id"], {})
        cov.at[idx, "has_laz"] = int(bool(flags.get("LAZ")))
        cov.at[idx, "has_rgb"] = int(bool(flags.get("RGB")))
        cov.at[idx, "has_ir"] = int(bool(flags.get("IR")))

    cov = cov[(cov["has_laz"] == 1) | (cov["has_rgb"] == 1) | (cov["has_ir"] == 1)].copy()
    keep_cols = [c for c in ["layer", "NOMENC_2K", "NOMENC_5K", "NOMENC_10K",
                             "has_laz", "has_rgb", "has_ir", "geometry"] if c in cov.columns]
    cov = cov[keep_cols]
    print(f"  Grids na cobertura desta rodada: {len(cov)}")

    if args.cobertura_append and os.path.exists(args.cobertura_out):
        try:
            antigo = gpd.read_file(args.cobertura_out)
            if antigo.crs is not None and antigo.crs != cov.crs:
                antigo = antigo.to_crs(cov.crs)
            juntos = pd.concat([antigo, cov], ignore_index=True)
            chave = [c for c in ["layer", "NOMENC_2K", "NOMENC_5K", "NOMENC_10K"] if c in juntos.columns]
            if chave:
                juntos = juntos.drop_duplicates(subset=chave, keep="last")
            cov = gpd.GeoDataFrame(juntos, geometry="geometry", crs=cov.crs)
            print(f"  Append: total após mesclar com existente: {len(cov)}")
        except Exception as exc:
            print(f"  [AVISO] Falha ao ler cobertura existente p/ append: {exc}")

    ext = os.path.splitext(args.cobertura_out)[1].lower()
    driver = {".gpkg": "GPKG", ".geojson": "GeoJSON", ".json": "GeoJSON",
              ".shp": "ESRI Shapefile"}.get(ext, "GPKG")
    os.makedirs(os.path.dirname(os.path.abspath(args.cobertura_out)) or ".", exist_ok=True)
    if driver == "GPKG":
        cov.to_file(args.cobertura_out, layer="cobertura", driver="GPKG")
    else:
        cov.to_file(args.cobertura_out, driver=driver)
    print("  Cobertura gravada.")


# =============================================================================
# CLI
# =============================================================================
def parse_args():
    p = argparse.ArgumentParser(
        description="Transfere tiles LAZ/RGB/IR do IGC/SP a partir de uma AOI "
                    "cruzada com a articulação mesclada.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--aoi", required=True,
                   help="Arquivo espacial da área de interesse (.shp/.geojson/.gpkg/.kml/.kmz).")
    p.add_argument("--aoi-layer", default=None,
                   help="Nome da camada dentro da AOI (para .gpkg com várias camadas).")
    p.add_argument("--articulacao", default=DEFAULT_ARTICULACAO,
                   help="GeoPackage da articulação mesclada.")
    p.add_argument("--articulacao-layer", default=DEFAULT_ARTIC_LAYER,
                   help="Camada da articulação dentro do GeoPackage.")

    p.add_argument("--source-laz", default=DEFAULT_SRC_LAZ, help="Pasta de origem dos LAZ.")
    p.add_argument("--source-rgb", default=DEFAULT_SRC_RGB, help="Pasta de origem dos RGB.")
    p.add_argument("--source-ir", default=DEFAULT_SRC_IR, help="Pasta de origem dos IR.")
    p.add_argument("--dest", default=DEFAULT_DEST,
                   help="Pasta de destino (serão criadas subpastas laz/rgb/ir).")

    p.add_argument("--laz", action=argparse.BooleanOptionalAction, default=True,
                   help="Copiar LAZ (use --no-laz para desativar).")
    p.add_argument("--rgb", action=argparse.BooleanOptionalAction, default=True,
                   help="Copiar RGB (use --no-rgb para desativar).")
    p.add_argument("--ir", action=argparse.BooleanOptionalAction, default=True,
                   help="Copiar IR (use --no-ir para desativar).")

    p.add_argument("--overwrite", action="store_true",
                   help="Recopiar arquivos que já existem no destino.")
    p.add_argument("--dry-run", action="store_true",
                   help="Só vistoria; não copia nada.")

    p.add_argument("--cobertura-out", default=None,
                   help="Se informado, grava um arquivo espacial com os grids cobertos.")
    p.add_argument("--cobertura-append", action="store_true",
                   help="Incrementa (append+dedup) uma cobertura já existente.")
    return p.parse_args()


def main():
    args = parse_args()
    t_ini = time.time()
    transferir(args)
    print(f"\nTempo total: {formatar_tempo(time.time() - t_ini)}")
    print("CONCLUÍDO.")


if __name__ == "__main__":
    main()

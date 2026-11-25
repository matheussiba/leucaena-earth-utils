r"""
===============================================================================
TRANSFERIR_LAZ_RGB_IR_v3  —  Transferência de tiles IGC/SP por ÁREA DE INTERESSE
===============================================================================

O que mudou em relação ao v2.0
-------------------------------
- NÃO precisa mais exportar os shapefiles de articulação com sufixo "_selecao".
- A ÁREA DE INTERESSE (AOI) é opcional. Se vier preenchida, o script cruza
  (intersecta) esse arquivo — ponto, linha ou polígono, .shp / .geojson /
  .gpkg / .kml / .kmz — com a articulação IGC já mesclada (Fehidro, Lote4 e
  Voo22 num único GeoPackage) e descobre os tiles. Se a AOI ficar vazia,
  não há recorte espacial: entram todos os grids que passarem nos filtros
  de atributo abaixo.
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

Filtros de atributo (além da AOI)
---------------------------------
- ``--sp-out`` / ``--no-sp-out`` (padrão: desligado).
  Desligado: só grids com ``sp_inside`` verdadeiro (dentro de SP).
  Ligado: também entram grids fora de SP.
- ``--max-water-percent 50``: só grids com ``water_percent`` < 50.
  Omitir o argumento: ignora a coluna (qualquer percentual de água).
  Valor ausente na linha não entra na cópia.
- ``--delete-laz-out-of-parameters``: apaga, em ``DEST\\laz``, os ``.laz``
  de runs anteriores cujo grid ficou de fora de ``--sp-out`` e de
  ``--max-water-percent``. A AOI não entra nessa limpeza. Nome que não
  casa com um grid é mantido. ``sp_inside`` ou ``water_percent`` ausente
  também é mantido (não dá para afirmar que está fora).

Launcher
--------
O arquivo versionado é ``transferir_laz_rgb_ir_v3_por_aoi-sample.bat``.
Copie para ``transferir_laz_rgb_ir_v3_por_aoi.bat`` (essa cópia fica de
fora do git) e edite AOI, pastas e os três filtros.

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

  # Sem AOI: todos os grids dentro de SP com menos de 50% de água
  python transferir_laz_rgb_ir_v3_por_aoi.py --no-sp-out --max-water-percent 50 --dest "G:\\"

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


def _ausente(value) -> bool:
    if value is None:
        return True
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(bytes(value)) == 0
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def sp_inside_valor(value) -> bool | None:
    """True = dentro de SP, False = fora, None = sem valor.

    O campo vem como booleano/binário (1/0, True/False ou um byte).
    """
    if _ausente(value):
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if raw in (b"\x01", b"1", b"true", b"True", b"TRUE", b"t", b"T"):
            return True
        if raw in (b"\x00", b"0", b"false", b"False", b"FALSE", b"f", b"F"):
            return False
        if len(raw) == 1:
            return raw[0] != 0
        return None
    if isinstance(value, bool):
        return bool(value)
    numero = _numero(value)
    if numero is not None:
        return numero != 0.0
    text = str(value).strip().lower()
    if text in ("1", "true", "t", "yes", "y", "sim"):
        return True
    if text in ("0", "false", "f", "no", "n", "nao", "não"):
        return False
    return None


def water_percent_valor(value) -> float | None:
    if _ausente(value):
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            value = bytes(value).decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError:
            return None
    return _numero(value)


def _numero(value) -> float | None:
    if isinstance(value, bool):
        numero = float(value)
    elif isinstance(value, (int, float)):
        numero = float(value)
    else:
        item = getattr(value, "item", None)
        if callable(item) and not isinstance(value, (str, bytes, bytearray)):
            try:
                numero = float(item())
            except (TypeError, ValueError):
                return None
        else:
            try:
                numero = float(value)
            except (TypeError, ValueError):
                return None
    try:
        if pd.isna(numero):
            return None
    except (TypeError, ValueError):
        return None
    return numero


def _nome_coluna(gdf, nome: str) -> str | None:
    if nome in gdf.columns:
        return nome
    alvo = nome.casefold()
    for col in gdf.columns:
        if str(col).casefold() == alvo:
            return col
    return None


def _fmt_num(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def classificar_grid(sp_val, water_val, sp_out: bool, max_water: float | None):
    """'pass' entra na cópia. 'fail' está fora dos filtros. 'unknown' fica de fora da cópia e não é apagado."""
    motivos = []
    fail = False
    unknown = False
    if not sp_out:
        if sp_val is True:
            pass
        elif sp_val is False:
            fail = True
            motivos.append("fora de SP (sp_inside=false)")
        else:
            unknown = True
            motivos.append("sp_inside ausente")
    if max_water is not None:
        if water_val is None:
            unknown = True
            motivos.append("water_percent ausente")
        elif water_val >= max_water:
            fail = True
            motivos.append(
                f"water_percent={_fmt_num(water_val)} >= {_fmt_num(max_water)}"
            )
    if fail:
        return "fail", "; ".join(motivos)
    if unknown:
        return "unknown", "; ".join(motivos)
    return "pass", ""


_STATUS_RANK = {"unknown": 0, "fail": 1, "pass": 2}


class _IdTrie:
    """Casa o nome do .laz com o id de tile mais longo (mesma borda do match preciso)."""

    def __init__(self):
        self.root: dict = {}

    def add(self, tile_id: str) -> None:
        node = self.root
        for ch in tile_id.casefold():
            node = node.setdefault(ch, {})
        node["$"] = tile_id

    def longest_match(self, filename: str) -> str | None:
        stem = os.path.splitext(filename)[0]
        node = self.root
        found = None
        folded = stem.casefold()
        for i, ch in enumerate(folded):
            nxt = node.get(ch)
            if nxt is None:
                break
            node = nxt
            if "$" in node:
                rest = stem[i + 1 : i + 2]
                if rest == "" or not rest.isalnum():
                    found = node["$"]
        return found


def _agregar_status_tiles(artic) -> dict:
    """Por id de tile: pass ganha de fail, fail ganha de unknown."""
    tile_status = {}
    for tid, status, motivo in zip(
        artic["__tile_id"], artic["__param_status"], artic["__param_motivo"]
    ):
        if not isinstance(tid, str) or not tid:
            continue
        key = tid.casefold()
        prev = tile_status.get(key)
        if prev is None or _STATUS_RANK[status] > _STATUS_RANK[prev["status"]]:
            tile_status[key] = {"id": tid, "status": status, "motivo": motivo}
        elif (
            status == "fail"
            and prev["status"] == "fail"
            and motivo
            and motivo not in prev["motivo"]
        ):
            tile_status[key]["motivo"] = f"{prev['motivo']}; {motivo}"
    return tile_status


def selecionar_grids(
    articulacao_path,
    artic_layer,
    aoi_path,
    aoi_layer,
    sp_out: bool,
    max_water: float | None,
):
    print("Lendo articulação mesclada...")
    artic = gpd.read_file(articulacao_path, layer=artic_layer)
    print(f"  Feições na articulação: {len(artic)}  | CRS: {artic.crs}")

    col_sp = _nome_coluna(artic, "sp_inside")
    col_water = _nome_coluna(artic, "water_percent")
    if not sp_out and col_sp is None:
        print(
            "[ABORTA] Coluna sp_inside não está na articulação. "
            "SP_OUT=false precisa dela (ou use SP_OUT=true)."
        )
        raise SystemExit(2)
    if max_water is not None and col_water is None:
        print(
            "[ABORTA] Coluna water_percent não está na articulação. "
            "Esvazie MAX_WATER_PERCENT para ignorar esse filtro."
        )
        raise SystemExit(2)

    if col_sp is not None:
        sp_vals = artic[col_sp].map(sp_inside_valor).tolist()
    else:
        sp_vals = [None] * len(artic)
    if col_water is not None:
        water_vals = artic[col_water].map(water_percent_valor).tolist()
    else:
        water_vals = [None] * len(artic)

    statuses = []
    motivos = []
    for sp_val, water_val in zip(sp_vals, water_vals):
        status, motivo = classificar_grid(sp_val, water_val, sp_out, max_water)
        statuses.append(status)
        motivos.append(motivo)

    artic = artic.copy()
    artic["__param_status"] = statuses
    artic["__param_motivo"] = motivos
    artic["__tile_id"] = artic.apply(id_do_grid, axis=1)

    n_pass = statuses.count("pass")
    n_fail = statuses.count("fail")
    n_unknown = statuses.count("unknown")
    print("Filtros de atributo:")
    if sp_out:
        print("  SP_OUT=true: dentro e fora de SP")
    else:
        n_in = sum(v is True for v in sp_vals)
        n_out = sum(v is False for v in sp_vals)
        n_sp_na = sum(v is None for v in sp_vals)
        print(
            f"  SP_OUT=false: sp_inside=true {n_in} | fora {n_out} | sem valor {n_sp_na}"
        )
    if max_water is None:
        print("  Água: filtro desligado")
    else:
        n_baixo = sum(v is not None and v < max_water for v in water_vals)
        n_alto = sum(v is not None and v >= max_water for v in water_vals)
        n_w_na = sum(v is None for v in water_vals)
        print(
            f"  Água: water_percent < {_fmt_num(max_water)} "
            f"-> {n_baixo} entram | {n_alto} acima ou igual | sem valor {n_w_na}"
        )
    print(f"  Depois dos dois filtros: entram {n_pass} | fora {n_fail} | incompletos {n_unknown}")

    tile_status = _agregar_status_tiles(artic)
    keep = artic[artic["__param_status"] == "pass"].copy()

    aoi_path = (aoi_path or "").strip() or None
    if keep.empty:
        print("  Nenhum grid passou nos filtros.")
        sel = keep.iloc[0:0].copy()
    elif aoi_path:
        print(f"Lendo AOI: {aoi_path}")
        aoi = ler_aoi(aoi_path, aoi_layer)
        print(f"  Feições na AOI: {len(aoi)}  | CRS: {aoi.crs}")
        if aoi.crs is None:
            raise ValueError(
                "A AOI não tem CRS definido. Defina o CRS do arquivo e tente de novo."
            )
        if artic.crs is not None and aoi.crs != artic.crs:
            print(f"  Reprojetando AOI {aoi.crs} -> {artic.crs}")
            aoi = aoi.to_crs(artic.crs)
        print("Cruzando AOI x articulação (intersects)...")
        sel = gpd.sjoin(keep, aoi[["geometry"]], predicate="intersects", how="inner")
        sel = sel[~sel.index.duplicated(keep="first")].copy()
        print(f"  Grids intersectados: {len(sel)}")
    else:
        print("AOI vazia: sem recorte espacial (todos os grids que passaram nos filtros).")
        sel = keep

    sel = sel[sel["__tile_id"].notna()].copy()
    tiles = []
    seen = set()
    for _, r in sel.iterrows():
        tid = r["__tile_id"]
        if tid in seen:
            continue
        seen.add(tid)
        tiles.append({"tile": tid, "origem": r.get("layer", "?")})
    print(f"  Tiles únicos para copiar: {len(tiles)}")
    return tiles, sel, tile_status


def _listar_amostra(titulo: str, itens, limite: int = 20) -> None:
    print(f"{titulo}: {len(itens)}")
    for item in itens[:limite]:
        print(f"   - {item}")
    if len(itens) > limite:
        print(f"   ... e mais {len(itens) - limite}")


def apagar_laz_fora_dos_parametros(args, tile_status: dict) -> None:
    print("\n" + "!" * 60)
    print("APAGAR LAZ FORA DOS PARAMETROS (DEST\\laz)")
    print("!" * 60)
    print("A AOI não entra aqui. Só SP_OUT e MAX_WATER_PERCENT.")
    if args.sp_out and args.max_water_percent is None:
        print("SP_OUT=true e MAX_WATER_PERCENT vazio: não há o que esteja fora. Nada será apagado.")
        return

    dest_laz = os.path.normcase(os.path.abspath(os.path.join(args.dest, "laz")))
    if not os.path.isdir(dest_laz):
        print(f"Pasta de LAZ no destino não existe (nada a apagar): {dest_laz}")
        return

    trie = _IdTrie()
    for info in tile_status.values():
        trie.add(info["id"])

    apagar = []
    manter_ok = 0
    sem_grid = []
    incerto = []
    for name in sorted(os.listdir(dest_laz)):
        path = os.path.abspath(os.path.join(dest_laz, name))
        if os.path.normcase(os.path.dirname(path)) != dest_laz:
            continue
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() != ".laz":
            continue
        tid = trie.longest_match(name)
        if tid is None:
            sem_grid.append(name)
            continue
        info = tile_status.get(tid.casefold())
        if info is None or info["status"] == "pass":
            manter_ok += 1
            continue
        if info["status"] == "unknown":
            incerto.append(f"{name}  ({info['motivo']})")
            continue
        apagar.append((name, path, info["motivo"]))

    print(f"LAZ que continuam dentro dos filtros: {manter_ok}")
    _listar_amostra("LAZ sem grid correspondente (mantidos)", sem_grid)
    _listar_amostra("LAZ com valor ausente (mantidos)", incerto)
    print(f"LAZ a apagar: {len(apagar)}")
    for name, _, motivo in apagar:
        prefixo = "[DRY-RUN] apagaria" if args.dry_run else "apagando"
        print(f"   {prefixo}: {name}  ({motivo})")

    if args.dry_run:
        print("[DRY-RUN] Nenhum arquivo foi apagado.")
        return
    if not apagar:
        print("Nada a apagar.")
        return

    log_path = os.path.join(dest_laz, "_delete_laz_out_of_parameters.log")
    apagados = 0
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(
            f"\n# {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"sp_out={args.sp_out} max_water={args.max_water_percent}\n"
        )
        for name, path, motivo in apagar:
            try:
                os.remove(path)
                apagados += 1
                log.write(f"{name}\t{motivo}\n")
            except OSError as exc:
                print(f"   !! ERRO ao apagar {name}: {exc}")
                log.write(f"ERRO\t{name}\t{exc}\n")
    print(f"Apagados: {apagados} de {len(apagar)}. Registro: {log_path}")


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

    # G:\. no .bat vira G:\ aqui. O CMD não aceita set "DEST=G:\".
    args.dest = os.path.abspath(args.dest)

    sources = {"LAZ": args.source_laz, "RGB": args.source_rgb, "IR": args.source_ir}
    dests = {
        "LAZ": os.path.join(args.dest, "laz"),
        "RGB": os.path.join(args.dest, "rgb"),
        "IR": os.path.join(args.dest, "ir"),
    }

    aoi = (args.aoi or "").strip() or None
    print("AOI:", aoi or "(vazia = sem recorte espacial)")
    print(
        "SP_OUT:",
        "true (dentro e fora de SP)" if args.sp_out else "false (só sp_inside=true)",
    )
    if args.max_water_percent is None:
        print("MAX_WATER_PERCENT: (vazio = ignora water_percent)")
    else:
        print(f"MAX_WATER_PERCENT: water_percent < {_fmt_num(args.max_water_percent)}")
    print(
        "Apagar LAZ fora dos filtros:",
        "SIM" if args.delete_laz_out_of_parameters else "não",
    )

    tiles, sel, tile_status = selecionar_grids(
        args.articulacao,
        args.articulacao_layer,
        aoi,
        args.aoi_layer,
        args.sp_out,
        args.max_water_percent,
    )
    if args.delete_laz_out_of_parameters:
        apagar_laz_fora_dos_parametros(args, tile_status)
    if not tiles:
        print("\n[ABORTA] Nenhum tile para copiar. Encerrando.")
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
def _float_ou_vazio(value: str):
    texto = str(value).strip()
    if texto == "":
        return None
    return float(texto)


def parse_args():
    p = argparse.ArgumentParser(
        description="Transfere tiles LAZ/RGB/IR do IGC/SP. AOI opcional; "
                    "sem AOI usa os grids que passarem em sp_inside e water_percent.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--aoi", default=None,
                   help="Arquivo espacial da área de interesse "
                        "(.shp/.geojson/.gpkg/.kml/.kmz). "
                        "Vazio: sem recorte espacial.")
    p.add_argument("--sp-out", action=argparse.BooleanOptionalAction, default=False,
                   help="Incluir grids fora de SP. Padrão: só sp_inside=true.")
    p.add_argument("--max-water-percent", type=_float_ou_vazio, default=None,
                   help="Só grids com water_percent menor que este valor. "
                        "Vazio: ignora a coluna.")
    p.add_argument("--delete-laz-out-of-parameters", action="store_true",
                   help="Apaga .laz já em DEST/laz cujo grid está fora de "
                        "--sp-out e --max-water-percent. Não usa a AOI.")
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
                   help="Só vistoria; não copia e não apaga nada.")

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

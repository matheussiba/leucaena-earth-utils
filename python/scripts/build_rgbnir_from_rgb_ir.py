"""
Build 4-band RGBNIR GeoTIFFs from paired RGB and IR tiles.

Lab machine (data center), double-click the companion .bat or run::

    python build_rgbnir_from_rgb_ir.py

Defaults, meant for that machine:

    E:\\RGB     3-band RGB   (band 1 Red, band 2 Green, band 3 Blue)
    F:\\IR      IR raster    (band 1 is the NIR channel)
    G:\\RGBNIR  4-band output (Red, Green, Blue, NIR)

The fusion method is the same one used by
``leucaena-earth-segmentation/prep-rgbnir-from-rgb-ir.py``:

    band 1 = Red    (RGB band 1)
    band 2 = Green  (RGB band 2)
    band 3 = Blue   (RGB band 3)
    band 4 = NIR    (IR  band 1, not treated as alpha)

Resume: each tile is written to ``*.tmp.tif`` and renamed only after the
file is complete. A second run skips any final ``.tif`` already in
``G:\\RGBNIR`` and continues with what is missing. Stop it with Ctrl+C
and start the .bat again; finished tiles stay on disk.

Three CSV files are kept in the output folder and rewritten after every
tile, so the spreadsheet is useful even if the run is interrupted:

    rgbnir_summary.csv              counts (done, still missing, unpaired)
    rgbnir_registry.csv             one row per RGB/IR pair or leftover file
    rgbnir_registry_dicionario.csv  what each column and status value means

A spatial cobertura GeoPackage is also kept in the output folder
(``rgbnir_cobertura.gpkg``), with the same articulation schema used by
``transferir_laz_rgb_ir_v3_por_aoi.py``. The first run that finds no GPKG
bootstraps every finished TIFF already in ``G:\\RGBNIR``; afterwards each
new successful write appends one feature. New attribute columns
(``cenario``, ``tipo_juncao``, ``alinhamento``, ...) are added once when an
older GPKG is loaded; later runs only append rows. Updates are always
written to a temporary file and renamed into place so a crash does not
leave a half-written GPKG. Articulation polygons come from
``C:\\Users\\admin\\Documents\\Projetos_Matheus_Barros\\msb-phd-leucaena-sp.gpkg``.

Queue order: pending pairs with matching RGB/IR grids (direct NIR copy)
run first; pairs that need ``-resampled`` run only after those finish.
The direto/resampled classification is cached in ``rgbnir_grade_vistoria.csv``
so later runs reuse it and only probe new pairs (unless ``--force-grade``).

A pair is the same filename in both folders (case-insensitive). If the
names differ only by the usual IGC wrappers (``ORTM_RGB_`` / ``ORTM_IR_``,
``ORTO_RGB_`` / ``ORTO_IR_``, ``_ORTO_RGB`` / ``_ORTO_IR``, ``_V1``), they are still paired and the
registry records ``modo_pareamento=nome_normalizado``.

Several products of the same tile are all kept. For each cleaned tile id
the script applies this cascade:

    1. RAW + RAW              -> <ID>.tif
    2. same family and version-> <ID>_ORTO.tif, <ID>_ORTM_V1.tif, ...
    3. RAW paired with a family that has no twin -> <ID>-CEN3-RAW-ORTO.tif
    4. different families     -> <ID>-CEN4-ORTM-ORTO.tif
    5. nothing left to pair   -> no GeoTIFF; the file is only listed in the CSV

The output grid is always the RGB grid. When the IR origin already matches,
band 4 is copied and the filename stays as above. When the grids differ, band
4 is warped with nearest neighbor (``rasterio.warp.reproject``) and the
filename gains ``-resampled`` before the extension (``<ID>-resampled.tif``).

Only tiles that intersect Sao Paulo are fused. The state polygons come from
``data/sp-bounds.gpkg``, already split into two layers: ``epsg31982`` and
``epsg31983``. A TIFF is compared with the layer of the same EPSG. The script
does not reproject that polygon. Each TIFF is tested by its header bounds,
not by its pixels. That result is stored in ``rgbnir_sp_vistoria.csv`` and
reused on the next run. Set ``FORCE_SP_VISTORIA = True`` or pass ``--force-sp``
to compare every TIFF again.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import os
import re
import shutil
import socket
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    import rasterio
    from rasterio.enums import ColorInterp
    from rasterio.errors import RasterioIOError
except ImportError:  # checked again in main(); matching logic stays importable
    rasterio = None  # type: ignore[assignment]
    ColorInterp = None  # type: ignore[assignment]
    RasterioIOError = OSError  # type: ignore[misc, assignment]

# -----------------------------------------------------------------------------
# Defaults for the lab data-center machine. Override with CLI flags.
# -----------------------------------------------------------------------------

DEFAULT_RGB_DIR = r"E:\RGB"
DEFAULT_IR_DIR = r"F:\IR"
DEFAULT_OUT_DIR = r"G:\RGBNIR"
_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_SP_BOUNDS = str(_DATA_DIR / "sp-bounds.gpkg")
DEFAULT_ARTICULACAO = str(
    _DATA_DIR / "articulacao_igc_sp" / "articulacao-igc-merged-spatialindex-sirgas2000.gpkg"
)
DEFAULT_ARTIC_LAYER = "articulacao-igc-merged-spatialindex-sirgas2000"
# Filename suffix when the TIFF CRS is remapped via articulacao.epsg (user spelling).
PROJETION_ASSIGNED_SUFFIX = "-projetion_assigned"

# False reuses rgbnir_sp_vistoria.csv and does not reopen TIFF headers.
# True compares every RGB and IR against Sao Paulo again.
FORCE_SP_VISTORIA = False

IR_NIR_BAND_INDEX = 1
RASTER_SUFFIXES = (".tif", ".tiff")
TMP_SUFFIX = ".tmp.tif"
LOCK_NAME = "rgbnir_running.lock"

SUMMARY_NAME = "rgbnir_summary.csv"
REGISTRY_NAME = "rgbnir_registry.csv"
DICTIONARY_NAME = "rgbnir_registry_dicionario.csv"
SP_VISTORIA_NAME = "rgbnir_sp_vistoria.csv"
GRADE_VISTORIA_NAME = "rgbnir_grade_vistoria.csv"
COBERTURA_NAME = "rgbnir_cobertura.gpkg"
COBERTURA_LAYER = "cobertura"

COMPRESS = "DEFLATE"
BLOCK = 512
VERSION = "1.12.0"

# Same id columns as transferir_laz_rgb_ir_v3_por_aoi.py
_ID_COLUMN_BY_LAYER = {
    "Articulacao_Laser_Fehidro": "NOMENC_2K",
    "Articulacao_Laser_Lote4": "NOMENC_5K",
    "Articulacao_Laser_Voo22": "NOMENC_5K",
}
_AUTO_ID_COLUMNS = ("NOMENC_2K", "NOMENC_5K", "NOMENC_10K")
_COBERTURA_KEEP_COLS = (
    "layer",
    "NOMENC_2K",
    "NOMENC_5K",
    "NOMENC_10K",
    "has_rgbnir",
    "saida_nome",
    "tile_key",
    "cenario",
    "modo_pareamento",
    "tipo_registro",
    "status",
    "tipo_juncao",
    "alinhamento",
    "geometry",
)
# tipo_juncao / alinhamento vocabulary written into the cobertura GPKG.
_TIPO_JUNCAO_DIRETOS = "direto"
_TIPO_JUNCAO_RESAMPLED = "resampled"
_ALINHAMENTO_NA = "nao_aplicavel"

# Do not let GDAL drop .aux.xml sidecars beside the source tiles on E: and F:.
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_PAM_ENABLED", "NO")

# Printed and written into the dictionary so the CSV explains itself.
COLUMNS: list[tuple[str, str, str]] = [
    (
        "status",
        "Situacao desta linha agora. Veja as linhas 'valor_de_status' neste dicionario.",
        "pulado_ja_existe",
    ),
    (
        "tile_key",
        "Identificador do tile. No pareamento por nome igual, e o nome do arquivo sem extensao.",
        "SF-23-Y-C-IV-2",
    ),
    (
        "notas",
        "Frase com a juncao usada: qual RGB, qual IR, quais bandas e qual arquivo de saida.",
        "Juntou E:\\RGB\\SF-23.tif bandas 1-3 com F:\\IR\\SF-23.tif banda 1 em G:\\RGBNIR\\SF-23.tif.",
    ),
    ("rgb_nome", "Nome do arquivo RGB usado neste par. Vazio se este RGB nao existe.", "SF-23.tif"),
    ("ir_nome", "Nome do arquivo IR usado neste par. Vazio se este IR nao existe.", "SF-23.tif"),
    (
        "saida_nome",
        "Nome do GeoTIFF. Cenario 1: ID.tif. Cenario 2: ID_ORTO.tif ou ID_ORTM_V1.tif. Cenario 3: ID-CEN3-RAW-ORTO.tif. Cenario 4: ID-CEN4-ORTM-ORTO.tif. Se a grade do IR diferir da do RGB, o mesmo nome ganha -resampled antes da extensao.",
        "SF-22-X_ORTM_V1.tif",
    ),
    (
        "cenario",
        "1=puro+puro, 2=mesma familia e versao, 3=puro com familia sem par, 4=familias diferentes, 5=sem par ou anomalia (nao gera tif).",
        "2",
    ),
    (
        "tipo_rgb",
        "Classificacao do RGB: RAW (puro), ou familia/versao como ORTO, ORTM, ORTM_V1, R1, V2.",
        "ORTM_V1",
    ),
    (
        "tipo_ir",
        "Classificacao do IR, no mesmo vocabulario de tipo_rgb. RAW representa o arquivo puro.",
        "ORTO",
    ),
    (
        "modo_pareamento",
        "cenario_1 a cenario_4 quando houve par. nenhum quando o arquivo ficou no cenario 5.",
        "cenario_2",
    ),
    (
        "tipo_registro",
        "par = RGB e IR casados; rgb_sem_ir / ir_sem_rgb = arquivo sem parceiro; duplicata_ignorada = mesmo nome repetido, ficou o maior; conflito_saida = dois pares queriam o mesmo nome de saida.",
        "par",
    ),
    ("rgb_caminho", "Caminho completo do RGB. A pasta de origem nao e alterada.", r"E:\RGB\SF-23.tif"),
    ("ir_caminho", "Caminho completo do IR. A pasta de origem nao e alterada.", r"F:\IR\SF-23.tif"),
    ("saida_caminho", "Caminho completo do RGBNIR de 4 bandas.", r"G:\RGBNIR\SF-23.tif"),
    ("bandas_rgb", "Bandas copiadas do RGB, nesta ordem: 1=Red, 2=Green, 3=Blue.", "1,2,3"),
    ("banda_nir_ir", "Banda do arquivo IR copiada para a banda 4 (NIR) da saida.", "1"),
    ("largura_px", "Largura do raster em pixels, preenchida quando o tile e gravado.", "20000"),
    ("altura_px", "Altura do raster em pixels, preenchida quando o tile e gravado.", "20000"),
    ("crs", "CRS lido do RGB no momento da gravacao.", "EPSG:31983"),
    ("rgb_bytes", "Tamanho do arquivo RGB em bytes.", "1500000000"),
    ("ir_bytes", "Tamanho do arquivo IR em bytes.", "1400000000"),
    ("saida_bytes", "Tamanho do RGBNIR gravado, em bytes. Vazio enquanto ainda nao existe.", "2800000000"),
    ("inicio_utc", "Hora UTC em que a gravacao deste tile comecou. Preservada nas execucoes seguintes.", "2026-11-05T20:15:01Z"),
    ("fim_utc", "Hora UTC em que a gravacao terminou com sucesso.", "2026-11-05T20:21:40Z"),
    ("duracao_segundos", "Segundos gastos na gravacao deste tile.", "399.2"),
    ("detalhe", "Aviso ou motivo tecnico da ultima tentativa (grade diferente, banda faltando, etc.).", ""),
    (
        "ultimo_erro",
        "Ultimo erro de gravacao. Limpo quando uma gravacao da certo. Mantido se o arquivo ja existia.",
        "",
    ),
    (
        "run_id_que_gravou",
        "Identificador da execucao que criou o arquivo final. Nao muda quando o tile e apenas pulado.",
        "20261105T201501Z",
    ),
    (
        "run_id_desta_execucao",
        "Identificador da execucao que reescreveu esta planilha.",
        "20261105T221001Z",
    ),
    (
        "atualizado_utc",
        "Hora UTC da ultima vez que esta planilha foi regravada (depois de cada tile).",
        "2026-11-05T22:10:01Z",
    ),
]

STATUS_HELP: list[tuple[str, str]] = [
    ("ok", "GeoTIFF de 4 bandas gravado nesta execucao."),
    (
        "pulado_ja_existe",
        "O arquivo final ja estava na pasta de saida (de uma execucao anterior ou desta). Nao foi refeito.",
    ),
    ("pendente", "Par encontrado, ainda sem arquivo final. Entra na fila desta execucao."),
    ("falha", "A gravacao nao terminou. O parcial foi apagado. Rodar o .bat de novo tenta so este de novo."),
    ("interrompido", "Ctrl+C no meio deste tile. Nada final foi deixado. Rodar de novo continua por ele."),
    ("sem_par", "Cenario 5. Nao havia arquivo do outro lado para formar par. Nenhum tif foi gerado."),
    (
        "anomalia",
        "Cenario 5. O tile tinha mais de um arquivo do mesmo tipo, ou o nome nao virou um id. Nenhum tif foi gerado para este arquivo.",
    ),
    (
        "duplicata_ignorada",
        "Dois arquivos com o mesmo nome. O script ficou com o maior e registrou este aqui sem usa-lo.",
    ),
    (
        "conflito_saida",
        "Dois pares diferentes produziriam o mesmo nome em G:\\RGBNIR. Este nao foi gravado, para nao sobrescrever o outro.",
    ),
    (
        "fora_de_sp",
        "O retangulo do raster nao intersecta o limite de Sao Paulo. Nao entra no pareamento nem na fusao.",
    ),
    (
        "sem_localizacao",
        "Nao deu para ler o CRS ou o retangulo do raster, entao ele nao foi comparado com Sao Paulo e ficou de fora.",
    ),
]

COLUMN_NAMES = [name for name, _desc, _example in COLUMNS]


def log(message: str) -> None:
    """Console line with a local timestamp. Flushed so the .bat window stays live."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def fmt_size(num_bytes: float | int | None) -> str:
    if num_bytes is None or num_bytes == "":
        return "-"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def fmt_duration(seconds: float) -> str:
    total = int(max(0.0, seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}h {minutes:02d}m"
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes:02d}m {secs:02d}s"


def _to_int_bytes(value: object) -> int:
    try:
        text = str(value or "").strip()
        if not text:
            return 0
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def folder_size_bytes(folder: Path) -> int:
    """Sum file sizes under ``folder`` (RGBNIR tree, including sidecars/CSV)."""
    total = 0
    if not folder.is_dir():
        return 0
    for dirpath, _dirnames, filenames in os.walk(folder):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total


def average_saida_bytes(rows: list[dict[str, str]]) -> int:
    sizes = [_to_int_bytes(row.get("saida_bytes")) for row in rows if _to_int_bytes(row.get("saida_bytes")) > 0]
    if not sizes:
        return 0
    return int(sum(sizes) / len(sizes))


def estimate_row_output_bytes(row: dict[str, str], avg_saida: int) -> int:
    known = _to_int_bytes(row.get("saida_bytes"))
    if known > 0:
        return known
    if avg_saida > 0:
        return avg_saida
    # Rough fallback before any output exists: RGB+IR input is a weak proxy.
    return max(_to_int_bytes(row.get("rgb_bytes")) + _to_int_bytes(row.get("ir_bytes")), 0)


def blank_row() -> dict[str, str]:
    return {name: "" for name in COLUMN_NAMES}


# -----------------------------------------------------------------------------
# Windows: stay awake, and refuse a second copy of this script.
# -----------------------------------------------------------------------------

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


class _KeepAwake:
    """Stop Windows from sleeping while a long fusion run is in progress."""

    def __enter__(self) -> "_KeepAwake":
        self._ok = False
        if not sys.platform.startswith("win"):
            return self
        try:
            flags = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
            prev = ctypes.windll.kernel32.SetThreadExecutionState(flags)
            self._ok = prev != 0
            if self._ok:
                log("Windows nao vai dormir nem desligar a tela enquanto este script roda.")
            else:
                log("AVISO: nao consegui bloquear a suspensao do Windows. Confira o plano de energia.")
        except Exception as exc:  # noqa: BLE001
            log(f"AVISO: keep-awake falhou: {exc}")
        return self

    def __exit__(self, *_) -> None:
        if not self._ok:
            return
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
            log("Suspensao do Windows restaurada.")
        except Exception:  # noqa: BLE001
            pass


def _pid_is_running(pid: int) -> bool:
    if pid <= 0 or not sys.platform.startswith("win"):
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    # 5 = access denied: the process exists, we just cannot inspect it.
    return ctypes.get_last_error() == 5


class RunLock:
    """One writer at a time. A lock left by a dead process is removed."""

    def __init__(self, folder: Path, run_id: str) -> None:
        self.path = folder / LOCK_NAME
        self.run_id = run_id
        self._held = False
        self.blocked_by_other = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            pid = _read_lock_pid(self.path)
            if pid and _pid_is_running(pid):
                self.blocked_by_other = True
                log(
                    f"ABORTADO: ja existe uma copia deste script rodando (PID {pid}). "
                    f"Trava: {self.path}"
                )
                log("Feche a outra janela antes de comecar de novo. Os arquivos ja prontos nao foram mexidos.")
                return False
            log(f"Trava antiga encontrada (PID {pid or '?'} nao esta mais rodando). Vou remove-la e continuar.")
            try:
                self.path.unlink()
            except OSError as exc:
                log(f"ABORTADO: nao consegui remover a trava antiga {self.path}: {exc}")
                return False
        try:
            self.path.write_text(
                "\n".join(
                    [
                        f"pid={os.getpid()}",
                        f"hostname={socket.gethostname()}",
                        f"run_id={self.run_id}",
                        f"started_utc={utc_now()}",
                        "script=build_rgbnir_from_rgb_ir.py",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            log(f"ABORTADO: nao consegui criar a trava {self.path}: {exc}")
            return False
        self._held = True
        log(f"Trava criada: {self.path} (PID {os.getpid()}).")
        return True

    def release(self) -> None:
        if not self._held:
            return
        try:
            self.path.unlink()
            log("Trava removida.")
        except OSError as exc:
            log(f"AVISO: nao consegui remover a trava {self.path}: {exc}")
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


# -----------------------------------------------------------------------------
# Discover and pair files. Pure: no rasterio, so it can be checked on its own.
# -----------------------------------------------------------------------------

_ILLEGAL_NAME = re.compile(r'[<>:"/\\|?*]')


def tile_key_from_filename(filename: str) -> str:
    """Strip the usual IGC RGB/IR wrappers and return the bare tile id.

    Exact filename matches never need this. It only pairs files whose names
    differ by these decorations::

        ORTM_RGB_<ID>_V1.tif
        ORTM_IR_<ID>_V1.tif
        ORTO_RGB_<ID>.tif
        ORTO_IR_<ID>.tif
        <ID>_ORTO_RGB.tif / <ID>_ORTO-RGB.tif
        <ID>_ORTO_IR.tif  / <ID>_ORTO-IR.tif
        <ID>_ORTO-RGB_V2.tif

    Version (``_V1``) and the ORTO suffix are removed in either order, so
    ``<ID>_ORTO-RGB_V2`` still collapses to ``<ID>``. The prefix form
    ``ORTO_RGB_<ID>`` / ``ORTO_IR_<ID>`` collapses to the same id.
    """
    stem = Path(filename).stem
    stem = re.sub(r"(?i)^ORTM_(?:RGB|IR)_", "", stem)
    stem = re.sub(r"(?i)^ORTO[_-](?:RGB|IR)_", "", stem)
    previous = None
    while previous != stem:
        previous = stem
        stem = re.sub(r"(?i)_ORTO[_-](?:RGB|IR)$", "", stem)
        stem = re.sub(r"(?i)_V\d+$", "", stem)
        stem = re.sub(r"(?i)_R\d+$", "", stem)
    return stem.strip()


def classify_product(filename: str) -> str:
    """RAW, or a family/version token such as ORTO, ORTM_V1, R1, ORTO_V2.

    RAW means the name has none of ORTO, ORTM, ``_V1``/``_V2`` or ``_R1``.
    The token is what cenarios 2, 3 and 4 write into the output filename.
    """
    stem = Path(filename).stem
    upper = stem.upper()
    parts: list[str] = []
    if "ORTM" in upper:
        parts.append("ORTM")
    elif "ORTO" in upper:
        parts.append("ORTO")
    versions = re.findall(r"(?i)(?:^|_)(V\d+)(?:_|$)", stem)
    revisions = re.findall(r"(?i)(?:^|_)(R\d+)(?:_|$)", stem)
    if versions:
        parts.append(versions[-1].upper())
    if revisions:
        parts.append(revisions[-1].upper())
    return "_".join(parts) if parts else "RAW"


def output_name_for_scenario(tile_id: str, cenario: int, tipo_rgb: str, tipo_ir: str) -> str:
    """Filename for cenarios 1-4 when the IR grid already matches the RGB.

    Cenario 5 does not write a GeoTIFF. A grid mismatch does not use this
    name: ``resampled_output_name`` adds ``-resampled`` at fuse time.
    """
    stem = safe_output_stem(tile_id)
    if cenario == 1:
        base = stem
    elif cenario == 2:
        base = f"{stem}_{tipo_rgb}"
    elif cenario == 3:
        base = f"{stem}-CEN3-{tipo_rgb}-{tipo_ir}"
    elif cenario == 4:
        base = f"{stem}-CEN4-{tipo_rgb}-{tipo_ir}"
    else:
        raise ValueError(f"cenario sem arquivo de saida: {cenario}")
    return f"{base}.tif"


def _strip_known_output_suffixes(stem: str) -> str:
    text = stem
    changed = True
    while changed:
        changed = False
        lower = text.casefold()
        for suffix in (
            PROJETION_ASSIGNED_SUFFIX,
            "-resampled",
            "_resampled",
        ):
            if lower.endswith(suffix.casefold()):
                text = text[: -len(suffix)]
                changed = True
                break
    return text


def apply_output_suffixes(
    plain_name: str,
    *,
    resampled: bool = False,
    projetion_assigned: bool = False,
) -> str:
    """Build the final filename. Both suffixes may appear together."""
    path = Path(plain_name)
    stem = _strip_known_output_suffixes(path.stem)
    if resampled:
        stem = f"{stem}-resampled"
    if projetion_assigned:
        stem = f"{stem}{PROJETION_ASSIGNED_SUFFIX}"
    return f"{stem}{path.suffix or '.tif'}"


def resampled_output_name(plain_name: str) -> str:
    """Name used only when the IR grid has to be aligned onto the RGB grid."""
    return apply_output_suffixes(plain_name, resampled=True, projetion_assigned=False)


def finished_output(out_dir: Path, plain_name: str) -> Path | None:
    """An existing final TIFF for this pair (plain / resampled / projetion_assigned)."""
    stem = _strip_known_output_suffixes(Path(plain_name).stem)
    suffix = Path(plain_name).suffix or ".tif"
    base = f"{stem}{suffix}"
    candidates = [
        out_dir / base,
        out_dir / apply_output_suffixes(base, resampled=True),
        out_dir / apply_output_suffixes(base, projetion_assigned=True),
        out_dir / apply_output_suffixes(base, resampled=True, projetion_assigned=True),
        out_dir / f"{stem}_resampled{suffix}",
        out_dir / f"{stem}_resampled{PROJETION_ASSIGNED_SUFFIX}{suffix}",
    ]
    for path in candidates:
        if output_is_done(path):
            return path
    return None


def safe_output_stem(stem: str) -> str:
    cleaned = _ILLEGAL_NAME.sub("_", stem).strip().rstrip(".")
    return cleaned or "sem_nome"


@dataclass
class RasterHit:
    path: str
    filename: str
    name_key: str
    tile_key: str
    size: int


@dataclass
class Pairing:
    """Everything the scan found, already classified for the registry."""

    pairs: list[dict[str, str]] = field(default_factory=list)
    leftovers: list[dict[str, str]] = field(default_factory=list)

    def all_rows(self) -> list[dict[str, str]]:
        return self.pairs + self.leftovers


def scan_rasters(folder: Path) -> list[RasterHit]:
    """Walk ``folder`` recursively and collect GeoTIFF paths. Does not open them."""
    found: list[RasterHit] = []
    started = time.time()
    directories = 0
    log(f"Vasculhando {folder} ...")
    for dirpath, dirnames, filenames in os.walk(folder):
        directories += 1
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            lower = name.lower()
            if not lower.endswith(RASTER_SUFFIXES):
                continue
            if lower.endswith(TMP_SUFFIX):
                continue
            path = Path(dirpath) / name
            try:
                size = path.stat().st_size
            except OSError as exc:
                log(f"  AVISO: nao li o tamanho de {path}: {exc}")
                continue
            found.append(
                RasterHit(
                    path=str(path),
                    filename=name,
                    name_key=name.casefold(),
                    tile_key=tile_key_from_filename(name),
                    size=size,
                )
            )
            if len(found) % 2000 == 0:
                log(f"  ... {len(found)} rasters ate agora em {folder} ({fmt_duration(time.time() - started)})")
    log(
        f"  {len(found)} raster(s) em {directories} pasta(s), "
        f"em {fmt_duration(time.time() - started)}."
    )
    return found


# Layer name in data/sp-bounds.gpkg -> the EPSG the layer is already stored in.
# The script never reprojects these polygons.
_SP_LAYER_BY_EPSG = {31982: "epsg31982", 31983: "epsg31983"}


class AssignedEpsgIndex:
    """Map tile ids (NOMENC_*) -> EPSG 31982/31983 from the articulacao GPKG."""

    def __init__(self, path: Path, layer: str = DEFAULT_ARTIC_LAYER):
        import geopandas as gpd

        self.path = path
        self.layer = layer
        self._by_id: dict[str, int] = {}
        if not path.is_file():
            raise FileNotFoundError(f"Articulacao para EPSG atribuido nao encontrada: {path}")
        log(f"Lendo EPSG por tile na articulacao: {path} | layer={layer}")
        artic = gpd.read_file(path, layer=layer)
        if "epsg" not in artic.columns:
            raise ValueError(
                f"A articulacao {path} nao tem a coluna 'epsg' (31982/31983 por tile)."
            )
        for _, row in artic.iterrows():
            try:
                epsg = int(float(str(row.get("epsg")).strip()))
            except (TypeError, ValueError):
                continue
            if epsg not in (31982, 31983):
                continue
            for col in _AUTO_ID_COLUMNS:
                value = row.get(col)
                if value is None or (isinstance(value, float) and value != value):
                    continue
                text = str(value).strip()
                if text:
                    self._by_id.setdefault(text.casefold(), epsg)
        log(f"  EPSG atribuido indexado para {len(self._by_id)} id(s) de tile.")

    def lookup_filename(self, filename: str) -> int | None:
        for candidate in tile_id_candidates_from_saida(filename, tile_key_from_filename(filename)):
            hit = self._by_id.get(candidate.casefold())
            if hit is not None:
                return hit
        return None


class SpBounds:
    """Sao Paulo polygons, one layer per TIFF CRS, used without reprojection.

    ``epsg31982`` is crossed only with rasters in EPSG:31982. ``epsg31983`` is
    crossed only with rasters in EPSG:31983. The geometry kept is the full
    layer polygon, not its bounding rectangle.
    """

    def __init__(self, path: Path):
        import geopandas as gpd
        from pyproj import CRS
        from shapely.ops import unary_union
        from shapely.prepared import prep

        log(f"Lendo o limite de Sao Paulo: {path}")
        listed = gpd.list_layers(path)
        available = {str(name).casefold(): str(name) for name in listed["name"]}
        self.path = path
        self._by_epsg: dict[int, tuple] = {}
        missing: list[str] = []
        for epsg, layer_name in _SP_LAYER_BY_EPSG.items():
            actual = available.get(layer_name.casefold())
            if actual is None:
                missing.append(layer_name)
                continue
            gdf = gpd.read_file(path, layer=actual)
            if gdf.empty or gdf.crs is None:
                raise ValueError(f"A camada {actual} nao tem geometria ou CRS.")
            if not CRS.from_user_input(gdf.crs).equals(CRS.from_epsg(epsg)):
                raise ValueError(
                    f"A camada {actual} esta em {gdf.crs}, e o esperado e EPSG:{epsg}. "
                    "O script nao converte o limite; grave a camada nesse EPSG."
                )
            geom = unary_union(gdf.geometry.to_list())
            if geom is None or geom.is_empty:
                raise ValueError(f"A camada {actual} ficou vazia.")
            self._by_epsg[epsg] = (prep(geom), tuple(geom.bounds))
            log(
                f"  Camada {actual}: EPSG:{epsg}, {len(gdf)} feicao(oes), "
                "geometria completa, sem conversao."
            )
        if missing:
            found = ", ".join(sorted(available)) or "(nenhuma)"
            raise ValueError(
                f"Faltam as camadas {', '.join(missing)} em {path}. Camadas encontradas: {found}."
            )

    def for_epsg(self, epsg: int | None):
        if epsg is None:
            return None
        return self._by_epsg.get(int(epsg))


def _bounds_overlap(raster_bounds: tuple[float, float, float, float], envelope: tuple[float, float, float, float]) -> bool:
    left, bottom, right, top = raster_bounds
    env_left, env_bottom, env_right, env_top = envelope
    return not (right < env_left or left > env_right or top < env_bottom or bottom > env_top)


def _classify_against_sp(
    path: str,
    mask: SpBounds,
    assigned_index: AssignedEpsgIndex | None = None,
) -> tuple[str, str, str, str]:
    """Return (status, crs, detalhe, epsg_atribuido). Header only."""
    from shapely.geometry import box
    from shapely.ops import transform as shp_transform

    assigned = ""
    try:
        with rasterio.open(path) as src:
            if src.crs is None:
                return "sem_localizacao", "", "CRS ausente; nao da para saber se cai em Sao Paulo.", ""
            epsg = src.crs.to_epsg()
            crs_text = f"EPSG:{epsg}" if epsg else src.crs.to_string()
            bounds = (
                float(src.bounds.left),
                float(src.bounds.bottom),
                float(src.bounds.right),
                float(src.bounds.top),
            )
            src_crs = src.crs
    except Exception as exc:  # noqa: BLE001
        return "sem_localizacao", "", f"Nao abri o cabecalho: {type(exc).__name__}: {exc}", ""

    packed = mask.for_epsg(epsg) if epsg else None
    test_bounds = bounds
    if packed is None and assigned_index is not None:
        mapped = assigned_index.lookup_filename(Path(path).name)
        if mapped is not None:
            assigned = str(mapped)
            try:
                from pyproj import Transformer

                transformer = Transformer.from_crs(src_crs, f"EPSG:{mapped}", always_xy=True)
                geom = shp_transform(transformer.transform, box(*bounds))
                test_bounds = (float(geom.bounds[0]), float(geom.bounds[1]), float(geom.bounds[2]), float(geom.bounds[3]))
            except Exception:
                # UTM WGS84 vs SIRGAS are nearly identical; keep native bounds as fallback.
                test_bounds = bounds
            packed = mask.for_epsg(mapped)
            crs_text = f"{crs_text}->EPSG:{mapped}"
    if packed is None:
        return (
            "sem_localizacao",
            crs_text,
            f"CRS {crs_text} nao tem camada correspondente. "
            "O cruzamento usa epsg31982 ou epsg31983 (ou articulacao.epsg).",
            "",
        )
    prepared, envelope = packed
    detail_assign = (
        f" EPSG {assigned} atribuido pela articulacao (sufixo {PROJETION_ASSIGNED_SUFFIX})."
        if assigned
        else ""
    )
    if not _bounds_overlap(test_bounds, envelope):
        return (
            "fora_de_sp",
            crs_text,
            f"Retangulo fora do envelope de Sao Paulo ({crs_text}).{detail_assign}",
            assigned,
        )
    if prepared.intersects(box(*test_bounds)):
        return "dentro", crs_text, detail_assign.strip(), assigned
    return (
        "fora_de_sp",
        crs_text,
        f"Nao intersecta o limite de Sao Paulo ({crs_text}).{detail_assign}",
        assigned,
    )


_SP_VISTORIA_COLUMNS = ["caminho", "lado", "tamanho", "status", "crs", "detalhe", "epsg_atribuido"]
_SP_VISTORIA_STATUS = {"dentro", "fora_de_sp", "sem_localizacao"}


def _store_sp_vistoria(
    cache: dict[str, dict[str, str]],
    path: str,
    lado: str,
    tamanho: str,
    status: str,
    crs_text: str,
    detalhe: str,
    epsg_atribuido: str = "",
) -> None:
    key = path.casefold()
    current = cache.get(key)
    # Never downgrade a previous "inside SP" result on a weaker recheck.
    if current and current.get("status") == "dentro" and status in {"fora_de_sp", "sem_localizacao"}:
        return
    cache[key] = {
        "caminho": path,
        "lado": lado,
        "tamanho": tamanho,
        "status": status,
        "crs": crs_text,
        "detalhe": detalhe,
        "epsg_atribuido": epsg_atribuido or (current or {}).get("epsg_atribuido") or "",
    }


def load_sp_vistoria(path: Path) -> dict[str, dict[str, str]]:
    """Previous inside/outside results, keyed by the source TIFF path."""
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            cache: dict[str, dict[str, str]] = {}
            for row in csv.DictReader(handle):
                caminho = (row.get("caminho") or "").strip()
                status = (row.get("status") or "").strip()
                if caminho and status in _SP_VISTORIA_STATUS:
                    _store_sp_vistoria(
                        cache,
                        caminho,
                        (row.get("lado") or "").strip(),
                        (row.get("tamanho") or "").strip(),
                        status,
                        (row.get("crs") or "").strip(),
                        (row.get("detalhe") or "").strip(),
                        (row.get("epsg_atribuido") or "").strip(),
                    )
            return cache
    except OSError as exc:
        log(f"AVISO: nao li a vistoria salva {path}: {exc}")
        return {}


def save_sp_vistoria(path: Path, cache: dict[str, dict[str, str]], hits: list[RasterHit]) -> None:
    rows = []
    for hit in hits:
        saved = cache.get(hit.path.casefold())
        if saved:
            rows.append(saved)
    rows.sort(key=lambda row: (row.get("lado", ""), row.get("caminho", "").casefold()))
    if write_csv_atomic(path, _SP_VISTORIA_COLUMNS, rows):
        log(f"Vistoria de SP salva: {path} ({len(rows)} arquivo(s)).")


def vistoria_from_registry(out_dir: Path) -> dict[str, dict[str, str]]:
    """Rebuild the SP cache from a registry written after a finished inspection.

    The summary metric ``fora_de_sp`` exists only once that inspection has been
    published. Older registries, from before the state filter, are ignored.
    """
    summary = out_dir / SUMMARY_NAME
    registry = out_dir / REGISTRY_NAME
    if not summary.is_file() or not registry.is_file():
        return {}
    try:
        with summary.open("r", encoding="utf-8-sig", newline="") as handle:
            inspected = any((row.get("metrica") or "") == "fora_de_sp" for row in csv.DictReader(handle))
    except OSError as exc:
        log(f"AVISO: nao li {summary}: {exc}")
        return {}
    if not inspected:
        return {}
    cache: dict[str, dict[str, str]] = {}
    try:
        with registry.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                status = (row.get("status") or "").strip()
                if status in {"fora_de_sp", "sem_localizacao"}:
                    if row.get("rgb_caminho"):
                        _store_sp_vistoria(
                            cache, row["rgb_caminho"], "rgb", row.get("rgb_bytes") or "",
                            status, row.get("crs") or "", row.get("detalhe") or "",
                        )
                    elif row.get("ir_caminho"):
                        _store_sp_vistoria(
                            cache, row["ir_caminho"], "ir", row.get("ir_bytes") or "",
                            status, row.get("crs") or "", row.get("detalhe") or "",
                        )
                    continue
                if row.get("rgb_caminho"):
                    _store_sp_vistoria(cache, row["rgb_caminho"], "rgb", row.get("rgb_bytes") or "", "dentro", "", "")
                if row.get("ir_caminho"):
                    _store_sp_vistoria(cache, row["ir_caminho"], "ir", row.get("ir_bytes") or "", "dentro", "", "")
    except OSError as exc:
        log(f"AVISO: nao li {registry}: {exc}")
        return {}
    return cache


def _saved_sp_result(
    cache: dict[str, dict[str, str]],
    hit: RasterHit,
    force: bool,
    *,
    can_assign_epsg: bool = False,
) -> dict[str, str] | None:
    if force:
        return None
    saved = cache.get(hit.path.casefold())
    if not saved or saved.get("status") not in _SP_VISTORIA_STATUS:
        return None
    tamanho = saved.get("tamanho") or ""
    if tamanho and tamanho != str(hit.size):
        return None
    # Old cache: CRS like 32723 was "sem_localizacao". Recheck with articulacao.epsg.
    if (
        can_assign_epsg
        and saved.get("status") == "sem_localizacao"
        and "nao tem camada correspondente" in (saved.get("detalhe") or "").casefold()
    ):
        return None
    return saved


def sp_vistoria_has_new_files(
    hits: list[RasterHit],
    cache: dict[str, dict[str, str]],
    force: bool,
    *,
    can_assign_epsg: bool = False,
) -> bool:
    if force:
        return True
    return any(_saved_sp_result(cache, hit, False, can_assign_epsg=can_assign_epsg) is None for hit in hits)


def filter_rasters_to_sp(
    hits: list[RasterHit],
    mask: SpBounds | None,
    cache: dict[str, dict[str, str]],
    *,
    label: str,
    rgb: bool,
    force: bool,
    assigned_index: AssignedEpsgIndex | None = None,
) -> tuple[list[RasterHit], list[RasterHit], list[dict[str, str]]]:
    """Split rasters into inside-SP, outside-SP (still fusible later), and other drops.

    Results already stored in ``cache`` are reused. Only a new file, a file
    whose size changed, or ``force=True`` opens the TIFF header.
    """
    if not hits:
        return [], [], []
    lado = "rgb" if rgb else "ir"
    pending = [
        hit
        for hit in hits
        if _saved_sp_result(cache, hit, force, can_assign_epsg=assigned_index is not None) is None
    ]
    reused = len(hits) - len(pending)
    if pending:
        if mask is None:
            raise RuntimeError("A vistoria de SP precisa do limite, mas ele nao foi carregado.")
        log(
            f"Comparando {len(pending)} {label} novo(s) com o poligono de Sao Paulo. "
            f"{reused} ja estavam na vistoria salva."
        )
        started = time.time()
        # One TIFF at a time. Opening GeoTIFFs from several threads crashes GDAL on
        # Windows with 0xC0000005 (exit code -1073741819).
        for done, hit in enumerate(pending, 1):
            status, crs_text, detail, epsg_atribuido = _classify_against_sp(
                hit.path, mask, assigned_index=assigned_index
            )
            _store_sp_vistoria(
                cache, hit.path, lado, str(hit.size), status, crs_text, detail, epsg_atribuido
            )
            if done % 500 == 0 or done == len(pending):
                log(
                    f"  ... {done}/{len(pending)} {label} conferidos "
                    f"({fmt_duration(time.time() - started)})"
                )
    else:
        log(f"{label}: {reused} arquivo(s) reaproveitados da vistoria de SP. Nenhum cabecalho foi aberto.")

    kept: list[RasterHit] = []
    fora: list[RasterHit] = []
    dropped: list[dict[str, str]] = []
    outside = 0
    unknown = 0
    for hit in hits:
        saved = cache.get(hit.path.casefold()) or {"status": "sem_localizacao", "crs": "", "detalhe": "sem resultado"}
        status = saved.get("status") or "sem_localizacao"
        if status == "dentro":
            kept.append(hit)
            continue
        if status == "fora_de_sp":
            outside += 1
            fora.append(hit)
            continue
        unknown += 1
        row = _row_for_leftover(
            hit,
            kind="sem_localizacao",
            status=status,
            detail=saved.get("detalhe") or "",
            rgb=rgb,
            cenario="",
            tipo="",
        )
        row["crs"] = saved.get("crs") or ""
        dropped.append(row)
    log(
        f"  {label}: {len(kept)} dentro de SP, {outside} fora "
        f"(serao fundidos depois que SP acabar), "
        f"{unknown} sem localizacao (ignorados)."
    )
    return kept, fora, dropped


def _index_by_filename(
    hits: list[RasterHit], label: str
) -> tuple[dict[str, RasterHit], list[RasterHit]]:
    """Keep one file per filename. Same name in two subfolders: keep the larger."""
    chosen: dict[str, RasterHit] = {}
    duplicates: list[RasterHit] = []
    for hit in hits:
        previous = chosen.get(hit.name_key)
        if previous is None:
            chosen[hit.name_key] = hit
            continue
        keep, drop = (hit, previous) if hit.size > previous.size else (previous, hit)
        chosen[hit.name_key] = keep
        duplicates.append(drop)
        log(
            f"  DUPLICATA {label}: '{drop.filename}' aparece mais de uma vez. "
            f"Vou usar {keep.path} ({fmt_size(keep.size)}) e ignorar {drop.path} ({fmt_size(drop.size)})."
        )
    return chosen, duplicates


def _row_for_pair(
    rgb: RasterHit,
    ir: RasterHit,
    *,
    tile_id: str,
    cenario: int,
    tipo_rgb: str,
    tipo_ir: str,
    output_name: str,
    detail: str = "",
) -> dict[str, str]:
    row = blank_row()
    row["tipo_registro"] = "par"
    row["modo_pareamento"] = f"cenario_{cenario}"
    row["cenario"] = str(cenario)
    row["tipo_rgb"] = tipo_rgb
    row["tipo_ir"] = tipo_ir
    row["tile_key"] = tile_id
    row["rgb_nome"] = rgb.filename
    row["ir_nome"] = ir.filename
    row["rgb_caminho"] = rgb.path
    row["ir_caminho"] = ir.path
    row["saida_nome"] = output_name
    row["rgb_bytes"] = str(rgb.size)
    row["ir_bytes"] = str(ir.size)
    row["bandas_rgb"] = "1,2,3"
    row["banda_nir_ir"] = str(IR_NIR_BAND_INDEX)
    row["detalhe"] = detail
    return row


def _row_for_leftover(
    hit: RasterHit | None,
    *,
    kind: str,
    status: str,
    detail: str,
    rgb: bool,
    cenario: str = "5",
    tipo: str = "",
) -> dict[str, str]:
    row = blank_row()
    row["tipo_registro"] = kind
    row["status"] = status
    row["modo_pareamento"] = "nenhum"
    row["cenario"] = cenario
    row["detalhe"] = detail
    row["bandas_rgb"] = "1,2,3" if rgb else ""
    row["banda_nir_ir"] = "" if rgb else str(IR_NIR_BAND_INDEX)
    if rgb:
        row["tipo_rgb"] = tipo
    else:
        row["tipo_ir"] = tipo
    if hit is None:
        return row
    row["tile_key"] = hit.tile_key or Path(hit.filename).stem
    if rgb:
        row["rgb_nome"] = hit.filename
        row["rgb_caminho"] = hit.path
        row["rgb_bytes"] = str(hit.size)
    else:
        row["ir_nome"] = hit.filename
        row["ir_caminho"] = hit.path
        row["ir_bytes"] = str(hit.size)
    return row


def _pick_one_per_type(
    hits: list[RasterHit],
) -> tuple[dict[str, RasterHit], list[tuple[RasterHit, str]]]:
    """One file per product type (the largest). The others are anomalies."""
    grouped: dict[str, list[RasterHit]] = {}
    for hit in hits:
        grouped.setdefault(classify_product(hit.filename), []).append(hit)
    chosen: dict[str, RasterHit] = {}
    extras: list[tuple[RasterHit, str]] = []
    for tipo, group in grouped.items():
        group.sort(key=lambda item: (-item.size, item.filename.casefold()))
        chosen[tipo] = group[0]
        for extra in group[1:]:
            extras.append((extra, tipo))
    return chosen, extras


def _cascade_one_tile(
    tile_id: str,
    rgb_hits: list[RasterHit],
    ir_hits: list[RasterHit],
    pairing: Pairing,
    claimed_output: dict[str, str],
) -> None:
    """Apply cenarios 1-5 to every file that shares ``tile_id``."""
    rgb_by_type, rgb_extras = _pick_one_per_type(rgb_hits)
    ir_by_type, ir_extras = _pick_one_per_type(ir_hits)
    used_rgb: set[str] = set()
    used_ir: set[str] = set()

    def claim(rgb: RasterHit, ir: RasterHit, cenario: int, tipo_rgb: str, tipo_ir: str, detail: str) -> None:
        output_name = output_name_for_scenario(tile_id, cenario, tipo_rgb, tipo_ir)
        row = _row_for_pair(
            rgb,
            ir,
            tile_id=tile_id,
            cenario=cenario,
            tipo_rgb=tipo_rgb,
            tipo_ir=tipo_ir,
            output_name=output_name,
            detail=detail,
        )
        key = output_name.casefold()
        if key in claimed_output:
            row["tipo_registro"] = "conflito_saida"
            row["status"] = "conflito_saida"
            row["cenario"] = "5"
            row["saida_nome"] = ""
            row["saida_caminho"] = ""
            row["detalhe"] = (
                f"O nome de saida ja foi reservado por {claimed_output[key]}. "
                "Este par nao foi gravado."
            )
            pairing.leftovers.append(row)
            log(
                f"  CONFLITO {tile_id}: {output_name} ja reservado por {claimed_output[key]}."
            )
            return
        claimed_output[key] = f"RGB={rgb.filename} + IR={ir.filename}"
        pairing.pairs.append(row)
        used_rgb.add(tipo_rgb)
        used_ir.add(tipo_ir)
        if cenario in {3, 4}:
            log(
                f"  CENARIO {cenario} {tile_id}: "
                f"RGB {rgb.filename} ({tipo_rgb}) + IR {ir.filename} ({tipo_ir}) -> {output_name}"
            )

    if "RAW" in rgb_by_type and "RAW" in ir_by_type:
        claim(
            rgb_by_type["RAW"],
            ir_by_type["RAW"],
            1,
            "RAW",
            "RAW",
            "Cenario 1: puro do RGB com puro do IR.",
        )

    for tipo in sorted(set(rgb_by_type) & set(ir_by_type)):
        if tipo == "RAW" or tipo in used_rgb or tipo in used_ir:
            continue
        claim(
            rgb_by_type[tipo],
            ir_by_type[tipo],
            2,
            tipo,
            tipo,
            f"Cenario 2: mesma familia e versao ({tipo}) nos dois lados.",
        )

    raw_rgb_left = "RAW" in rgb_by_type and "RAW" not in used_rgb
    raw_ir_left = "RAW" in ir_by_type and "RAW" not in used_ir
    fam_rgb = sorted(tipo for tipo in rgb_by_type if tipo != "RAW" and tipo not in used_rgb)
    fam_ir = sorted(tipo for tipo in ir_by_type if tipo != "RAW" and tipo not in used_ir)

    if raw_rgb_left and fam_ir and not raw_ir_left:
        for tipo_ir in fam_ir:
            claim(
                rgb_by_type["RAW"],
                ir_by_type[tipo_ir],
                3,
                "RAW",
                tipo_ir,
                f"Cenario 3: RGB puro fechou par com IR {tipo_ir}, que nao tinha familia igual.",
            )
    elif raw_ir_left and fam_rgb and not raw_rgb_left:
        for tipo_rgb in fam_rgb:
            claim(
                rgb_by_type[tipo_rgb],
                ir_by_type["RAW"],
                3,
                tipo_rgb,
                "RAW",
                f"Cenario 3: IR puro fechou par com RGB {tipo_rgb}, que nao tinha familia igual.",
            )

    fam_rgb = sorted(tipo for tipo in rgb_by_type if tipo != "RAW" and tipo not in used_rgb)
    fam_ir = sorted(tipo for tipo in ir_by_type if tipo != "RAW" and tipo not in used_ir)
    for tipo_rgb, tipo_ir in zip(fam_rgb, fam_ir):
        claim(
            rgb_by_type[tipo_rgb],
            ir_by_type[tipo_ir],
            4,
            tipo_rgb,
            tipo_ir,
            f"Cenario 4: familias diferentes, RGB {tipo_rgb} com IR {tipo_ir}.",
        )

    def _leftover_side(by_type: dict[str, RasterHit], extras: list[tuple[RasterHit, str]], *, rgb: bool) -> None:
        used = used_rgb if rgb else used_ir
        kind = "rgb_sem_ir" if rgb else "ir_sem_rgb"
        lado = "RGB" if rgb else "IR"
        outro = "IR" if rgb else "RGB"
        for tipo, hit in sorted(by_type.items()):
            if tipo in used:
                continue
            pairing.leftovers.append(
                _row_for_leftover(
                    hit,
                    kind=kind,
                    status="sem_par",
                    detail=(
                        f"Cenario 5: {lado} tipo {tipo} do tile {tile_id} ficou sem par no {outro}. "
                        "Nenhum tif foi gerado."
                    ),
                    rgb=rgb,
                    cenario="5",
                    tipo=tipo,
                )
            )
        for hit, tipo in extras:
            pairing.leftovers.append(
                _row_for_leftover(
                    hit,
                    kind="anomalia",
                    status="anomalia",
                    detail=(
                        f"Cenario 5: outro arquivo {lado} do tipo {tipo} ja representa o tile {tile_id} "
                        f"(ficou o maior). '{hit.filename}' nao gerou tif."
                    ),
                    rgb=rgb,
                    cenario="5",
                    tipo=tipo,
                )
            )

    _leftover_side(rgb_by_type, rgb_extras, rgb=True)
    _leftover_side(ir_by_type, ir_extras, rgb=False)


def pair_rasters(rgb_hits: list[RasterHit], ir_hits: list[RasterHit]) -> Pairing:
    """Pair every consistent RGB/IR option of the same tile id.

    Cenarios 1 to 4 write a GeoTIFF. Cenario 5 only records the file.
    Counts are not fixed here: each row carries ``cenario`` and the summary
    adds them up.
    """
    rgb_by_name, rgb_dups = _index_by_filename(rgb_hits, "RGB")
    ir_by_name, ir_dups = _index_by_filename(ir_hits, "IR")
    pairing = Pairing()
    claimed_output: dict[str, str] = {}

    rgb_groups: dict[str, list[RasterHit]] = {}
    ir_groups: dict[str, list[RasterHit]] = {}
    rgb_nameless: list[RasterHit] = []
    ir_nameless: list[RasterHit] = []
    for hit in rgb_by_name.values():
        if hit.tile_key:
            rgb_groups.setdefault(hit.tile_key.casefold(), []).append(hit)
        else:
            rgb_nameless.append(hit)
    for hit in ir_by_name.values():
        if hit.tile_key:
            ir_groups.setdefault(hit.tile_key.casefold(), []).append(hit)
        else:
            ir_nameless.append(hit)

    display_id: dict[str, str] = {}
    for groups in (rgb_groups, ir_groups):
        for key, hits in groups.items():
            display_id.setdefault(key, hits[0].tile_key)

    for key in sorted(set(rgb_groups) | set(ir_groups)):
        _cascade_one_tile(
            display_id[key],
            rgb_groups.get(key, []),
            ir_groups.get(key, []),
            pairing,
            claimed_output,
        )

    for hit in rgb_nameless:
        pairing.leftovers.append(
            _row_for_leftover(
                hit,
                kind="anomalia",
                status="anomalia",
                detail="Cenario 5: o nome do RGB nao produziu um id de tile. Nenhum tif foi gerado.",
                rgb=True,
                cenario="5",
                tipo=classify_product(hit.filename),
            )
        )
    for hit in ir_nameless:
        pairing.leftovers.append(
            _row_for_leftover(
                hit,
                kind="anomalia",
                status="anomalia",
                detail="Cenario 5: o nome do IR nao produziu um id de tile. Nenhum tif foi gerado.",
                rgb=False,
                cenario="5",
                tipo=classify_product(hit.filename),
            )
        )
    for hit in rgb_dups:
        pairing.leftovers.append(
            _row_for_leftover(
                hit,
                kind="duplicata_ignorada",
                status="duplicata_ignorada",
                detail="Mesmo nome de arquivo RGB em outro caminho. Ficou o arquivo maior; este foi ignorado.",
                rgb=True,
                cenario="",
                tipo=classify_product(hit.filename),
            )
        )
    for hit in ir_dups:
        pairing.leftovers.append(
            _row_for_leftover(
                hit,
                kind="duplicata_ignorada",
                status="duplicata_ignorada",
                detail="Mesmo nome de arquivo IR em outro caminho. Ficou o arquivo maior; este foi ignorado.",
                rgb=False,
                cenario="",
                tipo=classify_product(hit.filename),
            )
        )
    pairing.pairs.sort(key=lambda row: (row["tile_key"].casefold(), row["cenario"], row["saida_nome"].casefold()))
    return pairing


def compose_note(row: dict[str, str], out_dir: Path) -> str:
    """One sentence describing which files were joined, or why nothing was written."""
    status = row["status"]
    rgb = row["rgb_caminho"] or row["rgb_nome"] or "(sem RGB)"
    ir = row["ir_caminho"] or row["ir_nome"] or "(sem IR)"
    out = row["saida_caminho"] or (str(out_dir / row["saida_nome"]) if row["saida_nome"] else "(sem saida)")
    band = row["banda_nir_ir"] or str(IR_NIR_BAND_INDEX)
    if status in {"ok", "pulado_ja_existe", "pendente"}:
        verb = {
            "ok": "Gravou agora",
            "pulado_ja_existe": "Ja existia, nao refez",
            "pendente": "Vai juntar",
        }[status]
        cenario = row.get("cenario") or "?"
        tipo_rgb = row.get("tipo_rgb") or "?"
        tipo_ir = row.get("tipo_ir") or "?"
        return (
            f"{verb} cenario {cenario}: RGB '{rgb}' ({tipo_rgb}) bandas 1-3 "
            f"+ IR '{ir}' ({tipo_ir}) banda {band} (NIR) -> '{out}'."
        )
    if status == "falha":
        return f"Falhou ao juntar RGB '{rgb}' com IR '{ir}' em '{out}'. {row['detalhe']}".strip()
    if status == "interrompido":
        return f"Interrompido no meio da juncao de RGB '{rgb}' com IR '{ir}'. Rode de novo para continuar."
    if status == "sem_par":
        return row["detalhe"] or f"Cenario 5, sem par: RGB '{rgb}' IR '{ir}'."
    if status == "anomalia":
        return row["detalhe"] or f"Cenario 5, anomalia: RGB '{rgb}' IR '{ir}'."
    if status == "duplicata_ignorada":
        return row["detalhe"]
    if status == "conflito_saida":
        return row["detalhe"]
    if status == "fora_de_sp":
        nome = row["rgb_nome"] or row["ir_nome"] or row["tile_key"]
        return f"Ignorado, fora de Sao Paulo: '{nome}'. {row['detalhe']}".strip()
    if status == "sem_localizacao":
        nome = row["rgb_nome"] or row["ir_nome"] or row["tile_key"]
        return f"Ignorado, sem localizacao: '{nome}'. {row['detalhe']}".strip()
    return row["detalhe"]


# -----------------------------------------------------------------------------
# Cobertura GPKG (same articulation pattern as transferir_laz_rgb_ir_v3_por_aoi.py).
# -----------------------------------------------------------------------------

def tile_id_candidates_from_saida(saida_nome: str, tile_key: str = "") -> list[str]:
    """Exact articulation-id candidates from an RGBNIR output filename.

    Only exact NOMENC matches are used (no dash-cascade). A shortened id would
    attach the wrong grid polygon to the cobertura feature.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def push(value: str) -> None:
        text = (value or "").strip()
        if not text:
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        ordered.append(text)

    push(tile_key)
    stem = Path(saida_nome).stem
    stem = re.sub(r"(?i)[-_]resampled$", "", stem)
    push(stem)

    cen = re.match(r"(?i)^(.+)-CEN[34]-.+$", stem)
    if cen:
        push(cen.group(1))

    cleaned = stem
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = re.sub(r"(?i)_(?:ORTM|ORTO)(?:_V\d+)?$", "", cleaned)
        cleaned = re.sub(r"(?i)_V\d+$", "", cleaned)
        cleaned = re.sub(r"(?i)_R\d+$", "", cleaned)
    push(cleaned)
    push(tile_key_from_filename(f"{cleaned}.tif"))
    return ordered


def _raster_bounds_polygon(path: Path):
    """Fallback geometry when the articulation has no match for this tile id."""
    import geopandas as gpd
    from shapely.geometry import box

    if rasterio is None:
        return None, None
    try:
        with rasterio.open(path) as src:
            if src.crs is None:
                return None, None
            geom = box(
                float(src.bounds.left),
                float(src.bounds.bottom),
                float(src.bounds.right),
                float(src.bounds.top),
            )
            gdf = gpd.GeoDataFrame({"geometry": [geom]}, crs=src.crs)
            return gdf.to_crs(epsg=4674).geometry.iloc[0], "EPSG:4674"
    except Exception as exc:  # noqa: BLE001
        log(f"AVISO: nao li o retangulo de {path.name} para a cobertura: {type(exc).__name__}: {exc}")
        return None, None


class RgbnirCobertura:
    """Incremental RGBNIR cobertura GPKG with atomic replace (never in-place append)."""

    def __init__(self, path: Path, artic_path: Path, artic_layer: str):
        import geopandas as gpd

        self.path = path
        self.artic_path = artic_path
        self.artic_layer = artic_layer
        self._gpd = gpd
        self._artic = None
        self._indexes: dict[str, dict[str, int]] | None = None
        self._gdf = None
        self._known: set[str] = set()
        self.enabled = True

    def _empty_frame(self, crs="EPSG:4674"):
        return self._gpd.GeoDataFrame(
            {col: [] for col in _COBERTURA_KEEP_COLS},
            geometry="geometry",
            crs=crs,
        )

    def _ensure_artic(self) -> bool:
        if self._artic is not None:
            return True
        if not self.artic_path.is_file():
            log(f"AVISO: articulacao nao encontrada ({self.artic_path}). Cobertura desligada.")
            self.enabled = False
            return False
        try:
            log(f"Lendo articulacao para cobertura: {self.artic_path} | layer={self.artic_layer}")
            artic = self._gpd.read_file(self.artic_path, layer=self.artic_layer)
            if artic.crs is None:
                artic = artic.set_crs(epsg=4674)
            elif artic.crs.to_epsg() != 4674:
                artic = artic.to_crs(epsg=4674)
            indexes: dict[str, dict[str, int]] = {col: {} for col in _AUTO_ID_COLUMNS}
            for idx, row in artic.iterrows():
                for col in _AUTO_ID_COLUMNS:
                    value = row.get(col)
                    if value is None or (isinstance(value, float) and value != value):
                        continue
                    text = str(value).strip()
                    if not text:
                        continue
                    bucket = indexes[col]
                    key = text.casefold()
                    if key not in bucket:
                        bucket[key] = idx
            self._artic = artic
            self._indexes = indexes
            log(
                f"  Articulacao: {len(artic)} feicoes | "
                f"ids 2K={len(indexes['NOMENC_2K'])} "
                f"5K={len(indexes['NOMENC_5K'])} "
                f"10K={len(indexes['NOMENC_10K'])}"
            )
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"AVISO: falha ao ler articulacao ({self.artic_path}): {type(exc).__name__}: {exc}")
            self.enabled = False
            return False

    def _lookup_artic_row(self, saida_nome: str, tile_key: str = ""):
        if not self._ensure_artic() or self._artic is None or self._indexes is None:
            return None
        # Prefer the id column each laser layer uses (same map as the transfer script).
        preferred_cols = list(dict.fromkeys([*_ID_COLUMN_BY_LAYER.values(), *_AUTO_ID_COLUMNS]))
        for candidate in tile_id_candidates_from_saida(saida_nome, tile_key):
            key = candidate.casefold()
            for col in preferred_cols:
                idx = self._indexes[col].get(key)
                if idx is None:
                    continue
                return self._artic.loc[idx]
        return None

    def _attrs_from_registry(self, meta: dict[str, str] | None, saida_nome: str) -> dict:
        meta = meta or {}
        alinhamento = infer_alinhamento_from_saida(saida_nome, meta.get("detalhe") or "")
        tipo_juncao = tipo_juncao_for_row(meta, alinhamento=alinhamento) if meta else (
            _TIPO_JUNCAO_RESAMPLED if alinhamento == _TIPO_JUNCAO_RESAMPLED else _TIPO_JUNCAO_DIRETOS
        )
        return {
            "cenario": meta.get("cenario") or "",
            "modo_pareamento": meta.get("modo_pareamento") or "",
            "tipo_registro": meta.get("tipo_registro") or ("par" if saida_nome else ""),
            "status": meta.get("status") or ("ok" if saida_nome else ""),
            "tipo_juncao": tipo_juncao,
            "alinhamento": alinhamento if saida_nome else _ALINHAMENTO_NA,
        }

    def _feature_from_saida(
        self,
        saida_path: Path,
        tile_key: str = "",
        meta: dict[str, str] | None = None,
    ) -> dict | None:
        if not saida_path.is_file() or saida_path.stat().st_size <= 0:
            return None
        saida_nome = saida_path.name
        attrs = self._attrs_from_registry(meta, saida_nome)
        row = self._lookup_artic_row(saida_nome, tile_key or (meta or {}).get("tile_key") or "")
        if row is not None:
            return {
                "layer": row.get("layer"),
                "NOMENC_2K": row.get("NOMENC_2K"),
                "NOMENC_5K": row.get("NOMENC_5K"),
                "NOMENC_10K": row.get("NOMENC_10K"),
                "has_rgbnir": 1,
                "saida_nome": saida_nome,
                "tile_key": tile_key
                or (meta or {}).get("tile_key")
                or tile_id_candidates_from_saida(saida_nome)[0],
                **attrs,
                "geometry": row.geometry,
            }
        geom, _crs = _raster_bounds_polygon(saida_path)
        if geom is None:
            log(f"AVISO: cobertura sem geometria para {saida_nome} (sem match na articulacao e sem CRS no TIFF).")
            return None
        return {
            "layer": None,
            "NOMENC_2K": None,
            "NOMENC_5K": None,
            "NOMENC_10K": None,
            "has_rgbnir": 1,
            "saida_nome": saida_nome,
            "tile_key": tile_key
            or (meta or {}).get("tile_key")
            or tile_id_candidates_from_saida(saida_nome)[0],
            **attrs,
            "geometry": geom,
        }

    def _ensure_schema_columns(self) -> bool:
        """Add missing cobertura columns once; caller must save if this returns True."""
        if self._gdf is None:
            self._gdf = self._empty_frame()
            return True
        changed = False
        for col in _COBERTURA_KEEP_COLS:
            if col == "geometry":
                continue
            if col not in self._gdf.columns:
                if col == "has_rgbnir":
                    self._gdf[col] = 1
                else:
                    self._gdf[col] = None
                changed = True
        return changed

    def _save_atomic(self) -> bool:
        if self._gdf is None:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.stem + ".writing.gpkg")
        sidecars = [
            tmp,
            Path(str(tmp) + "-wal"),
            Path(str(tmp) + "-shm"),
            Path(str(tmp) + ".wal"),
            Path(str(tmp) + ".shm"),
        ]
        try:
            for side in sidecars:
                if side.exists():
                    side.unlink()
            frame = self._gdf.copy()
            keep = [c for c in _COBERTURA_KEEP_COLS if c in frame.columns]
            frame = frame[keep]
            frame.to_file(tmp, layer=COBERTURA_LAYER, driver="GPKG")
            os.replace(tmp, self.path)
            for side in sidecars[1:]:
                if side.exists():
                    side.unlink()
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"AVISO: nao consegui gravar a cobertura {self.path}: {type(exc).__name__}: {exc}")
            for side in sidecars:
                try:
                    if side.exists():
                        side.unlink()
                except OSError:
                    pass
            return False

    def load_or_bootstrap(self, out_dir: Path) -> None:
        """Load existing GPKG, or build it once from every finished TIFF in out_dir."""
        if not self.enabled:
            return
        if self.path.is_file():
            try:
                existing = self._gpd.read_file(self.path, layer=COBERTURA_LAYER)
                if existing.crs is None:
                    existing = existing.set_crs(epsg=4674)
                elif existing.crs.to_epsg() != 4674:
                    existing = existing.to_crs(epsg=4674)
                for col in _COBERTURA_KEEP_COLS:
                    if col == "geometry":
                        continue
                    if col not in existing.columns:
                        existing[col] = 1 if col == "has_rgbnir" else None
                keep = [c for c in _COBERTURA_KEEP_COLS if c in existing.columns]
                self._gdf = existing[keep].copy()
                self._known = {
                    str(name).casefold()
                    for name in self._gdf["saida_nome"].fillna("").astype(str)
                    if str(name).strip()
                }
                migrated = self._ensure_schema_columns()
                # Fill new join fields for rows that already exist (once).
                filled = 0
                if "tipo_juncao" in self._gdf.columns:
                    for idx, feat in self._gdf.iterrows():
                        nome = str(feat.get("saida_nome") or "")
                        if not nome or nome.lower() == "nan":
                            continue
                        current = feat.get("tipo_juncao")
                        if current is not None and str(current).strip() and str(current).lower() != "nan":
                            continue
                        alinhamento = infer_alinhamento_from_saida(nome)
                        self._gdf.at[idx, "alinhamento"] = alinhamento
                        self._gdf.at[idx, "tipo_juncao"] = (
                            _TIPO_JUNCAO_RESAMPLED
                            if alinhamento == _TIPO_JUNCAO_RESAMPLED
                            else _TIPO_JUNCAO_DIRETOS
                        )
                        st = feat.get("status")
                        if st is None or str(st).strip() in {"", "nan"}:
                            self._gdf.at[idx, "status"] = "pulado_ja_existe"
                        tr = feat.get("tipo_registro")
                        if tr is None or str(tr).strip() in {"", "nan"}:
                            self._gdf.at[idx, "tipo_registro"] = "par"
                        filled += 1
                if migrated or filled:
                    if self._save_atomic():
                        log(
                            f"Cobertura existente atualizada uma vez "
                            f"(colunas novas / {filled} feicoes com tipo_juncao): {self.path}"
                        )
                    else:
                        log("AVISO: nao gravei a migracao da cobertura; tentarei de novo depois.")
                else:
                    log(f"Cobertura existente: {len(self._gdf)} feicao(oes) em {self.path}")
                return
            except Exception as exc:  # noqa: BLE001
                log(
                    f"AVISO: cobertura existente ilegivel ({self.path}): {type(exc).__name__}: {exc}. "
                    "Vou reconstruir a partir dos TIFF em disco."
                )

        if not self._ensure_artic():
            return
        log(f"Cobertura ausente. Vou inventariar os TIFF ja prontos em {out_dir} ...")
        features: list[dict] = []
        listed = 0
        for path in sorted(out_dir.iterdir() if out_dir.is_dir() else []):
            if not path.is_file():
                continue
            lower = path.name.lower()
            if not lower.endswith(RASTER_SUFFIXES):
                continue
            if lower.endswith(TMP_SUFFIX):
                continue
            listed += 1
            feat = self._feature_from_saida(path)
            if feat is not None:
                features.append(feat)
            if listed % 500 == 0:
                log(f"  ... cobertura bootstrap: {listed} TIFF lidos, {len(features)} com geometria")
        if features:
            self._gdf = self._gpd.GeoDataFrame(features, geometry="geometry", crs="EPSG:4674")
            self._known = {str(f["saida_nome"]).casefold() for f in features}
        else:
            self._gdf = self._empty_frame()
            self._known = set()
        if self._save_atomic():
            log(f"Cobertura inicial gravada: {len(self._gdf)} feicao(oes) -> {self.path}")
        else:
            log("AVISO: bootstrap da cobertura falhou na gravacao. Continuarei tentando tile a tile.")

    def sync_missing_from_rows(self, rows: list[dict[str, str]], out_dir: Path) -> int:
        """Append finished pairs that are on disk but still missing from the GPKG (one save)."""
        if not self.enabled:
            return 0
        self._ensure_schema_columns()
        added = 0
        pending_feats: list[dict] = []
        for row in rows:
            if row.get("tipo_registro") != "par":
                continue
            if row.get("status") not in {"ok", "pulado_ja_existe"}:
                continue
            nome = (row.get("saida_nome") or "").strip()
            if not nome or nome.casefold() in self._known:
                continue
            caminho = (row.get("saida_caminho") or "").strip()
            path = Path(caminho) if caminho else (out_dir / nome)
            if not path.is_file():
                found = finished_output(out_dir, nome)
                if found is None:
                    continue
                path = found
            feat = self._feature_from_saida(path, tile_key=row.get("tile_key") or "", meta=row)
            if feat is None:
                continue
            pending_feats.append(feat)
            self._known.add(path.name.casefold())
            added += 1
        if not pending_feats:
            return 0
        import pandas as pd

        target_crs = self._gdf.crs if self._gdf is not None else "EPSG:4674"
        novo = self._gpd.GeoDataFrame(pending_feats, geometry="geometry", crs="EPSG:4674")
        if target_crs is not None and novo.crs != target_crs:
            novo = novo.to_crs(target_crs)
        if self._gdf is None or not len(self._gdf):
            self._gdf = novo
        else:
            juntos = pd.concat([self._gdf, novo], ignore_index=True)
            self._gdf = self._gpd.GeoDataFrame(juntos, geometry="geometry", crs=target_crs)
        if self._save_atomic():
            log(f"Cobertura: acrescentei {added} feicao(oes) que ja estavam em disco e faltavam no GPKG.")
            return added
        log("AVISO: falha ao gravar o sync da cobertura.")
        return 0

    def add_saida(
        self,
        saida_path: Path | str,
        tile_key: str = "",
        meta: dict[str, str] | None = None,
    ) -> bool:
        """Append one finished output. No-op if already present. Atomic rewrite."""
        if not self.enabled:
            return False
        path = Path(saida_path)
        key = path.name.casefold()
        if key in self._known:
            return False
        if self._gdf is None:
            self._gdf = self._empty_frame()
        else:
            self._ensure_schema_columns()
        feat = self._feature_from_saida(path, tile_key=tile_key, meta=meta)
        if feat is None:
            return False
        import pandas as pd

        target_crs = self._gdf.crs or "EPSG:4674"
        novo = self._gpd.GeoDataFrame([feat], geometry="geometry", crs="EPSG:4674")
        if target_crs is not None and novo.crs != target_crs:
            novo = novo.to_crs(target_crs)
        if len(self._gdf):
            juntos = pd.concat([self._gdf, novo], ignore_index=True)
            self._gdf = self._gpd.GeoDataFrame(juntos, geometry="geometry", crs=target_crs)
        else:
            self._gdf = novo
        if self._save_atomic():
            self._known.add(key)
            return True
        # roll back the in-memory row if disk write failed
        self._gdf = self._gdf.iloc[:-1].copy() if len(self._gdf) else self._empty_frame(target_crs)
        return False

    def add_from_registry_row(self, row: dict[str, str], out_dir: Path) -> bool:
        nome = (row.get("saida_nome") or "").strip()
        if not nome:
            return False
        caminho = (row.get("saida_caminho") or "").strip()
        path = Path(caminho) if caminho else (out_dir / nome)
        if not path.is_file():
            found = finished_output(out_dir, nome)
            if found is None:
                return False
            path = found
        return self.add_saida(path, tile_key=row.get("tile_key") or "", meta=row)


# -----------------------------------------------------------------------------
# CSV registry. Disk is the source of truth for "already done"; the CSV is the log.
# -----------------------------------------------------------------------------

_STATUS_ORDER = {
    "falha": 0,
    "interrompido": 1,
    "pendente": 2,
    "pulado_ja_existe": 3,
    "ok": 4,
    "conflito_saida": 5,
    "sem_par": 6,
    "anomalia": 7,
    "duplicata_ignorada": 8,
    "fora_de_sp": 9,
    "sem_localizacao": 10,
}

_PRESERVE_FROM_PREVIOUS = (
    "inicio_utc",
    "fim_utc",
    "duracao_segundos",
    "run_id_que_gravou",
    "largura_px",
    "altura_px",
    "crs",
    "saida_bytes",
    "ultimo_erro",
)


def write_csv_atomic(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=fieldnames,
                extrasaction="ignore",
                lineterminator="\r\n",
            )
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in fieldnames})
        os.replace(tmp, path)
        return True
    except OSError as exc:
        log(f"AVISO: nao consegui atualizar {path}: {exc}")
        log("Se o CSV estiver aberto no Excel, feche-o. A fusao continua; a planilha atualiza na proxima tentativa.")
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def write_dictionary(path: Path) -> None:
    rows: list[dict[str, str]] = []
    for name, desc, example in COLUMNS:
        rows.append(
            {
                "topico": "coluna",
                "nome": name,
                "descricao": desc,
                "exemplo": example,
            }
        )
    for code, desc in STATUS_HELP:
        rows.append(
            {
                "topico": "valor_de_status",
                "nome": code,
                "descricao": desc,
                "exemplo": code,
            }
        )
    for code, desc in (
        ("cenario_1", "Puro com puro. Saida: ID.tif. Se a grade diferir: ID-resampled.tif."),
        ("cenario_2", "Mesma familia e versao. Saida: ID_ORTO.tif ou ID_ORTM_V1.tif. Se a grade diferir, o nome ganha -resampled."),
        ("cenario_3", "Puro de um lado com familia do outro. Saida: ID-CEN3-RAW-ORTO.tif. Se a grade diferir, o nome ganha -resampled."),
        ("cenario_4", "Familias diferentes, sem puro sobrando. Saida: ID-CEN4-ORTM-ORTO.tif. Se a grade diferir, o nome ganha -resampled."),
        ("nenhum", "Cenario 5, duplicata ou conflito. Nao houve GeoTIFF para esta linha."),
    ):
        rows.append(
            {
                "topico": "valor_de_modo_pareamento",
                "nome": code,
                "descricao": desc,
                "exemplo": code,
            }
        )
    rows.append(
        {
            "topico": "receita_das_bandas",
            "nome": "RGBNIR",
            "descricao": (
                "Banda 1 = Red (RGB banda 1). Banda 2 = Green (RGB banda 2). "
                "Banda 3 = Blue (RGB banda 3). Banda 4 = NIR (IR banda 1). "
                "A banda 4 NAO e alfa, para o QGIS nao aplicar transparencia."
            ),
            "exemplo": "4 bandas",
        }
    )
    write_csv_atomic(path, ["topico", "nome", "descricao", "exemplo"], rows)


def load_previous_registry(path: Path) -> dict[str, dict[str, str]]:
    """Previous rows keyed by output filename, so a new run keeps 'when it was written'."""
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            indexed: dict[str, dict[str, str]] = {}
            for row in reader:
                name = (row.get("saida_nome") or "").strip()
                if name:
                    indexed[name.casefold()] = row
            return indexed
    except OSError as exc:
        log(f"AVISO: nao consegui ler o registro anterior {path}: {exc}")
        return {}


def restore_previous_fields(row: dict[str, str], previous: dict[str, dict[str, str]]) -> None:
    old = previous.get(row["saida_nome"].casefold())
    if not old:
        return
    for key in _PRESERVE_FROM_PREVIOUS:
        if not row.get(key) and old.get(key):
            row[key] = old[key]


def sort_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(
        rows,
        key=lambda row: (
            _STATUS_ORDER.get(row.get("status", ""), 99),
            row.get("tile_key", "").casefold(),
            row.get("rgb_nome", "").casefold(),
            row.get("ir_nome", "").casefold(),
        ),
    )


def summarize(rows: list[dict[str, str]], *, rgb_dir: Path, ir_dir: Path, out_dir: Path, run_id: str) -> list[dict[str, str]]:
    pairs = [row for row in rows if row["tipo_registro"] == "par"]
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1

    ready = sum(1 for row in pairs if row["status"] in {"ok", "pulado_ja_existe"})
    missing = sum(1 for row in pairs if row["status"] in {"pendente", "falha", "interrompido"})
    written_now = by_status.get("ok", 0)
    failed_now = by_status.get("falha", 0)
    total_pairs = len(pairs)
    percent = (100.0 * ready / total_pairs) if total_pairs else 100.0

    def metric(name: str, value: object, meaning: str) -> dict[str, str]:
        return {"metrica": name, "valor": str(value), "o_que_significa": meaning}

    return [
        metric(
            "faltam_gravar",
            missing,
            "Pares RGB+IR que AINDA nao tem GeoTIFF final. Rodar o .bat de novo continua so por estes.",
        ),
        metric(
            "prontos_no_disco",
            ready,
            "Pares cujo arquivo final ja esta na pasta de saida (gravados agora ou antes).",
        ),
        metric(
            "percentual_pronto",
            f"{percent:.2f}",
            "Percentual dos pares que ja tem arquivo final. 100 significa que nao falta nenhum par.",
        ),
        metric("pares_encontrados", total_pairs, "Pares dos cenarios 1 a 4. Cada um gera um GeoTIFF."),
        metric(
            "cenario_1_puro_puro",
            sum(1 for row in pairs if row.get("cenario") == "1"),
            "Cenario 1. Puro do RGB com puro do IR. Saida ID.tif, ou ID-resampled.tif se a grade diferir.",
        ),
        metric(
            "cenario_2_mesma_familia",
            sum(1 for row in pairs if row.get("cenario") == "2"),
            "Cenario 2. Mesma familia e versao nos dois lados. Saida ID_ORTO.tif ou ID_ORTM_V1.tif. Se a grade diferir, o nome ganha -resampled.",
        ),
        metric(
            "cenario_3_puro_com_familia",
            sum(1 for row in pairs if row.get("cenario") == "3"),
            "Cenario 3. Puro de um lado fechou com uma familia do outro. Saida ID-CEN3-TIPO_RGB-TIPO_IR.tif. Se a grade diferir, o nome ganha -resampled.",
        ),
        metric(
            "cenario_4_familias_diferentes",
            sum(1 for row in pairs if row.get("cenario") == "4"),
            "Cenario 4. Familias diferentes, sem puro sobrando. Saida ID-CEN4-TIPO_RGB-TIPO_IR.tif. Se a grade diferir, o nome ganha -resampled.",
        ),
        metric(
            "cenario_5_sem_tif",
            sum(1 for row in rows if row.get("cenario") == "5"),
            "Cenario 5. Arquivos que nao formaram par. Nenhum tif foi gerado para eles.",
        ),
        metric(
            "fora_de_sp",
            sum(1 for row in rows if row.get("status") == "fora_de_sp"),
            "Rasters cujo retangulo nao intersecta Sao Paulo. Foram ignorados antes do pareamento.",
        ),
        metric(
            "sem_localizacao",
            sum(1 for row in rows if row.get("status") == "sem_localizacao"),
            "Rasters sem CRS ou sem retangulo legivel. Foram ignorados porque nao deu para compara-los com SP.",
        ),
        metric(
            "gravados_nesta_execucao",
            written_now,
            "Arquivos de 4 bandas escritos do zero nesta execucao (status ok).",
        ),
        metric(
            "pulados_ja_existiam",
            by_status.get("pulado_ja_existe", 0),
            "Arquivos que ja estavam na pasta de saida e foram deixados como estavam.",
        ),
        metric(
            "falhas_nesta_execucao",
            failed_now,
            "Tentativas que falharam nesta execucao. Entram de novo em faltam_gravar.",
        ),
        metric(
            "rgb_sem_ir",
            sum(1 for row in rows if row["tipo_registro"] == "rgb_sem_ir"),
            "Arquivos em E:\\RGB sem IR correspondente. Na proxima execucao eles entram se o IR aparecer.",
        ),
        metric(
            "ir_sem_rgb",
            sum(1 for row in rows if row["tipo_registro"] == "ir_sem_rgb"),
            "Arquivos em F:\\IR sem RGB correspondente. Na proxima execucao eles entram se o RGB aparecer.",
        ),
        metric(
            "anomalias",
            sum(1 for row in rows if row.get("status") == "anomalia"),
            "Cenario 5. Tipo repetido no mesmo tile, ou nome sem id. Nao geraram tif.",
        ),
        metric(
            "duplicatas_ignoradas",
            by_status.get("duplicata_ignorada", 0),
            "Arquivos de mesmo nome repetidos em subpastas. O maior foi o escolhido.",
        ),
        metric(
            "conflitos_de_nome_de_saida",
            by_status.get("conflito_saida", 0),
            "Pares que produziriam o mesmo nome de saida. Nao foram gravados; olhe o registro.",
        ),
        metric("arquivos_rgb_lidos", sum(1 for row in rows if row["rgb_caminho"]), "Linhas do registro que apontam um RGB."),
        metric("arquivos_ir_lidos", sum(1 for row in rows if row["ir_caminho"]), "Linhas do registro que apontam um IR."),
        metric("run_id", run_id, "Identificador desta execucao (UTC)."),
        metric("pasta_rgb", str(rgb_dir), "Pasta de origem RGB. Nao e modificada."),
        metric("pasta_ir", str(ir_dir), "Pasta de origem IR. Nao e modificada."),
        metric("pasta_saida", str(out_dir), "Pasta onde os GeoTIFF de 4 bandas e estes CSV sao gravados."),
        metric("registro_detalhado", str(out_dir / REGISTRY_NAME), "Uma linha por par ou arquivo sem par, com os caminhos juntados."),
        metric("dicionario", str(out_dir / DICTIONARY_NAME), "Explicacao de cada coluna e de cada valor de status."),
        metric(
            "cobertura_gpkg",
            str(out_dir / COBERTURA_NAME),
            "GeoPackage com os grids da articulacao (ou retangulo do TIFF) ja fundidos em RGBNIR.",
        ),
        metric(
            "receita",
            "banda1=Red(RGB1) banda2=Green(RGB2) banda3=Blue(RGB3) banda4=NIR(IR1)",
            "Como as bandas sao empilhadas. A banda 4 nao e canal alfa.",
        ),
        metric("script", f"build_rgbnir_from_rgb_ir.py {VERSION}", "Versao do script que escreveu este resumo."),
        metric("atualizado_utc", utc_now(), "Ultima vez que este resumo foi reescrito."),
    ]


def publish(
    rows: list[dict[str, str]],
    *,
    out_dir: Path,
    rgb_dir: Path,
    ir_dir: Path,
    run_id: str,
) -> None:
    """Rewrite summary + registry. Called after every tile so a crash keeps the log."""
    stamped = utc_now()
    for row in rows:
        row["run_id_desta_execucao"] = run_id
        row["atualizado_utc"] = stamped
        if not row["notas"]:
            row["notas"] = compose_note(row, out_dir)
    ordered = sort_rows(rows)
    write_csv_atomic(out_dir / REGISTRY_NAME, COLUMN_NAMES, ordered)
    write_csv_atomic(
        out_dir / SUMMARY_NAME,
        ["metrica", "valor", "o_que_significa"],
        summarize(ordered, rgb_dir=rgb_dir, ir_dir=ir_dir, out_dir=out_dir, run_id=run_id),
    )


def output_is_done(path: Path) -> bool:
    """A final name exists only after a successful rename, so presence means done."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def cleanup_stale_tmp(folder: Path) -> int:
    removed = 0
    if not folder.is_dir():
        return 0
    for path in folder.glob(f"*{TMP_SUFFIX}"):
        try:
            path.unlink()
            removed += 1
            log(f"Removi parcial interrompido: {path.name}")
        except OSError as exc:
            log(f"AVISO: nao consegui remover {path}: {exc}")
    return removed


def verify_existing_geotiff(path: Path, nir_band: int) -> tuple[bool, str]:
    """Open an existing output and confirm it is a 4-band GeoTIFF."""
    if rasterio is None:
        return False, "rasterio nao esta instalado"
    try:
        with rasterio.open(path) as src:
            if src.count < 4:
                return False, f"tem {src.count} bandas, esperava 4"
            if src.width <= 0 or src.height <= 0:
                return False, "dimensao vazia"
            return True, f"{src.width}x{src.height} crs={src.crs} bandas={src.count} nir_esperada={nir_band}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


# -----------------------------------------------------------------------------
# Fuse one pair. Writes *.tmp.tif and renames only when the file is complete.
# -----------------------------------------------------------------------------

def _approx_equal_transform(left, right, tol: float = 1e-6) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(tuple(left)[:6], tuple(right)[:6]))


def probe_alinhamento(rgb_path: str, ir_path: str) -> str:
    """Header-only check: ``direto``, ``resampled`` or ``desconhecido``.

    Same rule as ``fuse_one`` (shape + CRS + transform), without writing anything.
    """
    if rasterio is None:
        return "desconhecido"
    try:
        with rasterio.open(rgb_path) as rgb_src, rasterio.open(ir_path) as ir_src:
            if rgb_src.crs is None or ir_src.crs is None:
                return "desconhecido"
            same_shape = (rgb_src.width, rgb_src.height) == (ir_src.width, ir_src.height)
            same_grid = (
                same_shape
                and rgb_src.crs == ir_src.crs
                and _approx_equal_transform(rgb_src.transform, ir_src.transform)
            )
            return _TIPO_JUNCAO_DIRETOS if same_grid else _TIPO_JUNCAO_RESAMPLED
    except Exception:  # noqa: BLE001
        return "desconhecido"


def infer_alinhamento_from_saida(saida_nome: str, detalhe: str = "") -> str:
    name = (saida_nome or "").casefold()
    note = (detalhe or "").casefold()
    if "resampled" in name:
        return _TIPO_JUNCAO_RESAMPLED
    if "alinhado na grade" in note or "nearest neighbor" in note:
        return _TIPO_JUNCAO_RESAMPLED
    if "ja coincidia" in note or "sem reamostrar" in note:
        return _TIPO_JUNCAO_DIRETOS
    if saida_nome:
        return _TIPO_JUNCAO_DIRETOS
    return _ALINHAMENTO_NA


def tipo_juncao_for_row(row: dict[str, str], *, alinhamento: str | None = None) -> str:
    """Single cobertura label covering pairing outcome + alignment + errors."""
    status = (row.get("status") or "").strip()
    tipo = (row.get("tipo_registro") or "").strip()
    if str(row.get("_fora_de_sp") or "").strip() in {"1", "true", "True"}:
        return "fora_de_sp"
    if status == "fora_de_sp" or tipo == "fora_de_sp":
        return "fora_de_sp"
    if status == "sem_localizacao" or tipo == "sem_localizacao":
        return "sem_localizacao"
    if tipo == "rgb_sem_ir":
        return "rgb_sem_ir"
    if tipo == "ir_sem_rgb":
        return "ir_sem_rgb"
    if tipo == "anomalia" or status == "anomalia":
        return "anomalia"
    if tipo == "duplicata_ignorada" or status == "duplicata_ignorada":
        return "duplicata_ignorada"
    if tipo == "conflito_saida" or status == "conflito_saida":
        return "conflito_saida"
    if status == "falha":
        return "falha"
    if status == "interrompido":
        return "interrompido"
    if tipo == "par" or row.get("cenario") in {"1", "2", "3", "4"}:
        saida = row.get("saida_nome") or ""
        align = alinhamento or infer_alinhamento_from_saida(saida, row.get("detalhe") or "")
        needs_proj = PROJETION_ASSIGNED_SUFFIX.casefold() in saida.casefold() or bool(
            str(row.get("_projetion_assigned") or "").strip() in {"1", "true", "True"}
            or row.get("epsg_atribuido")
        )
        if needs_proj and align == _TIPO_JUNCAO_RESAMPLED:
            return "resampled_projetion_assigned"
        if needs_proj:
            return "projetion_assigned"
        if align == _TIPO_JUNCAO_RESAMPLED:
            return _TIPO_JUNCAO_RESAMPLED
        if align == _TIPO_JUNCAO_DIRETOS:
            return _TIPO_JUNCAO_DIRETOS
        return "par_pendente"
    if row.get("cenario") == "5":
        return "sem_par"
    return status or tipo or "desconhecido"


_GRADE_VISTORIA_COLUMNS = [
    "chave",
    "rgb_caminho",
    "ir_caminho",
    "rgb_bytes",
    "ir_bytes",
    "alinhamento",
    "saida_nome",
    "atualizado_utc",
]
_GRADE_OK = {_TIPO_JUNCAO_DIRETOS, _TIPO_JUNCAO_RESAMPLED}


def grade_cache_key(rgb_path: str, ir_path: str) -> str:
    return f"{(rgb_path or '').casefold()}|{(ir_path or '').casefold()}"


def load_grade_vistoria(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            cache: dict[str, dict[str, str]] = {}
            for row in csv.DictReader(handle):
                chave = (row.get("chave") or "").strip()
                align = (row.get("alinhamento") or "").strip()
                if not chave or align not in _GRADE_OK:
                    continue
                cache[chave] = row
            return cache
    except OSError as exc:
        log(f"AVISO: nao li a vistoria de grade {path}: {exc}")
        return {}


def save_grade_vistoria(path: Path, cache: dict[str, dict[str, str]]) -> None:
    rows = sorted(cache.values(), key=lambda row: (row.get("chave") or "").casefold())
    if write_csv_atomic(path, _GRADE_VISTORIA_COLUMNS, rows):
        log(f"Vistoria de grade salva: {path} ({len(rows)} par(es)). Proximas execucoes reaproveitam.")


def _grade_cache_valid(entry: dict[str, str], row: dict[str, str]) -> bool:
    if (entry.get("alinhamento") or "") not in _GRADE_OK:
        return False
    rgb_b = str(_to_int_bytes(row.get("rgb_bytes")))
    ir_b = str(_to_int_bytes(row.get("ir_bytes")))
    return (entry.get("rgb_bytes") or "0") == rgb_b and (entry.get("ir_bytes") or "0") == ir_b


def store_grade_result(
    cache: dict[str, dict[str, str]],
    row: dict[str, str],
    alinhamento: str,
    *,
    saida_nome: str = "",
) -> None:
    if alinhamento not in _GRADE_OK:
        return
    rgb = row.get("rgb_caminho") or ""
    ir = row.get("ir_caminho") or ""
    if not rgb or not ir:
        return
    cache[grade_cache_key(rgb, ir)] = {
        "chave": grade_cache_key(rgb, ir),
        "rgb_caminho": rgb,
        "ir_caminho": ir,
        "rgb_bytes": str(_to_int_bytes(row.get("rgb_bytes"))),
        "ir_bytes": str(_to_int_bytes(row.get("ir_bytes"))),
        "alinhamento": alinhamento,
        "saida_nome": saida_nome or (row.get("saida_nome") or ""),
        "atualizado_utc": utc_now(),
    }


def seed_grade_cache_from_finished(
    rows: list[dict[str, str]],
    cache: dict[str, dict[str, str]],
) -> int:
    """Fill cache from finished outputs (filename) without opening RGB/IR headers."""
    added = 0
    for row in rows:
        if row.get("tipo_registro") != "par":
            continue
        if row.get("status") not in {"ok", "pulado_ja_existe"}:
            continue
        align = infer_alinhamento_from_saida(row.get("saida_nome") or "", row.get("detalhe") or "")
        if align not in _GRADE_OK:
            continue
        key = grade_cache_key(row.get("rgb_caminho") or "", row.get("ir_caminho") or "")
        if not key or key == "|":
            continue
        if key in cache and _grade_cache_valid(cache[key], row):
            continue
        store_grade_result(cache, row, align, saida_nome=row.get("saida_nome") or "")
        added += 1
    return added


def prioritize_queue_direto_then_resampled(
    queue: list[dict[str, str]],
    *,
    cache: dict[str, dict[str, str]],
    cache_path: Path,
    force: bool = False,
) -> list[dict[str, str]]:
    """Order queue as direct-copy first, then resampled.

    Grid classification is cached in ``rgbnir_grade_vistoria.csv``. Only pairs
    missing from the cache (or with changed source sizes) are probed again.
    """
    if not queue:
        return queue
    direto: list[dict[str, str]] = []
    resampled: list[dict[str, str]] = []
    desconhecido: list[dict[str, str]] = []
    projetion: list[dict[str, str]] = []
    reused = 0
    probed = 0
    started = time.time()
    log(
        "Ordenando a fila: SP (direto -> resampled -> projetion_assigned) "
        "e so depois fora_de_sp. Vistoria de grade so para pares novos/ausentes no cache."
    )
    fora_direto: list[dict[str, str]] = []
    fora_resampled: list[dict[str, str]] = []
    fora_desconhecido: list[dict[str, str]] = []
    fora_projetion: list[dict[str, str]] = []
    for index, row in enumerate(queue, 1):
        key = grade_cache_key(row.get("rgb_caminho") or "", row.get("ir_caminho") or "")
        entry = cache.get(key)
        if not force and entry and _grade_cache_valid(entry, row):
            align = entry["alinhamento"]
            reused += 1
        else:
            align = probe_alinhamento(row.get("rgb_caminho") or "", row.get("ir_caminho") or "")
            probed += 1
            if align in _GRADE_OK:
                store_grade_result(cache, row, align)
        needs_proj = bool(
            str(row.get("_projetion_assigned") or "").strip() in {"1", "true", "True"}
            or row.get("epsg_atribuido")
        )
        is_fora = str(row.get("_fora_de_sp") or "").strip() in {"1", "true", "True"}
        if needs_proj:
            tag = f"{align}+projetion_assigned" if align in _GRADE_OK else "projetion_assigned"
        else:
            tag = align
        if is_fora:
            tag = f"fora_de_sp+{tag}" if tag else "fora_de_sp"
        row["_fila_alinhamento"] = tag
        if is_fora:
            if needs_proj:
                fora_projetion.append(row)
            elif align == _TIPO_JUNCAO_DIRETOS:
                fora_direto.append(row)
            elif align == _TIPO_JUNCAO_RESAMPLED:
                fora_resampled.append(row)
            else:
                fora_desconhecido.append(row)
        elif needs_proj:
            projetion.append(row)
        elif align == _TIPO_JUNCAO_DIRETOS:
            direto.append(row)
        elif align == _TIPO_JUNCAO_RESAMPLED:
            resampled.append(row)
        else:
            desconhecido.append(row)
        if probed and (probed % 500 == 0):
            log(
                f"  ... novas vistorias de grade: {probed} "
                f"(reaproveitadas={reused}) em {fmt_duration(time.time() - started)}"
            )
    if probed or force:
        save_grade_vistoria(cache_path, cache)
    elif reused:
        log(f"Vistoria de grade: reaproveitei {reused} par(es) de {cache_path} (0 novas leituras de cabecalho).")
    ordered = (
        direto
        + desconhecido
        + resampled
        + projetion
        + fora_direto
        + fora_desconhecido
        + fora_resampled
        + fora_projetion
    )
    n_fora = len(fora_direto) + len(fora_desconhecido) + len(fora_resampled) + len(fora_projetion)
    log("=" * 78)
    log("ORDEM DA FILA NESTA EXECUCAO")
    log(f"  1) SP copia direta:                              {len(direto)}")
    log(f"  2) SP grade nao classificada:                    {len(desconhecido)}")
    log(f"  3) SP resampled:                                 {len(resampled)}")
    log(f"  4) SP {PROJETION_ASSIGNED_SUFFIX}:               {len(projetion)}")
    log(f"  5) Fora de SP (so depois que SP da fila acabar): {n_fora}")
    log(f"  Cache: reaproveitadas={reused} | novas vistorias={probed}")
    log("  Os ja gravados em disco continuam pulados; so estes da fila serao escritos.")
    log("=" * 78)
    return ordered


def annotate_projetion_assigned(rows: list[dict[str, str]], sp_cache: dict[str, dict[str, str]]) -> int:
    """Mark pairs whose RGB/IR needed articulacao.epsg to enter Sao Paulo."""
    marked = 0
    for row in rows:
        if row.get("tipo_registro") != "par":
            continue
        assigned = ""
        for key in (row.get("rgb_caminho") or "", row.get("ir_caminho") or ""):
            if not key:
                continue
            saved = sp_cache.get(key.casefold()) or {}
            value = (saved.get("epsg_atribuido") or "").strip()
            if value:
                assigned = value
                break
        if not assigned:
            continue
        row["_projetion_assigned"] = "1"
        row["epsg_atribuido"] = assigned
        marked += 1
    return marked


@dataclass
class RunProgress:
    """Rolling % / ETA / disk estimates updated after each finished tile."""

    out_dir: Path
    grand_total: int
    already_done: int
    queue_total: int
    folder_bytes_start: int
    avg_saida_bytes: int
    pending_bytes_est_start: int
    free_bytes_start: int
    total_disk_bytes: int
    session_out_bytes: int = 0
    session_ok: int = 0

    def note_success(self, saida_bytes: int) -> None:
        size = max(0, int(saida_bytes))
        self.session_out_bytes += size
        self.session_ok += 1
        if self.session_ok >= 3:
            # Stabilize average with new outputs from this run.
            blended = int(
                (self.avg_saida_bytes * max(self.already_done, 1) + self.session_out_bytes)
                / max(self.already_done + self.session_ok, 1)
            )
            if blended > 0:
                self.avg_saida_bytes = blended

    def log_snapshot(self, *, position: int, session_done: int, session_total: int, elapsed: float) -> None:
        pct = (100.0 * position / self.grand_total) if self.grand_total else 100.0
        remaining_session = max(session_total - session_done, 0)
        remaining_overall = max(self.grand_total - position, 0)

        eta_count = None
        if session_done > 0 and remaining_session > 0:
            eta_count = (elapsed / session_done) * remaining_session

        eta_bytes = None
        if self.session_out_bytes > 0 and elapsed > 0 and remaining_session > 0:
            avg_now = self.session_out_bytes / max(self.session_ok, 1)
            remaining_bytes = avg_now * remaining_session
            rate = self.session_out_bytes / elapsed
            if rate > 0:
                eta_bytes = remaining_bytes / rate

        if eta_count is not None and eta_bytes is not None:
            # Weight toward size-based ETA as more bytes accumulate.
            w = min(0.8, self.session_ok / 20.0)
            eta = (1.0 - w) * eta_count + w * eta_bytes
            eta_txt = fmt_duration(eta)
        elif eta_bytes is not None:
            eta_txt = fmt_duration(eta_bytes)
        elif eta_count is not None:
            eta_txt = fmt_duration(eta_count)
        else:
            eta_txt = "calculando"

        folder_now = self.folder_bytes_start + self.session_out_bytes
        pending_left = max(remaining_overall, 0)
        pending_bytes = (self.avg_saida_bytes * pending_left) if self.avg_saida_bytes > 0 else 0
        projected_total = folder_now + pending_bytes
        try:
            free_now = shutil.disk_usage(self.out_dir).free
        except OSError:
            free_now = self.free_bytes_start

        log(
            f"Progresso geral {position}/{self.grand_total} ({pct:.2f}%) | "
            f"nesta execucao {session_done}/{session_total} | "
            f"ETA restante ~ {eta_txt} | decorrido {fmt_duration(elapsed)}"
        )
        log(
            f"Disco {self.out_dir.drive or self.out_dir}: "
            f"pasta RGBNIR agora ~ {fmt_size(folder_now)} | "
            f"media/tile ~ {fmt_size(self.avg_saida_bytes) if self.avg_saida_bytes else 'n/d'} | "
            f"falta estimar ~ {fmt_size(pending_bytes)} ({pending_left} tiles) | "
            f"total projetado ~ {fmt_size(projected_total)} | "
            f"livre agora {fmt_size(free_now)}"
        )
        if pending_bytes > 0 and free_now < pending_bytes:
            log(
                f"AVISO: espaco livre ({fmt_size(free_now)}) < estimativa do que falta "
                f"({fmt_size(pending_bytes)}). Libere disco ou mova dados."
            )


def build_run_progress(
    *,
    out_dir: Path,
    pairs: list[dict[str, str]],
    skipped: list[dict[str, str]],
    queue: list[dict[str, str]],
) -> RunProgress:
    log("Medindo tamanho atual de G:\\RGBNIR para estimativa de disco/tempo...")
    folder_bytes = folder_size_bytes(out_dir)
    avg = average_saida_bytes(skipped)
    if avg <= 0 and skipped:
        avg = int(folder_bytes / max(len(skipped), 1))
    pending_bytes = sum(estimate_row_output_bytes(row, avg) for row in queue)
    try:
        usage = shutil.disk_usage(out_dir)
        free_b, total_b = usage.free, usage.total
    except OSError:
        free_b, total_b = 0, 0
    projected = folder_bytes + pending_bytes
    log(
        f"  Pasta saida agora: {fmt_size(folder_bytes)} | "
        f"ja prontos: {len(skipped)} | fila: {len(queue)} | "
        f"media saida: {fmt_size(avg) if avg else 'n/d'}"
    )
    log(
        f"  Estimativa do que falta gravar: ~ {fmt_size(pending_bytes)} | "
        f"total projetado na pasta: ~ {fmt_size(projected)} | "
        f"livre no disco: {fmt_size(free_b)}"
    )
    if pending_bytes > 0 and free_b and free_b < pending_bytes:
        log(
            f"  AVISO: livre ({fmt_size(free_b)}) < falta estimar ({fmt_size(pending_bytes)})."
        )
    return RunProgress(
        out_dir=out_dir,
        grand_total=len(pairs),
        already_done=len(skipped),
        queue_total=len(queue),
        folder_bytes_start=folder_bytes,
        avg_saida_bytes=avg,
        pending_bytes_est_start=pending_bytes,
        free_bytes_start=free_b,
        total_disk_bytes=total_b,
    )


def _align_nir_to_rgb_window(ir_src, nir_band: int, window, rgb_transform, rgb_crs, src_nodata, dst_nodata):
    """Resample one IR window onto the RGB pixel grid with nearest neighbor.

    The destination has the RGB window's width, height and transform. Pixels
    with the IR NoData value, and pixels outside the IR extent, stay NoData
    instead of being filled with a made-up reflectance.
    """
    import numpy as np
    from rasterio.warp import Resampling, reproject
    from rasterio.windows import bounds as window_bounds
    from rasterio.windows import from_bounds
    from rasterio.windows import transform as window_transform

    dtype = np.dtype(ir_src.dtypes[nir_band - 1])
    height = int(window.height)
    width = int(window.width)
    dst_transform = window_transform(window, rgb_transform)
    read_fill = dtype.type(0 if src_nodata is None else src_nodata)
    hole = dtype.type(0 if dst_nodata is None else dst_nodata)
    destination = np.full((height, width), hole, dtype=dtype)

    res_x = abs(float(ir_src.transform.a)) or 1.0
    res_y = abs(float(ir_src.transform.e)) or 1.0
    left, bottom, right, top = window_bounds(window, rgb_transform)
    src_window = from_bounds(
        left - 2 * res_x,
        bottom - 2 * res_y,
        right + 2 * res_x,
        top + 2 * res_y,
        transform=ir_src.transform,
    ).round_offsets(op="floor").round_lengths(op="ceil")
    if int(src_window.width) < 1 or int(src_window.height) < 1:
        return destination
    source = ir_src.read(nir_band, window=src_window, boundless=True, fill_value=read_fill)
    reproject(
        source=source,
        destination=destination,
        src_transform=window_transform(src_window, ir_src.transform),
        src_crs=ir_src.crs,
        dst_transform=dst_transform,
        dst_crs=rgb_crs,
        resampling=Resampling.nearest,
        src_nodata=None if src_nodata is None else read_fill,
        dst_nodata=None if dst_nodata is None else hole,
    )
    return destination


def fuse_one(job: dict) -> dict:
    """Stack RGB bands 1-3 and IR band 1 into one 4-band GeoTIFF.

    ``job`` is a plain dict so a process pool on Windows can send it.
    Returns a plain dict as well. Never leaves a ``.tmp.tif`` behind on failure.
    """
    if rasterio is None or ColorInterp is None:
        return {
            "status": "falha",
            "detalhe": "rasterio nao esta instalado neste Python.",
            "largura_px": "",
            "altura_px": "",
            "crs": "",
            "saida_bytes": "",
            "duracao_segundos": "0",
        }

    rgb_path = job["rgb_path"]
    ir_path = job["ir_path"]
    out_path = Path(job["out_path"])
    prefix = job.get("log_prefix") or ""
    nir_band = int(job["nir_band"])
    gdal_threads = str(job["gdal_threads"])
    cache_mb = int(job["cache_mb"])
    assigned_epsg_raw = str(job.get("assigned_epsg") or "").strip()
    assigned_epsg: int | None = None
    if assigned_epsg_raw:
        try:
            assigned_epsg = int(assigned_epsg_raw)
        except ValueError:
            assigned_epsg = None
    started = time.time()
    tmp_path = out_path.with_name(out_path.stem + TMP_SUFFIX)

    def fail(status: str, detail: str) -> dict:
        _remove_quiet(tmp_path)
        log(f"{prefix} FALHA {status}: {detail}")
        return {
            "status": "falha",
            "detalhe": detail,
            "largura_px": "",
            "altura_px": "",
            "crs": "",
            "saida_bytes": "",
            "duracao_segundos": f"{time.time() - started:.1f}",
        }

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _remove_quiet(tmp_path)
        log(f"{prefix} RGB: {rgb_path} ({fmt_size(os.path.getsize(rgb_path))})")
        log(f"{prefix} IR : {ir_path} ({fmt_size(os.path.getsize(ir_path))})")
        log(f"{prefix} Saida temporaria: {tmp_path}")

        env_opts: dict = {
            "GDAL_NUM_THREADS": gdal_threads,
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "GDAL_PAM_ENABLED": "NO",
        }
        if cache_mb > 0:
            env_opts["GDAL_CACHEMAX"] = cache_mb

        with rasterio.Env(**env_opts), rasterio.open(rgb_path) as rgb_src, rasterio.open(ir_path) as ir_src:
            if rgb_src.count < 3:
                return fail("bandas_rgb", f"RGB tem {rgb_src.count} banda(s); preciso de pelo menos 3.")
            if ir_src.count < nir_band:
                return fail(
                    "bandas_ir",
                    f"IR tem {ir_src.count} banda(s); a banda NIR pedida e a {nir_band}.",
                )
            if rgb_src.crs is None or ir_src.crs is None:
                return fail("crs", f"CRS ausente. RGB={rgb_src.crs} IR={ir_src.crs}")
            rgb_dtype = str(rgb_src.dtypes[0])
            ir_dtype = str(ir_src.dtypes[nir_band - 1])
            if rgb_dtype != ir_dtype:
                return fail(
                    "dtype",
                    f"Tipo de pixel diferente: RGB={rgb_dtype} IR={ir_dtype}. Nao converti para nao alterar o valor.",
                )
            same_shape = (rgb_src.width, rgb_src.height) == (ir_src.width, ir_src.height)
            same_grid = (
                same_shape
                and rgb_src.crs == ir_src.crs
                and _approx_equal_transform(rgb_src.transform, ir_src.transform)
            )
            if rgb_src.crs != ir_src.crs:
                log(
                    f"{prefix} AVISO: CRS diferente (RGB={rgb_src.crs} IR={ir_src.crs}). "
                    "O IR sera reprojetado para o CRS e a grade do RGB."
                )
            if not same_shape:
                log(
                    f"{prefix} Tamanho diferente: RGB {rgb_src.width}x{rgb_src.height} px, "
                    f"IR {ir_src.width}x{ir_src.height} px. A saida usa o tamanho do RGB."
                )
            if not _approx_equal_transform(rgb_src.transform, ir_src.transform):
                log(
                    f"{prefix} Origem diferente. RGB={tuple(rgb_src.transform)[:6]} "
                    f"IR={tuple(ir_src.transform)[:6]}"
                )
            out_nodata = rgb_src.nodata if rgb_src.nodata is not None else ir_src.nodata
            if rgb_src.nodata != ir_src.nodata:
                log(
                    f"{prefix} AVISO: nodata diferente (RGB={rgb_src.nodata} IR={ir_src.nodata}). "
                    f"Buracos do NIR ficam com nodata={out_nodata}."
                )
            if out_nodata is None:
                log(
                    f"{prefix} AVISO: nem o RGB nem o IR declaram NoData. "
                    "Area sem cobertura de IR fica 0."
                )
            needs_proj = assigned_epsg in (31982, 31983)
            plain_out = apply_output_suffixes(out_path.name, resampled=False, projetion_assigned=False)
            if same_grid:
                log(f"{prefix} Grade do IR ja coincide com o RGB. NIR copiado sem reamostrar.")
                alignment_note = "Grade do IR ja coincidia com o RGB; NIR copiado sem reamostrar."
            else:
                log(
                    f"{prefix} Alinhando IR na grade do RGB com nearest neighbor "
                    f"(rasterio.warp.reproject)."
                )
                alignment_note = (
                    "IR alinhado na grade do RGB com nearest neighbor. "
                    f"RGB={tuple(rgb_src.transform)[:6]} IR={tuple(ir_src.transform)[:6]}"
                )
            out_path = out_path.with_name(
                apply_output_suffixes(
                    plain_out,
                    resampled=not same_grid,
                    projetion_assigned=needs_proj,
                )
            )
            tmp_path = out_path.with_name(out_path.stem + TMP_SUFFIX)
            _remove_quiet(tmp_path)
            if needs_proj:
                log(
                    f"{prefix} CRS de saida sera EPSG:{assigned_epsg} "
                    f"(atribuido pela articulacao; era RGB={rgb_src.crs})."
                )
                alignment_note = (
                    f"{alignment_note} CRS atribuido EPSG:{assigned_epsg} "
                    f"via articulacao{PROJETION_ASSIGNED_SUFFIX}."
                ).strip()
            log(f"{prefix} Arquivo final sera {out_path.name}.")

            log(
                f"{prefix} Referencia RGB: {rgb_src.width}x{rgb_src.height} px | "
                f"dtype={rgb_dtype} | CRS={rgb_src.crs} | nodata={out_nodata}"
            )
            log(f"{prefix} Bandas: RGB 1,2,3 inalteradas -> saida 1,2,3 | IR banda {nir_band} alinhada -> saida 4 (NIR).")

            profile = rgb_src.profile.copy()
            for key in ("photometric", "alpha", "Photometric", "ALPHA"):
                profile.pop(key, None)
            predictor = 3 if rgb_dtype.startswith("float") else 2
            tiny = rgb_src.width < 16 or rgb_src.height < 16
            block = min(BLOCK, int(rgb_src.width), int(rgb_src.height))
            block = max(16, block - (block % 16)) if block >= 16 else 16
            width_px = str(rgb_src.width)
            height_px = str(rgb_src.height)
            if needs_proj:
                out_crs = rasterio.crs.CRS.from_epsg(assigned_epsg)
                crs_text = f"EPSG:{assigned_epsg}"
            else:
                out_crs = rgb_src.crs
                crs_text = str(rgb_src.crs or "")
            profile.update(
                count=4,
                driver="GTiff",
                crs=out_crs,
                tiled=not tiny,
                BIGTIFF="IF_SAFER",
                NUM_THREADS=gdal_threads,
                PHOTOMETRIC="RGB",
                ALPHA="UNSPECIFIED",
                compress=COMPRESS,
                predictor=predictor,
            )
            if not tiny:
                profile.update(blockxsize=block, blockysize=block)
            else:
                profile.pop("blockxsize", None)
                profile.pop("blockysize", None)
            if out_nodata is not None:
                profile["nodata"] = out_nodata

            log(f"{prefix} Escrevendo blocos de {block}x{block}, compressao {COMPRESS}, predictor {predictor}.")
            block_started = time.time()
            with rasterio.open(tmp_path, "w", **profile) as dst:
                dst.colorinterp = (
                    ColorInterp.red,
                    ColorInterp.green,
                    ColorInterp.blue,
                    ColorInterp.undefined,
                )
                log(f"{prefix} Interpretacao de cor: {[item.name for item in dst.colorinterp]}")
                windows = [window for _, window in dst.block_windows(1)]
                total = len(windows)
                step = max(1, total // 20)
                log(f"{prefix} Blocos a gravar: {total}")
                for index, window in enumerate(windows, 1):
                    rgb_block = rgb_src.read(indexes=[1, 2, 3], window=window)
                    if same_grid:
                        nir_block = ir_src.read(nir_band, window=window)
                    else:
                        nir_block = _align_nir_to_rgb_window(
                            ir_src,
                            nir_band,
                            window,
                            rgb_src.transform,
                            rgb_src.crs,
                            ir_src.nodata,
                            out_nodata,
                        )
                    dst.write(rgb_block, [1, 2, 3], window=window)
                    dst.write(nir_block, 4, window=window)
                    if index % step == 0 or index == total:
                        elapsed = time.time() - block_started
                        rate = index / elapsed if elapsed > 0 else 0.0
                        eta = (total - index) / rate if rate > 0 else 0.0
                        log(
                            f"{prefix}   bloco {index}/{total} ({index / total * 100:5.1f}%) "
                            f"decorrido {elapsed:.0f}s ETA {eta:.0f}s"
                        )
                dst.descriptions = ("Red", "Green", "Blue", "NIR")
                dst.update_tags(1, BAND_NAME="Red")
                dst.update_tags(2, BAND_NAME="Green")
                dst.update_tags(3, BAND_NAME="Blue")
                dst.update_tags(4, BAND_NAME="NIR")
                dst.update_tags(
                    PROCESSING_SCRIPT="build_rgbnir_from_rgb_ir.py",
                    SOURCE_RGB=rgb_path,
                    SOURCE_IR=ir_path,
                    SOURCE_RGB_NAME=Path(rgb_path).name,
                    SOURCE_IR_NAME=Path(ir_path).name,
                    IR_NIR_BAND_INDEX=str(nir_band),
                    BAND_INTERPRETATION="band1=Red, band2=Green, band3=Blue, band4=NIR (Undefined; NOT alpha)",
                    IR_ALIGNMENT="nearest neighbor onto the RGB grid" if not same_grid else "IR grid already matched RGB; copied",
                    RESAMPLING="nearest" if not same_grid else "none",
                )
            os.replace(tmp_path, out_path)
            elapsed = time.time() - started
            out_size = out_path.stat().st_size
            log(f"{prefix} PRONTO {out_path.name} em {fmt_duration(elapsed)} ({fmt_size(out_size)}).")
            return {
                "status": "ok",
                "detalhe": alignment_note,
                "saida_nome": out_path.name,
                "largura_px": width_px,
                "altura_px": height_px,
                "crs": crs_text,
                "saida_bytes": str(out_size),
                "duracao_segundos": f"{elapsed:.1f}",
            }
    except KeyboardInterrupt:
        if not out_path.exists():
            _remove_quiet(tmp_path)
            log(f"{prefix} INTERROMPIDO. Parcial removido: {tmp_path.name}")
        else:
            log(f"{prefix} INTERROMPIDO depois que {out_path.name} ja tinha sido fechado. O arquivo final fica.")
        raise
    except RasterioIOError as exc:
        return fail("io", str(exc))
    except OSError as exc:
        return fail("os", str(exc))
    except Exception as exc:  # noqa: BLE001
        log(f"{prefix} traceback:\n{traceback.format_exc()}")
        return fail("erro", f"{type(exc).__name__}: {exc}")


def _remove_quiet(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def apply_result(row: dict[str, str], result: dict, *, run_id: str, out_dir: Path) -> None:
    pairing_note = row.get("detalhe") or ""
    row["status"] = result["status"]
    row["duracao_segundos"] = result.get("duracao_segundos", "")
    row["fim_utc"] = utc_now()
    if result.get("saida_nome"):
        row["saida_nome"] = result["saida_nome"]
        row["saida_caminho"] = str(out_dir / result["saida_nome"])
    if result["status"] == "ok":
        alignment_note = result.get("detalhe") or ""
        row["detalhe"] = " ".join(part for part in (pairing_note, alignment_note) if part)
        row["largura_px"] = result.get("largura_px", "")
        row["altura_px"] = result.get("altura_px", "")
        row["crs"] = result.get("crs", "")
        row["saida_bytes"] = result.get("saida_bytes", "")
        row["run_id_que_gravou"] = run_id
        row["ultimo_erro"] = ""
    else:
        row["detalhe"] = result.get("detalhe", "") or pairing_note
        row["ultimo_erro"] = result.get("detalhe", "")
    row["notas"] = compose_note(row, out_dir)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Junta RGB (3 bandas) + IR (banda 1 = NIR) em GeoTIFF RGBNIR de 4 bandas. "
            "Pula o que ja existe em G:\\RGBNIR e pode ser interrompido e retomado."
        )
    )
    parser.add_argument("--rgb", default=DEFAULT_RGB_DIR, help=f"Pasta dos RGB (padrao: {DEFAULT_RGB_DIR})")
    parser.add_argument("--ir", default=DEFAULT_IR_DIR, help=f"Pasta dos IR (padrao: {DEFAULT_IR_DIR})")
    parser.add_argument("--out", default=DEFAULT_OUT_DIR, help=f"Pasta de saida (padrao: {DEFAULT_OUT_DIR})")
    parser.add_argument(
        "--sp-bounds",
        default=DEFAULT_SP_BOUNDS,
        help=(
            "GeoPackage com as camadas epsg31982 e epsg31983, cada uma ja no seu EPSG. "
            f"Rasters que nao intersectam a camada do mesmo CRS sao ignorados. Padrao: {DEFAULT_SP_BOUNDS}"
        ),
    )
    parser.add_argument(
        "--articulacao",
        default=DEFAULT_ARTICULACAO,
        help=(
            "GeoPackage da articulacao IGC usado para a cobertura espacial dos RGBNIR. "
            f"Padrao: {DEFAULT_ARTICULACAO}"
        ),
    )
    parser.add_argument(
        "--articulacao-layer",
        default=DEFAULT_ARTIC_LAYER,
        help=f"Camada da articulacao dentro do GeoPackage. Padrao: {DEFAULT_ARTIC_LAYER}",
    )
    parser.add_argument(
        "--no-cobertura",
        action="store_true",
        help="Nao grava nem atualiza o GeoPackage de cobertura na pasta de saida.",
    )
    parser.add_argument(
        "--force-sp",
        action="store_true",
        help=(
            "Refaz a vistoria de Sao Paulo em todos os TIFF. "
            "Sem esta opcao, e com FORCE_SP_VISTORIA = False, o resultado salvo e reaproveitado."
        ),
    )
    parser.add_argument(
        "--force-grade",
        action="store_true",
        help=(
            "Refaz a vistoria de grade (direto vs resampled) em todos os pares pendentes. "
            f"Sem esta opcao, reaproveita {GRADE_VISTORIA_NAME} e so inspeciona pares novos."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Tiles em paralelo. 1 deixa o log mais facil de ler (padrao).",
    )
    parser.add_argument(
        "--gdal-threads",
        type=int,
        default=0,
        help="Threads GDAL por tile. 0 escolhe 4 se workers=1, senao 2.",
    )
    parser.add_argument("--cache-mb", type=int, default=512, help="Cache GDAL em MB por processo (padrao: 512).")
    parser.add_argument(
        "--nir-band",
        type=int,
        default=IR_NIR_BAND_INDEX,
        help="Banda 1-based do IR que vira a banda NIR (padrao: 1).",
    )
    parser.add_argument("--overwrite", action="store_true", help="Refaz tambem os arquivos que ja existem.")
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="Abre cada saida ja existente e confere se tem 4 bandas. Mais lento. Arquivo invalido vai para _invalid e e refeito.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="So escaneia, pareia e escreve os CSV. Nao grava GeoTIFF.",
    )
    return parser.parse_args(argv)


def _require_folder(path: Path, label: str) -> bool:
    if path.is_dir():
        log(f"{label}: {path} (existe)")
        return True
    log(f"ABORTADO: pasta de {label} nao existe: {path}")
    return False


def _print_banner(args: argparse.Namespace, run_id: str, gdal_threads: int) -> None:
    log("=" * 78)
    log(f"RGBNIR  |  script {VERSION}  |  execucao {run_id}  |  micro {socket.gethostname()}")
    log("Receita: banda1=Red (RGB 1), banda2=Green (RGB 2), banda3=Blue (RGB 3), banda4=NIR (IR banda %d)." % args.nir_band)
    log("A banda 4 e NIR, nao e alfa. O QGIS nao deve trata-la como transparencia.")
    log(f"RGB     -> {args.rgb}")
    log(f"IR      -> {args.ir}")
    log(f"Saida   -> {args.out}")
    log(f"Limite SP -> {args.sp_bounds}")
    if FORCE_SP_VISTORIA or args.force_sp:
        log("Vistoria SP -> FORCADA. Todos os TIFF serao comparados de novo.")
    else:
        log(f"Vistoria SP -> reaproveita {Path(args.out) / SP_VISTORIA_NAME} se ele ja existir.")
    log(f"Resumo  -> {Path(args.out) / SUMMARY_NAME}")
    log(f"Registro-> {Path(args.out) / REGISTRY_NAME}")
    log(f"Dicionario -> {Path(args.out) / DICTIONARY_NAME}")
    if getattr(args, "no_cobertura", False):
        log("Cobertura GPKG -> desligada (--no-cobertura).")
    else:
        log(f"Cobertura GPKG -> {Path(args.out) / COBERTURA_NAME}")
        log(f"Articulacao cobertura -> {args.articulacao} | layer={args.articulacao_layer}")
    if getattr(args, "force_grade", False):
        log("Vistoria de grade -> FORCADA em todos os pares pendentes.")
    else:
        log(
            f"Vistoria de grade -> reaproveita {Path(args.out) / GRADE_VISTORIA_NAME}; "
            "so pares novos/ausentes sao abertos."
        )
    log(
        "Retomada: se este script parar, rode o .bat de novo. "
        f"O que ja estiver em {args.out} com tamanho > 0 e pulado."
    )
    log(f"Workers={max(1, args.workers)} | threads GDAL/tile={gdal_threads} | cache={args.cache_mb} MB")
    if args.dry_run:
        log("MODO DRY-RUN: nenhum GeoTIFF sera gravado. So o CSV.")
    if args.overwrite:
        log("MODO OVERWRITE: arquivos que ja existem serao refeitos.")
    log("=" * 78)


def _assign_resume_status(
    rows: list[dict[str, str]],
    *,
    out_dir: Path,
    previous: dict[str, dict[str, str]],
    overwrite: bool,
    verify_existing: bool,
    nir_band: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split pair rows into skip vs work queue. Mutates status on each row."""
    skipped: list[dict[str, str]] = []
    queue: list[dict[str, str]] = []
    invalid_dir = out_dir / "_invalid"
    for row in rows:
        if row["tipo_registro"] != "par" or row["status"] == "conflito_saida":
            continue
        plain_name = row["saida_nome"]
        found = None if overwrite else finished_output(out_dir, plain_name)
        if found is not None:
            row["saida_nome"] = found.name
            row["saida_caminho"] = str(found)
            restore_previous_fields(row, previous)
            if verify_existing:
                ok, detail = verify_existing_geotiff(found, nir_band)
                if not ok:
                    invalid_dir.mkdir(parents=True, exist_ok=True)
                    moved = invalid_dir / found.name
                    log(f"Saida invalida ({detail}). Movi {found.name} para {moved}")
                    try:
                        if moved.exists():
                            moved.unlink()
                        found.replace(moved)
                    except OSError as exc:
                        log(f"AVISO: nao consegui mover o invalido {found}: {exc}")
                    row["saida_nome"] = plain_name
                    row["saida_caminho"] = str(out_dir / plain_name)
                    row["status"] = "pendente"
                    row["detalhe"] = f"Arquivo anterior invalido ({detail}) movido para {moved}."
                    row["notas"] = ""
                    queue.append(row)
                    continue
                log(f"Conferido OK: {found.name} ({detail})")
            row["status"] = "pulado_ja_existe"
            if not row["saida_bytes"]:
                try:
                    row["saida_bytes"] = str(found.stat().st_size)
                except OSError:
                    pass
            row["notas"] = ""
            skipped.append(row)
            continue
        out_path = out_dir / plain_name
        row["saida_caminho"] = str(out_path)
        restore_previous_fields(row, previous)
        if out_path.is_file() and out_path.stat().st_size <= 0:
            log(f"Arquivo de saida vazio, vou refazer: {out_path.name}")
            _remove_quiet(out_path)
        row["status"] = "pendente"
        row["notas"] = ""
        queue.append(row)
    return skipped, queue


def _print_scenario_audit(
    rows: list[dict[str, str]],
    *,
    rgb_all_count: int | None = None,
    ir_all_count: int | None = None,
    skipped: list[dict[str, str]] | None = None,
    queue: list[dict[str, str]] | None = None,
) -> None:
    """Startup inventory: ~9k origin files vs ~8.8k pairs the script will fuse."""
    pairs = [row for row in rows if row.get("tipo_registro") == "par"]

    def ncen(number: str) -> int:
        return sum(1 for row in pairs if row.get("cenario") == number)

    rgb_sem = sum(1 for row in rows if row.get("tipo_registro") == "rgb_sem_ir")
    ir_sem = sum(1 for row in rows if row.get("tipo_registro") == "ir_sem_rgb")
    anomalias = sum(1 for row in rows if row.get("status") == "anomalia" or row.get("tipo_registro") == "anomalia")
    fora = sum(1 for row in rows if row.get("status") == "fora_de_sp")
    sem_loc = sum(1 for row in rows if row.get("status") == "sem_localizacao")
    conflitos = sum(1 for row in rows if row.get("status") == "conflito_saida")
    duplicatas = sum(1 for row in rows if row.get("tipo_registro") == "duplicata_ignorada")
    cen5 = sum(1 for row in rows if row.get("cenario") == "5")
    n_rgb = rgb_all_count if rgb_all_count is not None else sum(1 for row in rows if row.get("rgb_caminho"))
    n_ir = ir_all_count if ir_all_count is not None else sum(1 for row in rows if row.get("ir_caminho"))
    done = len(skipped or [])
    pending = len(queue or [])
    ready = sum(1 for row in pairs if row.get("status") in {"ok", "pulado_ja_existe"})
    if skipped is not None:
        ready = done

    log("=" * 78)
    log("INVENTARIO (leia com calma: entrada ~9k por pasta != saida ~8.8k pares)")
    log("=" * 78)
    log(f"  Arquivos RGB encontrados na origem:  {n_rgb}")
    log(f"  Arquivos IR  encontrados na origem:  {n_ir}")
    log(f"  Soma bruta RGB+IR (dois lados):      {n_rgb + n_ir}")
    log("  Isso NAO e a meta de GeoTIFF. Um arquivo de origem nao vira um RGBNIR.")
    log("-" * 78)
    log(f"  Pares que GERAM GeoTIFF (cenarios 1-4): {len(pairs)}")
    log(f"    cenario 1 RAW+RAW:                 {ncen('1')}")
    log(f"    cenario 2 mesma familia/versao:    {ncen('2')}")
    log(f"    cenario 3 RAW x familia:           {ncen('3')}")
    log(f"    cenario 4 familias diferentes:     {ncen('4')}")
    log(f"  Conferencia 1+2+3+4:                 {ncen('1') + ncen('2') + ncen('3') + ncen('4')}")
    log("-" * 78)
    log("  Linhas que NAO geram GeoTIFF:")
    log(f"    cenario 5 (sem tif, agregado):     {cen5}")
    log(f"    RGB sem IR:                        {rgb_sem}")
    log(f"    IR sem RGB:                        {ir_sem}")
    log(f"    anomalias:                         {anomalias}")
    log(f"    fora de Sao Paulo (fila depois):   {fora}")
    log(f"    sem localizacao (sem CRS/bounds):  {sem_loc}")
    log(f"    conflitos de nome de saida:        {conflitos}")
    log(f"    duplicatas ignoradas:              {duplicatas}")
    log(
        f"  Conferencia residual sem tif:        "
        f"{rgb_sem + ir_sem + anomalias} "
        f"(rgb_sem_ir + ir_sem_rgb + anomalias; deve bater com cenario 5 se so esses)"
    )
    log(f"  Linhas totais no registro:           {len(rows)}")
    log(
        f"  Conferencia registro:                "
        f"{len(pairs) + rgb_sem + ir_sem + anomalias + fora + sem_loc + conflitos + duplicatas} "
        f"(pares + residual + filtros)"
    )
    log("-" * 78)
    log(f"  META DE PRODUCAO DESTE SCRIPT:       {len(pairs)} GeoTIFF RGBNIR")
    log(f"  Ja prontos em disco (serao pulados): {ready}")
    if queue is not None:
        log(f"  Ainda na fila desta execucao:        {pending}")
        log("  Ordem da fila: primeiro copia direta (mesma grade); so depois resampled.")
    log("=" * 78)


def _print_pair_plan(pairs: list[dict[str, str]], queue: list[dict[str, str]], skipped: list[dict[str, str]]) -> None:
    total = len(pairs)
    done = len(skipped)
    log(f"Ja prontos em disco (serao pulados): {done}")
    log(f"Ainda faltam gravar nesta execucao: {len(queue)}")
    if total:
        proximo = done + 1 if queue else total
        log(f"Andamento geral: {done}/{total} ja processados. O proximo entra como {proximo}/{total}.")
    n_dir = sum(1 for row in queue if row.get("_fila_alinhamento") == _TIPO_JUNCAO_DIRETOS)
    n_res = sum(1 for row in queue if row.get("_fila_alinhamento") == _TIPO_JUNCAO_RESAMPLED)
    n_unk = sum(1 for row in queue if row.get("_fila_alinhamento") == "desconhecido")
    if queue:
        log(f"  Na fila agora -> direto={n_dir} | desconhecido={n_unk} | resampled={n_res}")
    preview = skipped[:15]
    for row in preview:
        log(f"  PULA  {row['saida_nome']}  <=  RGB {row['rgb_nome']}  +  IR {row['ir_nome']}")
    if len(skipped) > len(preview):
        log(f"  ... e mais {len(skipped) - len(preview)} arquivo(s) ja pronto(s). A lista completa esta no CSV.")
    preview_queue = queue[:40]
    for offset, row in enumerate(preview_queue, 1):
        pos = done + offset
        tag = row.get("_fila_alinhamento") or "?"
        log(
            f"  FILA  [{pos}/{total}] ({tag}) {row['saida_nome']}  <=  "
            f"RGB {row['rgb_nome']}  +  IR {row['ir_nome']}"
        )
    if len(queue) > len(preview_queue):
        log(f"  ... e mais {len(queue) - len(preview_queue)} na fila. Cada um sera impresso de novo quando comecar.")


def _print_leftovers(rows: list[dict[str, str]]) -> None:
    groups = {
        "rgb_sem_ir": "RGB sem IR correspondente",
        "ir_sem_rgb": "IR sem RGB correspondente",
        "anomalia": "Anomalias (cenario 5)",
        "duplicata_ignorada": "Duplicatas ignoradas",
        "conflito_saida": "Conflitos de nome de saida",
        "fora_de_sp": "Fora de Sao Paulo (nao pareados; pares fora entram depois de SP)",
        "sem_localizacao": "Sem CRS ou retangulo (ignorados)",
    }
    for kind, title in groups.items():
        items = [row for row in rows if row["tipo_registro"] == kind]
        if not items:
            continue
        log(f"{title}: {len(items)}")
        for row in items[:15]:
            name = row["rgb_nome"] or row["ir_nome"] or row["tile_key"]
            log(f"  - {name} | {row['detalhe']}")
        if len(items) > 15:
            log(f"  ... e mais {len(items) - 15}. Veja o CSV.")


def _run_queue(
    queue: list[dict[str, str]],
    *,
    workers: int,
    run_id: str,
    out_dir: Path,
    rgb_dir: Path,
    ir_dir: Path,
    all_rows: list[dict[str, str]],
    gdal_threads: int,
    cache_mb: int,
    nir_band: int,
    already_done: int,
    grand_total: int,
    cobertura: RgbnirCobertura | None = None,
    progress: RunProgress | None = None,
    grade_cache: dict[str, dict[str, str]] | None = None,
    grade_cache_path: Path | None = None,
) -> str:
    """Fuse the queue. Returns 'ok', 'falha' or 'interrompido'.

    ``already_done`` tiles are already on disk. The counter shown to the user
    is ``already_done + position`` over ``grand_total``, so a resumed run that
    finished 50 of 9000 starts at 51/9000.
    """
    total = len(queue)
    started = time.time()
    written = 0
    failed = 0
    outcome = "ok"
    grade_dirty = False

    def remember(row: dict[str, str], result: dict, position: int) -> None:
        nonlocal written, failed, grade_dirty
        apply_result(row, result, run_id=run_id, out_dir=out_dir)
        if result["status"] == "ok":
            written += 1
            align = infer_alinhamento_from_saida(row.get("saida_nome") or "", row.get("detalhe") or "")
            if align in _GRADE_OK:
                row["_fila_alinhamento"] = align
                if grade_cache is not None:
                    store_grade_result(grade_cache, row, align, saida_nome=row.get("saida_nome") or "")
                    grade_dirty = True
            if progress is not None:
                progress.note_success(_to_int_bytes(row.get("saida_bytes") or result.get("saida_bytes")))
            if cobertura is not None:
                if cobertura.add_from_registry_row(row, out_dir):
                    log(f"Cobertura +1 -> {out_dir / COBERTURA_NAME} ({row.get('saida_nome')})")
        else:
            failed += 1
        publish(all_rows, out_dir=out_dir, rgb_dir=rgb_dir, ir_dir=ir_dir, run_id=run_id)
        done = written + failed
        elapsed = time.time() - started
        if progress is not None:
            progress.log_snapshot(
                position=position,
                session_done=done,
                session_total=total,
                elapsed=elapsed,
            )
        else:
            eta_txt = fmt_duration((elapsed / done) * (total - done)) if done else "calculando"
            pct = (100.0 * position / grand_total) if grand_total else 100.0
            log(
                f"Progresso {position}/{grand_total} ({pct:.2f}%) | nesta execucao {done}/{total} | "
                f"gravados {written} | falhas {failed} | "
                f"restam na fila {total - done} | ETA {eta_txt} | decorrido {fmt_duration(elapsed)}"
            )
        log(f"CSV atualizado: {out_dir / SUMMARY_NAME}")
        if grade_dirty and grade_cache is not None and grade_cache_path is not None and done % 25 == 0:
            save_grade_vistoria(grade_cache_path, grade_cache)
            grade_dirty = False

    settings_base = {
        "nir_band": nir_band,
        "gdal_threads": str(gdal_threads),
        "cache_mb": cache_mb,
    }

    if workers == 1:
        for index, row in enumerate(queue, 1):
            position = already_done + index
            prefix = f"[{position}/{grand_total}] {row['saida_nome']}"
            row["status"] = "pendente"
            row["inicio_utc"] = utc_now()
            row["notas"] = ""
            log("-" * 78)
            log(f"{prefix} comecando. RGB={row['rgb_nome']} IR={row['ir_nome']} -> {row['saida_nome']}")
            job = {
                **settings_base,
                "rgb_path": row["rgb_caminho"],
                "ir_path": row["ir_caminho"],
                "out_path": row["saida_caminho"],
                "log_prefix": prefix,
                "assigned_epsg": row.get("epsg_atribuido") or "",
            }
            try:
                result = fuse_one(job)
            except KeyboardInterrupt:
                row["status"] = "interrompido"
                row["fim_utc"] = utc_now()
                row["detalhe"] = "Ctrl+C durante este tile. O parcial foi apagado."
                row["ultimo_erro"] = row["detalhe"]
                row["notas"] = ""
                publish(all_rows, out_dir=out_dir, rgb_dir=rgb_dir, ir_dir=ir_dir, run_id=run_id)
                if grade_dirty and grade_cache is not None and grade_cache_path is not None:
                    save_grade_vistoria(grade_cache_path, grade_cache)
                log("Interrompido pelo usuario. O que ja estava pronto foi mantido.")
                log("Rode o .bat de novo para continuar do que falta.")
                return "interrompido"
            remember(row, result, position)
        if grade_dirty and grade_cache is not None and grade_cache_path is not None:
            save_grade_vistoria(grade_cache_path, grade_cache)
        return "falha" if failed else "ok"

    log(f"Fila paralela com {workers} processos. As linhas de tiles diferentes vao se misturar; cada uma traz o id do tile.")
    jobs = []
    positions: dict[int, int] = {}
    for index, row in enumerate(queue, 1):
        row["inicio_utc"] = utc_now()
        row["notas"] = ""
        position = already_done + index
        positions[id(row)] = position
        prefix = f"[{position}/{grand_total}] {row['saida_nome']}"
        jobs.append(
            (
                row,
                {
                    **settings_base,
                    "rgb_path": row["rgb_caminho"],
                    "ir_path": row["ir_caminho"],
                    "out_path": row["saida_caminho"],
                    "log_prefix": prefix,
                    "assigned_epsg": row.get("epsg_atribuido") or "",
                },
            )
        )
    executor = ProcessPoolExecutor(max_workers=workers)
    try:
        futures = {executor.submit(fuse_one, job): row for row, job in jobs}
        for future in as_completed(futures):
            row = futures[future]
            try:
                result = future.result()
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                result = {
                    "status": "falha",
                    "detalhe": f"{type(exc).__name__}: {exc}",
                    "duracao_segundos": "",
                }
            remember(row, result, positions.get(id(row), already_done + written + failed))
    except KeyboardInterrupt:
        log("Interrompido pelo usuario. Pedindo para os processos pararem.")
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=False)
        for row in queue:
            if row["status"] == "pendente":
                row["status"] = "interrompido"
                row["detalhe"] = "Execucao interrompida antes de terminar este tile."
                row["notas"] = ""
        publish(all_rows, out_dir=out_dir, rgb_dir=rgb_dir, ir_dir=ir_dir, run_id=run_id)
        if grade_dirty and grade_cache is not None and grade_cache_path is not None:
            save_grade_vistoria(grade_cache_path, grade_cache)
        return "interrompido"
    else:
        executor.shutdown(wait=True)
    if grade_dirty and grade_cache is not None and grade_cache_path is not None:
        save_grade_vistoria(grade_cache_path, grade_cache)
    return "falha" if failed else outcome


def _print_final(rows: list[dict[str, str]], out_dir: Path, started: float) -> None:
    summary_pairs = [row for row in rows if row["tipo_registro"] == "par"]
    ready = sum(1 for row in summary_pairs if row["status"] in {"ok", "pulado_ja_existe"})
    missing = sum(1 for row in summary_pairs if row["status"] in {"pendente", "falha", "interrompido"})
    log("=" * 78)
    log("FIM DESTA EXECUCAO")
    log(f"Pares prontos no disco: {ready}/{len(summary_pairs) or 0}")
    log(f"Ainda faltam gravar:   {missing}")
    _print_scenario_audit(rows)
    log(f"Duracao desta execucao: {fmt_duration(time.time() - started)}")
    log(f"Resumo (abra no Excel): {out_dir / SUMMARY_NAME}")
    log(f"Registro tile a tile:   {out_dir / REGISTRY_NAME}")
    log(f"Dicionario das colunas: {out_dir / DICTIONARY_NAME}")
    log(f"Cobertura espacial:     {out_dir / COBERTURA_NAME}")
    if missing:
        log("Para continuar, rode de novo o arquivo build_rgbnir_from_rgb_ir.bat.")
        log(f"Ele ve o que ja esta em {out_dir} e segue apenas no que falta.")
    else:
        log("Nenhum par ficou pendente. Se aparecer tile novo em E:\\RGB e F:\\IR, rode o .bat de novo.")
    try:
        usage = shutil.disk_usage(out_dir)
        log(f"Espaco livre agora em {out_dir.drive or out_dir}: {fmt_size(usage.free)}")
    except OSError:
        pass
    log("=" * 78)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    rgb_dir = Path(args.rgb)
    ir_dir = Path(args.ir)
    out_dir = Path(args.out)
    run_id = utc_run_id()
    workers = max(1, int(args.workers))
    gdal_threads = int(args.gdal_threads) if int(args.gdal_threads) > 0 else (4 if workers == 1 else 2)
    started = time.time()

    _print_banner(args, run_id, gdal_threads)

    if rasterio is None and not args.dry_run:
        log("ABORTADO: este Python nao tem rasterio.")
        log("Instale com:  pip install rasterio")
        log("Ou rode com o Python do OSGeo4W / QGIS, que ja traz o GDAL.")
        return 2
    if rasterio is None:
        log("AVISO: rasterio nao esta instalado. Este dry-run so consegue listar e parear arquivos.")
    else:
        log(f"rasterio {rasterio.__version__} | GDAL {rasterio.__gdal_version__}")

    if not _require_folder(rgb_dir, "RGB"):
        return 2
    if not _require_folder(ir_dir, "IR"):
        return 2
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log(f"ABORTADO: nao consegui criar a pasta de saida {out_dir}: {exc}")
        return 2
    log(f"Saida: {out_dir} (existe)")
    try:
        usage = shutil.disk_usage(out_dir)
        log(
            f"Espaco no disco de saida: livre {fmt_size(usage.free)} de {fmt_size(usage.total)}."
        )
    except OSError as exc:
        log(f"AVISO: nao li o espaco livre de {out_dir}: {exc}")

    lock = RunLock(out_dir, run_id)
    if not lock.acquire():
        # 3 = another live copy holds the lock. The .bat closes without pausing.
        return 3 if lock.blocked_by_other else 2

    exit_code = 0
    try:
        removed = cleanup_stale_tmp(out_dir)
        if removed:
            log(f"Parciais de uma execucao interrompida removidos: {removed}")
        else:
            log("Nenhum arquivo .tmp.tif parado na pasta de saida.")

        log("Escrevendo o dicionario das colunas do CSV...")
        write_dictionary(out_dir / DICTIONARY_NAME)
        log(f"Dicionario: {out_dir / DICTIONARY_NAME}")

        cobertura: RgbnirCobertura | None = None
        if not args.no_cobertura:
            try:
                cobertura = RgbnirCobertura(
                    path=out_dir / COBERTURA_NAME,
                    artic_path=Path(args.articulacao),
                    artic_layer=args.articulacao_layer,
                )
                cobertura.load_or_bootstrap(out_dir)
                if not cobertura.enabled:
                    cobertura = None
            except Exception as exc:  # noqa: BLE001
                log(f"AVISO: cobertura desligada apos erro: {type(exc).__name__}: {exc}")
                cobertura = None
        else:
            log("Cobertura GPKG desligada por --no-cobertura.")

        sp_path = Path(args.sp_bounds)
        force_sp = FORCE_SP_VISTORIA or args.force_sp
        vistoria_path = out_dir / SP_VISTORIA_NAME
        if force_sp:
            sp_cache = {}
            log("Vistoria de SP forcada. Todos os TIFF serao comparados de novo.")
        else:
            sp_cache = load_sp_vistoria(vistoria_path)
            if sp_cache:
                log(f"Vistoria de SP encontrada: {len(sp_cache)} arquivo(s) em {vistoria_path}.")
            else:
                sp_cache = vistoria_from_registry(out_dir)
                if sp_cache:
                    log(
                        f"Vistoria de SP recuperada do registro anterior: {len(sp_cache)} arquivo(s). "
                        "Os cabecalhos nao serao reabertos."
                    )
                else:
                    log("Nenhuma vistoria de SP salva. Esta execucao abre o cabecalho de cada TIFF uma vez.")

        rgb_all = scan_rasters(rgb_dir)
        ir_all = scan_rasters(ir_dir)
        if not rgb_all:
            log(f"ABORTADO: nenhum .tif/.tiff em {rgb_dir}")
            return 2
        if not ir_all:
            log(f"ABORTADO: nenhum .tif/.tiff em {ir_dir}")
            return 2

        assigned_index: AssignedEpsgIndex | None = None
        try:
            assigned_index = AssignedEpsgIndex(Path(args.articulacao), args.articulacao_layer)
        except Exception as exc:  # noqa: BLE001
            log(
                f"AVISO: nao carreguei EPSG por tile da articulacao ({args.articulacao}): "
                f"{type(exc).__name__}: {exc}. Tiles com CRS fora de 31982/31983 continuam sem_localizacao."
            )

        need_sp = sp_vistoria_has_new_files(
            rgb_all, sp_cache, force_sp, can_assign_epsg=assigned_index is not None
        ) or sp_vistoria_has_new_files(
            ir_all, sp_cache, force_sp, can_assign_epsg=assigned_index is not None
        )
        sp_mask = None
        if need_sp:
            if not sp_path.is_file():
                log(f"ABORTADO: limite de Sao Paulo nao encontrado: {sp_path}")
                return 2
            if rasterio is None:
                log("ABORTADO: preciso do rasterio para ler o retangulo de cada TIFF e comparar com SP.")
                return 2
            try:
                sp_mask = SpBounds(sp_path)
            except Exception as exc:  # noqa: BLE001
                log(f"ABORTADO: nao li o limite de Sao Paulo ({sp_path}): {type(exc).__name__}: {exc}")
                return 2
        else:
            log("Nenhum TIFF novo ou com tamanho diferente. A vistoria de SP nao sera refeita.")

        rgb_hits, rgb_fora, rgb_outside = filter_rasters_to_sp(
            rgb_all,
            sp_mask,
            sp_cache,
            label="RGB",
            rgb=True,
            force=force_sp,
            assigned_index=assigned_index,
        )
        ir_hits, ir_fora, ir_outside = filter_rasters_to_sp(
            ir_all,
            sp_mask,
            sp_cache,
            label="IR",
            rgb=False,
            force=force_sp,
            assigned_index=assigned_index,
        )
        save_sp_vistoria(vistoria_path, sp_cache, rgb_all + ir_all)
        if (not rgb_hits and not rgb_fora) or (not ir_hits and not ir_fora):
            dropped = rgb_outside + ir_outside
            for row in dropped:
                row["run_id_desta_execucao"] = run_id
                row["notas"] = compose_note(row, out_dir)
            publish(dropped, out_dir=out_dir, rgb_dir=rgb_dir, ir_dir=ir_dir, run_id=run_id)
            log("ABORTADO: nenhum RGB/IR utilizavel (dentro ou fora de SP) para parear.")
            log(f"Os ignorados foram listados em {out_dir / REGISTRY_NAME}.")
            return 2

        log("Pareando RGB com IR pelo nome do arquivo...")
        pairing_sp = pair_rasters(rgb_hits, ir_hits) if rgb_hits and ir_hits else Pairing()
        pairing_fora = pair_rasters(rgb_fora, ir_fora) if rgb_fora and ir_fora else Pairing()
        for row in pairing_fora.pairs:
            row["_fora_de_sp"] = "1"
            note = (row.get("detalhe") or "").strip()
            extra = "Fora de Sao Paulo: processado so depois que os pares dentro de SP acabarem."
            row["detalhe"] = f"{note} {extra}".strip() if note else extra
        if pairing_fora.pairs:
            log(
                f"Pares fora de SP: {len(pairing_fora.pairs)} "
                "(entram na fila so depois de todos os pares dentro de SP desta execucao)."
            )
        pairing_pairs = pairing_sp.pairs + pairing_fora.pairs
        previous = load_previous_registry(out_dir / REGISTRY_NAME)
        if previous:
            log(f"Registro anterior carregado ({len(previous)} saidas) para preservar data e duracao.")
        else:
            log("Nao havia registro anterior. Este CSV comeca do zero.")

        all_rows = (
            pairing_sp.all_rows()
            + pairing_fora.all_rows()
            + rgb_outside
            + ir_outside
        )
        for row in all_rows:
            if row["saida_nome"]:
                row["saida_caminho"] = str(out_dir / row["saida_nome"])
            row["run_id_desta_execucao"] = run_id
        n_proj = annotate_projetion_assigned(pairing_pairs, sp_cache)
        if n_proj:
            log(
                f"Pares que precisarao de {PROJETION_ASSIGNED_SUFFIX}: {n_proj} "
                "(CRS atribuido via articulacao.epsg; processados depois dos resampled)."
            )

        skipped, queue = _assign_resume_status(
            pairing_pairs,
            out_dir=out_dir,
            previous=previous,
            overwrite=args.overwrite,
            verify_existing=args.verify_existing,
            nir_band=int(args.nir_band),
        )
        for row in all_rows:
            row["notas"] = compose_note(row, out_dir)

        # Resume-safe: never rewrite finished GeoTIFFs; only fill missing GPKG rows/attrs once.
        if cobertura is not None:
            cobertura.sync_missing_from_rows(skipped + [r for r in pairing_pairs if r.get("status") == "ok"], out_dir)

        grade_path = out_dir / GRADE_VISTORIA_NAME
        grade_cache = load_grade_vistoria(grade_path)
        if grade_cache:
            log(f"Vistoria de grade em cache: {len(grade_cache)} par(es) em {grade_path}")
        seeded = seed_grade_cache_from_finished(skipped, grade_cache)
        if seeded:
            save_grade_vistoria(grade_path, grade_cache)
            log(f"Cache de grade: {seeded} par(es) ja prontos inferidos pelo nome da saida (sem reabrir RGB/IR).")

        progress: RunProgress | None = None
        if queue and not args.dry_run:
            queue = prioritize_queue_direto_then_resampled(
                queue,
                cache=grade_cache,
                cache_path=grade_path,
                force=bool(args.force_grade),
            )
            progress = build_run_progress(
                out_dir=out_dir,
                pairs=pairing_pairs,
                skipped=skipped,
                queue=queue,
            )

        ready_now = len(skipped)
        total_pairs = len(pairing_pairs)
        pct_now = (100.0 * ready_now / total_pairs) if total_pairs else 100.0
        _print_scenario_audit(
            all_rows,
            rgb_all_count=len(rgb_all),
            ir_all_count=len(ir_all),
            skipped=skipped,
            queue=queue,
        )
        log(
            f"Andamento ja em disco: {ready_now}/{total_pairs} ({pct_now:.2f}%). "
            "ETA e disco serao recalculados a cada tile gravado."
        )
        _print_pair_plan(pairing_pairs, queue, skipped)
        _print_leftovers(all_rows)
        publish(all_rows, out_dir=out_dir, rgb_dir=rgb_dir, ir_dir=ir_dir, run_id=run_id)
        log("CSV inicial gravado, antes de comecar a fundir. Da para abrir o resumo enquanto roda (melhor copiar o arquivo).")

        if not pairing_pairs:
            log("Nenhum par RGB+IR. Nada foi fundido. Confira os nomes no CSV (rgb_sem_ir / ir_sem_rgb).")
            _print_final(all_rows, out_dir, started)
            return 2

        if args.dry_run:
            log("DRY-RUN terminou. Nenhum GeoTIFF foi gravado.")
            _print_final(all_rows, out_dir, started)
            return 0

        if not queue:
            log("Nada novo para gravar. Todos os pares ja estao em " + str(out_dir))
            _print_final(all_rows, out_dir, started)
            return 0

        proximo = len(skipped) + 1
        n_dir = sum(1 for row in queue if row.get("_fila_alinhamento") == _TIPO_JUNCAO_DIRETOS)
        n_res = sum(1 for row in queue if row.get("_fila_alinhamento") == _TIPO_JUNCAO_RESAMPLED)
        n_fora_q = sum(1 for row in queue if str(row.get("_fora_de_sp") or "").strip() in {"1", "true", "True"})
        log(
            f"Comecando a gravar {len(queue)} tile(s) "
            f"(direto={n_dir}, resampled/outros={len(queue) - n_dir}; resampled na fila={n_res}; "
            f"fora_de_sp na fila={n_fora_q}). "
            f"Contador: {proximo}/{len(pairing_pairs)} ({pct_now:.2f}% ja pronto) "
            f"({len(skipped)} ja estavam em disco e NAO serao refeitos)."
        )
        outcome = _run_queue(
            queue,
            workers=workers,
            run_id=run_id,
            out_dir=out_dir,
            rgb_dir=rgb_dir,
            ir_dir=ir_dir,
            all_rows=all_rows,
            gdal_threads=gdal_threads,
            cache_mb=int(args.cache_mb),
            nir_band=int(args.nir_band),
            already_done=len(skipped),
            grand_total=len(pairing_pairs),
            cobertura=cobertura,
            progress=progress,
            grade_cache=grade_cache,
            grade_cache_path=grade_path,
        )
        _print_final(all_rows, out_dir, started)
        if outcome == "interrompido":
            return 130
        if outcome == "falha":
            return 1
        return 0
    except KeyboardInterrupt:
        log("Interrompido pelo usuario antes da fila. Rode o .bat de novo para continuar.")
        exit_code = 130
        return exit_code
    finally:
        lock.release()


def _disable_windows_console_quick_edit() -> None:
    """Stop CMD Quick Edit from freezing stdout until the window is clicked/resized."""
    if not sys.platform.startswith("win"):
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(ctypes.c_ulong(-10 & 0xFFFFFFFF))  # STD_INPUT_HANDLE
        mode = ctypes.c_uint()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return
        ENABLE_QUICK_EDIT = 0x0040
        ENABLE_EXTENDED_FLAGS = 0x0080
        new_mode = (mode.value | ENABLE_EXTENDED_FLAGS) & ~ENABLE_QUICK_EDIT
        kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass


if __name__ == "__main__":
    _disable_windows_console_quick_edit()
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
        sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass
    with _KeepAwake():
        sys.exit(main())

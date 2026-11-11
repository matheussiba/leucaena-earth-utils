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

A pair is the same filename in both folders (case-insensitive). If the
names differ only by the usual IGC wrappers (``ORTM_RGB_`` / ``ORTM_IR_``,
``ORTO_RGB_`` / ``ORTO_IR_``, ``_ORTO_RGB`` / ``_ORTO_IR``, ``_V1``), they are still paired and the
registry records ``modo_pareamento=nome_normalizado``.

Several products of the same tile are all kept. For each cleaned tile id
the script applies this cascade:

    1. RAW + RAW              -> <ID>_resampled.tif
    2. same family and version-> <ID>_ORTO_resampled.tif, <ID>_ORTM_V1_resampled.tif, ...
    3. RAW paired with a family that has no twin -> <ID>-CEN3-RAW-ORTO_resampled.tif
    4. different families     -> <ID>-CEN4-ORTM-ORTO_resampled.tif
    5. nothing left to pair   -> no GeoTIFF; the file is only listed in the CSV

The output grid is always the RGB grid. When the IR origin does not land on
the same pixels, band 4 is warped with nearest neighbor
(``rasterio.warp.reproject``). RGB bands are copied unchanged.
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

IR_NIR_BAND_INDEX = 1
RASTER_SUFFIXES = (".tif", ".tiff")
TMP_SUFFIX = ".tmp.tif"
LOCK_NAME = "rgbnir_running.lock"

SUMMARY_NAME = "rgbnir_summary.csv"
REGISTRY_NAME = "rgbnir_registry.csv"
DICTIONARY_NAME = "rgbnir_registry_dicionario.csv"

COMPRESS = "DEFLATE"
BLOCK = 512
VERSION = "1.3.0"

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
        "Nome do GeoTIFF, sempre com sufixo _resampled. Cenario 1: ID_resampled.tif. Cenario 2: ID_ORTO_resampled.tif ou ID_ORTM_V1_resampled.tif. Cenario 3: ID-CEN3-RAW-ORTO_resampled.tif. Cenario 4: ID-CEN4-ORTM-ORTO_resampled.tif.",
        "SF-22-X_ORTM_V1_resampled.tif",
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
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes:02d}m {secs:02d}s"


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

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            pid = _read_lock_pid(self.path)
            if pid and _pid_is_running(pid):
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
    """Filename for cenarios 1-4. Cenario 5 does not write a GeoTIFF.

    Every product ends in ``_resampled`` so an aligned RGBNIR is not confused
    with an older file that was rejected when the IR grid did not match.
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
    return f"{base}_resampled.tif"


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
    return row["detalhe"]


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
        ("cenario_1", "Puro com puro. Saida: ID_resampled.tif."),
        ("cenario_2", "Mesma familia e versao. Saida: ID_ORTO_resampled.tif ou ID_ORTM_V1_resampled.tif."),
        ("cenario_3", "Puro de um lado com familia do outro. Saida: ID-CEN3-RAW-ORTO_resampled.tif."),
        ("cenario_4", "Familias diferentes, sem puro sobrando. Saida: ID-CEN4-ORTM-ORTO_resampled.tif."),
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
            _STATUS_ORDER.get(row.get("status", ""), 9),
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
            "Cenario 1. Puro do RGB com puro do IR. Saida ID_resampled.tif.",
        ),
        metric(
            "cenario_2_mesma_familia",
            sum(1 for row in pairs if row.get("cenario") == "2"),
            "Cenario 2. Mesma familia e versao nos dois lados. Saida ID_ORTO_resampled.tif ou ID_ORTM_V1_resampled.tif.",
        ),
        metric(
            "cenario_3_puro_com_familia",
            sum(1 for row in pairs if row.get("cenario") == "3"),
            "Cenario 3. Puro de um lado fechou com uma familia do outro. Saida ID-CEN3-TIPO_RGB-TIPO_IR_resampled.tif.",
        ),
        metric(
            "cenario_4_familias_diferentes",
            sum(1 for row in pairs if row.get("cenario") == "4"),
            "Cenario 4. Familias diferentes, sem puro sobrando. Saida ID-CEN4-TIPO_RGB-TIPO_IR_resampled.tif.",
        ),
        metric(
            "cenario_5_sem_tif",
            sum(1 for row in rows if row.get("cenario") == "5"),
            "Cenario 5. Arquivos que nao formaram par. Nenhum tif foi gerado para eles.",
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
            crs_text = str(rgb_src.crs or "")
            profile.update(
                count=4,
                driver="GTiff",
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
    log(f"Resumo  -> {Path(args.out) / SUMMARY_NAME}")
    log(f"Registro-> {Path(args.out) / REGISTRY_NAME}")
    log(f"Dicionario -> {Path(args.out) / DICTIONARY_NAME}")
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
        out_path = out_dir / row["saida_nome"]
        row["saida_caminho"] = str(out_path)
        restore_previous_fields(row, previous)
        if overwrite or not output_is_done(out_path):
            if out_path.is_file() and out_path.stat().st_size <= 0:
                log(f"Arquivo de saida vazio, vou refazer: {out_path.name}")
                _remove_quiet(out_path)
            row["status"] = "pendente"
            row["notas"] = ""
            queue.append(row)
            continue
        if verify_existing:
            ok, detail = verify_existing_geotiff(out_path, nir_band)
            if not ok:
                invalid_dir.mkdir(parents=True, exist_ok=True)
                moved = invalid_dir / out_path.name
                log(f"Saida invalida ({detail}). Movi {out_path.name} para {moved}")
                try:
                    if moved.exists():
                        moved.unlink()
                    out_path.replace(moved)
                except OSError as exc:
                    log(f"AVISO: nao consegui mover o invalido {out_path}: {exc}")
                row["status"] = "pendente"
                row["detalhe"] = f"Arquivo anterior invalido ({detail}) movido para {moved}."
                row["notas"] = ""
                queue.append(row)
                continue
            log(f"Conferido OK: {out_path.name} ({detail})")
        row["status"] = "pulado_ja_existe"
        if not row["saida_bytes"]:
            try:
                row["saida_bytes"] = str(out_path.stat().st_size)
            except OSError:
                pass
        row["notas"] = ""
        skipped.append(row)
    return skipped, queue


def _print_scenario_audit(rows: list[dict[str, str]]) -> None:
    """Counts come from the rows themselves. Nothing here is a fixed quantity."""
    pairs = [row for row in rows if row.get("tipo_registro") == "par"]
    def ncen(number: str) -> int:
        return sum(1 for row in pairs if row.get("cenario") == number)

    rgb_sem = sum(1 for row in rows if row.get("tipo_registro") == "rgb_sem_ir")
    ir_sem = sum(1 for row in rows if row.get("tipo_registro") == "ir_sem_rgb")
    anomalias = sum(1 for row in rows if row.get("status") == "anomalia")
    log("Auditoria dos cenarios (contada nesta execucao):")
    log(f"  Cenario 1 puro+puro:            {ncen('1')}")
    log(f"  Cenario 2 mesma familia:        {ncen('2')}")
    log(f"  Cenario 3 puro com familia:     {ncen('3')}")
    log(f"  Cenario 4 familias diferentes:  {ncen('4')}")
    log(f"  Cenario 5 sem tif:              {sum(1 for row in rows if row.get('cenario') == '5')}")
    log(f"  {rgb_sem} arquivos RGB sem IR")
    log(f"  {ir_sem} arquivos IR sem RGB")
    log(f"  {anomalias} anomalias (tipo repetido ou nome sem id)")


def _print_pair_plan(pairs: list[dict[str, str]], queue: list[dict[str, str]], skipped: list[dict[str, str]]) -> None:
    total = len(pairs)
    done = len(skipped)
    log(f"Ja prontos em disco (serao pulados): {done}")
    log(f"Ainda faltam gravar nesta execucao: {len(queue)}")
    if total:
        proximo = done + 1 if queue else total
        log(f"Andamento geral: {done}/{total} ja processados. O proximo entra como {proximo}/{total}.")
    preview = skipped[:15]
    for row in preview:
        log(f"  PULA  {row['saida_nome']}  <=  RGB {row['rgb_nome']}  +  IR {row['ir_nome']}")
    if len(skipped) > len(preview):
        log(f"  ... e mais {len(skipped) - len(preview)} arquivo(s) ja pronto(s). A lista completa esta no CSV.")
    preview_queue = queue[:40]
    for offset, row in enumerate(preview_queue, 1):
        pos = done + offset
        log(f"  FILA  [{pos}/{total}] {row['saida_nome']}  <=  RGB {row['rgb_nome']}  +  IR {row['ir_nome']}")
    if len(queue) > len(preview_queue):
        log(f"  ... e mais {len(queue) - len(preview_queue)} na fila. Cada um sera impresso de novo quando comecar.")


def _print_leftovers(rows: list[dict[str, str]]) -> None:
    groups = {
        "rgb_sem_ir": "RGB sem IR correspondente",
        "ir_sem_rgb": "IR sem RGB correspondente",
        "anomalia": "Anomalias (cenario 5)",
        "duplicata_ignorada": "Duplicatas ignoradas",
        "conflito_saida": "Conflitos de nome de saida",
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

    def remember(row: dict[str, str], result: dict, position: int) -> None:
        nonlocal written, failed
        apply_result(row, result, run_id=run_id, out_dir=out_dir)
        if result["status"] == "ok":
            written += 1
        else:
            failed += 1
        publish(all_rows, out_dir=out_dir, rgb_dir=rgb_dir, ir_dir=ir_dir, run_id=run_id)
        done = written + failed
        elapsed = time.time() - started
        if done:
            eta = (elapsed / done) * (total - done)
            eta_txt = fmt_duration(eta)
        else:
            eta_txt = "calculando"
        log(
            f"Progresso {position}/{grand_total} | nesta execucao {done}/{total} | "
            f"gravados {written} | falhas {failed} | "
            f"restam na fila {total - done} | ETA {eta_txt} | decorrido {fmt_duration(elapsed)}"
        )
        log(f"CSV atualizado: {out_dir / SUMMARY_NAME}")

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
                log("Interrompido pelo usuario. O que ja estava pronto foi mantido.")
                log("Rode o .bat de novo para continuar do que falta.")
                return "interrompido"
            remember(row, result, position)
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
        return "interrompido"
    else:
        executor.shutdown(wait=True)
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
        return 2

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

        rgb_hits = scan_rasters(rgb_dir)
        ir_hits = scan_rasters(ir_dir)
        if not rgb_hits:
            log(f"ABORTADO: nenhum .tif/.tiff em {rgb_dir}")
            return 2
        if not ir_hits:
            log(f"ABORTADO: nenhum .tif/.tiff em {ir_dir}")
            return 2

        log("Pareando RGB com IR pelo nome do arquivo...")
        pairing = pair_rasters(rgb_hits, ir_hits)
        previous = load_previous_registry(out_dir / REGISTRY_NAME)
        if previous:
            log(f"Registro anterior carregado ({len(previous)} saidas) para preservar data e duracao.")
        else:
            log("Nao havia registro anterior. Este CSV comeca do zero.")

        all_rows = pairing.all_rows()
        for row in all_rows:
            if row["saida_nome"]:
                row["saida_caminho"] = str(out_dir / row["saida_nome"])
            row["run_id_desta_execucao"] = run_id

        skipped, queue = _assign_resume_status(
            pairing.pairs,
            out_dir=out_dir,
            previous=previous,
            overwrite=args.overwrite,
            verify_existing=args.verify_existing,
            nir_band=int(args.nir_band),
        )
        for row in all_rows:
            row["notas"] = compose_note(row, out_dir)

        _print_pair_plan(pairing.pairs, queue, skipped)
        _print_scenario_audit(all_rows)
        _print_leftovers(all_rows)
        publish(all_rows, out_dir=out_dir, rgb_dir=rgb_dir, ir_dir=ir_dir, run_id=run_id)
        log("CSV inicial gravado, antes de comecar a fundir. Da para abrir o resumo enquanto roda (melhor copiar o arquivo).")

        if not pairing.pairs:
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
        log(
            f"Comecando a gravar {len(queue)} tile(s). "
            f"Contador: {proximo}/{len(pairing.pairs)} "
            f"({len(skipped)} ja estavam em disco)."
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
            grand_total=len(pairing.pairs),
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


def _mostrar_area_de_trabalho():
    """Simula o atalho Win+D para mostrar a area de trabalho."""
    if not sys.platform.startswith("win"):
        return
    VK_LWIN = 0x5B
    VK_D = 0x44
    KEYEVENTF_KEYUP = 0x0002
    user32 = ctypes.windll.user32
    user32.keybd_event(VK_LWIN, 0, 0, 0)
    user32.keybd_event(VK_D, 0, 0, 0)
    user32.keybd_event(VK_D, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)


if __name__ == "__main__":
    _mostrar_area_de_trabalho()
    with _KeepAwake():
        sys.exit(main())

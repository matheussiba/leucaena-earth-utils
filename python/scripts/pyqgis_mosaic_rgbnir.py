
# =============================================================================
# MOSAICO RGB-NIR OTIMIZADO - QGIS / GDAL
#
# - Le os tiles do campo NOMENC_10K do GeoPackage
# - Cria mosaico RGB-NIR de 4 bandas
# - Prioriza o ultimo tile na ordem alfabetica nas sobreposicoes
# - Preserva a resolucao original de 25 cm
# - Utiliza compressao DEFLATE com nivel 3
# - Utiliza multiplos nucleos quando suportado
# - Cria overviews externos nos niveis 2, 4, 8, 16 e 32
# - Mostra progresso, tempos, arquivos e erros
#
# Executar pelo Editor do Console Python do QGIS.
# =============================================================================

from pathlib import Path
from time import perf_counter, strftime
from osgeo import gdal, ogr, osr
from qgis.core import QgsProject, QgsRasterLayer
import traceback
import sys
import gc

# =============================================================================
# 1. CONFIGURACOES
# =============================================================================

GPKG_PATH = (
    r"G:\My Drive\PHD\02-Tese\04-Processamento"
    r"\msb-phd-leucaena-sp.gpkg"
)

LAYER_NAME = "articulacao_aoi_v2_centro_piracicaba"
FIELD_NAME = "NOMENC_10K"

TIF_FOLDER = Path(
    r"C:\00_DATASETS_AI\260515-piracicaba-aoi\opt\rgbnir"
)

# Pasta nova para esta execucao
OUTPUT_FOLDER = TIF_FOLDER.parent / "mosaico_rgbnir_otimizado"

VRT_PATH = OUTPUT_FOLDER / "mosaico_rgbnir_piracicaba.vrt"
OUTPUT_TIF = OUTPUT_FOLDER / "mosaico_rgbnir_piracicaba.tif"
OVR_PATH = Path(str(OUTPUT_TIF) + ".ovr")

# Overviews sem o nivel 64
OVERVIEW_LEVELS = [2, 4, 8, 16, 32]

# Configuracoes de processamento
NUM_THREADS = "ALL_CPUS"
COMPRESSION = "DEFLATE"
COMPRESSION_LEVEL = 3
BLOCK_SIZE = 512

# Cache GDAL em MB. Reduza para 512 se tiver pouca RAM.
GDAL_CACHE_MB = 1024

# NoData original: 255 nas quatro bandas
SOURCE_NODATA = "255 255 255 255"

# True = ultimo arquivo alfabetico prevalece nas sobreposicoes
LAST_TILE_WINS = True

# =============================================================================
# 2. INICIALIZACAO E LOGS
# =============================================================================

START_TIME = perf_counter()


def log(message):
    elapsed = perf_counter() - START_TIME
    clock = strftime("%H:%M:%S")
    print(
        f"[{clock} | +{elapsed:.1f}s] {message}",
        flush=True
    )


def duration(seconds):
    seconds = max(0.0, float(seconds))

    if seconds < 60:
        return f"{seconds:.1f} s"

    minutes, secs = divmod(seconds, 60)

    if minutes < 60:
        return f"{int(minutes)} min {secs:.1f} s"

    hours, minutes = divmod(int(minutes), 60)
    return f"{hours} h {minutes} min"


def make_progress(label):
    return {
        "label": label,
        "start": perf_counter(),
        "last_milestone": -1
    }


def progress_callback(complete, message, data):
    percent = int(complete * 100)
    milestone = min(100, (percent // 5) * 5)

    if milestone != data["last_milestone"]:
        elapsed = perf_counter() - data["start"]
        log(
            f"{data['label']}: {percent}% "
            f"| tempo da etapa: {duration(elapsed)}"
        )
        data["last_milestone"] = milestone

    return 1


def run_gdal_config():
    # Configuracoes globais GDAL
    gdal.UseExceptions()

    gdal.SetConfigOption("GDAL_NUM_THREADS", NUM_THREADS)
    gdal.SetConfigOption("GDAL_CACHEMAX", str(GDAL_CACHE_MB))
    gdal.SetConfigOption("GDAL_TIFF_INTERNAL_MASK", "YES")

    # Configuracoes dos overviews
    gdal.SetConfigOption("COMPRESS_OVERVIEW", COMPRESSION)
    gdal.SetConfigOption("PREDICTOR_OVERVIEW", "2")
    gdal.SetConfigOption("ZLEVEL_OVERVIEW", str(COMPRESSION_LEVEL))
    gdal.SetConfigOption("BIGTIFF_OVERVIEW", "IF_SAFER")
    gdal.SetConfigOption("GDAL_TIFF_OVR_BLOCKSIZE", str(BLOCK_SIZE))

    log(f"Threads configuradas: {NUM_THREADS}")
    log(f"Cache GDAL: {GDAL_CACHE_MB} MB")
    log(f"Compressao: {COMPRESSION}, nivel {COMPRESSION_LEVEL}")


# =============================================================================
# 3. LER O GEOPACKAGE
# =============================================================================

def read_tile_names():
    log("ETAPA 1/7 - Abrindo GeoPackage")

    if not Path(GPKG_PATH).is_file():
        raise FileNotFoundError(f"GeoPackage nao encontrado: {GPKG_PATH}")

    gpkg = ogr.Open(GPKG_PATH, 0)

    if gpkg is None:
        raise RuntimeError(f"Nao foi possivel abrir: {GPKG_PATH}")

    layer = gpkg.GetLayerByName(LAYER_NAME)

    if layer is None:
        available = [
            gpkg.GetLayerByIndex(i).GetName()
            for i in range(gpkg.GetLayerCount())
        ]
        gpkg = None
        raise RuntimeError(
            f"Camada nao encontrada: {LAYER_NAME}\n"
            f"Camadas disponiveis: {available}"
        )

    field_index = layer.GetLayerDefn().GetFieldIndex(FIELD_NAME)

    if field_index < 0:
        gpkg = None
        raise RuntimeError(f"Campo nao encontrado: {FIELD_NAME}")

    names = set()
    count = 0

    for feature in layer:
        count += 1
        value = feature.GetField(field_index)

        if value is None:
            continue

        name = Path(str(value).strip()).name

        if not name:
            continue

        if Path(name).suffix.lower() not in (".tif", ".tiff"):
            name += ".tif"

        names.add(name.lower())

        if count % 10000 == 0:
            log(f"Registros lidos do GeoPackage: {count}")

    gpkg = None

    if not names:
        raise RuntimeError(f"Nenhum tile encontrado em {FIELD_NAME}")

    log(f"Registros examinados: {count}")
    log(f"Tiles unicos identificados: {len(names)}")

    return names


# =============================================================================
# 4. LOCALIZAR TIFFS
# =============================================================================

def find_tiffs(tile_names):
    log("ETAPA 2/7 - Localizando arquivos TIFF")

    if not TIF_FOLDER.is_dir():
        raise FileNotFoundError(f"Pasta nao encontrada: {TIF_FOLDER}")

    # Indexa apenas arquivos TIFF da pasta indicada
    index = {}

    for p in TIF_FOLDER.iterdir():
        if p.is_file() and p.suffix.lower() in (".tif", ".tiff"):
            index[p.name.lower()] = p

    missing = sorted(tile_names - set(index))

    files = [
        index[name]
        for name in sorted(tile_names)
        if name in index
    ]

    log(f"TIFFs localizados: {len(files)}")
    log(f"TIFFs ausentes: {len(missing)}")

    if missing:
        for name in missing:
            log(f"AUSENTE: {name}")

        raise RuntimeError(
            "Ha TIFFs ausentes. O mosaico foi interrompido "
            "para nao gerar um resultado incompleto."
        )

    if not files:
        raise RuntimeError("Nenhum TIFF correspondente encontrado.")

    total_bytes = sum(p.stat().st_size for p in files)
    log(f"Tamanho total dos TIFFs de entrada: {total_bytes / 1024**3:.2f} GB")

    for i, p in enumerate(files, 1):
        log(f"Tile {i}/{len(files)}: {p.name}")

    return files


# =============================================================================
# 5. VALIDAR BANDAS, CRS, RESOLUCAO E NODATA
# =============================================================================

def validate_tiffs(files):
    log("ETAPA 3/7 - Validando os rasters")

    ref = gdal.Open(str(files[0]), gdal.GA_ReadOnly)

    if ref is None:
        raise RuntimeError(f"Nao foi possivel abrir: {files[0]}")

    ref_bands = ref.RasterCount
    ref_gt = ref.GetGeoTransform()
    ref_projection = ref.GetProjection()

    if ref_bands != 4:
        ref = None
        raise RuntimeError(
            f"Esperadas 4 bandas RGB-NIR; encontradas {ref_bands}."
        )

    ref_srs = osr.SpatialReference()
    ref_srs.ImportFromWkt(ref_projection)

    log(f"Raster de referencia: {files[0].name}")
    log(f"Bandas: {ref_bands}")
    log(f"Dimensoes: {ref.RasterXSize} x {ref.RasterYSize}")
    log(f"Resolucao: {ref_gt[1]} x {ref_gt[5]} m")

    # Confere NoData do raster de referencia
    ref_nodata = [
        ref.GetRasterBand(i).GetNoDataValue()
        for i in range(1, ref_bands + 1)
    ]

    log(f"NoData por banda: {ref_nodata}")
    ref = None

    for i, path in enumerate(files, 1):
        ds = gdal.Open(str(path), gdal.GA_ReadOnly)

        if ds is None:
            raise RuntimeError(f"TIFF ilegivel: {path}")

        if ds.RasterCount != ref_bands:
            ds = None
            raise RuntimeError(f"Numero de bandas diferente: {path}")

        srs = osr.SpatialReference()
        srs.ImportFromWkt(ds.GetProjection())

        if not bool(ref_srs.IsSame(srs)):
            ds = None
            raise RuntimeError(f"CRS diferente: {path}")

        gt = ds.GetGeoTransform()

        if (
            abs(gt[1] - ref_gt[1]) > 1e-8
            or abs(gt[5] - ref_gt[5]) > 1e-8
            or abs(gt[2] - ref_gt[2]) > 1e-8
            or abs(gt[4] - ref_gt[4]) > 1e-8
        ):
            ds = None
            raise RuntimeError(f"Resolucao ou orientacao diferente: {path}")

        ds = None

        if i % 25 == 0 or i == len(files):
            log(f"Validacao: {i}/{len(files)} tiles")

    log("Validacao concluida.")


# =============================================================================
# 6. CRIAR VRT
# =============================================================================

def create_vrt(files):
    log("ETAPA 4/7 - Criando mosaico virtual VRT")
    log("O VRT referencia os TIFFs; nao grava todos os pixels.")
    log(f"Destino: {VRT_PATH}")

    vrt_files = files if LAST_TILE_WINS else list(reversed(files))

    options = gdal.BuildVRTOptions(
        resolution="highest",
        resampleAlg="nearest",
        srcNodata=SOURCE_NODATA,
        VRTNodata=SOURCE_NODATA
    )

    t0 = perf_counter()

    vrt = gdal.BuildVRT(
        str(VRT_PATH),
        [str(p) for p in vrt_files],
        options=options,
        callback=progress_callback,
        callback_data=make_progress("Criacao do VRT")
    )

    if vrt is None:
        raise RuntimeError("Falha ao criar o VRT.")

    log(f"Dimensoes do VRT: {vrt.RasterXSize} x {vrt.RasterYSize}")
    log(f"Bandas: {vrt.RasterCount}")
    log(f"Resolucao X: {vrt.GetGeoTransform()[1]} m")
    log(f"Resolucao Y: {vrt.GetGeoTransform()[5]} m")

    vrt = None

    log(f"VRT concluido em {duration(perf_counter() - t0)}")


# =============================================================================
# 7. GRAVAR GEOTIFF
# =============================================================================

def create_geotiff():
    log("ETAPA 5/7 - Gravando GeoTIFF final")
    log("ATENCAO: esta etapa pode demorar bastante.")
    log(f"Destino: {OUTPUT_TIF}")
    log(f"Compressao: {COMPRESSION}")
    log(f"Nivel: {COMPRESSION_LEVEL}")
    log(f"Threads: {NUM_THREADS}")

    t0 = perf_counter()

    options = gdal.TranslateOptions(
        format="GTiff",
        creationOptions=[
            "TILED=YES",
            f"BLOCKXSIZE={BLOCK_SIZE}",
            f"BLOCKYSIZE={BLOCK_SIZE}",
            f"COMPRESS={COMPRESSION}",
            f"ZLEVEL={COMPRESSION_LEVEL}",
            "PREDICTOR=2",
            "BIGTIFF=IF_SAFER",
            f"NUM_THREADS={NUM_THREADS}"
        ],
        callback=progress_callback,
        callback_data=make_progress("Gravacao do GeoTIFF")
    )

    ds = gdal.Translate(
        str(OUTPUT_TIF),
        str(VRT_PATH),
        options=options
    )

    if ds is None:
        raise RuntimeError("Falha ao gravar o GeoTIFF.")

    ds.FlushCache()

    log(f"Dimensoes: {ds.RasterXSize} x {ds.RasterYSize}")
    log(f"Bandas: {ds.RasterCount}")
    log(f"Resolucao: {ds.GetGeoTransform()[1]} m")

    ds = None
    gc.collect()

    if not OUTPUT_TIF.exists():
        raise RuntimeError("O GeoTIFF de saida nao foi encontrado.")

    size_gb = OUTPUT_TIF.stat().st_size / 1024**3

    log(f"Tamanho final do TIFF: {size_gb:.2f} GB")
    log(f"GeoTIFF concluido em {duration(perf_counter() - t0)}")


# =============================================================================
# 8. OVERVIEWS EXTERNOS
# =============================================================================

def create_overviews():
    log("ETAPA 6/7 - Gerando overviews externos")
    log(f"Niveis: {OVERVIEW_LEVELS}")
    log("Algoritmo: AVERAGE")
    log("Compressao dos overviews: DEFLATE")
    log("O arquivo .ovr pode levar tempo para ser criado.")

    gdal.SetConfigOption("COMPRESS_OVERVIEW", COMPRESSION)
    gdal.SetConfigOption("PREDICTOR_OVERVIEW", "2")
    gdal.SetConfigOption("ZLEVEL_OVERVIEW", str(COMPRESSION_LEVEL))
    gdal.SetConfigOption("BIGTIFF_OVERVIEW", "IF_SAFER")
    gdal.SetConfigOption("GDAL_TIFF_OVR_BLOCKSIZE", str(BLOCK_SIZE))
    gdal.SetConfigOption("GDAL_NUM_THREADS", NUM_THREADS)

    ds = gdal.Open(str(OUTPUT_TIF), gdal.GA_ReadOnly)

    if ds is None:
        raise RuntimeError(f"Nao foi possivel abrir: {OUTPUT_TIF}")

    t0 = perf_counter()

    result = ds.BuildOverviews(
        "AVERAGE",
        OVERVIEW_LEVELS,
        callback=progress_callback,
        callback_data=make_progress("Geracao de overviews")
    )

    ds = None
    gc.collect()

    if result not in (0, None):
        raise RuntimeError(f"BuildOverviews falhou: codigo {result}")

    log(f"Overviews concluidos em {duration(perf_counter() - t0)}")

    if OVR_PATH.exists():
        log(f"Arquivo OVR: {OVR_PATH}")
        log(f"Tamanho OVR: {OVR_PATH.stat().st_size / 1024**3:.2f} GB")
    else:
        log(
            "Aviso: o arquivo .ovr externo nao apareceu. "
            "Verifique os metadados do TIFF para confirmar os overviews."
        )


# =============================================================================
# 9. CARREGAR NO QGIS E RESUMO
# =============================================================================

def load_result():
    log("ETAPA 7/7 - Verificando e carregando o resultado no QGIS")

    raster = QgsRasterLayer(str(OUTPUT_TIF), OUTPUT_TIF.stem)

    if not raster.isValid():
        log("AVISO: nao foi possivel carregar o mosaico no QGIS.")
        return

    QgsProject.instance().addMapLayer(raster)
    log("Mosaico adicionado ao projeto QGIS.")


# =============================================================================
# 10. EXECUCAO
# =============================================================================

def main():
    print("\n" + "=" * 76)
    print("MOSAICO RGB-NIR OTIMIZADO - INICIO")
    print("=" * 76)

    run_gdal_config()

    OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

    # Evita sobrescrever resultados anteriores
    existing = [
        p for p in (VRT_PATH, OUTPUT_TIF, OVR_PATH)
        if p.exists()
    ]

    if existing:
        raise FileExistsError(
            "Existem arquivos de saida desta execucao:\n"
            + "\n".join(str(p) for p in existing)
            + "\nAltere OUTPUT_FOLDER para uma pasta nova antes de executar."
        )

    names = read_tile_names()
    files = find_tiffs(names)

    validate_tiffs(files)
    create_vrt(files)
    create_geotiff()
    create_overviews()
    load_result()

    print("\n" + "=" * 76)
    print("PROCESSAMENTO CONCLUIDO COM SUCESSO")
    print("=" * 76)
    print(f"Tiles utilizados: {len(files)}")
    print(f"Mosaico: {OUTPUT_TIF}")
    print(f"VRT: {VRT_PATH}")
    print(f"Overviews: {OVR_PATH}")
    print(f"Tempo total: {duration(perf_counter() - START_TIME)}")
    print("=" * 76)


try:
    main()

except Exception as exc:
    log(f"ERRO: {exc}")
    traceback.print_exc()
    log("O processamento foi interrompido. Confira a mensagem acima.")

finally:
    log("Fim da execucao.")

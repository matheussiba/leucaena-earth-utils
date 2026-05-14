# ==============================================================================
# Download Brazilian state boundaries (IBGE / geobr) and save as GeoPackage
# ==============================================================================
#
# What it does
#   Fetches all state polygons for Brazil (year 2020) via {geobr} and writes
#   them to a GeoPackage path you configure below.
#
# Requirements
#   - R (>= 4.0 recommended)
#   - install.packages(c("geobr", "sf"))
#
# How to run
#   - In RStudio: open this script, set `output_path`, Source.
#   - CLI:  Rscript r/scripts/r_download_brazil_states_geobr_ibge2020.R
#
# Note
#   Edit `output_path` to a folder on your machine. The sample path below is
#   a placeholder from the original author environment.
# ==============================================================================

library(geobr)
library(sf)

estados_br <- read_state(code_state = "all", year = 2020)

# Optional quick map
plot(estados_br$geom)

# --- configure output (required) ---
output_path <- "H:/My Drive/PHD/02-Tese/02-data/adote-uma-leucena/v1-LEUCENA MAPPING/estados_brasil_2020.gpkg"

# Example: use an env var instead of hard-coding
# output_path <- Sys.getenv("BRAZIL_STATES_GPKG", unset = "C:/data/estados_brasil_2020.gpkg")

st_write(estados_br, output_path)

message("Written: ", output_path)

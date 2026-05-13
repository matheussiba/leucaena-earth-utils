# 1. Carregue as bibliotecas necessárias
library(geobr)
library(sf) 

# 2. Baixe todos os estados do Brasil utilizando o ano mais recente disponível (2020)
estados_br <- read_state(code_state = "all", year = 2020)

# Para visualizar os dados (opcional)
plot(estados_br$geom)

# 3. Definir o caminho completo (incluindo o nome do arquivo final)
caminho_salvar <- "H:/My Drive/PHD/02-Tese/02-data/adote-uma-leucena/v1-LEUCENA MAPPING/estados_brasil_2020.gpkg"

# 4. Salvar o arquivo
st_write(estados_br, caminho_salvar)
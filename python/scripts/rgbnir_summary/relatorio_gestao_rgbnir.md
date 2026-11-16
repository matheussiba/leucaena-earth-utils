# Relatório de gestão de dados — RGBNIR (IGC/SP)

**Data do snapshot:** 16/11/2026 (UTC `2026-11-16T18:34:11Z`)  
**Fonte:** CSVs em `python/scripts/rgbnir_summary/` (espelho do processamento em lab)  
**Script:** `build_rgbnir_from_rgb_ir.py` 1.7.0  
**Objetivo do pipeline:** juntar ortofotos RGB (3 bandas) + IR (banda NIR) em GeoTIFF **RGBNIR de 4 bandas**, só para tiles que intersectam São Paulo.

---

## 1. Em uma frase

Já há **~39% dos pares** fundidos em disco (**3 451 / 8 860**); restam **5 409** GeoTIFF a gravar. As pastas de origem **não são alteradas**; a saída vai para `G:\RGBNIR`, com planilhas de controle atualizadas a cada tile (retomável após interrupção).

---

## 2. Arquitetura de dados (lab)

| Papel | Caminho | Observação |
|--------|---------|------------|
| Origem RGB | `E:\RGB` | Somente leitura |
| Origem IR | `F:\IR` | Somente leitura |
| Saída RGBNIR | `G:\RGBNIR` | GeoTIFF 4 bandas + CSVs de controle |
| Filtro espacial | polígonos SP por EPSG (`sp-bounds`) | Tile fora de SP ou sem CRS → ignorado |

**Receita das bandas:** 1=Red, 2=Green, 3=Blue (do RGB); 4=NIR (banda 1 do IR). A banda 4 **não** é alfa.

---

## 3. Andamento da produção

| Métrica | Valor |
|---------|------:|
| Pares RGB+IR (cenários 1–4) | **8 860** |
| Já prontos em disco | **3 451** (**38,95%**) |
| Ainda faltam gravar | **5 409** |
| Gravados na última execução registrada | 48 |
| Pulados (já existiam) | 3 403 |
| Falhas na última execução | **0** |

Volume já contabilizado nos produtos prontos (quando `saida_bytes` está preenchido): **~3,64 TB** em `G:\RGBNIR`.

---

## 4. Inventário de entrada ≠ meta de saída (a desconexão 9k → 8k)

Há **dois totais diferentes** e eles **não devem ser iguais**.

### A) Acervo de entrada (arquivos RGB e IR no lab)

| Item | Contagem | Volume |
|------|--------:|-------:|
| Arquivos RGB | 9 323 | ~15,9 TB |
| Arquivos IR | 9 313 | ~16,9 TB |
| **Soma (entrada)** | **18 636** | **~32,8 TB (~33 TB)** |

Esse **18 636** é o mesmo total da vistoria SP (cada raster de origem é avaliado uma vez):

| Resultado da vistoria SP | Qtd |
|--------------------------|----:|
| Dentro de SP | 17 943 |
| Fora de SP | 573 |
| Sem localização (sem CRS / bounds) | 120 |
| **Soma** | **18 636** |

Conferência: `9 323 + 9 313 = 18 636` e `17 943 + 573 + 120 = 18 636`.

### B) Meta de produção do script (GeoTIFF RGBNIR)

O script **não** gera um RGBNIR por arquivo de entrada. Ele gera **um GeoTIFF por par** RGB+IR dos cenários 1–4.

| Destino no registro | Qtd | Gera TIFF? |
|---------------------|----:|:----------:|
| Pares (cenários 1–4) | **8 860** | sim |
| RGB sem IR | 15 | não |
| IR sem RGB | 112 | não |
| Anomalias | 96 | não |
| Fora de SP | 573 | não |
| Sem localização | 120 | não |
| **Soma (linhas do registro)** | **9 776** | — |

Conferência do “sem TIFF” (cenário 5 + residual): `15 + 112 + 96 = 223`.  
Conferência do andamento: `3 451 prontos + 5 409 faltam = 8 860`.

### Por que parece “9 mil” mas a fila é “8 mil”

| Confusão comum | Número | O que é de verdade |
|----------------|-------:|--------------------|
| “Temos ~9k tiles” | ~9 300 RGB ou ~9 300 IR | Contagem de **arquivos de origem** (um lado) |
| “São ~18k rasters” | 18 636 | RGB **+** IR somados (entrada bruta) |
| “O script vai gerar ~9k” | — | **Incorreto** |
| **Meta real de saída** | **8 860** | Pares válidos após filtro SP + pareamento |

Em outras palavras: o inventário de gestão fala em **~9k por pasta / ~18k arquivos / ~33 TB**.  
A produção fala em **8 860 produtos RGBNIR**. A diferença (~693 linhas do registro que não são par, e o fato de um arquivo de origem poder entrar em no máximo um papel no pareamento) é esperada — não é erro de contagem.

Tempo acumulado de gravação (amostra com duração no CSV): ~**114 h** em 2 028 tiles (~**202 s**/tile). Volume já em `G:\RGBNIR` (prontos com `saida_bytes`): ~**3,64 TB**. Conflitos de nome / duplicatas ignoradas: **0**.

---

## 6. Lógica de pareamento (cenários 1–4) — com exemplos

No acervo IGC o **mesmo tile** pode ter vários produtos: arquivo “puro” (RAW, sem sufixo ORTO/ORTM) e versões processadas (`_ORTO`, `ORTM_…_V1`, `_R1`, etc.). O script **não descarta** essas variantes: classifica cada arquivo e aplica uma **cascata** para formar o máximo de pares úteis. Cada par vira **um** GeoTIFF RGBNIR (nome diferente).

Vocabulário rápido:

- **RAW** = nome “limpo”, sem família ORTO/ORTM/versão (ex.: `SF-22-Z-A-II-2-SE-C.tif`)
- **Família** = ORTO, ORTM, R1, V1… (ex.: `…_ORTO-RGB.tif`, `ORTM_IR_…_V2.tif`)
- **Grade / `-resampled`** = se o IR não estiver na mesma grade do RGB, a banda NIR é alinhada (nearest) e o nome da saída ganha `-resampled`

### Resumo numérico

| Cenário | Ideia | Pares |
|---------|--------|------:|
| **1** | RAW + RAW | 4 532 |
| **2** | Mesma família e mesma versão nos dois lados | 3 987 |
| **3** | RAW de um lado com família do outro (sem gêmeo) | 156 |
| **4** | Famílias diferentes entre RGB e IR | 185 |
| **5** | Não forma par → **não gera TIFF** | 223 |

### Cenário 1 — puro com puro

Os dois arquivos são RAW (mesmo id de tile, sem ORTO/ORTM). Saída: `ID.tif` (ou `ID-resampled.tif` se a grade diferir).

| | Exemplo real do registro |
|--|--|
| RGB | `SF-22-Z-A-II-2-SE-C.tif` (RAW) |
| IR | `SF-22-Z-A-II-2-SE-C.tif` (RAW) |
| Saída | `G:\RGBNIR\SF-22-Z-A-II-2-SE-C.tif` |

### Cenário 2 — mesma família e versão

RGB e IR são o “mesmo produto” (ex. ambos ORTO, ou ambos ORTM_V1). Saída: `ID_ORTO.tif`, `ID_ORTM_V1.tif`, etc.

| | Exemplo real do registro |
|--|--|
| RGB | `SF-22-Y-B-I-2-NE-F_ORTO-RGB.tif` (ORTO) |
| IR | `SF-22-Y-B-I-2-NE-F_ORTO-IR.tif` (ORTO) |
| Saída | `G:\RGBNIR\SF-22-Y-B-I-2-NE-F_ORTO.tif` |

### Cenário 3 — puro de um lado × família do outro

Sobrou um RAW sem gêmeo RAW, e do outro lado há uma família sem par da mesma família. O script **ainda junta** e marca no nome com `CEN3`.

| | Exemplo real do registro |
|--|--|
| RGB | `SF-22-X-C-III-3-NE-E_R1.tif` (R1) |
| IR | `SF-22-X-C-III-3-NE-E.tif` (RAW) |
| Saída | `G:\RGBNIR\SF-22-X-C-III-3-NE-E-CEN3-R1-RAW-resampled.tif` |

(Neste exemplo a grade diferiu → `-resampled`.)

### Cenário 4 — famílias diferentes

Não há RAW sobrando para fechar o cen. 3; restam produtos de **famílias distintas** (ex. R1 × ORTM_V2). Também gera TIFF, com `CEN4` no nome.

| | Exemplo real do registro |
|--|--|
| RGB | `SF-22-Z-A-III-2-SE-D_R1.tif` (R1) |
| IR | `ORTM_IR_SF-22-Z-A-III-2-SE-D_V2.tif` (ORTM_V2) |
| Saída | `G:\RGBNIR\SF-22-Z-A-III-2-SE-D-CEN4-R1-ORTM_V2.tif` |

### Cenário 5 — sem TIFF

Arquivo sem parceiro, tipo repetido no mesmo tile, ou nome sem id utilizável. Fica só no CSV (`sem_par` / `anomalia`); **nenhum** GeoTIFF é gravado.

### Nota sobre `-resampled` e CRS

Nos produtos já prontos: **1 479** saídas com `resampled` no nome. CRS preenchido na amostra do registro: **EPSG:31982**. Tipos mais frequentes nos pares: **RAW** e **ORTO**; também ORTM / ORTM_V1 / revisões.

---

## 7. Controles de gestão (o que o professor pode olhar)

1. **`rgbnir_summary.csv`** — painel de métricas (andamento, cenários, faltas).  
2. **`rgbnir_registry.csv`** — uma linha por par ou arquivo residual (caminho, status, tamanho, duração, erro).  
3. **`rgbnir_registry_dicionario.csv`** — legenda das colunas e dos status.  
4. **`rgbnir_sp_vistoria.csv`** — cache da decisão dentro/fora de SP (evita reabrir milhares de cabeçalhos).  
5. **Retomada:** arquivo final só após `.tmp.tif` completo; reexecução pula o que já está em `G:\RGBNIR`.  
6. **Origens imutáveis:** `E:\RGB` e `F:\IR` não são modificadas pelo script.

---

## 8. Mensagens úteis para a reunião

- **Entrada ~9k RGB + ~9k IR (~33 TB)**; **saída planejada = 8 860** RGBNIR — não 9k.  
- Pipeline **reprodutível e retomável**, com auditoria tile a tile.  
- Escopo explícito: **apenas SP**; fora de SP e sem CRS ficam documentados, não “somem”.  
- Completude RGB↔IR quase fechada (poucos sem par: 15 + 112; cenário 5 total 223).  
- Produção em andamento: **~39%** dos **8 860** pares; fila restante **5 409**, sem falhas na última rodada registrada.  
- Saída RGBNIR já na casa de **~3–4 TB** e crescendo.

---

*Gerado a partir dos CSVs locais desta pasta. Números refletem o snapshot acima; uma nova execução do `.bat` no lab atualiza os CSV em `G:\RGBNIR`.*

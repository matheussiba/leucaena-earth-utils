import sqlite3
from pathlib import Path

p = Path(r"C:\Users\mathe\OneDrive\Documents\0-GITHUB\leucaena-earth-utils\data\articulacao_igc_sp\articulacao-igc-merged-spatialindex-sirgas2000.gpkg")
c = sqlite3.connect(p)
print("TABLES")
for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    print(r[0])
print("COLS")
layer = "articulacao-igc-merged-spatialindex-sirgas2000"
for r in c.execute(f'PRAGMA table_info("{layer}")'):
    print(r)
print("SAMPLE")
cols = [r[1] for r in c.execute(f'PRAGMA table_info("{layer}")')]
want = [x for x in cols if x.lower() in {"bloco", "layer", "nomenc_2k", "nomenc_5k", "nomenc_10k", "path", "overlap", "msb_use"} or "bloco" in x.lower() or "block" in x.lower()]
print("interesting", want)
q = ", ".join(f'"{x}"' for x in want)
if q:
    rows = c.execute(f'SELECT {q} FROM "{layer}" LIMIT 8').fetchall()
    for row in rows:
        print(row)
print("N", c.execute(f'SELECT COUNT(*) FROM "{layer}"').fetchone()[0])
if "BLOCO" in cols or "bloco" in cols:
    col = "BLOCO" if "BLOCO" in cols else "bloco"
    print("BLOCOS")
    for r in c.execute(f'SELECT "{col}", COUNT(*) FROM "{layer}" GROUP BY "{col}" ORDER BY 2 DESC LIMIT 40'):
        print(r)

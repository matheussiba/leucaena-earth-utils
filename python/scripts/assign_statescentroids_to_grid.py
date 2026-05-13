from qgis.core import (
    QgsProject,
    QgsField,
    QgsSpatialIndex
)
from PyQt5.QtCore import QVariant
import time

start_time = time.time()

print("🚀 Iniciando centroid assignment...")

# ================================
# LOAD LAYERS
# ================================
grid_layer = QgsProject.instance().mapLayersByName("grid_mix_br_leucaenaearth-prep")[0]
states_layer = QgsProject.instance().mapLayersByName("states_br_ibge_2020")[0]

total = grid_layer.featureCount()

print(f"📦 Grid: {total}")
print(f"🗺️ States: {states_layer.featureCount()}")

# ================================
# ADD FIELD
# ================================
field_name = "state_centroid"

if field_name not in [f.name() for f in grid_layer.fields()]:
    print("➕ Criando campo...")
    grid_layer.dataProvider().addAttributes([QgsField(field_name, QVariant.String)])
    grid_layer.updateFields()

field_index = grid_layer.fields().indexOf(field_name)

# ================================
# CACHE STATES
# ================================
print("⚡ Cache estados...")
state_feats = {f.id(): f for f in states_layer.getFeatures()}

# ================================
# SPATIAL INDEX
# ================================
index = QgsSpatialIndex(states_layer.getFeatures())

# ================================
# PROGRESS
# ================================
def should_print(i):
    if i <= 10:
        return i % 2 == 0
    elif i <= 100:
        return i % 10 == 0
    else:
        return i % 100 == 0 or i == total

# ================================
# PROCESS
# ================================
updates = {}

print("🔄 Processando centroides...")

for i, grid_feat in enumerate(grid_layer.getFeatures(), 1):
    geom = grid_feat.geometry()
    
    if geom is None or geom.isEmpty():
        updates[grid_feat.id()] = {field_index: None}
        continue

    centroid = geom.centroid()
    
    # busca candidatos
    candidate_ids = index.intersects(centroid.boundingBox())
    
    found_state = None

    for fid in candidate_ids:
        state_feat = state_feats[fid]
        
        if centroid.within(state_feat.geometry()):
            val = state_feat["abbrev_state"]
            if val:
                found_state = val.lower()
                break  # só um estado

    updates[grid_feat.id()] = {field_index: found_state}

    if should_print(i):
        print(f"⏳ {i}/{total} ({int(i/total*100)}%)")

# ================================
# APPLY
# ================================
print("💾 Salvando...")
ok = grid_layer.dataProvider().changeAttributeValues(updates)

if ok:
    print("✅ Concluído")
else:
    print("❌ Erro ao salvar")

print(f"🏁 Tempo: {round(time.time() - start_time, 2)}s")
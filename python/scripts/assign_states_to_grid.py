from qgis.core import (
    QgsProject,
    QgsField,
    QgsSpatialIndex
)
from PyQt5.QtCore import QVariant
import time

start_time = time.time()

print("🚀 Iniciando script...")

# ================================
# LOAD LAYERS
# ================================
grid_layer = QgsProject.instance().mapLayersByName("grid_mix_br_leucaenaearth-prep")[0]
states_layer = QgsProject.instance().mapLayersByName("states_br_ibge_2020")[0]

total = grid_layer.featureCount()

print(f"📦 Grid features: {total}")
print(f"🗺️ States features: {states_layer.featureCount()}")

# ================================
# ADD FIELD
# ================================
field_name = "states"

if field_name not in [f.name() for f in grid_layer.fields()]:
    print("➕ Criando campo 'states'...")
    grid_layer.dataProvider().addAttributes([QgsField(field_name, QVariant.String)])
    grid_layer.updateFields()

field_index = grid_layer.fields().indexOf(field_name)

# ================================
# CACHE STATES
# ================================
print("⚡ Carregando estados em memória...")
state_feats = {f.id(): f for f in states_layer.getFeatures()}
print(f"✅ {len(state_feats)} estados carregados")

# ================================
# SPATIAL INDEX
# ================================
print("🧭 Criando spatial index...")
index = QgsSpatialIndex(states_layer.getFeatures())
print("✅ Spatial index pronto")

# ================================
# PROGRESS FUNCTION
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

print("🔄 Processando grid...")

for i, grid_feat in enumerate(grid_layer.getFeatures(), 1):
    grid_geom = grid_feat.geometry()
    
    if grid_geom is None or grid_geom.isEmpty():
        print(f"⚠️ Geometria vazia no grid ID {grid_feat.id()}")
        continue

    candidate_ids = index.intersects(grid_geom.boundingBox())
    
    states_set = set()
    
    for fid in candidate_ids:
        state_feat = state_feats.get(fid)
        if not state_feat:
            continue
        
        if grid_geom.intersects(state_feat.geometry()):
            val = state_feat["abbrev_state"]
            if val:
                states_set.add(val.lower())
    
    states_str = ";".join(sorted(states_set)) if states_set else None
    updates[grid_feat.id()] = {field_index: states_str}

    # progresso inteligente
    if should_print(i):
        print(f"⏳ {i}/{total} ({int(i/total*100)}%)")

# ================================
# APPLY
# ================================
print("💾 Aplicando mudanças...")
ok = grid_layer.dataProvider().changeAttributeValues(updates)

if ok:
    print("✅ Atualização concluída")
else:
    print("❌ Erro ao atualizar atributos")

end_time = time.time()

print(f"🏁 Finalizado em {round(end_time - start_time, 2)} segundos")
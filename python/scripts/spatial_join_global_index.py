from qgis.core import (
    QgsProject,
    QgsField,
    QgsSpatialIndex
)
from PyQt5.QtCore import QVariant
import time

start = time.time()

print("🚀 Spatial join com buffer negativo...")

# ================================
# LOAD LAYERS
# ================================
target = QgsProject.instance().mapLayersByName("grid_4dd_world_ALIGNED")[0]
index_layer = QgsProject.instance().mapLayersByName("grid_4dd_REFERENCE_global_index_NOT_ALIGNED")[0]

print(f"📦 Target: {target.featureCount()}")
print(f"🌍 Index: {index_layer.featureCount()}")

# ================================
# ADD FIELDS
# ================================
fields_to_add = ["row_value", "col_value"]

existing = [f.name() for f in target.fields()]
new_fields = []

for f in fields_to_add:
    if f not in existing:
        new_fields.append(QgsField(f, QVariant.String))

if new_fields:
    print("➕ Criando campos...")
    target.dataProvider().addAttributes(new_fields)
    target.updateFields()

idx_row = target.fields().indexOf("row_value")
idx_col = target.fields().indexOf("col_value")

# ================================
# BUFFER CONFIG
# ================================
buffer_dist = -0.01  # graus
segments = 5  # suficiente para quadrado

print("⚡ Aplicando buffer negativo em memória...")

# ================================
# CACHE + BUFFER + INDEX
# ================================
buffered_feats = {}
spatial_index = QgsSpatialIndex()

for f in index_layer.getFeatures():
    geom = f.geometry()
    
    if geom is None or geom.isEmpty():
        continue

    buffered = geom.buffer(buffer_dist, segments)

    if buffered.isEmpty():
        continue

    new_feat = f
    new_feat.setGeometry(buffered)

    buffered_feats[f.id()] = new_feat
    spatial_index.insertFeature(new_feat)

print(f"✅ {len(buffered_feats)} células indexadas com buffer")

# ================================
# PROCESS
# ================================
updates = {}
total = target.featureCount()

def should_print(i):
    if i <= 10:
        return i % 2 == 0
    elif i <= 100:
        return i % 10 == 0
    else:
        return i % 100 == 0 or i == total

print("🔄 Processando overlap real...")

for i, feat in enumerate(target.getFeatures(), 1):
    geom = feat.geometry()
    
    if geom is None or geom.isEmpty():
        continue

    candidate_ids = spatial_index.intersects(geom.boundingBox())

    row_val = None
    col_val = None

    for fid in candidate_ids:
        idx_feat = buffered_feats[fid]
        
        if geom.intersects(idx_feat.geometry()):
            row_val = idx_feat["row_value"]
            col_val = idx_feat["col_value"]
            break

    updates[feat.id()] = {
        idx_row: row_val,
        idx_col: col_val
    }

    if should_print(i):
        print(f"⏳ {i}/{total} ({int(i/total*100)}%)")

# ================================
# APPLY
# ================================
print("💾 Aplicando mudanças...")
ok = target.dataProvider().changeAttributeValues(updates)

if ok:
    print("✅ Join com buffer concluído!")
else:
    print("❌ Erro no update")

print(f"🏁 Tempo: {round(time.time() - start, 2)}s")
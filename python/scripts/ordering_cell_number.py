from qgis.core import QgsProject, QgsField
from PyQt5.QtCore import QVariant
import time

start = time.time()

print("🚀 Iniciando enumeração por estado...")

layer = QgsProject.instance().mapLayersByName("grid_mix_br_leucaenaearth-prep")[0]

# ================================
# CONFIG
# ================================
id_field = "id"
state_field = "state_centroid"
new_field = "state_seq"

# ================================
# ADD FIELD
# ================================
if new_field not in [f.name() for f in layer.fields()]:
    print("➕ Criando campo state_seq...")
    layer.dataProvider().addAttributes([QgsField(new_field, QVariant.String)])
    layer.updateFields()

idx_new = layer.fields().indexOf(new_field)

# ================================
# GROUP FEATURES
# ================================
groups = {}

print("📦 Agrupando por estado...")

for f in layer.getFeatures():
    if f[id_field] is None:
        continue

    state = f[state_field]
    if not state:
        continue

    geom = f.geometry()
    if geom is None or geom.isEmpty():
        continue

    centroid = geom.centroid().asPoint()

    key = state

    if key not in groups:
        groups[key] = []

    groups[key].append({
        "id": f.id(),
        "x": centroid.x(),
        "y": centroid.y()
    })

print(f"🗺️ Estados encontrados: {len(groups)}")

# ================================
# ENUMERATION
# ================================
updates = {}

for state, feats in groups.items():
    print(f"🔢 Processando estado: {state} ({len(feats)} células)")

    # ordenar: topo → baixo, esquerda → direita
    feats_sorted = sorted(
        feats,
        key=lambda f: (-f["y"], f["x"])
    )

    for i, feat in enumerate(feats_sorted, 1):
        seq = str(i).zfill(3)  # 001, 002...
        updates[feat["id"]] = {idx_new: seq}

print("💾 Aplicando mudanças...")
layer.dataProvider().changeAttributeValues(updates)

print(f"✅ Finalizado em {round(time.time() - start, 2)}s")
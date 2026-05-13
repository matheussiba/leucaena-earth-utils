from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsFeature,
    QgsField
)
from PyQt5.QtCore import QVariant

print("🚀 Criando cópia em memória com subgrid...")

# ================================
# LOAD ORIGINAL
# ================================
orig = QgsProject.instance().mapLayersByName("grid_mix_br_leucaenaearth-prep")[0]

# ================================
# CREATE MEMORY COPY
# ================================
mem_layer = QgsVectorLayer(
    f"Polygon?crs={orig.crs().authid()}",
    "grid_mix_br_subgrid",
    "memory"
)

provider = mem_layer.dataProvider()

# copiar campos
provider.addAttributes(orig.fields())
provider.addAttributes([QgsField("sub_2dd", QVariant.Int)])
mem_layer.updateFields()

# copiar features
features = []

for f in orig.getFeatures():
    new_f = QgsFeature(mem_layer.fields())
    new_f.setGeometry(f.geometry())
    new_f.setAttributes(f.attributes() + [None])
    features.append(new_f)

provider.addFeatures(features)
mem_layer.updateExtents()

print("✅ Cópia criada")

# ================================
# PREPARAR CAMPOS
# ================================
idx_row = mem_layer.fields().indexOf("row_value")
idx_col = mem_layer.fields().indexOf("col_value")
idx_state = mem_layer.fields().indexOf("states")
idx_sub = mem_layer.fields().indexOf("sub_2dd")

# ================================
# GROUPING
# ================================
groups = {}

for f in mem_layer.getFeatures():

    # só SP (ou contém SP)
    if not f["states"] or "sp" not in f["states"]:
        continue

    row = f["row_value"]
    col = f["col_value"]

    if not row or not col:
        continue

    key = (row, col)

    geom = f.geometry()
    if geom is None or geom.isEmpty():
        continue

    c = geom.centroid().asPoint()

    if key not in groups:
        groups[key] = []

    groups[key].append({
        "id": f.id(),
        "x": c.x(),
        "y": c.y()
    })

print(f"📦 Grupos encontrados: {len(groups)}")

# ================================
# ENUMERATION
# ================================
updates = {}

for key, feats in groups.items():

    if len(feats) != 4:
        continue

    feats_sorted = sorted(
        feats,
        key=lambda f: (-f["y"], f["x"])
    )

    for i, feat in enumerate(feats_sorted, 1):
        updates[feat["id"]] = {idx_sub: i}

# ================================
# APPLY
# ================================
mem_layer.dataProvider().changeAttributeValues(updates)

# ================================
# ADD TO QGIS
# ================================
QgsProject.instance().addMapLayer(mem_layer)

print("✅ Nova layer criada com subgrid 2dd")
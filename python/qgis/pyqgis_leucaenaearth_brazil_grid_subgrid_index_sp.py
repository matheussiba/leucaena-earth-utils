"""
================================================================================
Subgrid index (sub_2dd) for Leucaena.Earth export prep — São Paulo cells (PyQGIS)
================================================================================

What it does
    1. Clones ``grid_mix_br_leucaenaearth-prep`` to an in-memory layer
       ``grid_mix_br_subgrid`` and adds integer field ``sub_2dd``.
    2. Groups cells that share the same ``row_value`` + ``col_value`` **and**
       have ``states`` containing ``sp`` (São Paulo).
    3. For groups with **exactly four** cells, sorts them by (-y, x) (top to
       bottom, left to right) and writes sub-indices 1..4 into ``sub_2dd``.

Why
    Prepares hierarchical / sub-cell numbering expected by the Leucaena.Earth
    grid export workflow for subdivided cells in SP.

Prerequisites
    - Source layer ``grid_mix_br_leucaenaearth-prep`` with fields:
      ``row_value``, ``col_value``, ``states`` (``states`` should list UF codes
      as produced by assign_states_to_grid.py).

How to run
    *Python Console* → *Show Editor* → paste → *Run script*.

Output
    - New memory layer ``grid_mix_br_subgrid`` added to the project.

Limitations
    - Only processes groups tied to SP; groups with count != 4 are skipped.
================================================================================
"""

from qgis.core import QgsFeature, QgsField, QgsProject, QgsVectorLayer
from PyQt5.QtCore import QVariant

print("Building in-memory copy with subgrid field...")

# ----- LOAD SOURCE -----
orig = QgsProject.instance().mapLayersByName("grid_mix_br_leucaenaearth-prep")[0]

# ----- MEMORY COPY -----
mem_layer = QgsVectorLayer(
    f"Polygon?crs={orig.crs().authid()}",
    "grid_mix_br_subgrid",
    "memory",
)

provider = mem_layer.dataProvider()

provider.addAttributes(orig.fields())
provider.addAttributes([QgsField("sub_2dd", QVariant.Int)])
mem_layer.updateFields()

features = []

for f in orig.getFeatures():
    new_f = QgsFeature(mem_layer.fields())
    new_f.setGeometry(f.geometry())
    new_f.setAttributes(f.attributes() + [None])
    features.append(new_f)

provider.addFeatures(features)
mem_layer.updateExtents()

print("Memory copy created.")

idx_sub = mem_layer.fields().indexOf("sub_2dd")

# ----- GROUP BY (row, col) FOR SP -----
groups = {}

for f in mem_layer.getFeatures():
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

    groups[key].append({"id": f.id(), "x": c.x(), "y": c.y()})

print(f"Groups found: {len(groups)}")

# ----- ENUMERATE sub_2dd -----
updates = {}

for _key, feats in groups.items():
    if len(feats) != 4:
        continue

    feats_sorted = sorted(feats, key=lambda rec: (-rec["y"], rec["x"]))

    for i, feat in enumerate(feats_sorted, 1):
        updates[feat["id"]] = {idx_sub: i}

mem_layer.dataProvider().changeAttributeValues(updates)

QgsProject.instance().addMapLayer(mem_layer)

print("Done: grid_mix_br_subgrid added with sub_2dd populated where applicable.")

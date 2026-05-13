"""
================================================================================
Transfer global row/col index onto aligned grid using slight negative buffer (PyQGIS)
================================================================================

What it does
    Copies ``row_value`` and ``col_value`` from a **reference** global index
    grid onto a **target** aligned grid. To reduce edge artefacts, each
    reference cell geometry is shrunk with a small **negative** buffer (degrees)
    before testing ``intersects`` with the target cell.

Layer names (exact)
    - ``grid_4dd_world_ALIGNED`` — receives attributes (edited in place).
    - ``grid_4dd_REFERENCE_global_index_NOT_ALIGNED`` — source of row/col labels.

Fields
    - Creates ``row_value`` and ``col_value`` on the target if missing (String).

How to run
    *Python Console* → *Show Editor* → paste → *Run script*.

Tuning
    - ``buffer_dist`` (degrees): more negative = smaller reference footprint.
    - ``segments``: buffer segmentation (5 is enough for rectangular cells).
================================================================================
"""

from qgis.core import QgsField, QgsProject, QgsSpatialIndex
from PyQt5.QtCore import QVariant
import time

start = time.time()

print("Starting spatial_join_global_index (negative buffer join)...")

# ----- LOAD LAYERS -----
target = QgsProject.instance().mapLayersByName("grid_4dd_world_ALIGNED")[0]
index_layer = QgsProject.instance().mapLayersByName(
    "grid_4dd_REFERENCE_global_index_NOT_ALIGNED"
)[0]

print(f"Target features: {target.featureCount()}")
print(f"Index layer features: {index_layer.featureCount()}")

# ----- ADD FIELDS -----
fields_to_add = ["row_value", "col_value"]

existing = [f.name() for f in target.fields()]
new_fields = []

for fname in fields_to_add:
    if fname not in existing:
        new_fields.append(QgsField(fname, QVariant.String))

if new_fields:
    print("Adding missing fields...")
    target.dataProvider().addAttributes(new_fields)
    target.updateFields()

idx_row = target.fields().indexOf("row_value")
idx_col = target.fields().indexOf("col_value")

# ----- BUFFER + INDEX -----
buffer_dist = -0.01  # degrees
segments = 5

print("Building buffered geometries and spatial index...")

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

print(f"{len(buffered_feats)} index cells inserted (buffered)")

# ----- JOIN -----
updates = {}
total = target.featureCount()


def should_print(i):
    if i <= 10:
        return i % 2 == 0
    if i <= 100:
        return i % 10 == 0
    return i % 100 == 0 or i == total


print("Computing intersections...")

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

    updates[feat.id()] = {idx_row: row_val, idx_col: col_val}

    if should_print(i):
        print(f"{i}/{total} ({int(i / total * 100)}%)")

print("Writing attributes...")
ok = target.dataProvider().changeAttributeValues(updates)

if ok:
    print("Join completed.")
else:
    print("Error: changeAttributeValues returned false.")

print(f"Elapsed: {round(time.time() - start, 2)} s")

"""
================================================================================
Per-state cell sequence (state_seq) by spatial sort (PyQGIS)
================================================================================

What it does
    Groups all grid cells by ``state_centroid``, sorts cells within each state
    by (-centroid_y, centroid_x) (north to south, west to east), and writes a
    zero-padded sequence string into ``state_seq`` (001, 002, …).

Layer name
    - ``grid_mix_br_leucaenaearth-prep`` — must contain ``id``, ``state_centroid``
      (run assign_statescentroids_to_grid.py first).

How to run
    *Python Console* → *Show Editor* → paste → *Run script*.

Output
    - Field ``state_seq`` on the same layer (created if missing).
================================================================================
"""

from qgis.core import QgsField, QgsProject
from PyQt5.QtCore import QVariant
import time

start = time.time()

print("Starting ordering_cell_number...")

layer = QgsProject.instance().mapLayersByName("grid_mix_br_leucaenaearth-prep")[0]

id_field = "id"
state_field = "state_centroid"
new_field = "state_seq"

if new_field not in [f.name() for f in layer.fields()]:
    print("Creating field 'state_seq'...")
    layer.dataProvider().addAttributes([QgsField(new_field, QVariant.String)])
    layer.updateFields()

idx_new = layer.fields().indexOf(new_field)

groups = {}

print("Grouping features by state...")

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

    groups[key].append({"id": f.id(), "x": centroid.x(), "y": centroid.y()})

print(f"States in data: {len(groups)}")

updates = {}

for state, feats in groups.items():
    print(f"State {state}: {len(feats)} cells")

    # North → south, then west → east
    feats_sorted = sorted(feats, key=lambda rec: (-rec["y"], rec["x"]))

    for i, feat in enumerate(feats_sorted, 1):
        seq = str(i).zfill(3)
        updates[feat["id"]] = {idx_new: seq}

print("Applying updates...")
layer.dataProvider().changeAttributeValues(updates)

print(f"Finished in {round(time.time() - start, 2)} s")

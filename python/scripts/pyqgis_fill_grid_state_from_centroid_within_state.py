"""
================================================================================
Assign state by cell centroid (PyQGIS)
================================================================================

What it does
    For each grid polygon, takes the **centroid** of the cell and finds which
    state polygon contains it. Writes a single lower-case UF code to
    ``state_centroid`` (first match wins).

Layer names (exact match in QGIS)
    - ``grid_mix_br_leucaenaearth-prep``
    - ``states_br_ibge_2020`` (field ``abbrev_state``)

Difference vs assign_states_to_grid.py
    That script lists **all** states intersecting the cell. This script picks
    **one** state based on centroid containment (``within``).

How to run
    *Python Console* → *Show Editor* → paste → *Run script*.

Output
    - Field ``state_centroid`` on the grid layer (created if missing).
================================================================================
"""

from qgis.core import QgsField, QgsProject, QgsSpatialIndex
from PyQt5.QtCore import QVariant
import time

start_time = time.time()

print("Starting assign_statescentroids_to_grid...")

# ----- LOAD LAYERS -----
grid_layer = QgsProject.instance().mapLayersByName("grid_mix_br_leucaenaearth-prep")[0]
states_layer = QgsProject.instance().mapLayersByName("states_br_ibge_2020")[0]

total = grid_layer.featureCount()

print(f"Grid: {total} features")
print(f"States: {states_layer.featureCount()} features")

# ----- ADD FIELD -----
field_name = "state_centroid"

if field_name not in [f.name() for f in grid_layer.fields()]:
    print("Creating field 'state_centroid'...")
    grid_layer.dataProvider().addAttributes([QgsField(field_name, QVariant.String)])
    grid_layer.updateFields()

field_index = grid_layer.fields().indexOf(field_name)

# ----- CACHE + INDEX -----
print("Caching state features...")
state_feats = {f.id(): f for f in states_layer.getFeatures()}

index = QgsSpatialIndex(states_layer.getFeatures())


def should_print(i):
    if i <= 10:
        return i % 2 == 0
    if i <= 100:
        return i % 10 == 0
    return i % 100 == 0 or i == total


updates = {}

print("Assigning state by centroid...")

for i, grid_feat in enumerate(grid_layer.getFeatures(), 1):
    geom = grid_feat.geometry()

    if geom is None or geom.isEmpty():
        updates[grid_feat.id()] = {field_index: None}
        continue

    centroid = geom.centroid()

    candidate_ids = index.intersects(centroid.boundingBox())

    found_state = None

    for fid in candidate_ids:
        state_feat = state_feats[fid]

        if centroid.within(state_feat.geometry()):
            val = state_feat["abbrev_state"]
            if val:
                found_state = val.lower()
                break  # single state

    updates[grid_feat.id()] = {field_index: found_state}

    if should_print(i):
        print(f"{i}/{total} ({int(i / total * 100)}%)")

print("Saving attribute changes...")
ok = grid_layer.dataProvider().changeAttributeValues(updates)

if ok:
    print("Done.")
else:
    print("Error saving attributes.")

print(f"Elapsed: {round(time.time() - start_time, 2)} s")

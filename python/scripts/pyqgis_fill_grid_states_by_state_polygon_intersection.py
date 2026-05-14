"""
================================================================================
Assign Brazilian states to grid cells by intersection (PyQGIS)
================================================================================

What it does
    For each polygon in the Brazil prep grid, finds all state polygons that
    **intersect** the cell and writes a semicolon-separated list of lower-case
    state abbreviations into attribute ``states`` (e.g. ``sp`` or ``mg;sp``).

Layer names (must match the QGIS layer tree exactly)
    - ``grid_mix_br_leucaenaearth-prep`` — grid polygons (edited in place).
    - ``states_br_ibge_2020`` — state boundaries; must expose field
      ``abbrev_state`` (UF code).

Requirements
    - QGIS 3.x, PyQt5.

How to run
    *Python Console* → *Show Editor* → paste → *Run script*.

Output
    - Field ``states`` created if missing; values updated on the grid layer.

Performance
    - Uses a spatial index on states; progress printed on console.
================================================================================
"""

from qgis.core import QgsField, QgsProject, QgsSpatialIndex
from PyQt5.QtCore import QVariant
import time

start_time = time.time()

print("Starting assign_states_to_grid...")

# ----- LOAD LAYERS -----
grid_layer = QgsProject.instance().mapLayersByName("grid_mix_br_leucaenaearth-prep")[0]
states_layer = QgsProject.instance().mapLayersByName("states_br_ibge_2020")[0]

total = grid_layer.featureCount()

print(f"Grid features: {total}")
print(f"State features: {states_layer.featureCount()}")

# ----- ADD FIELD -----
field_name = "states"

if field_name not in [f.name() for f in grid_layer.fields()]:
    print("Creating field 'states'...")
    grid_layer.dataProvider().addAttributes([QgsField(field_name, QVariant.String)])
    grid_layer.updateFields()

field_index = grid_layer.fields().indexOf(field_name)

# ----- CACHE STATES -----
print("Loading state features into memory...")
state_feats = {f.id(): f for f in states_layer.getFeatures()}
print(f"{len(state_feats)} state features cached")

# ----- SPATIAL INDEX -----
print("Building spatial index on states...")
index = QgsSpatialIndex(states_layer.getFeatures())
print("Spatial index ready")

# ----- PROGRESS THROTTLING -----


def should_print(i):
    if i <= 10:
        return i % 2 == 0
    if i <= 100:
        return i % 10 == 0
    return i % 100 == 0 or i == total


# ----- MAIN LOOP -----
updates = {}

print("Processing grid cells...")

for i, grid_feat in enumerate(grid_layer.getFeatures(), 1):
    grid_geom = grid_feat.geometry()

    if grid_geom is None or grid_geom.isEmpty():
        print(f"Warning: empty geometry on grid feature id={grid_feat.id()}")
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

    if should_print(i):
        print(f"{i}/{total} ({int(i / total * 100)}%)")

# ----- APPLY -----
print("Writing attribute changes...")
ok = grid_layer.dataProvider().changeAttributeValues(updates)

if ok:
    print("Update finished successfully.")
else:
    print("Error: changeAttributeValues returned false.")

elapsed = time.time() - start_time
print(f"Finished in {round(elapsed, 2)} seconds.")

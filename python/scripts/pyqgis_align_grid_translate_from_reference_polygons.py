"""
================================================================================
Translate a world grid to align two reference polygons (PyQGIS)
================================================================================

What it does
    Computes the shift (dx, dy) between the centroid of polygon ``cell_origin``
    and the centroid of ``cell_target``, applies that translation to every
    feature of ``world_grid_0.4x0.4deg``, and adds the result as a new in-memory
    layer ``world_grid_aligned``.

Why
    Useful when you have a fixed global grid but need it shifted to match a
    concrete spatial reference (e.g. align the grid to a reference tile).

Requirements
    - QGIS 3.x with Python (PyQt5).
    - A QGIS project open with **three** vector layers whose **layer names**
      in the Layers panel match exactly:
        * ``cell_origin``   — one reference feature (origin)
        * ``cell_target``   — one reference feature (target)
        * ``world_grid_0.4x0.4deg`` — grid to translate

How to run
    1. Open the QGIS project with the layers above loaded and correctly named
       (right-click layer → Rename if needed).
    2. *Plugins* → *Python Console*.
    3. *Show Editor* (notepad icon), paste this script, *Run script*.

Output
    - New in-memory layer: ``world_grid_aligned`` (same geometry type and
      attribute fields as the input grid; geometries translated).
    - Console prints dx, dy.

Limitations
    - Reads only the **first** feature from ``cell_origin`` and ``cell_target``.
    - Does not save to disk; export manually if needed.
================================================================================
"""

from qgis.core import (
    QgsFeature,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)

# Layer references (names must match the QGIS layer tree)
origin_layer = QgsProject.instance().mapLayersByName("cell_origin")[0]
target_layer = QgsProject.instance().mapLayersByName("cell_target")[0]
grid_layer = QgsProject.instance().mapLayersByName("world_grid_0.4x0.4deg")[0]

# First feature from each reference layer
origin_feat = next(origin_layer.getFeatures())
target_feat = next(target_layer.getFeatures())

# Centroids
origin_centroid = origin_feat.geometry().centroid().asPoint()
target_centroid = target_feat.geometry().centroid().asPoint()

# Translation vector
dx = target_centroid.x() - origin_centroid.x()
dy = target_centroid.y() - origin_centroid.y()

print(f"dx: {dx}, dy: {dy}")

# In-memory output layer
new_layer = QgsVectorLayer(
    f"{QgsWkbTypes.displayString(grid_layer.wkbType())}?crs={grid_layer.crs().authid()}",
    "world_grid_aligned",
    "memory",
)

prov = new_layer.dataProvider()
prov.addAttributes(grid_layer.fields())
new_layer.updateFields()

# Copy features with translated geometry
feats = []
for f in grid_layer.getFeatures():
    geom = f.geometry()
    geom.translate(dx, dy)

    new_feat = QgsFeature()
    new_feat.setGeometry(geom)
    new_feat.setAttributes(f.attributes())

    feats.append(new_feat)

prov.addFeatures(feats)

QgsProject.instance().addMapLayer(new_layer)

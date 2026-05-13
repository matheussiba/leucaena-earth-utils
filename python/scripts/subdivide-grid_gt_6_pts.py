"""
================================================================================
Adaptive hierarchical grid by point count (PyQGIS)
================================================================================

What it does
    1. Recomputes ``NUMPOINTS`` on ``grid-leucenas`` by counting occurrence
       points from ``all_gdb_leucena_pts_sp_export`` inside each cell
       (``contains``, with a point spatial index).
    2. Builds a new in-memory layer ``adaptive_grid``:
       - If a cell has **≤ 6** points: keep the original polygon; ``GRID_ID``
         equals the cell ``id`` string.
       - If **> 6** points: split the cell into four quadrants (``split_quad``).
         For each quadrant with **≤ 10** points, keep it; else split again
         into four (second-level quads). Each output row gets a hierarchical
         ``GRID_ID`` (e.g. ``12-3`` or ``12-3-2``).
    3. Refreshes ``NUMPOINTS``, sets ``grid_status`` to ``not finished yet`` if
       count > 0 else ``no point``, and rewrites ``fid`` as 1..N on the result
       layer.

Layer names (exact)
    - ``grid-leucenas`` — polygon grid (edited for NUMPOINTS step).
    - ``all_gdb_leucena_pts_sp_export`` — occurrence points.

How to run
    *Python Console* → *Show Editor* → paste → *Run script*.

Output
    - Updated source grid attributes; new layer ``adaptive_grid`` in the project.

Caution
    - ``setAttributes(feat.attributes() + [...])`` assumes attribute list length
      matches the field definition including ``GRID_ID``; adjust field list if
      your schema differs.
================================================================================
"""

from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
    QgsSpatialIndex,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant

# ----- LAYERS -----
grid_layer = QgsProject.instance().mapLayersByName("grid-leucenas")[0]
points_layer = QgsProject.instance().mapLayersByName("all_gdb_leucena_pts_sp_export")[0]

crs = grid_layer.crs()
id_field = "id"

# ----- POINT INDEX -----
points_index = QgsSpatialIndex(points_layer.getFeatures())
points_dict = {f.id(): f for f in points_layer.getFeatures()}


def count_points_geom(geom):
    """Count points strictly inside geometry (uses spatial index + contains)."""
    ids = points_index.intersects(geom.boundingBox())
    count = 0
    for pid in ids:
        pt = points_dict[pid]
        if geom.contains(pt.geometry()):
            count += 1
    return count


# ----- REFRESH NUMPOINTS ON SOURCE -----
num_idx_original = grid_layer.fields().indexOf("NUMPOINTS")

grid_layer.startEditing()

for f in grid_layer.getFeatures():
    geom = f.geometry()
    count = count_points_geom(geom)
    grid_layer.changeAttributeValue(f.id(), num_idx_original, count)

grid_layer.commitChanges()

print("NUMPOINTS updated on source grid layer.")


def split_quad(geom):
    """Split polygon bounding box into four quadrants (QgsGeometry rectangles)."""
    bbox = geom.boundingBox()

    xmin = bbox.xMinimum()
    xmax = bbox.xMaximum()
    ymin = bbox.yMinimum()
    ymax = bbox.yMaximum()

    xmid = (xmin + xmax) / 2
    ymid = (ymin + ymax) / 2

    return [
        QgsGeometry.fromRect(QgsRectangle(xmin, ymin, xmid, ymid)),
        QgsGeometry.fromRect(QgsRectangle(xmid, ymin, xmax, ymid)),
        QgsGeometry.fromRect(QgsRectangle(xmin, ymid, xmid, ymax)),
        QgsGeometry.fromRect(QgsRectangle(xmid, ymid, xmax, ymax)),
    ]


# ----- OUTPUT LAYER -----
fields = QgsFields(grid_layer.fields())

if "GRID_ID" not in [f.name() for f in fields]:
    fields.append(QgsField("GRID_ID", QVariant.String))

result_layer = QgsVectorLayer("Polygon?crs=" + crs.authid(), "adaptive_grid", "memory")

provider = result_layer.dataProvider()
provider.addAttributes(fields)
result_layer.updateFields()

features_final = []

# ----- BUILD ADAPTIVE FEATURES -----
for feat in grid_layer.getFeatures():
    geom = feat.geometry()
    base_id = str(feat[id_field])

    n = count_points_geom(geom)

    # Case 1: keep cell as-is
    if n <= 6:
        f = QgsFeature(fields)
        f.setGeometry(geom)
        f.setAttributes(feat.attributes() + [base_id, n])
        features_final.append(f)
        continue

    # Case 2: first-level quad split
    quads = split_quad(geom)

    for i, q in enumerate(quads, start=1):
        grid1_id = f"{base_id}-{i}"

        n1 = count_points_geom(q)

        if n1 <= 10:
            f = QgsFeature(fields)
            f.setGeometry(q)
            f.setAttributes(feat.attributes() + [grid1_id, n1])
            features_final.append(f)
        else:
            quads2 = split_quad(q)

            for j, q2 in enumerate(quads2, start=1):
                grid2_id = f"{grid1_id}-{j}"

                n2 = count_points_geom(q2)

                f = QgsFeature(fields)
                f.setGeometry(q2)
                f.setAttributes(feat.attributes() + [grid2_id, n2])
                features_final.append(f)

provider.addFeatures(features_final)

# ----- FINAL ATTRIBUTE PASS -----
num_idx = result_layer.fields().indexOf("NUMPOINTS")
status_idx = result_layer.fields().indexOf("grid_status")
fid_idx = result_layer.fields().indexOf("fid")

result_layer.startEditing()

row = 1

for f in result_layer.getFeatures():
    geom = f.geometry()
    count = count_points_geom(geom)

    result_layer.changeAttributeValue(f.id(), num_idx, count)

    if count > 0:
        status = "not finished yet"
    else:
        status = "no point"

    result_layer.changeAttributeValue(f.id(), status_idx, status)
    result_layer.changeAttributeValue(f.id(), fid_idx, row)

    row += 1

result_layer.commitChanges()

QgsProject.instance().addMapLayer(result_layer)

print("Adaptive hierarchical grid built and added to the project.")

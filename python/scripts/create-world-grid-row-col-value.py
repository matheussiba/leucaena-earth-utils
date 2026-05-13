"""
================================================================================
Global index grid with row/column labels (PyQGIS, in-memory)
================================================================================

What it does
    Builds a **global** rectangular grid in EPSG:4326 covering
    [-180,180] x [-90,90] with configurable cell size (default 0.4°).
    Each cell gets:
      - ``row_value``: two-letter row label (AA, AB, … ZZ pattern).
      - ``col_value``: zero-padded column number (001, 002, …).

Why
    Provides a stable row/column naming scheme for downstream joins and
    Leucaena.Earth-style hierarchical IDs.

Requirements
    - QGIS 3.x, PyQt5.

How to run
    *Python Console* → *Show Editor* → paste → *Run script*.

Output
    - In-memory polygon layer ``global_grid_index`` added to the project.

Tuning
    Edit ``cell_size``, ``xmin``, ``xmax``, ``ymin``, ``ymax`` in CONFIG below.
================================================================================
"""

from qgis.core import (
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)
from PyQt5.QtCore import QVariant

# ----- CONFIG -----
xmin, xmax = -180, 180
ymin, ymax = -90, 90
cell_size = 0.4

cols = int((xmax - xmin) / cell_size)
rows = int((ymax - ymin) / cell_size)

print(f"Grid: {rows} rows x {cols} columns")


def index_to_letters(i):
    """Map row index 0.. to two-letter codes AA, AB, …"""
    first = chr(65 + (i // 26))
    second = chr(65 + (i % 26))
    return first + second


# ----- BUILD LAYER -----
layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "global_grid_index", "memory")
provider = layer.dataProvider()

provider.addAttributes(
    [
        QgsField("row_value", QVariant.String),
        QgsField("col_value", QVariant.String),
    ]
)
layer.updateFields()

features = []

for row in range(rows):
    y_top = ymax - row * cell_size
    y_bottom = y_top - cell_size

    row_label = index_to_letters(row)

    for col in range(cols):
        x_left = xmin + col * cell_size
        x_right = x_left + cell_size

        col_label = str(col + 1).zfill(3)

        geom = QgsGeometry.fromPolygonXY(
            [
                [
                    QgsPointXY(x_left, y_top),
                    QgsPointXY(x_right, y_top),
                    QgsPointXY(x_right, y_bottom),
                    QgsPointXY(x_left, y_bottom),
                    QgsPointXY(x_left, y_top),
                ]
            ]
        )

        feat = QgsFeature()
        feat.setGeometry(geom)
        feat.setAttributes([row_label, col_label])

        features.append(feat)

    if row % 20 == 0:
        print(f"Row {row}/{rows}")

provider.addFeatures(features)
layer.updateExtents()

QgsProject.instance().addMapLayer(layer)

print("Done: global_grid_index added.")

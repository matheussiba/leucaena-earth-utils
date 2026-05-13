from qgis.core import (
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsField,
    QgsProject
)
from PyQt5.QtCore import QVariant

# ================================
# CONFIG
# ================================
xmin, xmax = -180, 180
ymin, ymax = -90, 90
cell_size = 0.4

cols = int((xmax - xmin) / cell_size)
rows = int((ymax - ymin) / cell_size)

print(f"🌍 Grid: {rows} linhas x {cols} colunas")

# ================================
# FUNÇÃO LETRAS (AA → ZZ)
# ================================
def index_to_letters(i):
    first = chr(65 + (i // 26))
    second = chr(65 + (i % 26))
    return first + second

# ================================
# CREATE LAYER
# ================================
layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "global_grid_index", "memory")
provider = layer.dataProvider()

provider.addAttributes([
    QgsField("row_value", QVariant.String),
    QgsField("col_value", QVariant.String)
])
layer.updateFields()

# ================================
# CREATE FEATURES
# ================================
features = []

for row in range(rows):
    y_top = ymax - row * cell_size
    y_bottom = y_top - cell_size

    row_label = index_to_letters(row)

    for col in range(cols):
        x_left = xmin + col * cell_size
        x_right = x_left + cell_size

        col_label = str(col + 1).zfill(3)

        geom = QgsGeometry.fromPolygonXY([[
            QgsPointXY(x_left, y_top),
            QgsPointXY(x_right, y_top),
            QgsPointXY(x_right, y_bottom),
            QgsPointXY(x_left, y_bottom),
            QgsPointXY(x_left, y_top)
        ]])

        feat = QgsFeature()
        feat.setGeometry(geom)
        feat.setAttributes([row_label, col_label])

        features.append(feat)

    # progresso leve
    if row % 20 == 0:
        print(f"⏳ Linha {row}/{rows}")

# ================================
# ADD FEATURES
# ================================
provider.addFeatures(features)
layer.updateExtents()

QgsProject.instance().addMapLayer(layer)

print("✅ Grid global criado!")
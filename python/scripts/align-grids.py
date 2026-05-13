# nomes das layers
origin_layer = QgsProject.instance().mapLayersByName('cell_origin')[0]
target_layer = QgsProject.instance().mapLayersByName('cell_target')[0]
grid_layer = QgsProject.instance().mapLayersByName('world_grid_0.4x0.4deg')[0]

# pega a primeira feição (tile)
origin_feat = next(origin_layer.getFeatures())
target_feat = next(target_layer.getFeatures())

# pega centróides
origin_centroid = origin_feat.geometry().centroid().asPoint()
target_centroid = target_feat.geometry().centroid().asPoint()

# calcula deslocamento
dx = target_centroid.x() - origin_centroid.x()
dy = target_centroid.y() - origin_centroid.y()

print(f"dx: {dx}, dy: {dy}")

# cria nova layer transformada
new_layer = QgsVectorLayer(
    f"{QgsWkbTypes.displayString(grid_layer.wkbType())}?crs={grid_layer.crs().authid()}",
    "world_grid_aligned",
    "memory"
)

prov = new_layer.dataProvider()
prov.addAttributes(grid_layer.fields())
new_layer.updateFields()

# aplica transformação
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
import processing

params = {
    'TYPE': 2,  # polígonos (retângulos)
    'EXTENT': '-179.7,179.7,-73.7,73.7 [EPSG:4326]',
    'HSPACING': 0.2,
    'VSPACING': 0.2,
    'HOVERLAY': 0,
    'VOVERLAY': 0,
    'CRS': QgsCoordinateReferenceSystem('EPSG:4326'),
    'OUTPUT': 'memory:world_grid'
}

result = processing.run("native:creategrid", params)

# adiciona no projeto
QgsProject.instance().addMapLayer(result['OUTPUT'])
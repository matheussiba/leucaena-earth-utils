from qgis.PyQt.QtCore import QVariant

grid_layer = QgsProject.instance().mapLayersByName('grid-leucenas')[0]
points_layer = QgsProject.instance().mapLayersByName('all_gdb_leucena_pts_sp_export')[0]

crs = grid_layer.crs()
id_field = "id"

# --------------------------------------------------
# criar spatial index dos pontos
# --------------------------------------------------

points_index = QgsSpatialIndex(points_layer.getFeatures())
points_dict = {f.id(): f for f in points_layer.getFeatures()}

# --------------------------------------------------
# função contar pontos dentro de um polígono
# --------------------------------------------------

def count_points_geom(geom):

    ids = points_index.intersects(geom.boundingBox())

    count = 0

    for pid in ids:
        pt = points_dict[pid]
        if geom.contains(pt.geometry()):
            count += 1

    return count

# --------------------------------------------------
# recalcular NUMPOINTS no grid_layer original
# --------------------------------------------------

num_idx_original = grid_layer.fields().indexOf("NUMPOINTS")

grid_layer.startEditing()

for f in grid_layer.getFeatures():

    geom = f.geometry()

    count = count_points_geom(geom)

    grid_layer.changeAttributeValue(f.id(), num_idx_original, count)

grid_layer.commitChanges()

print("✔ NUMPOINTS atualizado no grid_layer original")

# --------------------------------------------------
# subdividir polígono em 4
# --------------------------------------------------

def split_quad(geom):

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
        QgsGeometry.fromRect(QgsRectangle(xmid, ymid, xmax, ymax))
    ]

# --------------------------------------------------
# criar layer resultado
# --------------------------------------------------

fields = QgsFields(grid_layer.fields())

if "GRID_ID" not in [f.name() for f in fields]:
    fields.append(QgsField("GRID_ID", QVariant.String))

result_layer = QgsVectorLayer(
    "Polygon?crs=" + crs.authid(),
    "adaptive_grid",
    "memory"
)

provider = result_layer.dataProvider()
provider.addAttributes(fields)
result_layer.updateFields()

features_final = []

# --------------------------------------------------
# PROCESSAMENTO
# --------------------------------------------------

for feat in grid_layer.getFeatures():

    geom = feat.geometry()
    base_id = str(feat[id_field])

    n = count_points_geom(geom)

    # ------------------------------------
    # caso 1: ≤6 pontos → mantém GRID
    # ------------------------------------

    if n <= 6:

        f = QgsFeature(fields)
        f.setGeometry(geom)
        f.setAttributes(feat.attributes() + [base_id, n])
        features_final.append(f)

        continue

    # ------------------------------------
    # caso 2: >6 → gerar GRID_1
    # ------------------------------------

    quads = split_quad(geom)

    for i, q in enumerate(quads, start=1):

        grid1_id = f"{base_id}-{i}"

        n1 = count_points_geom(q)

        # --------------------------------
        # GRID_1 ≤10 → mantém
        # --------------------------------

        if n1 <= 10:

            f = QgsFeature(fields)
            f.setGeometry(q)
            f.setAttributes(feat.attributes() + [grid1_id, n1])
            features_final.append(f)

        # --------------------------------
        # GRID_1 >10 → gerar GRID_2
        # --------------------------------

        else:

            quads2 = split_quad(q)

            for j, q2 in enumerate(quads2, start=1):

                grid2_id = f"{grid1_id}-{j}"

                n2 = count_points_geom(q2)

                f = QgsFeature(fields)
                f.setGeometry(q2)
                f.setAttributes(feat.attributes() + [grid2_id, n2])
                features_final.append(f)

# --------------------------------------------------
# salvar resultado
# --------------------------------------------------

provider.addFeatures(features_final)

# --------------------------------------------------
# atualizar campos finais
# --------------------------------------------------

num_idx = result_layer.fields().indexOf("NUMPOINTS")
status_idx = result_layer.fields().indexOf("grid_status")
fid_idx = result_layer.fields().indexOf("fid")

result_layer.startEditing()

row = 1

for f in result_layer.getFeatures():

    geom = f.geometry()

    count = count_points_geom(geom)

    # atualizar NUMPOINTS
    result_layer.changeAttributeValue(f.id(), num_idx, count)

    # atualizar grid_status
    if count > 0:
        status = "not finished yet"
    else:
        status = "no point"

    result_layer.changeAttributeValue(f.id(), status_idx, status)

    # atualizar fid
    result_layer.changeAttributeValue(f.id(), fid_idx, row)

    row += 1

result_layer.commitChanges()

QgsProject.instance().addMapLayer(result_layer)

print("✔ Adaptive hierarchical grid criado e atualizado")
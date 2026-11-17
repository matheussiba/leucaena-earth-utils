"""
================================================================================
Create a regular grid with native:creategrid (PyQGIS / Processing)
================================================================================

What it does
    Runs the QGIS Processing algorithm ``native:creategrid`` to build a
    rectangular polygon grid in EPSG:4326 and adds it to the current project
    as an in-memory layer named ``world_grid`` (see ``OUTPUT`` param).

Parameters (edit ``params`` as needed)
    - ``EXTENT``: WGS84 extent string as accepted by Processing.
    - ``HSPACING`` / ``VSPACING``: cell size in degrees (here 0.2).
    - ``TYPE``: 2 = rectangle polygons.

Requirements
    - QGIS 3.x.
    - Run inside QGIS *Python Console* or script editor so ``processing`` and
      ``QgsProject`` are available.

How to run
    1. *Plugins* → *Python Console* → *Show Editor*.
    2. Paste and *Run script*.

Output
    - In-memory layer added to the project (key ``OUTPUT`` from Processing).

Note
    If ``processing`` is not imported, from the console run once::
        import processing
        from qgis.core import QgsCoordinateReferenceSystem, QgsProject
================================================================================
"""

from qgis.core import QgsCoordinateReferenceSystem, QgsProject

import processing

params = {
    "TYPE": 2,  # rectangle polygons
    "EXTENT": "-179.7,179.7,-73.7,73.7 [EPSG:4326]",
    "HSPACING": 0.2,
    "VSPACING": 0.2,
    "HOVERLAY": 0,
    "VOVERLAY": 0,
    "CRS": QgsCoordinateReferenceSystem("EPSG:4326"),
    "OUTPUT": "memory:world_grid",
}

result = processing.run("native:creategrid", params)

QgsProject.instance().addMapLayer(result["OUTPUT"])

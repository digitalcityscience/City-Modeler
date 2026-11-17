<?xml version="1.0" encoding="UTF-8"?>
<!--
  Export-error ticket 05 fixture: a small purpose-built LoD3 building for the
  CityDoctor regression test (scripts/headless/test_export_error05_citydoctor_regression.py),
  standing in for the findings doc's own lod3.gml (13k lines, lives outside
  the repo at ~/Desktop/lod3.gml, impractical to commit for CI).

  One host WallSurface carries a real flat Window and Door (each a single
  gml:Polygon, no depth) - exactly the shape that triggers import-time
  synthetic opening depth (site 4). Two bare WallSurfaces (no fillingSurface)
  serve as Semantic Stamp copy-paste targets (site 2). All source geometry is
  deliberately valid (clean rectangles) - this fixture is a regression guard
  proving the fixed pipeline (docs/agent/export-error/opening-ring-degeneracy-findings.md)
  stays clean under the workflow that produced window18.gml's 985 ring
  errors, not a test of the loud-refusal error paths (those are covered by
  tickets 02-04's own headless tests).
-->
<CityModel
  xmlns="http://www.opengis.net/citygml/3.0"
  xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:bldg="http://www.opengis.net/citygml/building/3.0"
  xmlns:con="http://www.opengis.net/citygml/construction/3.0"
  xmlns:xlink="http://www.w3.org/1999/xlink"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://www.opengis.net/citygml/3.0 http://schemas.opengis.net/citygml/3.0/cityGMLBase.xsd http://www.opengis.net/citygml/building/3.0 http://schemas.opengis.net/citygml/building/3.0/building.xsd http://www.opengis.net/citygml/construction/3.0 http://schemas.opengis.net/citygml/construction/3.0/construction.xsd">
  <gml:boundedBy>
    <gml:Envelope srsName="urn:ogc:def:crs:EPSG::25832" srsDimension="3">
      <gml:lowerCorner>0 0 0</gml:lowerCorner>
      <gml:upperCorner>6 4 3</gml:upperCorner>
    </gml:Envelope>
  </gml:boundedBy>
  <cityObjectMember>
    <bldg:Building gml:id="EXPORTERROR_CDR_BUILDING_V3">
      <gml:name>Export-Error CityDoctor Regression Fixture</gml:name>

<boundary><con:WallSurface gml:id="EXPORTERROR_CDR_WALL_HOST_V3"><gml:name>Host Wall (Window + Door)</gml:name><lod3MultiSurface><gml:MultiSurface gml:id="EXPORTERROR_CDR_WALL_HOST_V3_MS"><gml:surfaceMember><gml:Polygon gml:id="EXPORTERROR_CDR_WALL_HOST_POLY_V3"><gml:exterior><gml:LinearRing gml:id="EXPORTERROR_CDR_WALL_HOST_POLY_V3_0_"><gml:posList srsDimension="3">0 0 0 0 1 0 0 1 2 0 0 2 0 0 0</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></lod3MultiSurface><con:fillingSurface><con:WindowSurface gml:id="EXPORTERROR_CDR_WINDOW_SOURCE_V3"><gml:name>Source Window</gml:name><lod3MultiSurface><gml:MultiSurface gml:id="EXPORTERROR_CDR_WINDOW_SOURCE_V3_MS"><gml:surfaceMember><gml:Polygon gml:id="EXPORTERROR_CDR_WINDOW_SOURCE_POLY_V3"><gml:exterior><gml:LinearRing gml:id="EXPORTERROR_CDR_WINDOW_SOURCE_POLY_V3_0_"><gml:posList srsDimension="3">0 0.3 0.3 0 0.7 0.3 0 0.7 1.3 0 0.3 1.3 0 0.3 0.3</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></lod3MultiSurface></con:WindowSurface></con:fillingSurface><con:fillingSurface><con:DoorSurface gml:id="EXPORTERROR_CDR_DOOR_SOURCE_V3"><gml:name>Source Door</gml:name><lod3MultiSurface><gml:MultiSurface gml:id="EXPORTERROR_CDR_DOOR_SOURCE_V3_MS"><gml:surfaceMember><gml:Polygon gml:id="EXPORTERROR_CDR_DOOR_SOURCE_POLY_V3"><gml:exterior><gml:LinearRing gml:id="EXPORTERROR_CDR_DOOR_SOURCE_POLY_V3_0_"><gml:posList srsDimension="3">0 0.75 0.4 0 0.95 0.4 0 0.95 1.4 0 0.75 1.4 0 0.75 0.4</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></lod3MultiSurface></con:DoorSurface></con:fillingSurface></con:WallSurface></boundary>

<boundary><con:WallSurface gml:id="EXPORTERROR_CDR_WALL_STAMP_WINDOW_TARGET_V3"><gml:name>Bare Wall (Window stamp target)</gml:name><lod3MultiSurface><gml:MultiSurface gml:id="EXPORTERROR_CDR_WALL_STAMP_WINDOW_TARGET_V3_MS"><gml:surfaceMember><gml:Polygon gml:id="EXPORTERROR_CDR_WALL_STAMP_WINDOW_TARGET_POLY_V3"><gml:exterior><gml:LinearRing gml:id="EXPORTERROR_CDR_WALL_STAMP_WINDOW_TARGET_POLY_V3_0_"><gml:posList srsDimension="3">3 0 0 3 4 0 3 4 3 3 0 3 3 0 0</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></lod3MultiSurface></con:WallSurface></boundary>

<boundary><con:WallSurface gml:id="EXPORTERROR_CDR_WALL_STAMP_DOOR_TARGET_V3"><gml:name>Bare Wall (Door stamp target)</gml:name><lod3MultiSurface><gml:MultiSurface gml:id="EXPORTERROR_CDR_WALL_STAMP_DOOR_TARGET_V3_MS"><gml:surfaceMember><gml:Polygon gml:id="EXPORTERROR_CDR_WALL_STAMP_DOOR_TARGET_POLY_V3"><gml:exterior><gml:LinearRing gml:id="EXPORTERROR_CDR_WALL_STAMP_DOOR_TARGET_POLY_V3_0_"><gml:posList srsDimension="3">6 0 0 6 4 0 6 4 3 6 0 3 6 0 0</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></lod3MultiSurface></con:WallSurface></boundary>

<boundary><con:RoofSurface gml:id="EXPORTERROR_CDR_ROOF_V3"><gml:name>Roof</gml:name><lod3MultiSurface><gml:MultiSurface gml:id="EXPORTERROR_CDR_ROOF_V3_MS"><gml:surfaceMember><gml:Polygon gml:id="EXPORTERROR_CDR_ROOF_POLY_V3"><gml:exterior><gml:LinearRing gml:id="EXPORTERROR_CDR_ROOF_POLY_V3_0_"><gml:posList srsDimension="3">0 0 3 0 4 3 6 4 3 6 0 3 0 0 3</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></lod3MultiSurface></con:RoofSurface></boundary>

<boundary><con:GroundSurface gml:id="EXPORTERROR_CDR_GROUND_V3"><gml:name>Ground</gml:name><lod3MultiSurface><gml:MultiSurface gml:id="EXPORTERROR_CDR_GROUND_V3_MS"><gml:surfaceMember><gml:Polygon gml:id="EXPORTERROR_CDR_GROUND_POLY_V3"><gml:exterior><gml:LinearRing gml:id="EXPORTERROR_CDR_GROUND_POLY_V3_0_"><gml:posList srsDimension="3">0 0 0 6 0 0 6 4 0 0 4 0 0 0 0</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface></lod3MultiSurface></con:GroundSurface></boundary>

    </bldg:Building>
  </cityObjectMember>

</CityModel>

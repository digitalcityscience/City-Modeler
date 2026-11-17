<?xml version="1.0" encoding="UTF-8"?>
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
      <gml:upperCorner>3 1 2</gml:upperCorner>
    </gml:Envelope>
  </gml:boundedBy>
  <cityObjectMember>
    <bldg:Building gml:id="B_PART_TEST_PARENT_V3">
      <gml:name>Building with inline part</gml:name>
      <boundary>
        <con:WallSurface gml:id="B_PARENT_WALL_SURFACE_V3">
          <lod2MultiSurface>
            <gml:MultiSurface gml:id="B_PARENT_WALL_MS_V3">
              <gml:surfaceMember>
                <gml:Polygon gml:id="B_PARENT_WALL_POLY_V3">
                  <gml:exterior>
                    <gml:LinearRing gml:id="B_PARENT_WALL_POLY_V3_0_">
                      <gml:posList srsDimension="3">0 0 0 0 1 0 0 1 2 0 0 2 0 0 0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </lod2MultiSurface>
        </con:WallSurface>
      </boundary>
      <bldg:buildingPart>
        <bldg:BuildingPart gml:id="B_PART_TEST_CHILD_V3">
          <gml:name>Inline building part</gml:name>
          <boundary>
            <con:WallSurface gml:id="B_PART_WALL_SURFACE_V3">
              <lod2MultiSurface>
                <gml:MultiSurface gml:id="B_PART_WALL_MS_V3">
                  <gml:surfaceMember>
                    <gml:Polygon gml:id="B_PART_WALL_POLY_V3">
                      <gml:exterior>
                        <gml:LinearRing gml:id="B_PART_WALL_POLY_V3_0_">
                          <gml:posList srsDimension="3">2 0 0 2 1 0 2 1 1 2 0 1 2 0 0</gml:posList>
                        </gml:LinearRing>
                      </gml:exterior>
                    </gml:Polygon>
                  </gml:surfaceMember>
                </gml:MultiSurface>
              </lod2MultiSurface>
            </con:WallSurface>
          </boundary>
        </bldg:BuildingPart>
      </bldg:buildingPart>
    </bldg:Building>
  </cityObjectMember>
</CityModel>

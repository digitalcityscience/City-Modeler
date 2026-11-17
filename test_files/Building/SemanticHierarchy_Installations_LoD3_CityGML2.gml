<?xml version="1.0" encoding="UTF-8"?>
<CityModel
  xmlns="http://www.opengis.net/citygml/2.0"
  xmlns:gml="http://www.opengis.net/gml"
  xmlns:bldg="http://www.opengis.net/citygml/building/2.0"
  xmlns:xlink="http://www.w3.org/1999/xlink"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://www.opengis.net/citygml/2.0 http://schemas.opengis.net/citygml/2.0/cityGMLBase.xsd http://www.opengis.net/citygml/building/2.0 http://schemas.opengis.net/citygml/building/2.0/building.xsd">
  <gml:boundedBy>
    <gml:Envelope srsName="urn:ogc:def:crs:EPSG::25832" srsDimension="3">
      <gml:lowerCorner>0 0 0</gml:lowerCorner>
      <gml:upperCorner>10 1 2</gml:upperCorner>
    </gml:Envelope>
  </gml:boundedBy>
  <cityObjectMember>
    <bldg:Building gml:id="SEMHIER_BUILDING_V2">
      <gml:name>Semantic Hierarchy Resolver Fixture</gml:name>

      <!-- Rung 2: explicit bldg:class (codelist 5010 = Balcony, see
           shared/semantic_types.py INSTALLATION_CLASS_CODES) -> Balcony.
           Name deliberately non-matching so only the class rung can fire. -->
      <bldg:outerBuildingInstallation>
        <bldg:BuildingInstallation gml:id="SEMHIER_INST_CLASS_BALCONY_V2">
          <gml:name>Installation A</gml:name>
          <bldg:class>5010</bldg:class>
          <bldg:lod3Geometry>
            <gml:MultiSurface gml:id="SEMHIER_INST_CLASS_BALCONY_MS_V2">
              <gml:surfaceMember>
                <gml:Polygon gml:id="SEMHIER_INST_CLASS_BALCONY_POLY_V2">
                  <gml:exterior>
                    <gml:LinearRing gml:id="SEMHIER_INST_CLASS_BALCONY_POLY_V2_0_">
                      <gml:posList srsDimension="3">2 0 0 2 1 0 2 1 1 2 0 1 2 0 0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </bldg:lod3Geometry>
        </bldg:BuildingInstallation>
      </bldg:outerBuildingInstallation>

      <!-- Rung 3: explicit bldg:function (codelist 5030 = Chimney) -> Chimney. -->
      <bldg:outerBuildingInstallation>
        <bldg:BuildingInstallation gml:id="SEMHIER_INST_FUNCTION_CHIMNEY_V2">
          <gml:name>Installation B</gml:name>
          <bldg:function>5030</bldg:function>
          <bldg:lod3Geometry>
            <gml:MultiSurface gml:id="SEMHIER_INST_FUNCTION_CHIMNEY_MS_V2">
              <gml:surfaceMember>
                <gml:Polygon gml:id="SEMHIER_INST_FUNCTION_CHIMNEY_POLY_V2">
                  <gml:exterior>
                    <gml:LinearRing gml:id="SEMHIER_INST_FUNCTION_CHIMNEY_POLY_V2_0_">
                      <gml:posList srsDimension="3">4 0 0 4 1 0 4 1 1 4 0 1 4 0 0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </bldg:lod3Geometry>
        </bldg:BuildingInstallation>
      </bldg:outerBuildingInstallation>

      <!-- Rung 6: name only, tokenized exact match -> Balcony. -->
      <bldg:outerBuildingInstallation>
        <bldg:BuildingInstallation gml:id="SEMHIER_INST_NAME_BALCONY_V2">
          <gml:name>Balcony 1</gml:name>
          <bldg:lod3Geometry>
            <gml:MultiSurface gml:id="SEMHIER_INST_NAME_BALCONY_MS_V2">
              <gml:surfaceMember>
                <gml:Polygon gml:id="SEMHIER_INST_NAME_BALCONY_POLY_V2">
                  <gml:exterior>
                    <gml:LinearRing gml:id="SEMHIER_INST_NAME_BALCONY_POLY_V2_0_">
                      <gml:posList srsDimension="3">6 0 0 6 1 0 6 1 1 6 0 1 6 0 0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </bldg:lod3Geometry>
        </bldg:BuildingInstallation>
      </bldg:outerBuildingInstallation>

      <!-- Rung 7: bare - no class/function/usage/name -> generic BuildingInstallation. -->
      <bldg:outerBuildingInstallation>
        <bldg:BuildingInstallation gml:id="SEMHIER_INST_BARE_V2">
          <bldg:lod3Geometry>
            <gml:MultiSurface gml:id="SEMHIER_INST_BARE_MS_V2">
              <gml:surfaceMember>
                <gml:Polygon gml:id="SEMHIER_INST_BARE_POLY_V2">
                  <gml:exterior>
                    <gml:LinearRing gml:id="SEMHIER_INST_BARE_POLY_V2_0_">
                      <gml:posList srsDimension="3">8 0 0 8 1 0 8 1 1 8 0 1 8 0 0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </bldg:lod3Geometry>
        </bldg:BuildingInstallation>
      </bldg:outerBuildingInstallation>

      <!-- Conflict: bldg:function (Chimney) beats a same-installation name of
           "Balcony 1" - rung 3 beats rung 6. -->
      <bldg:outerBuildingInstallation>
        <bldg:BuildingInstallation gml:id="SEMHIER_INST_CONFLICT_FUNCTION_BEATS_NAME_V2">
          <gml:name>Balcony 1</gml:name>
          <bldg:function>5030</bldg:function>
          <bldg:lod3Geometry>
            <gml:MultiSurface gml:id="SEMHIER_INST_CONFLICT_MS_V2">
              <gml:surfaceMember>
                <gml:Polygon gml:id="SEMHIER_INST_CONFLICT_POLY_V2">
                  <gml:exterior>
                    <gml:LinearRing gml:id="SEMHIER_INST_CONFLICT_POLY_V2_0_">
                      <gml:posList srsDimension="3">10 0 0 10 1 0 10 1 1 10 0 1 10 0 0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </bldg:lod3Geometry>
        </bldg:BuildingInstallation>
      </bldg:outerBuildingInstallation>

      <bldg:boundedBy>
        <bldg:WallSurface gml:id="SEMHIER_WALL_V2">
          <bldg:lod3MultiSurface>
            <gml:MultiSurface gml:id="SEMHIER_WALL_MS_V2">
              <gml:surfaceMember>
                <gml:Polygon gml:id="SEMHIER_WALL_POLY_V2">
                  <gml:exterior>
                    <gml:LinearRing gml:id="SEMHIER_WALL_POLY_V2_0_">
                      <gml:posList srsDimension="3">0 0 0 0 1 0 0 1 2 0 0 2 0 0 0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </bldg:lod3MultiSurface>
        </bldg:WallSurface>
      </bldg:boundedBy>
    </bldg:Building>
  </cityObjectMember>
</CityModel>

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
      <gml:upperCorner>10 1 2</gml:upperCorner>
    </gml:Envelope>
  </gml:boundedBy>
  <cityObjectMember>
    <bldg:Building gml:id="SEMHIER_BUILDING_V3">
      <gml:name>Semantic Hierarchy Resolver Fixture</gml:name>

      <boundary>
        <con:WallSurface gml:id="SEMHIER_WALL_V3">
          <lod3MultiSurface>
            <gml:MultiSurface gml:id="SEMHIER_WALL_MS_V3">
              <gml:surfaceMember>
                <gml:Polygon gml:id="SEMHIER_WALL_POLY_V3">
                  <gml:exterior>
                    <gml:LinearRing gml:id="SEMHIER_WALL_POLY_V3_0_">
                      <gml:posList srsDimension="3">0 0 0 0 1 0 0 1 2 0 0 2 0 0 0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </lod3MultiSurface>
        </con:WallSurface>
      </boundary>

      <!-- Rung 2: explicit bldg:class (codelist 5010 = Balcony, see
           shared/semantic_types.py INSTALLATION_CLASS_CODES) -> Balcony.
           Name deliberately non-matching so only the class rung can fire. -->
      <bldg:buildingInstallation>
        <bldg:BuildingInstallation gml:id="SEMHIER_INST_CLASS_BALCONY_V3">
          <gml:name>Installation A</gml:name>
          <lod3MultiSurface>
            <gml:MultiSurface gml:id="SEMHIER_INST_CLASS_BALCONY_MS_V3">
              <gml:surfaceMember>
                <gml:Polygon gml:id="SEMHIER_INST_CLASS_BALCONY_POLY_V3">
                  <gml:exterior>
                    <gml:LinearRing gml:id="SEMHIER_INST_CLASS_BALCONY_POLY_V3_0_">
                      <gml:posList srsDimension="3">2 0 0 2 1 0 2 1 1 2 0 1 2 0 0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </lod3MultiSurface>
          <bldg:class>5010</bldg:class>
        </bldg:BuildingInstallation>
      </bldg:buildingInstallation>

      <!-- Rung 3: explicit bldg:function (codelist 5030 = Chimney) -> Chimney. -->
      <bldg:buildingInstallation>
        <bldg:BuildingInstallation gml:id="SEMHIER_INST_FUNCTION_CHIMNEY_V3">
          <gml:name>Installation B</gml:name>
          <lod3MultiSurface>
            <gml:MultiSurface gml:id="SEMHIER_INST_FUNCTION_CHIMNEY_MS_V3">
              <gml:surfaceMember>
                <gml:Polygon gml:id="SEMHIER_INST_FUNCTION_CHIMNEY_POLY_V3">
                  <gml:exterior>
                    <gml:LinearRing gml:id="SEMHIER_INST_FUNCTION_CHIMNEY_POLY_V3_0_">
                      <gml:posList srsDimension="3">4 0 0 4 1 0 4 1 1 4 0 1 4 0 0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </lod3MultiSurface>
          <bldg:function>5030</bldg:function>
        </bldg:BuildingInstallation>
      </bldg:buildingInstallation>

      <!-- Rung 6: name only, tokenized exact match -> Balcony. -->
      <bldg:buildingInstallation>
        <bldg:BuildingInstallation gml:id="SEMHIER_INST_NAME_BALCONY_V3">
          <gml:name>Balcony 1</gml:name>
          <lod3MultiSurface>
            <gml:MultiSurface gml:id="SEMHIER_INST_NAME_BALCONY_MS_V3">
              <gml:surfaceMember>
                <gml:Polygon gml:id="SEMHIER_INST_NAME_BALCONY_POLY_V3">
                  <gml:exterior>
                    <gml:LinearRing gml:id="SEMHIER_INST_NAME_BALCONY_POLY_V3_0_">
                      <gml:posList srsDimension="3">6 0 0 6 1 0 6 1 1 6 0 1 6 0 0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </lod3MultiSurface>
        </bldg:BuildingInstallation>
      </bldg:buildingInstallation>

      <!-- Rung 7: bare - no class/function/usage/name -> generic BuildingInstallation. -->
      <bldg:buildingInstallation>
        <bldg:BuildingInstallation gml:id="SEMHIER_INST_BARE_V3">
          <lod3MultiSurface>
            <gml:MultiSurface gml:id="SEMHIER_INST_BARE_MS_V3">
              <gml:surfaceMember>
                <gml:Polygon gml:id="SEMHIER_INST_BARE_POLY_V3">
                  <gml:exterior>
                    <gml:LinearRing gml:id="SEMHIER_INST_BARE_POLY_V3_0_">
                      <gml:posList srsDimension="3">8 0 0 8 1 0 8 1 1 8 0 1 8 0 0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </lod3MultiSurface>
        </bldg:BuildingInstallation>
      </bldg:buildingInstallation>

      <!-- Conflict: bldg:function (Chimney) beats a same-installation name of
           "Balcony 1" - rung 3 beats rung 6. -->
      <bldg:buildingInstallation>
        <bldg:BuildingInstallation gml:id="SEMHIER_INST_CONFLICT_FUNCTION_BEATS_NAME_V3">
          <gml:name>Balcony 1</gml:name>
          <lod3MultiSurface>
            <gml:MultiSurface gml:id="SEMHIER_INST_CONFLICT_MS_V3">
              <gml:surfaceMember>
                <gml:Polygon gml:id="SEMHIER_INST_CONFLICT_POLY_V3">
                  <gml:exterior>
                    <gml:LinearRing gml:id="SEMHIER_INST_CONFLICT_POLY_V3_0_">
                      <gml:posList srsDimension="3">10 0 0 10 1 0 10 1 1 10 0 1 10 0 0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </lod3MultiSurface>
          <bldg:function>5030</bldg:function>
        </bldg:BuildingInstallation>
      </bldg:buildingInstallation>
    </bldg:Building>
  </cityObjectMember>
</CityModel>

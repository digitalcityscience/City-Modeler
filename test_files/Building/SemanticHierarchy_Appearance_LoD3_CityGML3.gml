<?xml version="1.0" encoding="UTF-8"?>
<CityModel
  xmlns="http://www.opengis.net/citygml/3.0"
  xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:bldg="http://www.opengis.net/citygml/building/3.0"
  xmlns:con="http://www.opengis.net/citygml/construction/3.0"
  xmlns:app="http://www.opengis.net/citygml/appearance/3.0"
  xmlns:xlink="http://www.w3.org/1999/xlink"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://www.opengis.net/citygml/3.0 http://schemas.opengis.net/citygml/3.0/cityGMLBase.xsd http://www.opengis.net/citygml/building/3.0 http://schemas.opengis.net/citygml/building/3.0/building.xsd http://www.opengis.net/citygml/construction/3.0 http://schemas.opengis.net/citygml/construction/3.0/construction.xsd http://www.opengis.net/citygml/appearance/3.0 http://schemas.opengis.net/citygml/appearance/3.0/appearance.xsd">
  <gml:boundedBy>
    <gml:Envelope srsName="urn:ogc:def:crs:EPSG::25832" srsDimension="3">
      <gml:lowerCorner>0 0 0</gml:lowerCorner>
      <gml:upperCorner>3 1 2</gml:upperCorner>
    </gml:Envelope>
  </gml:boundedBy>
  <cityObjectMember>
    <bldg:Building gml:id="SEMHIER_APP_BUILDING_V3">
      <gml:name>Semantic Hierarchy Appearance Fixture</gml:name>

      <boundary>
        <con:WallSurface gml:id="SEMHIER_APP_WALL_V3">
          <lod3MultiSurface>
            <gml:MultiSurface gml:id="SEMHIER_APP_WALL_MS_V3">
              <gml:surfaceMember>
                <gml:Polygon gml:id="SEMHIER_APP_WALL_POLY_V3">
                  <gml:exterior>
                    <gml:LinearRing gml:id="SEMHIER_APP_WALL_POLY_V3_0_">
                      <gml:posList srsDimension="3">0 0 0 0 1 0 0 1 2 0 0 2 0 0 0</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </lod3MultiSurface>
          <con:fillingSurface>
            <con:WindowSurface gml:id="SEMHIER_APP_WINDOW_V3">
              <lod3MultiSurface>
                <gml:MultiSurface gml:id="SEMHIER_APP_WINDOW_MS_V3">
                  <gml:surfaceMember>
                    <gml:Polygon gml:id="SEMHIER_APP_WINDOW_POLY_V3">
                      <gml:exterior>
                        <gml:LinearRing gml:id="SEMHIER_APP_WINDOW_POLY_V3_0_">
                          <gml:posList srsDimension="3">0 0.3 0.3 0 0.7 0.3 0 0.7 1.3 0 0.3 1.3 0 0.3 0.3</gml:posList>
                        </gml:LinearRing>
                      </gml:exterior>
                    </gml:Polygon>
                  </gml:surfaceMember>
                </gml:MultiSurface>
              </lod3MultiSurface>
            </con:WindowSurface>
          </con:fillingSurface>
        </con:WallSurface>
      </boundary>

      <bldg:buildingInstallation>
        <bldg:BuildingInstallation gml:id="SEMHIER_APP_BALCONY_V3">
          <gml:name>Installation Appearance</gml:name>
          <lod3MultiSurface>
            <gml:MultiSurface gml:id="SEMHIER_APP_BALCONY_MS_V3">
              <gml:surfaceMember>
                <gml:Polygon gml:id="SEMHIER_APP_BALCONY_POLY_V3">
                  <gml:exterior>
                    <gml:LinearRing gml:id="SEMHIER_APP_BALCONY_POLY_V3_0_">
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
    </bldg:Building>
  </cityObjectMember>

  <appearanceMember>
    <app:Appearance gml:id="SEMHIER_APP_APPEARANCE_V3">
      <app:theme>Default</app:theme>
      <app:surfaceData>
        <app:X3DMaterial gml:id="SEMHIER_APP_WINDOW_MAT_V3">
          <app:isFront>true</app:isFront>
          <app:diffuseColor>0.1 0.2 0.8</app:diffuseColor>
          <app:target>#SEMHIER_APP_WINDOW_POLY_V3</app:target>
        </app:X3DMaterial>
      </app:surfaceData>
      <app:surfaceData>
        <app:X3DMaterial gml:id="SEMHIER_APP_BALCONY_MAT_V3">
          <app:isFront>true</app:isFront>
          <app:diffuseColor>0.8 0.6 0.1</app:diffuseColor>
          <app:target>#SEMHIER_APP_BALCONY_POLY_V3</app:target>
        </app:X3DMaterial>
      </app:surfaceData>
    </app:Appearance>
  </appearanceMember>
</CityModel>

<?xml version='1.0' encoding='UTF-8'?>
<!--
  Ticket 19 fixture: CityGML 3 openings using the SECOND permitted spelling,
  con:filling/con:Window and con:filling/con:Door (the alternative to
  con:fillingSurface/con:WindowSurface used by
  Railway_Scene_LoD3_CityGML3_Building_only.gml).

  One Building, one WallSurface carrying its own wall polygon plus one Window
  and one Door, each nested under con:filling. Coordinates are lifted verbatim
  from the Railway LoD3 fixture so the geometry is real and non-degenerate.
  This fixture exists only to prove the con:filling/con:Window spelling
  explodes and parents correctly (docs/agent/semantic-hierarchy/issues/
  19-citygml3-alternate-opening-spelling-fixture.md).
-->
<CityModel xmlns:con="http://www.opengis.net/citygml/construction/3.0" xmlns="http://www.opengis.net/citygml/3.0" xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:bldg="http://www.opengis.net/citygml/building/3.0" xmlns:gen="http://www.opengis.net/citygml/generics/3.0" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <cityObjectMember>
    <bldg:Building gml:id="GMLID_BUI_ALTSPELLING_1">
      <gml:name>Alt Opening Spelling Building</gml:name>
      <boundary>
        <con:WallSurface gml:id="UUID_wall_altspelling_1">
          <gml:name>Wall 1</gml:name>
          <lod3MultiSurface>
            <gml:MultiSurface gml:id="UUID_wall_ms_1">
              <gml:surfaceMember>
                <gml:Polygon gml:id="Poly_wall_1">
                  <gml:exterior>
                    <gml:LinearRing gml:id="Poly_wall_1_0_">
                      <gml:posList srsDimension="3">-394.2600821489736 436.1893838939515 89.8233841428792 -394.2600875473224 436.1893887713968 94.9233755042238 -388.466235478404 437.7485946076798 94.9233792416004 -388.4662304630421 437.7485915079763 89.8233947977572 -389.3353072807215 437.5147080209791 89.8233884457525 -389.33531046284975 437.5147109208244 92.8233859661199 -390.494081291568 437.202869981509 92.82338217886442 -390.4940784924267 437.2028688594055 89.8233915759981 -394.2600821489736 436.1893838939515 89.8233841428792</gml:posList>
                    </gml:LinearRing>
                  </gml:exterior>
                </gml:Polygon>
              </gml:surfaceMember>
            </gml:MultiSurface>
          </lod3MultiSurface>
          <con:filling>
            <con:Window gml:id="UUID_window_altspelling_1">
              <lod3MultiSurface>
                <gml:MultiSurface gml:id="UUID_window_ms_1">
                  <gml:surfaceMember>
                    <gml:Polygon gml:id="Poly_window_1">
                      <gml:exterior>
                        <gml:LinearRing gml:id="Poly_window_1_0_">
                          <gml:posList srsDimension="3">-395.3697798913599 436.3878289223974 96.3333788866911 -396.2967966045681 436.1383573817305 96.33337610053889 -396.3279805552572 436.2542337373066 96.3333766125102 -395.4009639106386 436.50370545297073 96.33337939866209 -395.3697798913599 436.3878289223974 96.3333788866911</gml:posList>
                        </gml:LinearRing>
                      </gml:exterior>
                    </gml:Polygon>
                  </gml:surfaceMember>
                </gml:MultiSurface>
              </lod3MultiSurface>
            </con:Window>
          </con:filling>
          <con:filling>
            <con:Door gml:id="UUID_door_altspelling_1">
              <lod3MultiSurface>
                <gml:MultiSurface gml:id="UUID_door_ms_1">
                  <gml:surfaceMember>
                    <gml:Polygon gml:id="Poly_door_1">
                      <gml:exterior>
                        <gml:LinearRing gml:id="Poly_door_1_0_">
                          <gml:posList srsDimension="3">-390.4940784924267 437.2028688594055 89.8233915759981 -390.494081291568 437.202869981509 92.82338217886442 -389.33531046284975 437.5147109208244 92.8233859661199 -389.3353072807215 437.5147080209791 89.8233884457525 -390.4940784924267 437.2028688594055 89.8233915759981</gml:posList>
                        </gml:LinearRing>
                      </gml:exterior>
                    </gml:Polygon>
                  </gml:surfaceMember>
                </gml:MultiSurface>
              </lod3MultiSurface>
            </con:Door>
          </con:filling>
        </con:WallSurface>
      </boundary>
    </bldg:Building>
  </cityObjectMember>
</CityModel>

"""Small CityGML documents for the T08 unit tests (written to tmp_path)."""

NS_30 = (
    'xmlns="http://www.opengis.net/citygml/3.0" '
    'xmlns:gml="http://www.opengis.net/gml/3.2" '
    'xmlns:bldg="http://www.opengis.net/citygml/building/3.0" '
    'xmlns:app="http://www.opengis.net/citygml/appearance/3.0" '
    'xmlns:core="http://www.opengis.net/citygml/3.0" '
    'xmlns:xlink="http://www.w3.org/1999/xlink"'
)


def _solid(pid, x, y, z0=10.0, z1=20.0, dim_attr=' srsDimension="3"'):
    ring = f"{x} {y} {z0} {x + 10} {y} {z0} {x + 10} {y + 10} {z1} {x} {y} {z0}"
    return (
        f'<bldg:lod2Solid><gml:Solid><gml:exterior><gml:Shell><gml:surfaceMember>'
        f'<gml:Polygon gml:id="{pid}"><gml:exterior><gml:LinearRing>'
        f'<gml:posList{dim_attr}>{ring}</gml:posList>'
        f'</gml:LinearRing></gml:exterior></gml:Polygon>'
        f'</gml:surfaceMember></gml:Shell></gml:exterior></gml:Solid></bldg:lod2Solid>'
    )


def _env(lx, ly, lz, ux, uy, uz):
    return (f'<gml:boundedBy><gml:Envelope srsDimension="3"><gml:lowerCorner>{lx} {ly} {lz}</gml:lowerCorner>'
            f'<gml:upperCorner>{ux} {uy} {uz}</gml:upperCorner></gml:Envelope></gml:boundedBy>')


def citygml30(with_appearance=True) -> str:
    """Six buildings with one property each to check (see test_t08_index.py)."""
    a = (f'<cityObjectMember><bldg:Building gml:id="A">{_env(1000, 2000, 10, 1010, 2010, 20)}'
         f'{_solid("A_p1", 1000, 2000)}</bldg:Building></cityObjectMember>')
    # envelope 1 m off -> envelope_mismatch; has a name
    b = (f'<cityObjectMember><bldg:Building gml:id="B"><gml:name>Bee</gml:name>'
         f'{_env(1100, 2000, 10, 1111, 2010, 20)}{_solid("B_p1", 1100, 2000)}</bldg:Building></cityObjectMember>')
    # real LoD0 with single gml:pos elements and srsDimension inherited from the Polygon
    c = ('<cityObjectMember><bldg:Building gml:id="C"><bldg:lod0MultiSurface><gml:MultiSurface>'
         '<gml:surfaceMember><gml:Polygon srsDimension="3"><gml:exterior><gml:LinearRing>'
         '<gml:pos>1200 2000 5</gml:pos><gml:pos>1210 2000 5</gml:pos><gml:pos>1210 2010 5</gml:pos>'
         '<gml:pos>1200 2000 5</gml:pos></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>'
         '</gml:MultiSurface></bldg:lod0MultiSurface>'
         f'{_solid("C_p1", 1200, 2000)}</bldg:Building></cityObjectMember>')
    # ImplicitGeometry: template coordinates near 0, reference point in world coordinates
    d = ('<cityObjectMember><bldg:Building gml:id="D"><bldg:lod2ImplicitRepresentation><ImplicitGeometry>'
         '<relativeGeometry><gml:Point><gml:pos>0 0 0</gml:pos></gml:Point></relativeGeometry>'
         '<referencePoint><gml:Point><gml:pos>1300 2000 7</gml:pos></gml:Point></referencePoint>'
         '</ImplicitGeometry></bldg:lod2ImplicitRepresentation></bldg:Building></cityObjectMember>')
    # geometry by reference to a polygon of A -> external_xlink
    e = ('<cityObjectMember><bldg:Building gml:id="E"><bldg:lod2MultiSurface><gml:MultiSurface>'
         '<gml:surfaceMember xlink:href="#A_p1"/></gml:MultiSurface></bldg:lod2MultiSurface>'
         f'{_solid("E_p1", 1400, 2000)}</bldg:Building></cityObjectMember>')
    # srsName of another CRS -> srs_mismatch; 2D coordinates via gml:coordinates
    f = ('<cityObjectMember><bldg:Building gml:id="F"><bldg:lod2MultiSurface>'
         '<gml:MultiSurface srsName="EPSG:4326" srsDimension="2"><gml:surfaceMember><gml:Polygon>'
         '<gml:exterior><gml:LinearRing><gml:coordinates>1500,2000 1510,2000 1510,2010 1500,2000</gml:coordinates>'
         '</gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember></gml:MultiSurface>'
         '</bldg:lod2MultiSurface></bldg:Building></cityObjectMember>')
    app = ('<appearanceMember><app:Appearance gml:id="APP1"><app:surfaceDataMember>'
           '<app:ParameterizedTexture><app:imageURI>tex/a.jpg</app:imageURI>'
           '<app:target><app:TextureAssociation><app:target xlink:href="#A_p1"/></app:TextureAssociation></app:target>'
           '</app:ParameterizedTexture></app:surfaceDataMember></app:Appearance></appearanceMember>')
    body = a + "\n" + b + "\n" + c + "\n" + d + "\n" + e + "\n" + f + ("\n" + app if with_appearance else "")
    return (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<!-- comment before the root -->\n"
        f"<CityModel {NS_30}>\n"
        '<gml:boundedBy><gml:Envelope srsName="urn:ogc:def:crs:EPSG::25832" srsDimension="3">'
        "<gml:lowerCorner>1000 2000 5</gml:lowerCorner><gml:upperCorner>1510 2010 20</gml:upperCorner>"
        "</gml:Envelope></gml:boundedBy>\n"
        f"{body}\n</CityModel>\n"
    )


def citygml10(n=3) -> str:
    """CityGML 1.0 (Hamburg style): prefixed root, no feature envelopes, srsName only on the root."""
    members = "".join(
        f'\n  <core:cityObjectMember>\n    <bldg:Building gml:id="HH{i}">'
        f'<bldg:lod2Solid><gml:Solid><gml:exterior><gml:CompositeSurface><gml:surfaceMember>'
        f'<gml:Polygon gml:id="HH{i}_p"><gml:exterior><gml:LinearRing><gml:posList srsDimension="3">'
        f'{564000 + 20 * i} 5934000 13 {564010 + 20 * i} 5934000 13 {564010 + 20 * i} 5934010 25 {564000 + 20 * i} 5934000 13'
        f'</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>'
        f'</gml:CompositeSurface></gml:exterior></gml:Solid></bldg:lod2Solid>'
        f'</bldg:Building>\n  </core:cityObjectMember>'
        for i in range(n)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<core:CityModel xmlns:bldg="http://www.opengis.net/citygml/building/1.0" '
        'xmlns:gml="http://www.opengis.net/gml" xmlns:app="http://www.opengis.net/citygml/appearance/1.0" '
        'xmlns:core="http://www.opengis.net/citygml/1.0" xmlns:gen="http://www.opengis.net/citygml/generics/1.0" '
        'xmlns:veg="http://www.opengis.net/citygml/vegetation/1.0" '
        'xmlns:xlink="http://www.w3.org/1999/xlink">\n'
        '  <gml:boundedBy><gml:Envelope srsName="urn:adv:crs:ETRS89_UTM32*DE_DHHN2016_NH" srsDimension="3">'
        '<gml:lowerCorner>564000 5934000 13</gml:lowerCorner><gml:upperCorner>565000 5935000 80</gml:upperCorner>'
        '</gml:Envelope></gml:boundedBy>'
        f"{members}\n</core:CityModel>\n"
    )


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return str(p)

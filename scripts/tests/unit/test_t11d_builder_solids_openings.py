"""T11d (part 2): CityGML 3 inline solids and filling surfaces in the builder payload, and the
things the builder leaves to the old importer (interior spaces), no Blender.

Interior spaces (Room, Storey, BuildingUnit, furniture) are out of scope for this project:
making sense of them is a job for 3DCityDB v5, not for the Blender add-on. The builder
therefore never builds them (``unsupported_reasons`` names them), see tickets/T11d.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import builder_payload as bp  # noqa: E402
from shared.citygml_reader import read_document  # noqa: E402
from test_t11b_gml_geometry import FIXTURES  # noqa: E402
from test_t11c_citygml2_to_ir import need  # noqa: E402
from test_t11d_builder_payload_30 import doc3  # noqa: E402

EXPORT_ERROR = FIXTURES / "Building" / "ExportError_CityDoctorRegression_LoD3_CityGML3.gml"
SQ = "0 0 0 1 0 0 1 1 0 0 1 0 0 0 0"


def plan30(path, **kwargs):
    need(path)
    roots = read_document(ET.parse(path).getroot(), upgrade=False)
    assert bp.unsupported_reasons(roots, "3.0") == []
    return bp.plan_document(roots, (0.0, 0.0, 0.0), schema="3.0", **kwargs)


def poly(pid, coords=SQ):
    return (f'<gml:surfaceMember><gml:Polygon gml:id="{pid}"><gml:exterior><gml:LinearRing>'
            f'<gml:posList>{coords}</gml:posList></gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>')


@pytest.mark.parametrize("child", [
    '<bldg:buildingRoom><bldg:BuildingRoom gml:id="R"/></bldg:buildingRoom>',
    '<bldg:buildingSubdivision><bldg:Storey gml:id="S"/></bldg:buildingSubdivision>',
    '<bldg:buildingFurniture><bldg:BuildingFurniture gml:id="F"/></bldg:buildingFurniture>',
    '<bldg:buildingSubdivision><bldg:BuildingUnit gml:id="U"/></bldg:buildingSubdivision>',
])
def test_interior_spaces_are_left_to_the_old_importer(child):
    roots = read_document(doc3(f'<bldg:Building gml:id="B">{child}</bldg:Building>'), upgrade=False)
    reasons = bp.unsupported_reasons(roots, "3.0")
    assert len(reasons) == 1 and reasons[0].startswith("feature type ")


def test_2_0_room_is_left_to_the_old_importer():
    from test_t11c_citygml2_to_ir import doc2
    roots = read_document(doc2('<bldg:Building gml:id="B"><bldg:interiorRoom><bldg:Room gml:id="R"/>'
                               '</bldg:interiorRoom></bldg:Building>'), upgrade=False)
    assert bp.unsupported_reasons(roots, "2.0") == ["feature type Room"]


def test_geometry_in_an_unmapped_element_is_not_built():
    body = ('<bldg:Building gml:id="B"><core:boundary><con:WallSurface gml:id="W">'
            f'<core:lod2MultiSurface><gml:MultiSurface>{poly("P")}</gml:MultiSurface></core:lod2MultiSurface>'
            '<con:somethingNew><gml:MultiSurface>' + poly("Q") + '</gml:MultiSurface></con:somethingNew>'
            '</con:WallSurface></core:boundary></bldg:Building>')
    roots = read_document(doc3(body), upgrade=False)
    assert bp.unsupported_reasons(roots, "3.0") == ["geometry inside an element the IR does not map"]


def test_filling_surface_windows_are_openings():
    plan = plan30(EXPORT_ERROR)
    building = plan.features[0]
    labels = [l[0] for l in building.geometry.surf_labels]
    assert labels.count("WindowSurface") == 1 and labels.count("DoorSurface") == 1
    window = next(l for l in building.geometry.surf_labels if l[0] == "WindowSurface")
    attrs = building.geometry.surf_attrs_by_poly[window[1]]
    assert attrs["filling_parent_surface_id"] == "EXPORTERROR_CDR_WALL_HOST_V3"
    assert attrs["BoundarySurfaceType"] == "WallSurface"
    assert building.geometry.filling_parent_surface_id_by_poly[window[1]] == "EXPORTERROR_CDR_WALL_HOST_V3"


def test_inline_solid_is_built_and_flags_its_shell():
    solid = ('<bldg:Building gml:id="B"><core:lod2Solid><gml:Solid><gml:exterior><gml:Shell>'
             + poly("S1") + poly("S2", "5 5 5 6 5 5 6 6 5 5 5 5") +
             '</gml:Shell></gml:exterior></gml:Solid></core:lod2Solid>'
             '<core:lod2MultiSurface><gml:MultiSurface>' + poly("M1", "9 9 9 8 9 9 8 8 9 9 9 9") +
             '</gml:MultiSurface></core:lod2MultiSurface></bldg:Building>')
    roots = read_document(doc3(solid), upgrade=False)
    assert bp.unsupported_reasons(roots, "3.0") == []
    plan = bp.plan_document(roots, (0.0, 0.0, 0.0), schema="3.0", shell_ids={"S1", "S2"})
    labels = plan.features[0].geometry.surf_labels
    # MultiSurface polygons come first although the Solid is written first
    assert [l[1] for l in labels] == ["M1", "S1", "S2"]
    assert [l[6] for l in labels] == [False, True, True]
    assert [l[0] for l in labels] == ["Building"] * 3


@pytest.mark.parametrize("parent,child,schema,kind", [
    ("Building", "BuildingPart", "3.0", "part"),
    ("BuildingPart", "BuildingPart", "3.0", ""),
    ("BuildingPart", "BuildingInstallation", "3.0", "installation"),
    ("Building", "BuildingPart", "2.0", "part"),
])
def test_edge_kinds(parent, child, schema, kind):
    from shared.citygml_ir import SpaceNode
    assert bp.edge_kind(SpaceNode("P", parent), SpaceNode("C", child), schema) == kind



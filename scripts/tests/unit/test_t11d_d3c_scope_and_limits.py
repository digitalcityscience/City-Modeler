"""T11d d3-C: what the builder skips (Tunnel, interior rooms, PointCloud) and the Load-all limits. No Blender."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import builder_payload as bp  # noqa: E402
from shared import load_limits as ll  # noqa: E402
from shared.citygml_reader import read_document  # noqa: E402
from test_t11c_citygml2_to_ir import NS2  # noqa: E402
from test_t11d_builder_payload_30 import NS3  # noqa: E402
from test_t11d_builder_transport import surface  # noqa: E402

EXTRA2 = ('xmlns:tun="http://www.opengis.net/citygml/tunnel/2.0" '
          'xmlns:pcl="http://www.opengis.net/citygml/pointcloud/3.0"')


def members(body, schema="2.0"):
    ns = (NS2 if schema == "2.0" else NS3) + " " + EXTRA2
    return read_document(ET.fromstring(f"<core:CityModel {ns}>{body}</core:CityModel>"), upgrade=False)


def building(gid="B", inner=""):
    return (f'<core:cityObjectMember><bldg:Building gml:id="{gid}">{surface("P" + gid, "lod2MultiSurface", "bldg")}'
            f'{inner}</bldg:Building></core:cityObjectMember>')


TUNNEL = ('<core:cityObjectMember><tun:Tunnel gml:id="T">' + surface("TP", "lod2MultiSurface", "tun") +
          '</tun:Tunnel></core:cityObjectMember>')


def plan(roots, schema="2.0"):
    skip, reasons = bp.skipped_features(roots, schema)
    return skip, reasons, bp.plan_document(roots, (0.0, 0.0, 0.0), schema=schema, skip=skip)


def test_unsupported_feature_is_skipped_and_the_rest_is_built():
    roots = members(building("B1") + TUNNEL + building("B2"))
    skip, reasons, p = plan(roots)
    assert reasons == ["feature type Tunnel"]
    assert [f.node.gml_id for f in p.features] == ["B1", "B2"]
    assert len(skip) == 1


def test_only_unsupported_content_builds_nothing():
    roots = members(TUNNEL)
    skip, reasons, p = plan(roots)
    assert reasons and p.features == [] and len(skip) == 1


def test_skip_leaves_the_unfiltered_reasons_api_alone():
    roots = members(building() + TUNNEL)
    assert bp.unsupported_reasons(roots) == ["feature type Tunnel"]


def test_interior_room_is_skipped_but_the_building_shell_stays():
    room = ('<bldg:interiorRoom><bldg:Room gml:id="R">' + surface("RP", "lod4MultiSurface", "bldg") +
            '</bldg:Room></bldg:interiorRoom>')
    roots = members(building("B", room))
    skip, reasons, p = plan(roots)
    assert [f.node.gml_id for f in p.features] == ["B"]
    assert any("Room" in r for r in reasons)


def test_pointcloud_member_is_skipped_and_named():
    cloud = ('<core:cityObjectMember><pcl:PointCloud gml:id="PC"><pcl:points>'
             '<gml:Point><gml:pos>1 2 3</gml:pos></gml:Point></pcl:points></pcl:PointCloud></core:cityObjectMember>')
    roots = members(building() + cloud, "3.0")
    skip, reasons, p = plan(roots, "3.0")
    assert [f.node.gml_id for f in p.features] == ["B"]
    assert any("PointCloud" in r for r in reasons)


def test_notice_names_reasons_and_3dcitydb():
    text = bp.scope_notice(["feature type Tunnel"])
    assert text.startswith("Skipped") and "Tunnel" in text and "3DCityDB v5" in text


# -- Load all limit -------------------------------------------------------------------------------------

def test_small_file_is_silent():
    assert ll.size_notice(5.0, 30.0) == ""


def test_large_file_gets_a_memory_warning():
    text = ll.size_notice(120.0, 30.0)
    assert "120 MB" in text and "memory" in text and "Browse first" in text


def test_file_above_the_limit_is_refused_with_a_pointer_to_3dcitydb():
    with pytest.raises(ll.ImportTooLarge) as exc:
        ll.size_notice(201.0, 30.0)
    assert "Browse first" in str(exc.value) and "3DCityDB v5" in str(exc.value)


def test_limit_is_200_mb_and_exactly_200_is_still_allowed():
    assert ll.LOAD_ALL_MAX_MB == 200.0
    assert "memory" in ll.size_notice(200.0, 30.0)


def test_limit_can_be_lowered_by_the_caller():
    with pytest.raises(ll.ImportTooLarge):
        ll.size_notice(2.0, 1.0, max_mb=1.5)
    assert "memory" in ll.size_notice(1.2, 1.0, max_mb=1.5)


def test_feature_limit_is_2000_and_exactly_2000_is_still_allowed():
    assert ll.LOAD_ALL_MAX_FEATURES == 2000
    ll.check_feature_count(2000)
    with pytest.raises(ll.ImportTooLarge) as exc:
        ll.check_feature_count(2001)
    assert "2,001 features" in str(exc.value) and "3DCityDB v5" in str(exc.value)


def test_feature_limit_can_be_lowered_by_the_caller():
    ll.check_feature_count(2, max_features=2)
    with pytest.raises(ll.ImportTooLarge):
        ll.check_feature_count(3, max_features=2)

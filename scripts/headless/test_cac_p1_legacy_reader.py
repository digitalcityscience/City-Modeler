"""CAC-P1.3: records read from legacy state agree with the source CityGML.

For every P0.2 scene (7 fixtures x use_semantic_objects on/off) the scene is
read with blender/legacy_views + domain/legacy_reader. The oracle is the
source XML, not a golden written by this code:

- every source feature id has a record whose kind is the neutral name of
  its element (WindowSurface -> Window);
- in exploded scenes its parent is the nearest enclosing source feature;
- every source polygon belongs to the record of its nearest source
  feature; extra record polygons may only be import-minted ids (synthetic
  opening prism faces);
- interior ring counts per polygon equal the source;
- the record set validates, and exploded scenes produce no notes.

    BLENDER_USER_SCRIPTS=<probe>/bscripts "$BLENDER_EXE" --background --factory-startup \\
      --python scripts/headless/test_cac_p1_legacy_reader.py -- <out_dir>
"""
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cac_harness as h  # noqa: E402

B = h.FIXTURES / "Building"
FIXTURES = [
    ("railway_v2", B / "Railway_Scene_LoD3_CityGML2_Building_only.gml", "2.0"),
    ("railway_v3", B / "Railway_Scene_LoD3_CityGML3_Building_only.gml", "3.0"),
    ("installations_v2", B / "SemanticHierarchy_Installations_LoD3_CityGML2.gml", "2.0"),
    ("installations_v3", B / "SemanticHierarchy_Installations_LoD3_CityGML3.gml", "3.0"),
    ("parts_v2", h.FIXTURES / "BuildingParts" / "BuildingParts_citygml2.gml", "2.0"),
    ("parts_v3", h.FIXTURES / "BuildingParts" / "BuildingParts_citygml3.gml", "3.0"),
    ("altopening_v3", B / "AltOpeningSpelling_LoD3_CityGML3.gml", "3.0"),
]
MINTED = re.compile(r"^ID_[0-9a-f]{32}$")


def gid(elem):
    return next((elem.get(a) for a in h.GML_IDS if elem.get(a) is not None), None)


def source_model(path):
    """{feature id: kind, parent feature id, polygon ids}, {polygon id: interior count}."""
    root = ET.parse(path).getroot()
    parent = {child: elem for elem in root.iter() for child in elem}

    def is_feature(elem):
        return gid(elem) is not None and h.local_name(elem.tag) not in h.NON_FEATURE_ELEMENTS

    def enclosing_feature(elem):
        up = parent.get(elem)
        while up is not None and not is_feature(up):
            up = parent.get(up)
        return up

    features, interiors = {}, {}
    for elem in root.iter():
        if is_feature(elem):
            up = enclosing_feature(elem)
            features[gid(elem)] = {"kind": h.local_name(elem.tag),
                                   "parent": gid(up) if up is not None else "", "polygons": set()}
    for elem in root.iter():
        if h.local_name(elem.tag) == "Polygon" and gid(elem):
            owner = enclosing_feature(elem)
            if owner is not None:
                features[gid(owner)]["polygons"].add(gid(elem))
            interiors[gid(elem)] = sum(1 for c in elem if h.local_name(c.tag) == "interior")
    return features, interiors


def check_scene(R, case, path, exploded):
    from city_modeler.shared.authoring_core.blender.legacy_views import read_scene
    from city_modeler.shared.authoring_core.domain.legacy_reader import neutral_kind

    features, interiors = source_model(path)
    result = read_scene()
    rs = result.records

    missing, wrong_kind, wrong_parent = [], [], []
    for fid, src in features.items():
        rec = rs.feature(fid)
        if rec is None:
            missing.append(fid)
            continue
        if rec.kind != neutral_kind(src["kind"]):
            wrong_kind.append(f"{fid}: {rec.kind} != {src['kind']}")
        if exploded and rec.parent_id != src["parent"]:
            wrong_parent.append(f"{fid} ({rec.kind}): {rec.parent_id!r} != {src['parent']!r}")
    R.check(f"{case}_features_present", not missing, f"{len(missing)} source features without record: {missing[:5]}")
    R.check(f"{case}_kinds", not wrong_kind, "; ".join(wrong_kind[:5]))
    if exploded:
        R.check(f"{case}_parents", not wrong_parent, f"{len(wrong_parent)}: " + "; ".join(wrong_parent[:5]))

    misplaced, extra = [], []
    for fid, src in features.items():
        rec = rs.feature(fid)
        have = set(rec.polygon_ids) if rec else set()
        misplaced += [f"{p} (source {fid})" for p in src["polygons"] - have]
        extra += [p for p in have - src["polygons"] if not MINTED.match(p)]
    R.check(f"{case}_polygons", not misplaced and not extra,
            f"source polygons not on their feature: {misplaced[:5]}; unexpected record polygons: {extra[:5]}")

    ring_diff = [f"{p.polygon_id}: {len(p.interiors)} != {interiors[p.polygon_id]}"
                 for p in rs.polygons if p.polygon_id in interiors and len(p.interiors) != interiors[p.polygon_id]]
    R.check(f"{case}_interior_rings", not ring_diff, f"{len(ring_diff)}: " + "; ".join(ring_diff[:5]))

    problems = rs.validate()
    R.check(f"{case}_validates", not problems, f"{len(problems)}: " + "; ".join(problems[:5]))
    if exploded:
        R.check(f"{case}_no_notes", not result.notes, f"{len(result.notes)}: " + "; ".join(result.notes[:5]))


def main():
    R = h.Results(__file__)
    for name, path, version in FIXTURES:
        for semantic in (True, False):
            h.import_fixture(path, version, use_semantic_objects=semantic)
            check_scene(R, f"{name}_{'sem' if semantic else 'merged'}", path, exploded=semantic)
    R.finish()


main()

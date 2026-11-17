"""Export-error ticket 05: CityDoctor regression fixture for the opening-ring
degeneracy findings (`docs/agent/export-error/opening-ring-degeneracy-findings.md`).

Tickets 02-04 made it impossible to *produce* a degenerate opening/wall ring
in the first place (root-cause guards at Semantic Stamp copy + centered-prism
extrusion, plus a defense-in-depth halt at the shared exporter serializer).
This test is the plan's own verification step, made re-runnable: it drives
the exact workflow that produced `window18.gml`'s 985 `GE_R_*` ring errors -
import a real LoD3 Window/Door wall, exercise Semantic Stamp copy-paste (site
2) on top of import-time synthetic opening depth (site 4) - then shells out
to the CityDoctor docker validator (same external-tool pattern as
`test_ticket18_xmllint_validation.py`, but CityDoctor instead of xmllint) and
asserts zero `GE_R_*` ring errors on both a CityGML 2 and a CityGML 3 export.

The fixture (`test_files/Building/ExportError_CityDoctorRegression_LoD3_CityGML3.gml`)
is a small purpose-built LoD3 building (the findings doc's own `lod3.gml` is
13k lines, outside the repo, and impractical to commit for CI): one host
WallSurface carrying a real flat Window and Door (each a single gml:Polygon,
no gml:interior/depth - exactly the shape that triggers import-time synthetic
depth), plus two bare WallSurfaces used as Semantic Stamp targets.

Usage:
    SP=/tmp/cgml-export-error05
    mkdir -p "$SP/bscripts/addons"
    ln -sfn "$PWD" "$SP/bscripts/addons/city_modeler"
    BLENDER_USER_SCRIPTS=$SP/bscripts blender --background --factory-startup \\
      --python scripts/headless/test_export_error05_citydoctor_regression.py -- "$SP/out"

CityDoctor is invoked via the validator command in the CITYDOCTOR_VALIDATE_SH
env var (one CityGML file in, ``<file>.citydoctor-report.xml`` out); if it isn't set, the CityDoctor checks
are reported as failed rather than silently skipped, since they are this
test's entire point.
"""
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pydeps"))

import bpy

ADDON = "city_modeler"
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "test_files" / "Building" / "ExportError_CityDoctorRegression_LoD3_CityGML3.gml"
VALIDATE_SH = Path(os.environ.get("CITYDOCTOR_VALIDATE_SH", ""))  # empty: not a file, checks fail loudly
CD_NS = {"cd": "http://www.citydoctor.eu"}

FAILURES = []


def args():
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def check(label, condition, detail=""):
    print(f"[export-error05][{label}] {'OK' if condition else 'FAIL'}" + (f": {detail}" if detail else ""))
    if not condition:
        FAILURES.append(f"{label}: {detail}")


def by_gml_id(gid):
    return next((o for o in bpy.data.objects if o.get("gml_id") == gid), None)


def stamp(source, target_wall, position_uv):
    """Headless-sufficient Semantic Stamp: select source in Object Mode, mark
    it, then invoke the operator with an explicit ``object_name``/
    ``face_index`` (bypassing Edit-Mode face-selection resolution, same
    optional path the operator's own `execute()` supports for redo-panel/
    scripted use) so repeated calls can keep targeting the *original* host
    face by index.

    ``create_joined_surfaces`` cuts a new exterior-fill + interior-ring face
    pair into the wall and then removes the (now separate) fill object - the
    host's own original face keeps its full original extent and stays at
    index 0 across repeated stamps (new faces are only ever appended). That
    makes explicit ``face_index=0`` + a distinct ``position_uv`` the way to
    accumulate multiple *non-overlapping* interior rings on one host wall -
    the actual `window18.gml` reproduction shape (several openings on one
    wall polygon), rather than one stamp masking/duplicating another."""
    if bpy.context.view_layer.objects.active is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    assert bpy.ops.cgml.set_semantic_stamp_source() == {'FINISHED'}

    bpy.ops.object.select_all(action='DESELECT')
    target_wall.select_set(True)
    bpy.context.view_layer.objects.active = target_wall
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        result = bpy.ops.cgml.semantic_stamp(
            object_name=target_wall.name, face_index=0, position_uv=position_uv,
        )
    except RuntimeError as e:
        result = {'CANCELLED'}
        stamp.last_error = str(e)
    else:
        stamp.last_error = None
    bpy.ops.object.mode_set(mode='OBJECT')
    return result


def import_and_author():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module=ADDON)
    bpy.context.scene.cgml3.use_semantic_objects = True
    res = bpy.ops.cgml3.import_gml_file(filepath=str(FIXTURE))
    assert res == {'FINISHED'}, f"import failed: {res}"

    window_source = by_gml_id("EXPORTERROR_CDR_WINDOW_SOURCE_V3")
    door_source = by_gml_id("EXPORTERROR_CDR_DOOR_SOURCE_V3")
    window_target = by_gml_id("EXPORTERROR_CDR_WALL_STAMP_WINDOW_TARGET_V3")
    door_target = by_gml_id("EXPORTERROR_CDR_WALL_STAMP_DOOR_TARGET_V3")

    check("fixture: source Window imported", window_source is not None)
    check("fixture: source Door imported", door_source is not None)
    check("fixture: Window stamp target wall imported", window_target is not None)
    check("fixture: Door stamp target wall imported", door_target is not None)
    if None in (window_source, door_source, window_target, door_target):
        print("\n[ABORT] fixture did not import as expected - cannot continue")
        sys.exit(1)

    # Site 4 (import-time synthetic depth): a flat single-polygon opening
    # must have been extruded into a solid prism, not left as a coplanar
    # face - see window-door-upload ticket 02.
    for label, obj in (("Window", window_source), ("Door", door_source)):
        check(
            f"site4: {label} source got synthetic depth",
            obj.get("cgml_synthetic_opening_depth") is True and len(obj.data.polygons) > 1,
            f"cgml_synthetic_opening_depth={obj.get('cgml_synthetic_opening_depth')!r}, "
            f"faces={len(obj.data.polygons)}",
        )

    # Site 2 (Semantic Stamp copy-paste): stamp each source twice onto the
    # *same* bare target wall, accumulating two interior rings on one host
    # polygon - the findings doc's actual worst case (one wall ending up
    # with 6 degenerate interior rings from repeated stamping of one bad
    # source), not just an isolated single-opening smoke test.
    for label, source, target, uv in (
        ("Window#1", window_source, window_target, (0.25, 0.5)),
        ("Window#2", window_source, window_target, (0.75, 0.5)),
    ):
        result = stamp(source, target, uv)
        check(f"site2: {label} stamp succeeded", result == {'FINISHED'},
              f"result={result} error={getattr(stamp, 'last_error', None)!r}")
    # B50: a ring-paired Door is a Semantic Stamp source too (only a ground-notch Door is
    # refused); the imported Door still goes through site 4 and the export.
    bpy.ops.object.select_all(action='DESELECT')
    door_source.select_set(True)
    bpy.context.view_layer.objects.active = door_source
    check("site2: a Door is accepted as Semantic Stamp source (B50)",
          bpy.ops.cgml.set_semantic_stamp_source.poll())


def export_fixture(version, out_path):
    res = bpy.ops.cgml3.export_gml_file(filepath=str(out_path), citygml_version=version)
    assert res == {'FINISHED'}, f"export ({version}) failed: {res}"


def run_citydoctor(gml_path, label):
    if not VALIDATE_SH.is_file():
        check(f"citydoctor:{label}", False,
              f"CityDoctor wrapper not found at {VALIDATE_SH} (cannot validate)")
        return

    report_path = Path(str(gml_path) + ".citydoctor-report.xml")
    if report_path.exists():
        report_path.unlink()

    cmd = [str(VALIDATE_SH), str(gml_path)]
    print(f"[export-error05][{label}] command: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    print(f"[export-error05][{label}] validate.sh exit code: {proc.returncode}")
    if proc.stdout.strip():
        print(f"[export-error05][{label}] stdout: {proc.stdout.strip()}")
    if proc.stderr.strip():
        print(f"[export-error05][{label}] stderr: {proc.stderr.strip()}")

    check(f"citydoctor:{label}: report produced", report_path.is_file(), str(report_path))
    if not report_path.is_file():
        return

    root = ET.parse(str(report_path)).getroot()
    ring_errors = {}
    for err in root.findall("./cd:global_statistics/cd:errors/cd:error", CD_NS):
        name = err.get("name") or ""
        if name.startswith("GE_R_"):
            ring_errors[name] = int(err.text or "0")

    total = sum(ring_errors.values())
    check(f"citydoctor:{label}: 0 GE_R_* ring errors", total == 0,
          f"{ring_errors}" if ring_errors else "none found")


def main():
    out_dir = Path(args()[0]) if args() else Path("/tmp/cgml-out-export-error05")
    out_dir.mkdir(parents=True, exist_ok=True)

    check("harness", FIXTURE.is_file(), f"fixture present: {FIXTURE}")

    import_and_author()

    for version, suffix in (("2.0", "v2"), ("3.0", "v3")):
        out_path = out_dir / f"export_error05_{suffix}.gml"
        export_fixture(version, out_path)
        run_citydoctor(out_path, suffix)

    print("\n=== export-error05 summary ===")
    if FAILURES:
        for f in FAILURES:
            print(f"  FAIL {f}")
        print(f"\nRESULT: FAILED ({len(FAILURES)} checks)")
        sys.exit(1)
    print("RESULT: PASSED")


main()

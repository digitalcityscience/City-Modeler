"""CJ-03: CityJSON 2.0 / CityJSONSeq export of the scene (``cgml3.export_cityjson_file``).

K1 pivot (plan section 3.2): the scene is written by the CityGML 3.0 writer with the GML export's
own rules (``_write_scene_gml``: scope, export defaults, origin, preflight), read back by
``shared/citygml_reader`` (``scene_to_ir``) and turned into CityJSON by ``shared/cityjson_writer``.
What only the scene knows (original CityJSON ids, LoD texts) is put back on the IR by gml:id.
Building, Transportation (CityJSON core profile, CJ-07) and CityFurniture; what the core profile
flattens or cannot hold is counted in the export record's notes, other features are counted, never
written. Save into the source file (CJ-06) is Building only.
"""
import json
import os
import shutil
import tempfile
import time
from types import SimpleNamespace

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..shared import cityjson_open as co
from ..shared import cityjson_writer as cw
from ..shared import export_scope
from ..shared import georef as gr
from ..shared.citygml_reader import read_document
from ..shared.cityjson_reader import CityJSONError

REFUSE_SOURCE = ("{name} is the CityJSON file this scene was opened from: buildings and LoDs that were "
                 "not loaded would be lost. Use \"Save into Source File\" in the Save tab to write the changes "
                 "back into it, or save to another file.")
PARTIAL_NOTE = "only the loaded geometry was written (the scene holds part of {names})"
FORMATS = (('CITYGML', "CityGML", "CityGML 3.0 or 2.0 (chosen in the export dialog)"),
           ('CITYJSON', "CityJSON 2.0", "CityJSON 2.0 or CityJSONSeq: Building, Transportation (core "
                                        "profile) and CityFurniture"))


def _gml_options(**over):
    """The GML export operator's defaults (its RNA), so the pivot follows the GML rules as they are."""
    rna = bpy.ops.cgml3.export_gml_file.get_rna_type()
    vals = {p.identifier: p.default for p in rna.properties
            if p.identifier != "rna_type" and hasattr(p, "default") and not getattr(p, "is_array", False)}
    vals.update(citygml_version='3.0', srs_name="", **over)
    return SimpleNamespace(**vals)


def scene_cityjson_keys(scene):
    """``(ids, lods)`` the CityJSON open stored on the objects: gml_id -> original CityJSON id (K2),
    gml_id -> {"2": "2.2"} (K6)."""
    ids, lods = {}, {}
    for o in scene.objects:
        gid = str(o.get("gml_id") or "")
        if not gid:
            continue
        if o.get("cgml3_cityjson_id"):
            ids[gid] = str(o["cgml3_cityjson_id"])
        for key, target in (("cgml3_cityjson_surface_ids", ids), ("cgml3_cityjson_lods", None)):
            raw = o.get(key)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if target is None:
                lods[gid] = data
            else:
                target.update(data)
    return ids, lods


def scene_to_ir(context, scope="SCENE", gml_ids=(), report=None):
    """``(IR roots, GML export record)`` of what the CityGML 3.0 export writes for ``scope`` (K1).
    Raises ``ValueError`` for a scope problem (nothing written)."""
    from .. import _write_scene_gml
    try:
        from lxml import etree as ET
    except ImportError:
        import xml.etree.ElementTree as ET
    op = _gml_options(scope=scope, scope_gml_ids=",".join(gml_ids),
                      report=report or (lambda level, text: print(f"[CityJSON] {text}")))
    tmp = tempfile.mkdtemp(prefix="cgml_cj03_")
    try:
        path = os.path.join(tmp, "pivot.gml")
        rec = _write_scene_gml(op, context, path)
        roots = read_document(ET.parse(path).getroot(), upgrade=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ids, lods = scene_cityjson_keys(context.scene)
    cw.annotate(roots, ids, lods)
    return roots, rec


def _scene_crs() -> str:
    w = gr.world()
    if w is None:
        return ""
    crs, z = str(w.get(gr.CRS) or ""), str(w.get(gr.Z_EPSG) or "")
    return f"{crs} {z}" if z and z not in crs else crs


def export_file(context, path, scope="SCENE", gml_ids=(), seq=False, precision=3, report=None) -> dict:
    """Write ``scope`` of the scene to ``path`` as CityJSON 2.0 (``seq``: CityJSONSeq). Returns the
    export record (also in ``scene["cgml_last_export"]``). Raises ``ValueError`` when refused (the
    scene's own CityJSON source, scope, no building) and ``CityJSONError`` when the self-check fails;
    the target is not touched then."""
    from ..shared.authoring_core.blender.consistency_view import export_preflight
    path = os.path.abspath(path)
    world = gr.world()
    if co.source_conflict(world, path):
        raise ValueError(REFUSE_SOURCE.format(name=os.path.basename(path)))
    if export_preflight(report=report):
        raise ValueError("consistency errors, see above")
    roots, rec3 = scene_to_ir(context, scope, export_scope.parse_ids(gml_ids), report)
    doc, rep = cw.write_document(roots, _scene_crs(), precision)
    if not rep["written"]:
        raise ValueError("no Building, Road, Railway, Square or CityFurniture in the export scope: nothing written"
                         + (f" (not written: {rep['not_written']})" if rep["not_written"] else ""))
    cw.save(path, doc, seq=seq)
    for w in rep["warnings"]:
        print(f"[CityJSON] {w}")
    notes = cw.loss_notes(rep)      # CJ-07: what the core profile flattened, what has no core type
    partial = co.partly_loaded(world)
    if partial:
        notes.append(PARTIAL_NOTE.format(names=", ".join(os.path.basename(p) for p in partial)))
    kept = [r for r in roots if r.feature_type in cw.TOP_TYPES]
    skipped = rec3["skipped"] + [r.gml_id for r in roots if r.feature_type not in cw.TOP_TYPES]
    by_type = {}
    for r in kept:
        by_type[r.feature_type] = by_type.get(r.feature_type, 0) + 1
    rec = dict(rec3, path=path, version=f"CityJSON{'Seq' if seq else ''} {cw.VERSION}", format="CityJSON",
               time=time.strftime("%Y-%m-%dT%H:%M:%S"), written=rep["written"], written_count=len(kept),
               written_by_type=by_type, skipped=skipped, skipped_count=len(skipped),
               city_objects=rep["objects"], not_written=rep["not_written"], reduced=rep["reduced"],
               reduced_ids=rep["reduced_ids"], notes=notes,
               writer_warnings=len(rep["warnings"]), self_check="passed", cjval="not run")
    context.scene[export_scope.LAST_EXPORT_KEY] = json.dumps(rec)
    return rec


# -- CJ-06: save into the source file ------------------------------------------------------------

def source_changes(context, path) -> dict:
    """B84's classification (``citydb.changes.classify``) against one CityJSON source: ``changed`` /
    ``new`` / ``deleted`` / ``same`` gml_ids, ``ids`` (gml_id -> CityJSON id), ``loaded_lods``
    ({CityJSON id: [LoD texts]}) and ``not_written`` (new buildings when the scene has several sources:
    which file they belong to is not known). A loaded building missing from the scene (or with every
    surface deleted) is deleted; an unloaded one is no longer in the loaded set, so it is not."""
    from ..citydb.changes import CHANGED, DELETED, NEW, SYNCED, classify
    from .browse_gml import SOURCE_KIND_PROP
    from .commit_citydb import cityjson_fingerprint, is_shell
    srcs = co.sources(gr.world())
    key = co._key(path)
    loaded = dict((srcs.get(key) or {}).get("loaded") or {})
    scene = context.scene
    types = export_scope.top_level_features(scene)
    feats, ids, not_written = {}, dict(loaded), []
    for gid, obj in export_scope.top_level_feature_objects(scene).items():
        src = obj.get(co.SOURCE_PROP)
        mine = bool(src) and co._key(str(src)) == key
        if src and not mine:
            continue    # another CityJSON source's building
        if not mine:
            if types.get(gid) not in cw.ROOT_TYPES or obj.get(SOURCE_KIND_PROP) == "citydb":
                continue
            if len(srcs) != 1:
                not_written.append(gid)
                continue
        elif gid not in loaded:
            loaded[gid] = str(obj.get("cgml3_cityjson_id") or gid)
        if is_shell(obj):
            continue    # every surface deleted: a deleted building (as B84)
        feats[gid] = (obj.name, mine and obj.get(co.FINGERPRINT_PROP) == cityjson_fingerprint(obj))
        ids[gid] = str(obj.get("cgml3_cityjson_id") or gid)
    out = {k: [] for k in ("changed", "new", "deleted", "same")}
    names = {NEW: "new", CHANGED: "changed", DELETED: "deleted", SYNCED: "same"}
    for c in classify(feats, {g: 0 for g in loaded}):
        out[names[c.kind]].append(c.objectid)
    obj_ids, lods = scene_cityjson_keys(scene)
    out["loaded_lods"] = {obj_ids.get(g, g): sorted(set(m.values())) for g, m in lods.items()}
    # CJ-07: roads and furniture are opened and exported, but not merged into a source file
    other = sum(1 for t in types.values() if t in cw.TOP_TYPES and t not in cw.ROOT_TYPES)
    out.update(ids=ids, not_written=not_written, other=other)
    return out


def _source_record(path, ch, mrep, wrep, dry_run) -> dict:
    cj = ch["ids"]
    lists = {k: [cj[g] for g in ch[k]] for k in ("changed", "new", "deleted")}
    untouched = mrep.get("untouched_buildings", 0) if mrep else 0
    not_loaded = max(0, untouched - len(ch["same"]))
    notes = [f"{len(lists['changed'])} changed, {len(lists['new'])} new, {len(lists['deleted'])} deleted; kept as "
             f"in the file: {untouched} building(s) ({not_loaded} not loaded, {len(ch['same'])} unchanged), other "
             "modules, metadata, transform"] if mrep else ["no changes: the file was not touched"]
    kept_lods = (mrep or {}).get("kept_lods") or {}
    if kept_lods:
        notes.append("kept geometry of the changed buildings (not loaded): "
                     + ", ".join(f"LoD{t} {n}" for t, n in sorted(kept_lods.items())))
    lost = (mrep or {}).get("lost") or {}
    if lost:
        notes.append("lost on the changed buildings: " + ", ".join(f"{n} {k}" for k, n in sorted(lost.items())))
    if ch["not_written"]:
        notes.append(f"{len(ch['not_written'])} new building(s) not written: the scene has several CityJSON "
                     "sources (export them to their own file): " + ", ".join(ch["not_written"][:5]))
    notes += cw.loss_notes(wrep)
    if ch.get("other"):
        notes.append(f"{ch['other']} road / railway / square / city furniture object(s) of the scene are not "
                     "saved into the source file (buildings only): use Export for them")
    written =lists["changed"] + lists["new"]
    return {"path": path, "format": "CityJSON (source)", "version": f"{(mrep or {}).get('format', 'CityJSON')} 2.0",
            "scope": "SOURCE", "target": "file", "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "written": written, "written_count": len(written), "skipped": ch["not_written"],
            "skipped_count": len(ch["not_written"]), **lists, "changed_count": len(lists["changed"]),
            "new_count": len(lists["new"]), "deleted_count": len(lists["deleted"]), "same_count": len(ch["same"]),
            "untouched_buildings": untouched, "kept_not_loaded": not_loaded,
            "untouched_objects": (mrep or {}).get("untouched_objects", 0), "kept_lods": kept_lods, "lost": lost,
            "preserved": (mrep or {}).get("preserved") or {}, "not_written": wrep.get("not_written") or {},
            "writer_warnings": len(wrep.get("warnings") or ()), "notes": notes, "dry_run": bool(dry_run),
            "self_check": "passed" if mrep else "not run", "cjval": "not run"}


def save_source(context, path, dry_run=False, report=None) -> dict:
    """Write the scene's changed, new and deleted buildings into the CityJSON file ``path`` they were
    opened from (``shared/cityjson_merge``); everything else in the file stays as it is. ``dry_run``:
    the record only, nothing written. Raises ``ValueError`` / ``CityJSONError``; the file is unchanged then."""
    from ..shared import cityjson_merge as cm
    from .commit_citydb import cityjson_fingerprint, remove_shells
    path = os.path.abspath(path)
    name = os.path.basename(path)
    world = gr.world()
    entry = co.sources(world).get(co._key(path))
    if entry is None:
        raise ValueError(f"{name} is not a CityJSON file this scene was opened from")
    if not os.path.isfile(path):
        raise ValueError(f"{name} no longer exists: export the scene to a file instead")
    if not entry.get("stat"):
        raise ValueError(f"{name} was opened before save into the source existed: open it again")
    ch = source_changes(context, path)
    write_ids = ch["changed"] + ch["new"]
    if not (write_ids or ch["deleted"]):
        rec = _source_record(path, ch, None, {}, dry_run)
        if not dry_run:
            context.scene[export_scope.LAST_EXPORT_KEY] = json.dumps(rec)
        return rec
    if not dry_run:
        from ..shared.authoring_core.blender.consistency_view import export_preflight
        if export_preflight(report=report):
            raise ValueError("consistency errors, see above")
    written, wrep = {"type": "CityJSON", "version": cw.VERSION, "CityObjects": {}, "vertices": [],
                     "transform": {"scale": [1, 1, 1], "translate": [0, 0, 0]}}, {}
    if write_ids:
        roots, _rec3 = scene_to_ir(context, "IDS", write_ids, report)
        written, wrep = cw.write_document(roots, _scene_crs(), 3)
        missing = [g for g in write_ids if ch["ids"][g] not in written["CityObjects"]]
        if missing:
            raise ValueError(f"not written from the scene: {', '.join(missing)}")
    cj = ch["ids"]
    mrep = cm.merge_file(path, written, changed=[cj[g] for g in ch["changed"]], new=[cj[g] for g in ch["new"]],
                         deleted=[cj[g] for g in ch["deleted"]], loaded_lods=ch["loaded_lods"],
                         expected_stat=entry["stat"], dry_run=dry_run)
    for w in wrep.get("warnings") or ():
        print(f"[CityJSON] {w}")
    rec = _source_record(path, ch, mrep, wrep, dry_run)
    if not dry_run:   # the file now holds the scene's version: stamp it, track the new and deleted
        context.view_layer.update()
        objs = export_scope.top_level_feature_objects(context.scene)
        for gid in write_ids:
            objs[gid][co.SOURCE_PROP] = path
            objs[gid][co.FINGERPRINT_PROP] = cityjson_fingerprint(objs[gid])
        co.track_loaded(world, path, add={g: cj[g] for g in write_ids}, remove=ch["deleted"], stat=mrep["stat"])
        remove_shells(context.scene, ch["deleted"])
        context.scene[export_scope.LAST_EXPORT_KEY] = json.dumps(rec)
    return rec


_PREVIEW = {}   # path -> dry-run record shown in the confirmation dialog


def source_dialog_lines(rec) -> list:
    return ([f"File: {rec['path']}"] + rec["notes"]
            + ["Checked before writing (self-check, temporary file); not validated with cjval"])


class CGML3_OT_SaveCityJSONSource(Operator):
    bl_idname = "cgml3.save_cityjson_source"
    bl_label = "Save into Source File"
    bl_options = {'REGISTER'}
    bl_description = ("Write the changed, new and deleted buildings back into the CityJSON file they were opened "
                      "from; buildings and LoDs that were not loaded, other modules and metadata stay as they are")

    filepath: StringProperty(name="Source", subtype='FILE_PATH', options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, ctx):
        return bool(co.sources(gr.world()))

    def _path(self):
        srcs = list(co.sources(gr.world()))
        return self.filepath or (srcs[0] if len(srcs) == 1 else "")

    def invoke(self, ctx, evt):
        path = self._path()
        if not path:
            self.report({'ERROR'}, "The scene has several CityJSON sources: use the button of the one to save")
            return {'CANCELLED'}
        if ctx.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            _PREVIEW[path] = save_source(ctx, path, dry_run=True)
        except (ValueError, CityJSONError) as e:
            self.report({'ERROR'}, f"Save into source cancelled: {e}")
            return {'CANCELLED'}
        self.filepath = path
        return ctx.window_manager.invoke_props_dialog(self, width=520, title="Save into Source File",
                                                      confirm_text="Save")

    def draw(self, ctx):
        rec = _PREVIEW.get(self.filepath)
        col = self.layout.column(align=True)
        for line in source_dialog_lines(rec) if rec else [self.filepath]:
            col.label(text=line)

    def execute(self, ctx):
        path = self._path()
        if not path:
            self.report({'ERROR'}, "The scene has several CityJSON sources: name the one to save")
            return {'CANCELLED'}
        if ctx.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            rec = save_source(ctx, path, report=self.report)
        except (ValueError, CityJSONError) as e:
            self.report({'ERROR'}, f"Save into source cancelled, {os.path.basename(path)} unchanged: {e}")
            return {'CANCELLED'}
        finally:
            _PREVIEW.pop(path, None)
        self.report({'WARNING'} if rec["skipped"] or rec["lost"] else {'INFO'}, export_scope.summary(rec))
        return {'FINISHED'}


def _format_get(wm):
    if "_cgml_export_format" in wm:
        return int(wm["_cgml_export_format"])
    w = gr.world()   # default: the format the scene was opened from
    return 1 if w is not None and w.get(co.SOURCE_FORMAT_KEY) == "CityJSON" else 0


def _format_set(wm, value):
    wm["_cgml_export_format"] = int(value)


class CGML3_OT_ExportCityJSONFile(Operator):
    bl_idname = "cgml3.export_cityjson_file"
    bl_label = "Export CityJSON (File)"
    bl_options = {'REGISTER'}
    bl_description = ("Write the buildings, roads, railways, squares and city furniture of the scene as CityJSON "
                      "2.0 (or CityJSONSeq), through the CityGML 3.0 writer's rules. Sections, intersections and "
                      "traffic spaces are flattened into their road and counted in the summary; other feature "
                      "types are counted, not written")

    filepath: StringProperty(name="Output File", subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.json;*.jsonl", options={'HIDDEN'})
    sequence: BoolProperty(name="CityJSONSeq (.city.jsonl)", default=False,
                           description="One CityJSONFeature line per building after a header line")
    precision: IntProperty(name="Vertex Precision", default=3, min=0, max=9,
                           description="Decimals of the coordinates (3 = 1 mm)")
    scope: EnumProperty(
        name="Scope", description="Which features are written", default='SCENE',
        items=[('SCENE', 'Whole scene', 'Every visible feature of the scene, whatever is selected'),
               ('SELECTION', 'Selection', 'The selected features with their parts, installations and openings'),
               ('IDS', 'GML ids', 'The top-level features listed in GML ids')])
    scope_gml_ids: StringProperty(name="GML ids", default="",
                                  description="Comma-separated GML ids of top-level features (scope GML ids)")

    def draw(self, ctx):
        col = self.layout.column()
        col.prop(self, "scope")
        if self.scope == 'IDS':
            col.prop(self, "scope_gml_ids")
        col.prop(self, "sequence")
        col.prop(self, "precision")

    def invoke(self, ctx, evt):
        if not self.filepath:
            self.filepath = "export.city.jsonl" if self.sequence else "export.city.json"
        self.scope = 'SELECTION' if ctx.selected_objects else 'SCENE'
        ctx.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, ctx):
        path = bpy.path.abspath(self.filepath)
        ext = ".jsonl" if self.sequence else ".json"
        if not path.lower().endswith(ext):
            path = path.rstrip(".") + (".city" if not path.lower().endswith(".city") else "") + ext
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        if ctx.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            rec = export_file(ctx, path, self.scope, self.scope_gml_ids, self.sequence, self.precision,
                              report=self.report)
        except (ValueError, CityJSONError) as e:
            self.report({'ERROR'}, f"CityJSON Export cancelled: {e}")
            return {'CANCELLED'}
        self.report({'WARNING'} if rec["skipped"] or rec["notes"] else {'INFO'}, export_scope.summary(rec))
        self.report({'INFO'}, f"CityJSON Export finished: {path} (self-check passed; not validated with cjval)")
        return {'FINISHED'}


classes = (CGML3_OT_ExportCityJSONFile, CGML3_OT_SaveCityJSONSource)


def register():
    bpy.types.WindowManager.cgml_export_format = EnumProperty(
        name="Format", description="File format of Export", items=FORMATS, get=_format_get, set=_format_set)


def unregister():
    try:
        del bpy.types.WindowManager.cgml_export_format
    except AttributeError:
        pass

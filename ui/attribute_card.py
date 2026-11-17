# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# ui/attribute_card.py
"""B22: attribute card under "2 · Edit" (Building, Transportation, CityFurniture).

No new storage: the card reads and writes the custom properties the importer
already sets and both writers already export (``bldg:function``,
``bldg:storeysAboveGround``, ``con:height:value`` ...). The few extras are
``cgml3_`` keys, which no writer turns into generic attributes:

- ``cgml3_codelist``: the code list chosen for this feature (stored only when
  the user picks one or edits a coded field; otherwise it is detected).
- ``cgml3_codespace:<attr>``: codeSpace of a coded value (kept on import,
  written by both writers).

Window-manager proxy properties (get/set) put the active feature's values in
Blender widgets; a change goes to every selected feature of the same kind.

B88: a clicked wall, roof face, window, door or installation shows its own fields first
(``shared/codelists/elements.json``), then the card of its building.

B101: also in Edit Mode (active object only); a path line says what is edited (Building › Wall ›
Window); with an element clicked its building's fields fold away; an installation lists its own
surfaces (railing, floor ...) with one material each.
"""

import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel

from ..shared import codelists
from ..shared.attribute_utils import CODE_SPACE_PREFIX

LIST_PROP = "cgml3_codelist"
KINDS = {"Building": "bldg", "BuildingPart": "bldg", "Road": "tran", "Railway": "tran", "Track": "tran",
         "Square": "tran", "TrafficArea": "tran", "AuxiliaryTrafficArea": "tran", "CityFurniture": "frn"}
TRAFFIC_AREAS = ("TrafficArea", "AuxiliaryTrafficArea")
CODED_SLOTS = ("function", "usage", "roofType", "surfaceMaterial", "class")
GENERIC_STORE = "cgml3_generic_attributes"
GENERIC_JSON = "cgml3_generic_attributes_json"


def _feature(obj):
    return str(obj.get("cgml3_feature") or "") if obj is not None else ""


def target(obj):
    """The feature the card edits for ``obj``: itself or its nearest card-kind ancestor."""
    while obj is not None:
        if _feature(obj) in KINDS:
            return obj
        obj = obj.parent
    return None


def kind(t):
    return KINDS.get(_feature(t), "")


def targets(context):
    """Active target first, then the other selected features of the same kind (B101: in Edit Mode only
    the active one; Edit Facade has every part of the building selected)."""
    first = target(context.active_object)
    if first is None:
        return []
    out = [first]
    if context.mode == 'EDIT_MESH':
        return out
    for o in context.selected_objects:
        t = target(o)
        if t is not None and t not in out and kind(t) == kind(first):
            out.append(t)
    return out


def version(t):
    if t.get("cgml_source") == "authored":
        return "3.0"
    try:
        from ..Openings_Cutter.Openings_Cutter import detect_citygml_version_for_object
        return detect_citygml_version_for_object(t) or "3.0"
    except Exception:
        return "3.0"


def slots(t):
    """Card fields of ``t``: (slot, attribute key or None for version-mapped ones)."""
    k = kind(t)
    if k == "bldg":
        return [("class", "bldg:class"), ("function", "bldg:function"), ("usage", "bldg:usage"),
                ("roofType", "bldg:roofType"), ("height", None), ("storeys", "bldg:storeysAboveGround"),
                ("storeysBelow", "bldg:storeysBelowGround"), ("year", None)]  # B88: class, storeys below
    if k == "tran":
        rows = [("function", "tran:function"), ("usage", "tran:usage")]
        if _feature(t) in TRAFFIC_AREAS:
            rows.append(("surfaceMaterial", "tran:surfaceMaterial"))
        return rows
    if k == "frn":
        return [("function", "frn:function")]
    return []


def attr_key(t, slot):
    return dict(slots(t)).get(slot) or (f"{kind(t)}:{slot}" if slot in CODED_SLOTS else "")


def list_of(t):
    """Chosen list of ``t``, or the one its coded values fit (B16 decision 2)."""
    chosen = str(t.get(LIST_PROP) or "")
    if chosen in {r["id"] for r in codelists.registry()}:
        return chosen
    coded = [a for s, a in slots(t) if s in CODED_SLOTS]
    return codelists.detect({a: t.get(a) for a in coded}, {a: t.get(CODE_SPACE_PREFIX + a) for a in coded})


def codes_list(list_id, attr):
    """B88: the list ``attr``'s codes come from: the chosen one, or the default list where the
    chosen one has none for it (AdV has no bldg:class)."""
    return list_id if codelists.codes(list_id, attr) else codelists.default_id()


def set_code(t, attr, code, list_id):
    """Write a coded value with its codeSpace (from the list) and remember the list."""
    if not code:
        for key in (attr, CODE_SPACE_PREFIX + attr):
            if key in t:
                del t[key]
        return
    t[attr] = code
    source = codes_list(list_id, attr)
    cs = codelists.code_space(source, attr) if code in codelists.codes(source, attr) else ""
    if cs:
        t[CODE_SPACE_PREFIX + attr] = cs
    t[LIST_PROP] = list_id


# --- height / year: 2.0 and 3.0 keep them in different elements ----------------

# B46: a height typed here is not a survey either; "measured" is the user's explicit choice (Status).
HEIGHT_DEFAULTS = {"con:height:highReference": "highestRoofEdge", "con:height:lowReference": "lowestGroundPoint",
                   "con:height:status": "estimated", "con:height:uom": "m"}
HEIGHT_STATUSES = (('estimated', "Estimated", "Computed or guessed, not surveyed (con:HeightStatusValue)"),
                   ('measured', "Measured", "Surveyed height"))


def get_height_status(t):
    s = str(t.get("con:height:status") or "")
    return s if s in ("estimated", "measured") else "estimated"


def set_height_status(t, status):
    if version(t) == "2.0":
        return  # bldg:measuredHeight has no status
    t["con:height:status"] = status
    if "con:height:value" in t:
        for k, v in HEIGHT_DEFAULTS.items():  # keep the element complete
            if not t.get(k):
                t[k] = v


def get_height(t):
    key = "bldg:measuredHeight" if version(t) == "2.0" else "con:height:value"
    try:
        return float(t.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def set_height(t, value):
    if version(t) == "2.0":
        if value > 0:
            t["bldg:measuredHeight"] = float(value)
        else:
            for k in ("bldg:measuredHeight", "bldg:measuredHeight:uom"):
                if k in t:
                    del t[k]
        return
    if value > 0:
        t["con:height"] = t.get("con:height", "")
        t["con:height:value"] = float(value)
        for k, v in HEIGHT_DEFAULTS.items():  # the XSD needs all four next to the value
            if not t.get(k):
                t[k] = v
    else:
        for k in [k for k in t.keys() if k == "con:height" or k.startswith("con:height:")]:
            del t[k]


def get_year(t):
    raw = t.get("bldg:yearOfConstruction") if version(t) == "2.0" else str(t.get("con:dateOfConstruction") or "")[:4]
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def set_year(t, year):
    if version(t) == "2.0":
        if year > 0:
            t["bldg:yearOfConstruction"] = int(year)
        elif "bldg:yearOfConstruction" in t:
            del t["bldg:yearOfConstruction"]
        return
    if year > 0:
        old = str(t.get("con:dateOfConstruction") or "")
        rest = old[4:] if len(old) == 10 and old[4] == "-" else "-01-01"
        t["con:dateOfConstruction"] = f"{int(year):04d}{rest}"
    elif "con:dateOfConstruction" in t:
        del t["con:dateOfConstruction"]


def read(t, slot):
    if slot == "height":
        return get_height(t)
    if slot == "year":
        return get_year(t)
    value = t.get(attr_key(t, slot))
    if slot in ("storeys", "storeysBelow"):
        return int(value or 0)
    return "" if value is None else str(value)


def write(t, slot, value):
    if slot == "height":
        set_height(t, value)
    elif slot == "height_status":
        set_height_status(t, HEIGHT_STATUSES[value][0])
    elif slot == "year":
        set_year(t, value)
    elif slot in ("storeys", "storeysBelow"):
        key = attr_key(t, slot)
        if value > 0:
            t[key] = int(value)
        elif key in t:
            del t[key]
    else:
        key = attr_key(t, slot)
        if key and codelists.has_codes(key):
            set_code(t, key, value, list_of(t))
        elif key:
            if value:
                t[key] = value
            elif key in t:
                del t[key]


def mixed(context, slot):
    ts = targets(context)
    return len({repr(read(t, slot)) for t in ts}) > 1


# --- window-manager proxies ---------------------------------------------------------

NONE = "__none__"  # an empty enum identifier draws as a blank widget
_ITEMS = {}  # keep enum item strings alive (Blender reads them after the callback returns)


def _keep(key, out):
    """B82: the kept list while its content is unchanged; swapping in an equal copy (the getter rebuilds
    the items on every read) frees strings the widget still draws -> garbage text like "0w/"."""
    if _ITEMS.get(key) != out:
        _ITEMS[key] = out
    return _ITEMS[key]


def _code_items(slot):
    def items(_self, context):
        ts = targets(context)
        out = [(NONE, "— not set —", "No value", 0)]
        if ts:
            t = ts[0]
            key = attr_key(t, slot)
            lid = list_of(t)
            current = read(t, slot)
            if mixed(context, slot):
                out.append(("__mixed__", "— differs —", "The selected features have different values", 1))
            known = codelists.codes(codes_list(lid, key), key)
            for code, label in known.items():
                out.append((code, f"{code}  {label}", label, len(out)))
            if current and current not in known:
                out.append((current, f"(unknown code) {current}", "Kept and written back unchanged", len(out)))
        return _keep(slot, out)
    return items


def _code_get(slot):
    def get(self):
        ctx = bpy.context
        ts = targets(ctx)
        if not ts:
            return 0
        items = _code_items(slot)(self, ctx)  # fresh: the selection may have changed since the last draw
        want = "__mixed__" if mixed(ctx, slot) else (read(ts[0], slot) or NONE)
        return next((i[3] for i in items if i[0] == want), 0)
    return get


def _code_set(slot):
    def set_(self, value):
        items = _ITEMS.get(slot) or []
        ident = next((i[0] for i in items if i[3] == value), None)
        if ident is None or ident == "__mixed__":
            return
        for t in targets(bpy.context):
            write(t, slot, "" if ident == NONE else ident)
    return set_


def _plain_get(slot, default):
    def get(self):
        ts = targets(bpy.context)
        return read(ts[0], slot) if ts else default
    return get


def _plain_set(slot):
    def set_(self, value):
        for t in targets(bpy.context):
            write(t, slot, value)
    return set_


def _list_items(_self, _context):
    out = [(r["id"], r["label"], f"Code list: {r['label']}", i) for i, r in enumerate(codelists.registry())]
    return _keep("__lists__", out)


def _list_get(self):
    ts = targets(bpy.context)
    ids = [r["id"] for r in codelists.registry()]
    return ids.index(list_of(ts[0])) if ts and list_of(ts[0]) in ids else 0


def _list_set(self, value):
    lid = codelists.registry()[value]["id"]
    for t in targets(bpy.context):
        t[LIST_PROP] = lid
        for slot, key in slots(t):
            if slot in CODED_SLOTS and key and t.get(key):  # codeSpace follows the chosen list
                cs = codelists.code_space(lid, key)
                if cs:
                    t[CODE_SPACE_PREFIX + key] = cs
                elif CODE_SPACE_PREFIX + key in t:
                    del t[CODE_SPACE_PREFIX + key]


# --- B88: the selected element itself (surface, opening, installation) ---------------------
#
# Click a wall, a roof face, a window, a door, a balcony: its own fields come first on the card
# (``codelists.element_fields``). An installation's class / function / usage are CityGML
# attributes on the object; surfaces and openings have none of those in CityGML, so their
# fields are generic attributes in the store both writers read from the surface's material.

SURFACE_STORE = "cgml3_surface_generic_attributes"
ELEMENT_SLOTS = 3  # most fields any element has


def elements(context):
    """Active element first, then the other selected ones of the same feature type ([] when the
    active object has no fields of its own)."""
    first = context.active_object
    if first is None or not codelists.element_fields(_feature(first)):
        return []
    if context.mode == 'EDIT_MESH':  # B101: as in ``targets``
        return [first]
    return [first] + [o for o in context.selected_objects if o is not first and _feature(o) == _feature(first)]


def _own_materials(obj):
    """Material slots of ``obj`` that are the element itself: used by its faces (an imported
    surface keeps the slots of its whole building) and of its own kind (a wall object can also
    carry the faces of an opening); every used one when none says so."""
    if obj.type != 'MESH':
        return []
    used = {p.material_index for p in obj.data.polygons}
    slots_ = [(i, m) for i, m in enumerate(obj.data.materials) if m is not None and i in used]
    kind_ = codelists.element_kind(_feature(obj))
    own = [(i, m) for i, m in slots_ if codelists.element_kind(str(m.get("SurfaceTyp") or "")) == kind_]
    return own or slots_


def _derived_function(obj):
    """The function code both writers put on a balcony / chimney made here while it has none."""
    from ..shared.semantic_types import CANONICAL_TYPE_INSTALLATION_CODE
    if "cgml_subtype_origin" in obj.keys():
        return None  # imported: only what the source said
    return CANONICAL_TYPE_INSTALLATION_CODE.get(obj.get("cgml_semantic_type"))


def element_read(obj, field):
    if "attr" in field:
        value = obj.get(field["attr"])
        if value is None and field["attr"] == "bldg:function":
            value = _derived_function(obj)
        return "" if value is None else str(value)
    return next((v for _i, mat in _own_materials(obj) if (v := _store_read(mat, field["name"]))), "")


def _set_coded(obj, attr, code, code_space):
    for key in (attr, CODE_SPACE_PREFIX + attr):
        if key in obj:
            del obj[key]
    if code:
        obj[attr] = code
        if code_space:
            obj[CODE_SPACE_PREFIX + attr] = code_space


def element_write(obj, field, code):
    """Set (``code`` '' = remove) one field of an element."""
    if "attr" in field:
        derived = _derived_function(obj)
        if derived and field["attr"] != "bldg:function" and obj.get("bldg:function") is None:
            # the writers add the derived function only while class is unset, and after usage
            # (wrong element order): keep it as data once the user fills the other two
            fn = next(f for f in codelists.element_fields(_feature(obj)) if f.get("attr") == "bldg:function")
            _set_coded(obj, "bldg:function", derived, fn["codeSpace"])
        _set_coded(obj, field["attr"], code, field.get("codeSpace", "") if code in field["codes"] else "")
        return
    for _i, mat in _own_materials(obj):  # one material per surface (B36, importer): no sharing to split
        _store_write(mat, field["name"], code)


def _store_read(mat, name):
    from .generic_attributes_ui import _load_dict_from_idprop
    value = _load_dict_from_idprop(mat, SURFACE_STORE, SURFACE_STORE + "_json").get(name)
    return "" if value is None else str(value)


def _store_write(mat, name, code):
    """Set (``code`` '' = remove) generic attribute ``name`` in ``mat``'s surface store."""
    from .generic_attributes_ui import _load_dict_from_idprop, _save_dict_to_idprop
    data = _load_dict_from_idprop(mat, SURFACE_STORE, SURFACE_STORE + "_json")
    if code:
        data[name] = code
    else:
        data.pop(name, None)
    if data:
        _save_dict_to_idprop(mat, SURFACE_STORE, SURFACE_STORE + "_json", data)
    else:
        for key in (SURFACE_STORE, SURFACE_STORE + "_json"):
            if key in mat:
                del mat[key]


def _elem_field(context, i):
    es = elements(context)
    fields = codelists.element_fields(_feature(es[0])) if es else ()
    return es, (fields[i] if i < len(fields) else None)


def _elem_items(i):
    def items(_self, context):
        es, field = _elem_field(context, i)
        out = [(NONE, "— not set —", "No value", 0)]
        if field:
            current = element_read(es[0], field)
            if len({element_read(e, field) for e in es}) > 1:
                out.append(("__mixed__", "— differs —", "The selected elements have different values", 1))
            for code, label in field["codes"].items():
                out.append((code, f"{code}  {label}" if "attr" in field else label, label, len(out)))
            if current and current not in field["codes"]:
                out.append((current, f"(other) {current}", "Kept and written back unchanged", len(out)))
        return _keep(f"__elem{i}__", out)
    return items


def _elem_get(i):
    def get(self):
        es, field = _elem_field(bpy.context, i)
        if not field:
            return 0
        items = _elem_items(i)(self, bpy.context)
        mixed_ = len({element_read(e, field) for e in es}) > 1
        want = "__mixed__" if mixed_ else (element_read(es[0], field) or NONE)
        return next((it[3] for it in items if it[0] == want), 0)
    return get


def _elem_set(i):
    def set_(self, value):
        es, field = _elem_field(bpy.context, i)
        ident = next((it[0] for it in _ITEMS.get(f"__elem{i}__") or [] if it[3] == value), None)
        if not field or ident is None or ident == "__mixed__":
            return
        for e in es:
            element_write(e, field, "" if ident == NONE else ident)
    return set_


# --- B101: an installation's own surfaces (balcony floor, railing, chimney walls ...) ----------------
#
# One row per CityGML surface (the faces sharing a con_surface_id), not per Blender face: a value goes
# to every face of that surface, so a surface is never half set (the writers read one face's material
# per surface). Row 0 sets every surface at once. ClosureSurfaces are virtual: no row.

ISURF_SLOTS = 7  # "All" + six surfaces (a balcony has four)
NAMES = {"WallSurface": "Wall", "RoofSurface": "Roof", "GroundSurface": "Ground", "ClosureSurface": "Closure",
         "OuterFloorSurface": "Outer floor", "OuterCeilingSurface": "Outer ceiling", "WindowSurface": "Window",
         "Window": "Window", "DoorSurface": "Door", "Door": "Door", "BuildingInstallation": "Installation",
         "BuildingPart": "Building part"}
PART_LABELS = {"railing": "Railing"}


def nice(obj):
    """What ``obj`` is, in words: "Balcony", "Window", "Wall" ..."""
    return str(obj.get("cgml_semantic_type") or "") or NAMES.get(_feature(obj), _feature(obj) or obj.name)


def surface_groups(obj):
    """[(label, field, [materials], faces)] of ``obj``'s own surfaces that take a material, when ``obj`` is
    an installation; ``field`` is the wall / roof Material field of ``elements.json``."""
    if obj is None or obj.type != 'MESH' or _feature(obj) != "BuildingInstallation":
        return []
    from collections import Counter
    from ..ops.installation_authoring import PART_PROP
    faces = Counter(p.material_index for p in obj.data.polygons)
    groups = {}
    for i, mat in enumerate(obj.data.materials):
        cls = str(mat.get("SurfaceTyp") or "") if mat is not None else ""
        fields = codelists.element_fields(cls)
        if not faces.get(i) or not fields:
            continue
        label = PART_LABELS.get(str(mat.get(PART_PROP) or "")) or NAMES.get(cls, cls)
        g = groups.setdefault(str(mat.get("con_surface_id") or cls), [label, fields[0], [], 0])
        g[2].append(mat)
        g[3] += faces[i]
    return [tuple(g) for g in groups.values()]


def _isurf(context, i):
    """(field, materials) of surface row ``i`` of the active installation (0 = all), or (None, [])."""
    es = elements(context)
    gs = surface_groups(es[0]) if es else []
    if not gs or i > len(gs):
        return None, []
    if i == 0:  # shortcut: every row takes the wall list here; a roof keeps a facade code as "(other)"
        return codelists.element_fields("WallSurface")[0], [m for g in gs for m in g[2]]
    return gs[i - 1][1], gs[i - 1][2]


def _isurf_items(i):
    def items(_self, context):
        field, mats = _isurf(context, i)
        out = [(NONE, "— not set —", "No value", 0)]
        if field:
            values = {_store_read(m, field["name"]) for m in mats}
            if len(values) > 1:
                out.append(("__mixed__", "— differs —", "The faces have different values", 1))
            for code, label in field["codes"].items():
                out.append((code, label, label, len(out)))
            for v in sorted(values - set(field["codes"]) - {""}):
                out.append((v, f"(other) {v}", "Kept and written back unchanged", len(out)))
        return _keep(f"__isurf{i}__", out)
    return items


def _isurf_get(i):
    def get(self):
        field, mats = _isurf(bpy.context, i)
        if not field:
            return 0
        items = _isurf_items(i)(self, bpy.context)
        values = {_store_read(m, field["name"]) for m in mats}
        want = "__mixed__" if len(values) > 1 else (values.pop() or NONE)
        return next((it[3] for it in items if it[0] == want), 0)
    return get


def _isurf_set(i):
    def set_(self, value):
        field, mats = _isurf(bpy.context, i)
        ident = next((it[0] for it in _ITEMS.get(f"__isurf{i}__") or [] if it[3] == value), None)
        if not field or ident is None or ident == "__mixed__":
            return
        for m in mats:
            _store_write(m, field["name"], "" if ident == NONE else ident)
    return set_


def _register_props():
    wm = bpy.types.WindowManager
    wm.cgml_attr_list =EnumProperty(name="Code list", items=_list_items, get=_list_get, set=_list_set)
    for slot in CODED_SLOTS:
        setattr(wm, f"cgml_attr_{slot}", EnumProperty(name=slot, items=_code_items(slot), get=_code_get(slot),
                                                      set=_code_set(slot)))
        setattr(wm, f"cgml_attr_{slot}_text", StringProperty(name=slot, get=_plain_get(slot, ""),
                                                             set=_plain_set(slot)))
    wm.cgml_attr_height = FloatProperty(name="Height", min=0.0, unit='LENGTH', get=_plain_get("height", 0.0),
                                        set=_plain_set("height"), description="0 = not set")
    wm.cgml_attr_height_status = EnumProperty(
        name="Status", items=HEIGHT_STATUSES, description="con:height status (3.0); 2.0 has none",
        get=lambda self: 1 if (targets(bpy.context) and get_height_status(targets(bpy.context)[0]) == "measured") else 0,
        set=_plain_set("height_status"))
    wm.cgml_attr_storeys = IntProperty(name="Storeys above ground", min=0, get=_plain_get("storeys", 0),
                                       set=_plain_set("storeys"), description="0 = not set")
    wm.cgml_attr_storeysBelow = IntProperty(name="Storeys below ground", min=0, get=_plain_get("storeysBelow", 0),
                                            set=_plain_set("storeysBelow"), description="0 = not set")
    wm.cgml_attr_year = IntProperty(name="Year of construction", min=0, max=9999, get=_plain_get("year", 0),
                                    set=_plain_set("year"), description="0 = not set")
    for i in range(ELEMENT_SLOTS):
        setattr(wm, f"cgml_elem_{i}", EnumProperty(name="Element field", items=_elem_items(i), get=_elem_get(i),
                                                   set=_elem_set(i)))
    for i in range(ISURF_SLOTS):
        setattr(wm, f"cgml_isurf_{i}", EnumProperty(name="Surface material", items=_isurf_items(i),
                                                    get=_isurf_get(i), set=_isurf_set(i)))


def _unregister_props():
    wm = bpy.types.WindowManager
    names = ["cgml_attr_list", "cgml_attr_height", "cgml_attr_height_status", "cgml_attr_storeys",
             "cgml_attr_storeysBelow", "cgml_attr_year"] + [f"cgml_elem_{i}" for i in range(ELEMENT_SLOTS)]
    names += [f"cgml_isurf_{i}" for i in range(ISURF_SLOTS)]
    names += [f"cgml_attr_{s}{suffix}" for s in CODED_SLOTS for suffix in ("", "_text")]
    for name in names:
        if hasattr(wm, name):
            delattr(wm, name)


# --- generic attributes ---------------------------------------------------------------

_NOT_GENERIC = {"gml_id", "Typ", "lod", "ModelType", "surface_types", "CRS", "image_path", "EPSG",
                "structure_part", "structure_type", "name", "description"}


def generics(t):
    """``{name: value}`` of ``t``'s generic attributes (the 3.0 store, else flat props)."""
    store = t.get(GENERIC_STORE)
    if store is not None:
        return {k: store[k] for k in store.keys()}
    return {k: t[k] for k in t.keys()
            if ":" not in k and k not in _NOT_GENERIC and not k.startswith(("cgml", "apt_", "_", "x3d_"))
            and isinstance(t[k], (str, int, float))}


def set_generic(t, name, value):
    """Flat prop + the 3.0 dict and JSON store when present (the 3.0 writer reads the JSON)."""
    from ..common.builder_support.v3.importer import _safe_store_json_chunks
    if value is None:
        if name in t:
            del t[name]
    else:
        t[name] = value
    store = t.get(GENERIC_STORE)
    has_json = any(k == GENERIC_JSON or k.startswith(GENERIC_JSON + "_") for k in t.keys())
    if store is None and not has_json:
        return
    data = {k: store[k] for k in store.keys()} if store is not None else {}
    if value is None:
        data.pop(name, None)
    else:
        data[name] = value
    t[GENERIC_STORE] = data
    _safe_store_json_chunks(t, GENERIC_JSON, data)


GENERIC_TYPES = (('STRING', "Text", ""), ('INT', "Integer", ""), ('DOUBLE', "Number", ""),
                 ('DATE', "Date (YYYY-MM-DD)", ""))


def parse_generic(kind_, text):
    """The typed value, or raise ValueError with a message for the user."""
    text = text.strip()
    if kind_ == 'INT':
        return int(text)
    if kind_ == 'DOUBLE':
        return float(text)
    if kind_ == 'DATE':
        import datetime
        datetime.date.fromisoformat(text)
        return text
    return text


class CGML_OT_attr_generic_set(Operator):
    """Add or change a generic attribute on the selected features"""
    bl_idname = "cgml.attr_generic_set"
    bl_label = "Generic Attribute"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(name="Name")
    kind: EnumProperty(name="Type", items=GENERIC_TYPES)
    value: StringProperty(name="Value")

    @classmethod
    def poll(cls, context):
        return bool(targets(context))

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        name = self.name.strip()
        if not name or ":" in name or name.startswith(("cgml", "_")):
            self.report({'ERROR'}, "Give a plain name (no ':' and not starting with 'cgml' or '_')")
            return {'CANCELLED'}
        try:
            value = parse_generic(self.kind, self.value)
        except ValueError:
            self.report({'ERROR'}, f"'{self.value}' is not a valid {self.kind.lower()}")
            return {'CANCELLED'}
        ts = targets(context)
        for t in ts:
            set_generic(t, name, value)
        self.report({'INFO'}, f"{name} = {value} on {len(ts)} feature(s)")
        return {'FINISHED'}


class CGML_OT_attr_generic_remove(Operator):
    """Remove this generic attribute from the selected features"""
    bl_idname = "cgml.attr_generic_remove"
    bl_label = "Remove Generic Attribute"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(options={'HIDDEN'})

    def execute(self, context):
        for t in targets(context):
            set_generic(t, self.name, None)
        return {'FINISHED'}


def _template_items(_self, _context):
    out = [(tp["id"], tp["label"], "", i) for i, tp in enumerate(codelists.templates())]
    _ITEMS["__templates__"] = out
    return out


class CGML_OT_attr_template_apply(Operator):
    """Apply a template (function, usage, roof type, storeys) to the selected buildings"""
    bl_idname = "cgml.attr_template_apply"
    bl_label = "Apply Template"
    bl_options = {'REGISTER', 'UNDO'}

    template: EnumProperty(name="Template", items=_template_items)

    @classmethod
    def poll(cls, context):
        ts = targets(context)
        return bool(ts) and kind(ts[0]) == "bldg"

    def execute(self, context):
        tpl = next((tp for tp in codelists.templates() if tp["id"] == self.template), None)
        if tpl is None:
            return {'CANCELLED'}
        ts = targets(context)
        for t in ts:
            lid = list_of(t)
            for key, code in tpl.get("values", {}).get(lid, {}).items():
                set_code(t, key, code, lid)
            for key, value in tpl.get("fields", {}).items():
                t[key] = value
        self.report({'INFO'}, f"{tpl['label']} applied to {len(ts)} building(s)")
        return {'FINISHED'}


SEARCH_MIN = 10  # B94: lists longer than this get a search button next to the dropdown


def _items_key(prop):
    """``_ITEMS`` key of the dropdown ``prop`` (``cgml_attr_<slot>`` / ``cgml_elem_<i>`` / ``cgml_isurf_<i>``)."""
    if prop.startswith("cgml_attr_"):
        return prop[len("cgml_attr_"):]
    kind_, i = prop[len("cgml_"):].rsplit("_", 1)
    return f"__{kind_}{i}__"


def _search_items(self, context):
    getattr(context.window_manager, self.prop)  # the getter rebuilds the dropdown's items for this selection
    out = [it for it in _ITEMS.get(_items_key(self.prop)) or [] if it[0] != "__mixed__"]
    return _keep("__search__", out)


class CGML_OT_attr_search(Operator):
    """Search the list by code or name ("2463", "garage", "wohn")"""
    bl_idname = "cgml.attr_search"
    bl_label = "Search Code List"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}
    bl_property = "code"

    prop: StringProperty(options={'HIDDEN'})
    code: EnumProperty(name="Code", items=_search_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        setattr(context.window_manager, self.prop, self.code)  # the dropdown's setter writes all selected
        return {'FINISHED'}


def _prop_with_search(row, wm, prop):
    row.prop(wm, prop, text="")
    if len(_ITEMS.get(_items_key(prop)) or ()) > SEARCH_MIN:
        row.operator("cgml.attr_search", text="", icon='VIEWZOOM').prop = prop


# --- panel ----------------------------------------------------------------------------

LABELS = {"class": "Class", "function": "Function", "usage": "Usage", "roofType": "Roof type",
          "surfaceMaterial": "Surface material", "height": "Height", "storeys": "Storeys",
          "storeysBelow": "Storeys below", "year": "Built"}
BASIC_SLOTS = ("function", "roofType", "height", "storeys")  # B95: what LoD2-DE fills; the rest under "More"
ADV_ABSENT = ("class", "usage")  # not in the AdV profile: hidden for an AdV building unless the file has them


def _draw_rows(context, col, t, rows):
    wm = context.window_manager
    for slot, key in rows:
        row = col.row(align=True)
        row.label(text=LABELS.get(slot, slot))
        if slot in CODED_SLOTS:
            if codelists.has_codes(key):
                _prop_with_search(row, wm, f"cgml_attr_{slot}")
            else:
                row.prop(wm, f"cgml_attr_{slot}_text", text="")
        else:
            row.prop(wm, f"cgml_attr_{slot}", text="")
            if slot == "height" and version(t) != "2.0" and read(t, "height") > 0:
                row.prop(wm, "cgml_attr_height_status", text="")  # B46
            if mixed(context, slot):
                row.label(text="—")


def _path(obj, t):
    """"Building › Wall › Window": from the card's building down to ``obj``."""
    names = []
    while obj is not None:
        names.append(nice(obj))
        if obj is t:
            break
        obj = obj.parent
    return " › ".join(reversed(names))


def _active_face_material(obj):
    """The material of ``obj``'s active face in Edit Mode, or None."""
    if obj.mode != 'EDIT' or obj.type != 'MESH':
        return None
    import bmesh
    face = bmesh.from_edit_mesh(obj.data).faces.active
    mi = face.material_index if face is not None else -1
    return obj.data.materials[mi] if 0 <= mi < len(obj.data.materials) else None


def _draw_element(layout, wm, es):
    """B88 element box: the clicked element's own fields; B101: an installation's surfaces under them."""
    fields = codelists.element_fields(_feature(es[0]))
    box = layout.box()
    more = f" (+{len(es) - 1} selected)" if len(es) > 1 else ""
    box.label(text=f"{nice(es[0])} ({_feature(es[0])}){more}", icon='FACESEL')
    box.label(text=f"ID: {es[0].get('gml_id') or '—'}")  # B94: set on placement, shown so nobody types one
    col = box.column(align=True)
    for i, field in enumerate(fields):
        row = col.row(align=True)
        row.label(text=field["label"])
        _prop_with_search(row, wm, f"cgml_elem_{i}")
    gs = surface_groups(es[0])[:ISURF_SLOTS - 1]
    if gs:
        hot = _active_face_material(es[0])
        col = box.column(align=True)
        col.label(text="Surface material" + (" (active one only)" if len(es) > 1 else ""))
        row = col.row(align=True)
        row.label(text="All surfaces")
        _prop_with_search(row, wm, "cgml_isurf_0")
        for i, (label, _field, mats, faces) in enumerate(gs, 1):
            row = col.row(align=True)
            row.label(text=label + (f" ({faces} faces)" if faces > 1 else ""),
                      icon='RIGHTARROW' if hot in mats else 'BLANK1')  # the face clicked in Edit Mode
            _prop_with_search(row, wm, f"cgml_isurf_{i}")
    if "name" in fields[0] or gs:
        box.label(text="Written as generic attributes", icon='INFO')


class CGML3_PT_Attributes(Panel):
    bl_label = "Attributes"
    bl_idname = "CGML3_PT_Attributes"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = "CGML3_PT_Edit"

    @classmethod
    def poll(cls, context):  # B101: Edit Facade too, where windows and balconies are made
        return context.mode in ('OBJECT', 'EDIT_MESH') and target(context.active_object) is not None

    def draw_header(self, context):  # B101: what the card edits, readable while it is folded
        self.layout.label(text=nice(context.active_object))  # "Balcony" + bl_label = "Balcony Attributes"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        ts = targets(context)
        t = ts[0]
        es = elements(context)
        layout.label(text=_path(context.active_object, t), icon='OUTLINER')  # B101: Building › Wall › Window
        if es:  # B88: what was clicked comes first, its building below
            _draw_element(layout, wm, es)
            header, layout = layout.panel("CGML3_attr_building_of", default_closed=True)  # B101: folded
            header.label(text=f"Building: {t.name}", icon='HOME')
            if not layout:
                return
        if len(ts) > 1:
            layout.label(text=f"{len(ts)} selected: changes go to all", icon='INFO')
        rows = slots(t)
        if kind(t) == "bldg":  # B95: the AdV LoD2 core first; AdV has no class / usage (LoD2-DE)
            adv = list_of(t) == "ADV"
            rows = [(s, k) for s, k in rows if not (adv and s in ADV_ABSENT and not read(t, s))]
            basic = [r for r in rows if r[0] in BASIC_SLOTS]
            extra = [r for r in rows if r[0] not in BASIC_SLOTS]
        else:
            basic, extra = rows, []
        _draw_rows(context, layout.column(align=True), t, basic)
        if kind(t) == "bldg" and codelists.templates():
            layout.operator_menu_enum("cgml.attr_template_apply", "template", text="Apply Template", icon='PRESET')
        header, body = layout.panel("CGML3_attr_more", default_closed=True)
        header.label(text="More")
        if not body:
            return
        if any(s in CODED_SLOTS and codelists.has_codes(k) for s, k in slots(t)):
            body.prop(wm, "cgml_attr_list", text="List")
        _draw_rows(context, body.column(align=True), t, extra)
        box = body.box()
        box.label(text="Generic attributes", icon='PROPERTIES')
        values = generics(t)
        for name, value in sorted(values.items()):
            row = box.row(align=True)
            row.label(text=f"{name}: {value}")
            op = row.operator("cgml.attr_generic_set", text="", icon='GREASEPENCIL')
            op.name, op.value = name, str(value)
            op.kind = 'INT' if isinstance(value, int) else 'DOUBLE' if isinstance(value, float) else 'STRING'
            row.operator("cgml.attr_generic_remove", text="", icon='X').name = name
        op = box.operator("cgml.attr_generic_set", text="Add Attribute", icon='ADD')
        op.name, op.value = "", ""


class CGML3_PT_AllProperties(Panel):
    bl_label = "All Properties"
    bl_idname = "CGML3_PT_AllProperties"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_parent_id = "CGML3_PT_Attributes"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return CGML3_PT_Attributes.poll(context)

    def draw(self, context):
        t = target(context.active_object)
        col = self.layout.column(align=True)
        for key in sorted(t.keys()):
            value = t[key]
            if isinstance(value, (str, int, float)):
                col.label(text=f"{key}: {str(value)[:60]}")


classes = (CGML_OT_attr_generic_set, CGML_OT_attr_generic_remove, CGML_OT_attr_template_apply,
           CGML_OT_attr_search, CGML3_PT_Attributes, CGML3_PT_AllProperties)


def register():
    _register_props()
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    _unregister_props()

import ast
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "shared/semantic_appearance.py"


def load_module(relative_path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


sa = load_module("shared/semantic_appearance.py", "semantic_appearance_under_test")
semantic_types = load_module("shared/semantic_types.py", "semantic_types_for_appearance_test")


# ---------------------------------------------------------------------------
# Frozen dataclasses: immutability + equality
# ---------------------------------------------------------------------------


def test_polygon_appearance_context_is_frozen_and_supports_equality():
    context_a = sa.PolygonAppearanceContext(semantic_type="Window")
    context_b = sa.PolygonAppearanceContext(semantic_type="Window")

    assert context_a == context_b

    try:
        context_a.semantic_type = "Door"
        assert False, "expected FrozenInstanceError"
    except Exception as exc:
        assert type(exc).__name__ == "FrozenInstanceError"


def test_appearance_resolution_is_frozen_and_supports_equality():
    resolution_a = sa.resolve_appearance(sa.PolygonAppearanceContext(semantic_type="Window"))
    resolution_b = sa.resolve_appearance(sa.PolygonAppearanceContext(semantic_type="Window"))

    assert resolution_a == resolution_b

    try:
        resolution_a.kind = "material"
        assert False, "expected FrozenInstanceError"
    except Exception as exc:
        assert type(exc).__name__ == "FrozenInstanceError"


def test_texture_and_material_descriptors_are_frozen_and_support_equality():
    texture_a = sa.TextureDescriptor(True, True, True, True)
    texture_b = sa.TextureDescriptor(True, True, True, True)
    assert texture_a == texture_b
    try:
        texture_a.image_exists = False
        assert False, "expected FrozenInstanceError"
    except Exception as exc:
        assert type(exc).__name__ == "FrozenInstanceError"

    material_a = sa.MaterialDescriptor(True, True)
    material_b = sa.MaterialDescriptor(True, True)
    assert material_a == material_b
    try:
        material_a.has_usable_property = False
        assert False, "expected FrozenInstanceError"
    except Exception as exc:
        assert type(exc).__name__ == "FrozenInstanceError"


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------


_EXPECTED_HEX = {
    "WallSurface": "#C9C2B8",
    "RoofSurface": "#A84A3A",
    "GroundSurface": "#68635D",
    "Window": "#245A78",
    "Door": "#9A572D",
    "Balcony": "#D6A83E",
    "Chimney": "#70483E",
    "BuildingInstallation": "#D77C2F",
}


def _hex_to_rgba(hex_value: str):
    hex_value = hex_value.lstrip("#")
    return (
        int(hex_value[0:2], 16) / 255.0,
        int(hex_value[2:4], 16) / 255.0,
        int(hex_value[4:6], 16) / 255.0,
        1.0,
    )


def test_palette_has_exactly_the_eight_supported_classes():
    assert set(sa.SEMANTIC_PALETTE.keys()) == set(_EXPECTED_HEX.keys())


def test_palette_hex_values_match_spec_exactly():
    for semantic_type, hex_value in _EXPECTED_HEX.items():
        entry = sa.get_palette_entry(semantic_type)
        assert entry is not None
        assert entry.color == _hex_to_rgba(hex_value)
        assert entry.color[3] == 1.0


def test_generic_building_installation_has_its_own_distinct_color():
    installation_color = sa.get_palette_entry("BuildingInstallation").color
    balcony_color = sa.get_palette_entry("Balcony").color
    chimney_color = sa.get_palette_entry("Chimney").color

    assert installation_color != balcony_color
    assert installation_color != chimney_color
    assert sa.get_palette_entry("Unknown") is None


def test_unsupported_class_has_no_palette_entry():
    assert sa.get_palette_entry("SomeUnsupportedClass") is None


# ---------------------------------------------------------------------------
# Precedence: texture -> material -> fallback
# ---------------------------------------------------------------------------


def _valid_texture():
    return sa.TextureDescriptor(
        target_resolves=True, image_exists=True, image_loads=True, has_usable_uv=True
    )


def _valid_material():
    return sa.MaterialDescriptor(target_resolves=True, has_usable_property=True)


def test_valid_texture_wins_over_valid_material_and_fallback():
    context = sa.PolygonAppearanceContext(
        semantic_type="WallSurface",
        source_texture=_valid_texture(),
        source_material=_valid_material(),
    )
    resolution = sa.resolve_appearance(context)

    assert resolution.kind == "texture"
    assert resolution.texture == context.source_texture
    assert resolution.material is None
    assert resolution.palette_color is None


def test_valid_material_wins_over_fallback_when_texture_invalid():
    invalid_texture = sa.TextureDescriptor(
        target_resolves=True, image_exists=False, image_loads=False, has_usable_uv=True
    )
    context = sa.PolygonAppearanceContext(
        semantic_type="RoofSurface",
        source_texture=invalid_texture,
        source_material=_valid_material(),
    )
    resolution = sa.resolve_appearance(context)

    assert resolution.kind == "material"
    assert resolution.material == context.source_material
    assert resolution.texture is None


def test_fallback_used_when_both_texture_and_material_absent():
    context = sa.PolygonAppearanceContext(semantic_type="GroundSurface")
    resolution = sa.resolve_appearance(context)

    assert resolution.kind == "fallback"
    assert resolution.palette_color == sa.get_palette_entry("GroundSurface").color
    assert resolution.fallback_reason == "absent"


def test_fallback_used_when_both_texture_and_material_invalid():
    invalid_texture = sa.TextureDescriptor(
        target_resolves=True, image_exists=False, image_loads=False, has_usable_uv=False
    )
    invalid_material = sa.MaterialDescriptor(target_resolves=True, has_usable_property=False)
    context = sa.PolygonAppearanceContext(
        semantic_type="Door", source_texture=invalid_texture, source_material=invalid_material
    )
    resolution = sa.resolve_appearance(context)

    assert resolution.kind == "fallback"
    assert resolution.palette_color == sa.get_palette_entry("Door").color
    assert resolution.fallback_reason == "invalid"


# ---------------------------------------------------------------------------
# Invalid-texture / invalid-material fall-through
# ---------------------------------------------------------------------------


def test_texture_with_unresolvable_target_falls_through_to_material():
    unresolvable_texture = sa.TextureDescriptor(
        target_resolves=False, image_exists=True, image_loads=True, has_usable_uv=True
    )
    context = sa.PolygonAppearanceContext(
        semantic_type="Window", source_texture=unresolvable_texture, source_material=_valid_material()
    )
    resolution = sa.resolve_appearance(context)

    assert resolution.kind == "material"


def test_texture_with_missing_image_falls_through_to_material():
    missing_image_texture = sa.TextureDescriptor(
        target_resolves=True, image_exists=False, image_loads=False, has_usable_uv=True
    )
    context = sa.PolygonAppearanceContext(
        semantic_type="Window", source_texture=missing_image_texture, source_material=_valid_material()
    )
    resolution = sa.resolve_appearance(context)

    assert resolution.kind == "material"


def test_texture_with_missing_uvs_falls_through_to_fallback_without_error():
    missing_uv_texture = sa.TextureDescriptor(
        target_resolves=True, image_exists=True, image_loads=True, has_usable_uv=False
    )
    context = sa.PolygonAppearanceContext(semantic_type="Window", source_texture=missing_uv_texture)
    resolution = sa.resolve_appearance(context)

    assert resolution.kind == "fallback"
    assert resolution.palette_color == sa.get_palette_entry("Window").color


def test_material_with_unresolvable_target_falls_through_to_fallback():
    unresolvable_material = sa.MaterialDescriptor(target_resolves=False, has_usable_property=True)
    context = sa.PolygonAppearanceContext(semantic_type="Balcony", source_material=unresolvable_material)
    resolution = sa.resolve_appearance(context)

    assert resolution.kind == "fallback"
    assert resolution.palette_color == sa.get_palette_entry("Balcony").color


def test_material_with_no_parseable_property_falls_through_to_fallback():
    unparseable_material = sa.MaterialDescriptor(target_resolves=True, has_usable_property=False)
    context = sa.PolygonAppearanceContext(semantic_type="Chimney", source_material=unparseable_material)
    resolution = sa.resolve_appearance(context)

    assert resolution.kind == "fallback"
    assert resolution.palette_color == sa.get_palette_entry("Chimney").color


def test_xml_reference_alone_with_no_resolvable_target_is_treated_as_absent_not_valid():
    reference_only_texture = sa.TextureDescriptor(
        target_resolves=False, image_exists=False, image_loads=False, has_usable_uv=False
    )
    reference_only_material = sa.MaterialDescriptor(target_resolves=False, has_usable_property=False)
    context = sa.PolygonAppearanceContext(
        semantic_type="WallSurface",
        source_texture=reference_only_texture,
        source_material=reference_only_material,
    )
    resolution = sa.resolve_appearance(context)

    assert resolution.kind == "fallback"
    assert resolution.fallback_reason == "absent"


def test_import_appearance_disabled_forces_fallback_even_with_valid_source():
    context = sa.PolygonAppearanceContext(
        semantic_type="RoofSurface",
        source_texture=_valid_texture(),
        source_material=_valid_material(),
        import_appearance_enabled=False,
    )
    resolution = sa.resolve_appearance(context)

    assert resolution.kind == "fallback"
    assert resolution.fallback_reason == "disabled"
    assert resolution.palette_color == sa.get_palette_entry("RoofSurface").color


# ---------------------------------------------------------------------------
# Diagnostics counters
# ---------------------------------------------------------------------------


def test_diagnostics_counter_tallies_all_five_categories_from_a_sequence_of_resolutions():
    resolutions = [
        sa.resolve_appearance(
            sa.PolygonAppearanceContext(semantic_type="WallSurface", source_texture=_valid_texture())
        ),
        sa.resolve_appearance(
            sa.PolygonAppearanceContext(semantic_type="RoofSurface", source_texture=_valid_texture())
        ),
        sa.resolve_appearance(
            sa.PolygonAppearanceContext(semantic_type="Window", source_material=_valid_material())
        ),
        sa.resolve_appearance(sa.PolygonAppearanceContext(semantic_type="GroundSurface")),
        sa.resolve_appearance(
            sa.PolygonAppearanceContext(
                semantic_type="Door",
                source_texture=sa.TextureDescriptor(
                    target_resolves=True, image_exists=False, image_loads=False, has_usable_uv=False
                ),
            )
        ),
        sa.resolve_appearance(
            sa.PolygonAppearanceContext(
                semantic_type="BuildingInstallation", unknown_installation_subtype=True
            )
        ),
    ]

    counters = sa.tally_resolutions(resolutions)

    assert counters.source_textures_used == 2
    assert counters.source_materials_used == 1
    assert counters.semantic_fallback_used == 3
    assert counters.missing_invalid_appearance == 1
    assert counters.unknown_installation_subtypes == 1


def test_diagnostics_counter_starts_at_zero():
    counters = sa.AppearanceDiagnosticsCounters()

    assert counters.source_textures_used == 0
    assert counters.source_materials_used == 0
    assert counters.semantic_fallback_used == 0
    assert counters.missing_invalid_appearance == 0
    assert counters.unknown_installation_subtypes == 0


# ---------------------------------------------------------------------------
# Warning dedup / unknown-subtype summary cap
# ---------------------------------------------------------------------------


def test_warning_dedup_collapses_repeated_identical_warnings_into_one_entry_plus_count():
    warnings = sa.DeduplicatedWarnings()
    warnings.add("missing texture: foo.jpg")
    warnings.add("missing texture: foo.jpg")
    warnings.add("missing texture: bar.jpg")
    warnings.add("missing texture: foo.jpg")

    entries = warnings.entries()

    assert entries == [("missing texture: foo.jpg", 3), ("missing texture: bar.jpg", 1)]
    assert warnings.overflow_count() == 0


def test_unknown_subtype_summary_caps_at_20_distinct_values_plus_more_count():
    summary = sa.new_unknown_subtype_summary()
    for i in range(25):
        summary.add(f"value-{i}")
    # A repeat of an already-seen distinct value should not consume cap headroom
    # or inflate the overflow count.
    summary.add("value-0")

    entries = summary.entries()

    assert len(entries) == 20
    assert entries == [(f"value-{i}", 2 if i == 0 else 1) for i in range(20)]
    assert summary.overflow_count() == 5
    assert summary.distinct_count == 25


def test_unknown_subtype_summary_under_cap_has_no_overflow():
    summary = sa.new_unknown_subtype_summary()
    summary.add("9999")
    summary.add("9999")
    summary.add("abcd")

    assert summary.entries() == [("9999", 2), ("abcd", 1)]
    assert summary.overflow_count() == 0


# ---------------------------------------------------------------------------
# Integration with shared/semantic_types.py's strict resolver (not reimplemented here)
# ---------------------------------------------------------------------------


def test_uses_semantic_types_verdict_as_input_without_reimplementing_it():
    verdict = semantic_types.resolve_semantic_type(class_value="5010", name="Balcony 1")
    assert verdict.canonical_type == "Balcony"

    context = sa.PolygonAppearanceContext(semantic_type=verdict.canonical_type)
    resolution = sa.resolve_appearance(context)

    assert resolution.palette_color == sa.get_palette_entry("Balcony").color


def test_unresolved_installation_subtype_stays_generic_and_gets_installation_palette():
    verdict = semantic_types.resolve_semantic_type(
        source_feature_type="bldg:BuildingInstallation", class_value="9999", name="Balcony"
    )
    assert verdict.canonical_type == "BuildingInstallation"

    context = sa.PolygonAppearanceContext(
        semantic_type=verdict.canonical_type, unknown_installation_subtype=True
    )
    resolution = sa.resolve_appearance(context)

    assert resolution.kind == "fallback"
    assert resolution.palette_color == sa.get_palette_entry("BuildingInstallation").color
    assert resolution.unknown_installation_subtype is True


# ---------------------------------------------------------------------------
# Module boundary: no bpy, no XML parsing, no file I/O
# ---------------------------------------------------------------------------


_FORBIDDEN_MODULES = {
    "bpy",
    "bmesh",
    "mathutils",
    "xml",
    "lxml",
    "xml.etree",
    "xml.etree.ElementTree",
    "os",
    "pathlib",
    "io",
}


def test_module_imports_no_bpy_xml_or_file_io():
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE_PATH))

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_names.add(node.module.split(".")[0])

    forbidden_hits = imported_names & _FORBIDDEN_MODULES
    assert not forbidden_hits, f"forbidden imports found: {forbidden_hits}"

    assert "open(" not in source

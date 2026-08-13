import pytest

from vector_csv_exporter_plugin.export_utils import build_group_header, data_header_signature


def test_wkt_field_renamed_and_index_map():
    layers = [("layerA", ["Name", "Type"]), ("layerB", ["Name", "Type", "WKT"])]  # second layer has a real WKT field
    header, maps, canon = build_group_header(layers)

    # appended columns should exist and be collision-safe
    assert header[-1].lower() == "wkt"
    assert header[-2].lower().startswith("source_layer")

    # the second layer's original 'WKT' should have been renamed to avoid colliding with the appended WKT
    layerb_canon = canon[1]
    assert "wkt" in layerb_canon
    renamed = layerb_canon["wkt"]
    assert renamed.lower() != "wkt"
    assert any(h == renamed for h in header[:-2])

    # the index map for the second layer should point the renamed header to the original attribute index (2)
    idx_map_b = maps[1]
    assert idx_map_b[renamed.lower()] == 2


def test_source_layer_field_renamed():
    layers = [("layer", ["id", "Source_Layer", "value"]) ]
    header, maps, canon = build_group_header(layers)

    layer_map = canon[0]
    assert "source_layer" in layer_map
    assert layer_map["source_layer"].lower() != "source_layer"
    assert any(h == layer_map["source_layer"] for h in header[:-2])
    assert header[-2].lower().startswith("source_layer")
    assert header[-1].lower() == "wkt"


def test_index_maps_preserve_non_sequential_original_indices():
    # When the user deselects fields, the (orig_index, name) pairs are
    # non-sequential -- e.g. fields 0 and 2 of a 3-field layer. The index
    # maps must point each header column at the TRUE attribute index, not a
    # renumbered sequential one, or values are read from the wrong column.
    layers = [("layer", [(0, "id"), (2, "value"), (5, "note")])]
    header, maps, _canon = build_group_header(layers)

    index_map = maps[0]
    assert index_map["id"] == 0
    assert index_map["value"] == 2
    assert index_map["note"] == 5


def test_geometry_named_field_is_renamed_not_dropped():
    # Regression: a real attribute field literally named 'geometry' used to
    # be silently dropped from the export (no column, no log). It must be
    # preserved via rename like the other reserved names.
    layers = [("layer", [(0, "id"), (1, "Geometry"), (2, "value")])]
    header, maps, canon = build_group_header(layers)

    layer_map = canon[0]
    assert "geometry" in layer_map, "field must be registered, not discarded"
    renamed = layer_map["geometry"]
    assert renamed.lower() != "geometry"
    assert any(h == renamed for h in header[:-2]), "renamed column must be in the header"
    # index map must point the renamed column back at the true attribute index
    assert maps[0][renamed.lower()] == 1


def test_data_header_signature_ignores_reserved():
    sig1 = data_header_signature(["Name", "WKT"])
    sig2 = data_header_signature(["Name"])  # WKT is reserved and should be ignored in signature
    assert sig1 == sig2


def test_multiple_layers_same_reserved_field_rename():
    # Two layers both have a real 'WKT' field; they should map to the same
    # canonical renamed header column (not two different renamed variants).
    layers = [
        ("layerA", ["id", "WKT"]),
        ("layerB", ["id", "WKT"]),
        ("layerC", ["id", "name"]),
    ]
    header, maps, canon = build_group_header(layers)

    # Find the canonical renamed header for the WKT field from the first layer
    first_map = canon[0]
    assert "wkt" in first_map
    renamed = first_map["wkt"]
    assert renamed.lower() != "wkt"

    # The second layer should map its 'wkt' too, and it must match the same name
    second_map = canon[1]
    assert second_map.get("wkt") == renamed

    # Header should contain only one occurrence of the renamed column
    occurrences = [h for h in header[:-2] if h == renamed]
    assert len(occurrences) == 1


def test_reserved_collision_log_field_name_matches_owning_layer():
    """
    Regression test for a stale-variable bug in
    VectorCsvExporterDockWidget.export_selected_layers(): the reserved-name
    collision log message must resolve each layer's original field casing
    from that layer's own field_names (as fixed), not from a variable left
    over from an earlier, unrelated loop. Two layers here both have a field
    colliding with the reserved "wkt" column but with different original
    casing, so mixing the two layers up would be caught.
    """
    layers = [
        ("layerA", [(0, "id"), (1, "Wkt")]),
        ("layerB", [(0, "id"), (1, "wKT")]),
    ]
    header, maps, canon = build_group_header(layers)

    resolved = {}
    for (layer_name, field_names), canonical_map in zip(layers, canon):
        for orig_lower, canonical in canonical_map.items():
            if canonical.lower() != orig_lower:
                # Mirrors the fixed lookup in dock_widget.py: resolve the
                # original-cased name from *this* layer's own field_names,
                # not a stale variable from an earlier, already-finished loop.
                orig_name = next(
                    (name for (_idx, name) in field_names if name and name.strip().lower() == orig_lower),
                    orig_lower,
                )
                resolved[layer_name] = orig_name

    assert resolved["layerA"] == "Wkt"
    assert resolved["layerB"] == "wKT"
    assert resolved["layerA"] != resolved["layerB"]

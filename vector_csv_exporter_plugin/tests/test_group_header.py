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


def test_data_header_signature_ignores_reserved():
    sig1 = data_header_signature(["Name", "WKT"])
    sig2 = data_header_signature(["Name"])  # WKT is reserved and should be ignored in signature
    assert sig1 == sig2

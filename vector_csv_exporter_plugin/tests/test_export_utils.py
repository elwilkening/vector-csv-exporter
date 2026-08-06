from export_utils import (
    build_output_name,
    data_header_signature,
    normalize_value,
    sanitize_prefix,
    source_layer_column_name,
)


def test_strings_starting_with_formula_characters_are_escaped():
    assert normalize_value("=1+1") == "'=1+1"
    assert normalize_value("+SUM(A1:A2)") == "'+SUM(A1:A2)"
    assert normalize_value("-5") == "'-5"
    assert normalize_value("@cmd") == "'@cmd"


def test_non_string_values_are_left_as_strings_without_escaping():
    assert normalize_value(None) == ""
    assert normalize_value(42) == "42"
    assert normalize_value(3.14) == "3.14"


def test_bytes_are_decoded_and_escaped_when_needed():
    assert normalize_value(b"=cmd") == "'=cmd"


def test_sanitize_prefix_replaces_invalid_characters():
    assert sanitize_prefix("My Layer #1") == "My_Layer_1"
    assert sanitize_prefix("   ") == "export"


def test_build_output_name_uses_group_suffix_when_needed():
    assert build_output_name("export", 2, 1) == "export_group1.csv"
    assert build_output_name("export.csv", 1, 1) == "export.csv"


def test_data_header_signature_and_source_layer_name_are_stable():
    assert data_header_signature(["Name", "Geometry", "Age"]) == ("age", "name")
    assert source_layer_column_name(["id", "name"]) == "SOURCE_LAYER"
    assert source_layer_column_name(["SOURCE_LAYER", "name"]) == "SOURCE_LAYER_2"

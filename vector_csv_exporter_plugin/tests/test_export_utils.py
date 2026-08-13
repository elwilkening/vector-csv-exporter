from vector_csv_exporter_plugin.export_utils import (
    build_output_name,
    data_header_signature,
    normalize_value,
    sanitize_prefix,
    source_layer_column_name,
    wkt_column_name,
    dedupe_field_names,
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


def test_booleans_serialize_as_integers_matching_csvt():
    # The .csvt sidecar declares Bool columns as Integer, so the cell
    # values must be 1/0 -- "True" would parse back as 0 on re-import.
    assert normalize_value(True) == "1"
    assert normalize_value(False) == "0"


def test_dates_serialize_as_iso_matching_csvt():
    import datetime

    assert normalize_value(datetime.date(2024, 5, 1)) == "2024-05-01"
    assert normalize_value(datetime.datetime(2024, 5, 1, 13, 30, 5)) == "2024-05-01 13:30:05"
    assert normalize_value(datetime.time(13, 30, 5)) == "13:30:05"


def test_escaping_can_be_disabled():
    assert normalize_value("=1+1", escape_formulas=False) == "=1+1"
    assert normalize_value("-5", escape_formulas=False) == "-5"
    assert normalize_value(b"=cmd", escape_formulas=False) == "=cmd"


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


def test_wkt_column_name_is_collision_safe():
    assert wkt_column_name(["id", "name"]) == "WKT"
    assert wkt_column_name(["WKT", "name"]) == "WKT_2"


def test_dedupe_field_names_preserves_indices_and_renames():
    fields = ["Name", "name", "Value"]
    new_fields, renames = dedupe_field_names(fields)
    # Ensure rename happened for the duplicate and ordering/indices preserved
    assert len(new_fields) == 3
    assert new_fields[0][1].lower() == "name"
    # second field should have been renamed and not equal to first (case-insensitive)
    assert new_fields[1][1].lower() != "name"
    # renames should include an entry for the original 'name'
    assert any(orig == "name" for (orig, new, idx) in renames)


def test_dedupe_field_names_three_way_duplicate():
    fields = ["Name", "name", "NAME"]
    new_fields, renames = dedupe_field_names(fields)
    names = [n for (_i, n) in new_fields]
    # All three should be distinct after deduplication
    assert len(set(n.lower() for n in names)) == 3
    # renames should record two rename operations (for the 2nd and 3rd)
    assert len(renames) >= 2

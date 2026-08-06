import re


def normalize_value(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        decoded = value.decode("utf-8", errors="replace")
        return _escape_for_csv(decoded)
    if isinstance(value, str):
        text = value.encode("utf-8", errors="replace").decode("utf-8")
        return _escape_for_csv(text)
    return str(value)


def _escape_for_csv(text):
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def sanitize_prefix(prefix):
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", prefix.strip())
    return sanitized or "export"


def build_output_name(prefix, group_count, index):
    if group_count == 1:
        return f"{prefix}.csv" if not prefix.lower().endswith(".csv") else prefix
    if prefix.lower().endswith(".csv"):
        prefix = prefix[:-4]
    return f"{prefix}_group{index}.csv"


def data_header_signature(field_names):
    return tuple(sorted(name.strip().lower() for name in field_names if name.strip().lower() != "geometry"))


def source_layer_column_name(field_names):
    suggestion = "SOURCE_LAYER"
    if suggestion.lower() in {name.lower() for name in field_names}:
        return "SOURCE_LAYER_2"
    return suggestion

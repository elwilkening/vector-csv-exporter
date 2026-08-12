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
    """
    Return a signature tuple for grouping layers by their real attribute fields.
    Excludes reserved/plugin-appended names (geometry, wkt, source_layer) so
    that layers which only differ by carrying a real field named like a
    reserved column still group together.
    """
    reserved = {"geometry", "wkt", "source_layer"}
    return tuple(sorted(name.strip().lower() for name in field_names if name.strip() and name.strip().lower() not in reserved))


def _uniquify_name(base, used_lower):
    base_str = base
    candidate = f"{base_str}_ATTR"
    cand_lower = candidate.lower()
    if cand_lower not in used_lower:
        return candidate
    i = 2
    while True:
        candidate = f"{base_str}_{i}"
        cand_lower = candidate.lower()
        if cand_lower not in used_lower:
            return candidate
        i += 1


def source_layer_column_name(existing_names):
    """Return a collision-safe source layer column name based on existing header names.
    existing_names may be any iterable of strings; comparison is case-insensitive.
    """
    used = {n.lower() for n in existing_names}
    base = "SOURCE_LAYER"
    if base.lower() not in used:
        return base
    i = 2
    while True:
        candidate = f"{base}_{i}"
        if candidate.lower() not in used:
            return candidate
        i += 1


def wkt_column_name(existing_names):
    """Return a collision-safe WKT column name based on existing header names."""
    used = {n.lower() for n in existing_names}
    base = "WKT"
    if base.lower() not in used:
        return base
    i = 2
    while True:
        candidate = f"{base}_{i}"
        if candidate.lower() not in used:
            return candidate
        i += 1


def build_group_header(layers_with_fields):
    """
    Build a header list for a group of layers (unioning attribute fields) and
    append collision-safe source-layer and WKT columns. `layers_with_fields`
    is an iterable of (layer_object_or_name, field_name_list).

    Returns (header_list, per_layer_index_maps) where per_layer_index_maps is
    a list (parallel to layers_with_fields) of dicts mapping header_name.lower()
    -> attribute_index (or None).
    """
    header = []
    used_map = {}  # lower -> header_name (canonical)
    reserved = {"geometry", "wkt", "source_layer"}
    per_layer_maps = []

    # First pass: build canonical header names, renaming conflicting real fields
    for layer, field_names in layers_with_fields:
        layer_map = {}
        for idx, name in enumerate(field_names):
            if not name or not name.strip():
                continue
            normalized = name.strip()
            n_lower = normalized.lower()
            if n_lower == "geometry":
                continue
            if n_lower in used_map:
                # reuse existing canonical name
                layer_map[n_lower] = used_map[n_lower]
                continue
            if n_lower in reserved:
                # need to pick a unique renamed candidate
                candidate = _uniquify_name(normalized, set(used_map.keys()).union(reserved))
                used_map[candidate.lower()] = candidate
                header.append(candidate)
                layer_map[n_lower] = candidate
            else:
                used_map[n_lower] = normalized
                header.append(normalized)
                layer_map[n_lower] = normalized
        per_layer_maps.append((field_names, layer_map))

    # add source-layer and WKT columns (collision-safe)
    src_name = source_layer_column_name(header)
    header.append(src_name)
    wkt_name = wkt_column_name(header)
    header.append(wkt_name)

    # Build per-layer index maps aligned to header[:-2]
    header_before_reserved = header[:-2]
    result_layer_index_maps = []
    per_layer_canonical_maps = []
    for (field_names, layer_map) in per_layer_maps:
        orig_index = {name.strip().lower(): idx for idx, name in enumerate(field_names)}
        index_map = {}
        for h in header_before_reserved:
            h_lower = h.lower()
            # find if this header corresponds to one of the layer's original fields
            matched_idx = None
            # layer_map maps original lower -> canonical header name
            for orig_lower, canonical in layer_map.items():
                if canonical.lower() == h_lower:
                    matched_idx = orig_index.get(orig_lower)
                    break
            index_map[h_lower] = matched_idx
        result_layer_index_maps.append(index_map)

        per_layer_canonical_maps.append(layer_map)

    return header, result_layer_index_maps, per_layer_canonical_maps

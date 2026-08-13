import datetime
import re

# Reserved column names appended by the plugin or treated specially.
# Keep these lower-case for comparisons.
RESERVED_NAMES = {"geometry", "wkt", "source_layer"}


def normalize_value(value, escape_formulas=True):
    if value is None:
        return ""
    # bool must precede the generic fallback: str(True) would write "True"
    # into a column whose .csvt sidecar declares Integer, so GDAL would
    # parse every True back as 0 on re-import.
    if isinstance(value, bool):
        return "1" if value else "0"
    # datetime must precede date (datetime subclasses date). The formats
    # match what GDAL's CSV driver expects for csvt Date/Time/DateTime
    # columns, so re-imports round-trip.
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, datetime.time):
        return value.isoformat(timespec="seconds")
    if isinstance(value, bytes):
        decoded = value.decode("utf-8", errors="replace")
        return _escape_for_csv(decoded, escape_formulas)
    if isinstance(value, str):
        text = value.encode("utf-8", errors="replace").decode("utf-8")
        return _escape_for_csv(text, escape_formulas)
    return str(value)


# Spreadsheet formula-injection trigger prefixes (OWASP set): the classic
# formula starters plus tab and carriage return, which Excel strips before
# interpreting whatever follows (so "\t=cmd" executes like "=cmd").
FORMULA_TRIGGER_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _escape_for_csv(text, escape_formulas=True):
    if escape_formulas and text.startswith(FORMULA_TRIGGER_PREFIXES):
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
    return tuple(sorted(name.strip().lower() for name in field_names if name.strip() and name.strip().lower() not in RESERVED_NAMES))


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


def dedupe_field_names(field_names):
    """
    Deduplicate a single layer's field name list while preserving attribute
    ordering and indices. Returns (new_field_names, rename_map) where
    rename_map maps the original lowercased name to the new canonical name
    for any renamed fields in this list.
    """
    used_lower = set()
    new_field_pairs = []
    renames = []  # list of tuples (orig_lower, new_name, true_index)

    for item in field_names:
        # field_names may be a list of names or (index, name) pairs; normalize
        if isinstance(item, tuple) and len(item) >= 2:
            orig_index, name = item[0], item[1]
        else:
            # no index provided; assume sequential indices (caller should provide true indices)
            orig_index = None
            name = item

        if not name or not name.strip():
            new_field_pairs.append((orig_index, name))
            continue
        normalized = name.strip()
        n_lower = normalized.lower()
        if n_lower in used_lower:
            # Need to pick a unique candidate that doesn't collide with
            # already used names or reserved names.
            candidate = _uniquify_name(normalized, used_lower.union(RESERVED_NAMES))
            new_field_pairs.append((orig_index, candidate))
            renames.append((n_lower, candidate, orig_index))
            used_lower.add(candidate.lower())
        else:
            new_field_pairs.append((orig_index, normalized))
            used_lower.add(n_lower)

    return new_field_pairs, renames


def _collision_safe_name(base, existing_names):
    """Return `base`, or a `base_2`, `base_3`, ... variant, whichever doesn't
    collide case-insensitively with any name in existing_names."""
    used = {n.lower() for n in existing_names}
    if base.lower() not in used:
        return base
    i = 2
    while True:
        candidate = f"{base}_{i}"
        if candidate.lower() not in used:
            return candidate
        i += 1


def source_layer_column_name(existing_names):
    """Return a collision-safe source layer column name based on existing header names.
    existing_names may be any iterable of strings; comparison is case-insensitive.
    """
    return _collision_safe_name("SOURCE_LAYER", existing_names)


def wkt_column_name(existing_names):
    """Return a collision-safe WKT column name based on existing header names."""
    return _collision_safe_name("WKT", existing_names)


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
    reserved = RESERVED_NAMES
    per_layer_maps = []

    # First pass: build canonical header names, renaming conflicting real fields
    for layer, field_names in layers_with_fields:
        layer_map = {}
        # field_names may be a list of plain names or (orig_index, name) pairs
        for entry in field_names:
            if isinstance(entry, tuple) and len(entry) >= 2:
                _orig_idx, name = entry[0], entry[1]
            else:
                name = entry

            if not name or not name.strip():
                continue
            normalized = name.strip()
            n_lower = normalized.lower()
            if n_lower in used_map:
                # reuse existing canonical name
                layer_map[n_lower] = used_map[n_lower]
                continue
            if n_lower in reserved:
                # need to pick a unique renamed candidate; register both the
                # canonical candidate and the original lowercased reserved name
                candidate = _uniquify_name(normalized, set(used_map.keys()).union(reserved))
                used_map[candidate.lower()] = candidate
                used_map[n_lower] = candidate
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
        # Build a mapping from original lowercased field name to the true original
        # attribute index. field_names entries may be (orig_index, name) pairs.
        orig_index = {}
        seq_idx = 0
        for entry in field_names:
            if isinstance(entry, tuple) and len(entry) >= 2:
                idx, name = entry[0], entry[1]
            else:
                idx, name = seq_idx, entry
            seq_idx += 1
            if name and name.strip():
                orig_index[name.strip().lower()] = idx
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

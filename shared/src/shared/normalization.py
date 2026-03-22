"""Normalization utilities for stable state comparison."""

import json
from typing import Any


def normalize_for_comparison(data: Any) -> Any:
    """
    Recursively normalize data for stable equality comparisons.

    - Strips volatile fields (timestamps, RSSI, etc.)
    - Sorts lists of primitives and lists of dicts by stable keys
    - Ensures consistent types (tuples to lists)
    """
    # Volatile keys to ignore in comparisons
    volatile_keys = {
        "timestamp",
        "rssi",
        "RSSI",
        "last_seen",
        "uptime",
        "last_error",
        "pts_ns",
        "dropped_frames",
        "current_time",
        "sources_time",
        "metrics",
    }

    if isinstance(data, dict):
        # Filter out volatile keys and recurse
        return {
            k: normalize_for_comparison(v)
            for k, v in data.items()
            if k not in volatile_keys
        }

    if isinstance(data, (list, tuple)):
        # Recurse into elements
        normalized_list = [normalize_for_comparison(item) for item in data]

        # Attempt to sort the list for stable comparison
        try:
            # First, try to sort based on a common stable key if it's a list of dicts
            if all(isinstance(i, dict) for i in normalized_list):
                # Common ID keys in the project
                id_keys = ["source_id", "target_id", "mac", "Address", "uid", "adapter_id", "id", "path"]

                def get_sort_key(d: dict[str, Any]) -> str:
                    for key in id_keys:
                        if key in d:
                            return str(d[key])
                    # Fallback to json string representation if no ID key found
                    return json.dumps(d, sort_keys=True)

                normalized_list.sort(key=get_sort_key)
            else:
                # Try standard sort for primitives
                normalized_list.sort()
        except (TypeError, ValueError):
            # If sorting fails (mixed types or non-sortable), keep as is but logged or handled
            pass

        return normalized_list

    return data

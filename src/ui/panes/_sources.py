#!/usr/bin/env python3
"""
Settings Pane Data Sources

The blocking lookups behind the settings panes (camera names and permission,
NDI sources, measured frame cost, the OSC address map) behind plain functions,
imported lazily so opening the window never loads cv2 or MediaPipe. The
renderer calls them through this module's attributes from worker threads,
which lets tests substitute them.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def camera_names() -> List[str]:
    """Camera names in device-index order (see src/camera_devices.py for the index caveat)."""
    from ...camera_devices import camera_names as names
    return names()


def camera_authorization() -> str:
    """authorized, denied, restricted, undetermined or unknown."""
    from ...camera_devices import camera_authorization as auth
    return auth()


def ndi_sources() -> List[str]:
    """NDI sources currently visible on the network (blocks up to ~5 s)."""
    from ...ndi_capture import list_ndi_sources
    return list(list_ndi_sources())


def load_costs() -> Dict[str, float]:
    """Cached per-model frame times in ms."""
    from ...frame_cost import load_cache
    return load_cache()


def measure_cost(model: str) -> Optional[float]:
    """Measure one model, cache it and return its ms (None on failure)."""
    from ... import frame_cost
    ms = frame_cost.measure_model(model)
    if ms is not None:
        costs = frame_cost.load_cache()
        costs[model] = round(ms, 1)
        frame_cost.save_cache(costs)
    return ms


def address_map(protocol: str, mode: str, num_poses: int) -> List[Tuple[str, str, str]]:
    """(address, payload, rate) rows for the active format and mode."""
    from ...osc_preview import addresses
    return addresses(protocol, mode, num_poses)


def payload_sample(protocol: str, mode: str) -> Tuple[str, str]:
    """(sample text, note) for the active format and mode."""
    from ...osc_preview import payload_sample as sample
    return sample(protocol, mode)

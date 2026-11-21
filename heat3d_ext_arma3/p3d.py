"""
The size of a model (`.p3d`): its bounding box, for the boxes a level draws where a model stands.

* ODOL (binarised): `ODOL`, version, (from 59) an application id, (from 58) a zero-terminated prefix, the level of
  detail count and their resolutions, then the model info, which holds the bounding box (minimum and maximum corner)
  after the hints, aiming centre, colours and view density, and from version 52 the visual bounding box too. The
  start is found by trying the optional fields in turn and accepting only a lod count whose resolutions are ordered,
  positive numbers; the box only when its corners are ordered and under 5 km.
* MLOD (source models, as some addons ship): `MLOD`, version, lod count, then each lod `P3DM` with its points;
  the box is that of the first lod's points.

Anything else, or a file that fails the checks, gives None: the level then leaves that model out and counts it.
"""

from __future__ import annotations

import struct

import numpy as np


def _ordered_resolutions(res: tuple) -> bool:
    if not res or any(not np.isfinite(r) or r < 0 for r in res):
        return False
    return all(b >= a for a, b in zip(res, res[1:])) or res[0] < 1000


def _box(data: bytes, at: int) -> tuple[np.ndarray, np.ndarray] | None:
    try:
        lo = np.array(struct.unpack_from("<3f", data, at), dtype=np.float32)
        hi = np.array(struct.unpack_from("<3f", data, at + 12), dtype=np.float32)
    except struct.error:
        return None
    if not (np.isfinite(lo).all() and np.isfinite(hi).all()):
        return None
    if (hi < lo).any() or (hi - lo).max() > 5000 or (hi - lo).max() <= 0:
        return None
    return lo, hi


def odol_bounds(data: bytes) -> tuple[np.ndarray, np.ndarray] | None:
    if data[:4] != b"ODOL":
        return None
    (version,) = struct.unpack_from("<I", data, 4)
    candidates = []
    for app_id in (version >= 59, False, True):
        at = 8 + (4 if app_id else 0)
        starts = [at]
        if version >= 58:
            end = data.find(b"\0", at, at + 512)
            if end >= 0:
                starts.insert(0, end + 1)
        candidates += starts
    for at in dict.fromkeys(candidates):
        try:
            (lods,) = struct.unpack_from("<I", data, at)
            if not 1 <= lods <= 64:
                continue
            res = struct.unpack_from(f"<{lods}f", data, at + 4)
        except struct.error:
            continue
        if not _ordered_resolutions(res):
            continue
        info = at + 4 + 4 * lods
        # index, two sphere radii, remarks, and/or hints, aiming centre, colour, colour type, view density
        geometry = _box(data, info + 48)
        if geometry is None:
            continue
        if version >= 52:
            visual_at = info + 48 + 24 + (4 if version >= 70 else 0) + (4 if version >= 71 else 0)
            visual = _box(data, visual_at)
            if visual is not None:
                return visual
        return geometry
    return None


def mlod_bounds(data: bytes) -> tuple[np.ndarray, np.ndarray] | None:
    if data[:4] != b"MLOD":
        return None
    try:
        _version, lods = struct.unpack_from("<II", data, 4)
        at = 12
        if data[at : at + 4] not in (b"P3DM", b"SP3X"):
            return None
        _major, _minor, points, _normals, _faces, _flags = struct.unpack_from("<6I", data, at + 4)
        if not 0 < points < 5_000_000:
            return None
        pts = np.frombuffer(data, dtype="<f4", count=points * 4, offset=at + 28).reshape(points, 4)[:, :3]
    except (struct.error, ValueError):
        return None
    if not np.isfinite(pts).all():
        return None
    return pts.min(0), pts.max(0)


def bounds(data: bytes) -> tuple[np.ndarray, np.ndarray] | None:
    """(min corner, max corner) in the model's own frame (x right, y up, z forward), or None."""
    return odol_bounds(data) or mlod_bounds(data)


def write_odol(lo, hi, version: int = 73, lods: tuple = (1.0, 2.0, 1e15)) -> bytes:
    """A minimal ODOL head with a bounding box, for tests."""
    out = bytearray(b"ODOL" + struct.pack("<I", version))
    if version >= 59:
        out += struct.pack("<I", 107410)
    if version >= 58:
        out += b"a3\\test\0"
    out += struct.pack("<I", len(lods)) + struct.pack(f"<{len(lods)}f", *lods)
    out += struct.pack("<I2f3I3f2If", 0, 5.0, 5.0, 0, 0, 0, 0, 0, 0, 0, 0, 1.0)
    out += struct.pack("<6f", *lo, *hi)
    if version >= 70:
        out += struct.pack("<f", 1.0)
    if version >= 71:
        out += struct.pack("<f", 1.0)
    if version >= 52:
        out += struct.pack("<6f", *lo, *hi)
    out += b"\0" * 64
    return bytes(out)

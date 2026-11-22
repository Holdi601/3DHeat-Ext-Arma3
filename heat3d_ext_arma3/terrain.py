"""
A big terrain as a tiled level that the viewer streams: `<World>.terrain.json` (the index) and `<World>.terrain.bin`
(the tiles), format "3dheat-terrain" version 1. Not tied to Arma: any height grid with placed objects can be
written this way (`write_terrain`), and the viewer reads it the same for every game.

Heights: the grid, padded at its east and south edges to 128 x 2^T cells a side, as a pyramid of levels made by
taking every second sample of the level below (level 0 the finest), each level cut into tiles of 128 x 128 cells.
A tile holds 133 x 133 samples - its 129 plus two more round it, so a reader can take slopes at its edges without
the tile next to it - as uint16 (height = base + value * step, both per tile, in the index), rows running south,
then one uint8 class per sample (what the ground is: beach, rock, road ...; names and colours in the index). Each
tile is one zlib stream; the index gives its byte range, so a tile is read with one range request.

Objects: every placed object as a 20-byte record - x, y, z (float32: where the model's origin is, in the level's
frame), yaw (uint16, a whole turn in 65536 steps, about +y), model (uint16, an index into the index's model table of
boxes), the model's up axis tipped (int8 x and z, of 127), scale (uint16, of 4096) - in square cells of the index's
size, each cell's records in one range. A model's box is in its own frame turned to the level's handedness: x
right, y up, z back. A corner c of the box is at position + scale * T(up) * Ry(yaw) * c, where T(up) is the
smallest turn that takes +y to the up axis.

Frame: metres, x east, y up, z south (the level's frame, the same as the glb's); sample (i, j) of level l is at
x = origin x + i * spacing_l, z = origin z + j * spacing_l.
"""

from __future__ import annotations

import json
import math
import zlib
from pathlib import Path

import numpy as np

FORMAT = "3dheat-terrain"
VERSION = 1
TILE = 128
APRON = 2
SIDE = TILE + 1 + 2 * APRON
RECORD = np.dtype([("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("yaw", "<u2"), ("model", "<u2"),
                   ("ux", "i1"), ("uz", "i1"), ("scale", "<u2")])
assert RECORD.itemsize == 20

#: Ground classes, in the order of their numbers, and their sRGB colours.
CLASSES = ("low", "high", "slope", "rock", "beach", "sea floor", "road")
CLASS_COLOURS = {
    "low": (118, 132, 82), "high": (150, 146, 128), "slope": (110, 108, 84), "rock": (128, 124, 116),
    "beach": (196, 182, 140), "sea floor": (58, 92, 112), "road": (92, 90, 86),
}
#: Roads are painted into the levels whose samples are at most this far apart, metres.
ROAD_SPACING = 32.0


def pyramid_depth(cells: int) -> int:
    """T: the levels above level 0, so that 128 x 2^T cells cover `cells`."""
    return max(0, math.ceil(math.log2(max(cells, 1) / TILE)))


def pad(grid: np.ndarray) -> np.ndarray:
    """The grid padded at its east and south edges (by repeating them) to 128 x 2^T + 1 samples a side."""
    rows, cols = grid.shape
    cells = TILE << pyramid_depth(max(rows, cols) - 1)
    return np.pad(grid, ((0, cells + 1 - rows), (0, cells + 1 - cols)), mode="edge")


def classes_of(h: np.ndarray, spacing: float, high: float) -> np.ndarray:
    """What each sample is, from its height and slope (the numbers of `CLASSES`)."""
    gz, gx = np.gradient(h.astype(np.float32), spacing)
    slope = np.degrees(np.arctan(np.hypot(gx, gz)))
    c = np.zeros(h.shape, dtype=np.uint8)
    c[h > high] = 1
    c[slope > 22] = 2
    c[slope > 38] = 3
    c[(h >= 0) & (h < 2.5) & (slope < 12)] = 4
    c[h < 0] = 5
    return c


def paint_roads(c: np.ndarray, roads: np.ndarray, origin: tuple[float, float], spacing: float) -> int:
    """Marks the samples within half a road's width (at least half a sample) of each segment; returns how many."""
    if not len(roads):
        return 0
    rows, cols = c.shape
    painted = 0
    for x0, z0, x1, z1, width in roads:
        r = max(width / 2, spacing / 2)
        i0 = max(0, int(math.floor((min(x0, x1) - r - origin[0]) / spacing)))
        i1 = min(cols - 1, int(math.ceil((max(x0, x1) + r - origin[0]) / spacing)))
        j0 = max(0, int(math.floor((min(z0, z1) - r - origin[1]) / spacing)))
        j1 = min(rows - 1, int(math.ceil((max(z0, z1) + r - origin[1]) / spacing)))
        if i1 < i0 or j1 < j0:
            continue
        xs = origin[0] + np.arange(i0, i1 + 1) * spacing
        zs = origin[1] + np.arange(j0, j1 + 1) * spacing
        px, pz = np.meshgrid(xs, zs)
        dx, dz = x1 - x0, z1 - z0
        length2 = dx * dx + dz * dz
        t = np.clip(((px - x0) * dx + (pz - z0) * dz) / length2, 0, 1) if length2 > 0 else np.zeros_like(px)
        near = np.hypot(px - (x0 + t * dx), pz - (z0 + t * dz)) <= r
        c[j0:j1 + 1, i0:i1 + 1][near] = CLASSES.index("road")
        painted += int(near.sum())
    return painted


def tile_of(level: np.ndarray, tx: int, tz: int) -> np.ndarray:
    """Tile (tx, tz) of one level with its apron, SIDE x SIDE, edges repeated past the level's own."""
    n = level.shape[0] - 1
    idx = lambda t: np.clip(np.arange(SIDE) + t * TILE - APRON, 0, n)  # noqa: E731
    return level[np.ix_(idx(tz), idx(tx))]


def encode_tile(h: np.ndarray, c: np.ndarray, level: int = 6) -> tuple[bytes, float, float, float, float]:
    """(zlib stream, base, step, min, max) of one tile's heights and classes."""
    lo, hi = float(h.min()), float(h.max())
    step = (hi - lo) / 65535 if hi > lo else 1.0
    q = np.clip(np.rint((h - lo) / step), 0, 65535).astype("<u2")
    return zlib.compress(q.tobytes() + c.astype(np.uint8).tobytes(), level), lo, step, lo, hi


def instance_records(positions: np.ndarray, axes: np.ndarray, models: np.ndarray) -> np.ndarray:
    """
    Records for placed objects: `positions` (n, 3) in the level's frame, `axes` (n, 3, 3) the model's x, y and z axes
    in the level's frame (model frame already turned to the level's handedness, scale included), `models` (n,) the
    model numbers.
    """
    rec = np.zeros(len(positions), dtype=RECORD)
    rec["x"], rec["y"], rec["z"] = positions[:, 0], positions[:, 1], positions[:, 2]
    ax, up = axes[:, 0], axes[:, 1]
    scale = np.linalg.norm(ax, axis=1)
    yaw = np.arctan2(-ax[:, 2], ax[:, 0]) % (2 * np.pi)
    rec["yaw"] = np.rint(yaw / (2 * np.pi) * 65536).astype(np.int64) % 65536
    u = up / np.maximum(np.linalg.norm(up, axis=1), 1e-9)[:, None]
    rec["ux"] = np.clip(np.rint(u[:, 0] * 127), -127, 127)
    rec["uz"] = np.clip(np.rint(u[:, 2] * 127), -127, 127)
    rec["scale"] = np.clip(np.rint(scale * 4096), 1, 65535)
    rec["model"] = models
    return rec


def write_terrain(stem: Path, grid: np.ndarray, spacing: float, *, origin: tuple[float, float], info: dict,
                  sea: bool = False, high: float | None = None, roads: np.ndarray | None = None,
                  objects: tuple[np.ndarray, list[dict], float] | None = None, log=print) -> dict:
    """
    Writes `<stem>.terrain.json` and `<stem>.terrain.bin`.

    `grid`: heights, rows running south from the north edge, columns east, metres; `origin`: the level-frame (x, z)
    of its first sample. `roads`: (n, 5) segments x0, z0, x1, z1, width in the level's frame. `objects`: (records,
    models as {"path", "box": [6 floats]}, cell size in metres). Returns the counts written.
    """
    rows, cols = grid.shape
    padded = pad(grid.astype(np.float32))
    depth = pyramid_depth(padded.shape[0] - 1)
    if high is None:
        land = grid[grid > 0]
        top = float(np.percentile(land, 97)) if land.size else 1.0
        high = max(60.0, top * 0.8)
    bin_path = stem.with_name(stem.name + ".terrain.bin")
    levels = []
    offset = 0
    painted = 0
    with open(bin_path, "wb") as out:
        for lv in range(depth + 1):
            step = 1 << lv
            h = padded[::step, ::step]
            s = spacing * step
            c = classes_of(h, s, high)
            if roads is not None and s <= ROAD_SPACING:
                painted += paint_roads(c, roads, origin, s)
            n = (h.shape[0] - 1) // TILE
            index = []
            for tz in range(n):
                for tx in range(n):
                    blob, base, q, lo, hi = encode_tile(tile_of(h, tx, tz), tile_of(c, tx, tz))
                    out.write(blob)
                    index.append([offset, len(blob), round(base, 4), q, round(lo, 3), round(hi, 3)])
                    offset += len(blob)
            levels.append({"spacing": s, "cells": h.shape[0] - 1, "tiles": n, "index": index})
            log(f"  terrain level {lv}: {s:g} m, {n * n} tiles")
        inst = None
        if objects is not None and len(objects[0]):
            rec, models, cell = objects
            ncx = max(1, math.ceil((cols - 1) * spacing / cell))
            ncz = max(1, math.ceil((rows - 1) * spacing / cell))
            cx = np.clip(((rec["x"] - origin[0]) // cell).astype(np.int64), 0, ncx - 1)
            cz = np.clip(((rec["z"] - origin[1]) // cell).astype(np.int64), 0, ncz - 1)
            key = cz * ncx + cx
            order = np.argsort(key, kind="stable")
            rec, key = rec[order], key[order]
            cells = []
            starts = np.flatnonzero(np.diff(np.concatenate([[-1], key]))) if len(key) else []
            bounds = list(starts) + [len(key)]
            for a, b in zip(bounds[:-1], bounds[1:]):
                k = int(key[a])
                chunk = rec[a:b].tobytes()
                out.write(chunk)
                cells.append([k % ncx, k // ncx, offset, int(b - a)])
                offset += len(chunk)
            inst = {"cell": cell, "cells": [ncx, ncz], "origin": list(origin), "record": RECORD.itemsize,
                    "layout": "x f32, y f32, z f32, yaw u16 (65536 a turn), model u16, up x i8, up z i8 (of 127), "
                              "scale u16 (of 4096)",
                    "models": models, "index": cells, "count": int(len(rec))}
    doc = {
        "format": FORMAT, "version": VERSION, **info,
        "frame": {"x": "east", "y": "up", "z": "south", "units": "m"},
        "origin": [float(origin[0]), float(origin[1])],
        "spacing": float(spacing),
        "samples": [int(cols), int(rows)],
        "extent": [float(origin[0]), float(origin[1]), float(origin[0] + (cols - 1) * spacing),
                   float(origin[1] + (rows - 1) * spacing)],
        "heights": {"min": float(grid.min()), "max": float(grid.max())},
        "tile": TILE, "apron": APRON, "side": SIDE, "codec": "zlib",
        "layout": "per tile: side x side uint16 heights (rows south, base + value * step), then side x side uint8 classes",
        "classes": {"names": list(CLASSES), "colours": [list(CLASS_COLOURS[k]) for k in CLASSES]},
        "levels": levels,
        "water": {"y": 0.0} if sea else None,
        "instances": inst,
        "bin": bin_path.name,
        "bytes": offset,
    }
    json_path = stem.with_name(stem.name + ".terrain.json")
    json_path.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    return {"levels": depth + 1, "tiles": sum(len(lv["index"]) for lv in levels), "bytes": offset,
            "objects": inst["count"] if inst else 0, "road samples": painted, "files": [json_path, bin_path]}


def read_tile(doc: dict, data: bytes, level: int, tx: int, tz: int) -> tuple[np.ndarray, np.ndarray]:
    """(heights in metres, classes) of one tile, from the index and the .bin's bytes: the reader's side, for tests."""
    lv = doc["levels"][level]
    off, length, base, step, _lo, _hi = lv["index"][tz * lv["tiles"] + tx]
    raw = zlib.decompress(data[off:off + length])
    side = doc["side"]
    q = np.frombuffer(raw, dtype="<u2", count=side * side).reshape(side, side)
    c = np.frombuffer(raw, dtype=np.uint8, offset=side * side * 2).reshape(side, side)
    return base + q.astype(np.float64) * step, c

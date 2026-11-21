"""
One Arma 3 terrain as a 3DHeat level: `<World>.glb` and `<World>.json` beside it.

Parts (node names, as the viewer reads them): `ground:<World>` (the height grid, coloured by what it is: beach, low
ground, slopes, rock, high ground), `water:<World>` (the sea at zero), `road:<World>` (the road network as flat
ribbons just above the ground) and `structure:<World>` (every placed building and object as a box of its model's
size, where the terrain places it; vegetation only with `--trees`).

Frame: metres, x east, y up, z south - Arma's own world metres with north turned to -z, so the level is the right
way round in glTF and positions from a recording (x east, y north) land on it with y north drawn as -z, the way
the viewer reads OCAP recordings. The level's south-west corner is the origin, as in the game.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import __version__, p3d
from .game import Game, World, roads_path
from .pbo import Encrypted, PboError
from .shp import read_dbf, read_polylines
from .wrp import Wrp, read_wrp

#: Model paths drawn only with --trees.
VEGETATION = ("\\plants", "\\vegetation", "\\tree", "\\bush", "\\vegetation_f", "\\plants_f", "\\plants2", "\\clutter")

# Outward faces of a box whose corner i is (x, y, z) = bits (i & 1, i & 2, i & 4) in a right-handed frame; the
# model-to-level transform turns the frame over (Arma is left-handed), so they are wound the other way round.
_BOX = np.array([[0, 4, 6], [0, 6, 2], [1, 7, 5], [1, 3, 7], [0, 1, 5], [0, 5, 4],
                 [2, 7, 3], [2, 6, 7], [0, 2, 3], [0, 3, 1], [4, 7, 6], [4, 5, 7]], dtype=np.int64)[:, [0, 2, 1]]

COLOURS = {  # sRGB
    "sea floor": (58, 92, 112), "beach": (196, 182, 140), "low": (118, 132, 82), "slope": (110, 108, 84),
    "rock": (128, 124, 116), "high": (150, 146, 128),
}


@dataclass
class Report:
    world: str
    size: float = 0.0
    spacing: float = 0.0
    ground: int = 0
    water: int = 0
    roads: int = 0
    structures: int = 0
    objects: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def skip(self, why: str, n: int = 1) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + n


def _to_level(east, up, north):
    return np.stack([east, up, -north], -1).astype(np.float32)


def height_at(heights: np.ndarray, spacing: float, east: np.ndarray, north: np.ndarray) -> np.ndarray:
    rows, cols = heights.shape
    fx = np.clip(east / spacing, 0, cols - 1.001)
    fz = np.clip(north / spacing, 0, rows - 1.001)
    x0, z0 = fx.astype(int), fz.astype(int)
    tx, tz = fx - x0, fz - z0
    h00, h10 = heights[z0, x0], heights[z0, x0 + 1]
    h01, h11 = heights[z0 + 1, x0], heights[z0 + 1, x0 + 1]
    return (h00 * (1 - tx) + h10 * tx) * (1 - tz) + (h01 * (1 - tx) + h11 * tx) * tz


def ground_colours(heights: np.ndarray, spacing: float) -> np.ndarray:
    """An sRGB image (row 0 the north edge) of what each sample is: from its height and slope."""
    gz, gx = np.gradient(heights.astype(np.float32), spacing)
    slope = np.degrees(np.arctan(np.hypot(gx, gz)))
    img = np.empty(heights.shape + (3,), dtype=np.uint8)
    img[:] = COLOURS["low"]
    top = float(np.percentile(heights[heights > 0], 97)) if (heights > 0).any() else 1.0
    img[heights > max(60.0, top * 0.8)] = COLOURS["high"]
    img[slope > 22] = COLOURS["slope"]
    img[slope > 38] = COLOURS["rock"]
    img[(heights >= 0) & (heights < 2.5) & (slope < 12)] = COLOURS["beach"]
    img[heights < 0] = COLOURS["sea floor"]
    return img[::-1].copy()


def _sea(heights: np.ndarray, spacing: float, per: int = 8) -> tuple[np.ndarray, np.ndarray]:
    rows, cols = heights.shape
    nz, nx = -(-(rows - 1) // per), -(-(cols - 1) // per)
    padded = np.full((nz * per + 1, nx * per + 1), np.inf, dtype=np.float32)
    padded[:rows, :cols] = heights
    low = np.minimum.reduce([
        padded[:-1, :-1].reshape(nz, per, nx, per).min(axis=(1, 3)),
        padded[per::per, :-1].reshape(nz, nx, per).min(axis=2),
        padded[:-1, per::per].reshape(nz, per, nx).min(axis=1),
    ])
    sea = low < 0
    if not sea.any():
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64)
    east = np.minimum(np.arange(nx + 1) * per, cols - 1) * spacing
    north = np.minimum(np.arange(nz + 1) * per, rows - 1) * spacing
    grid = _to_level(np.broadcast_to(east[None, :], (nz + 1, nx + 1)), np.zeros((nz + 1, nx + 1)),
                     np.broadcast_to(north[:, None], (nz + 1, nx + 1)))
    index = np.arange((nz + 1) * (nx + 1)).reshape(nz + 1, nx + 1)
    a, b, c, d = index[:-1, :-1][sea], index[:-1, 1:][sea], index[1:, :-1][sea], index[1:, 1:][sea]
    faces = np.concatenate([np.stack([a, b, c], 1), np.stack([b, d, c], 1)])
    used = np.unique(faces)
    remap = np.full(index.size, -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return grid.reshape(-1, 3)[used], remap[faces]


def road_ribbons(game: Game, world: World, heights: np.ndarray, spacing: float, size: float, report: Report):
    path = roads_path(world)
    if not path or path not in game.fs:
        report.notes.append("no road network (data\\roads\\roads.shp) beside the terrain")
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64)
    try:
        lines = read_polylines(game.fs.read(path))
        dbf = path[:-4] + ".dbf"
        rows = read_dbf(game.fs.read(dbf)) if dbf in game.fs else []
    except (ValueError, PboError, Encrypted) as exc:
        report.notes.append(f"road network not readable: {exc}")
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64)
    xs = [p[0] for rec in lines for part in rec for p in part]
    shift = 200000.0 if xs and min(xs) >= 200000.0 and max(xs) - 200000.0 <= size + 1 else 0.0
    quads = []
    for i, rec in enumerate(lines):
        row = rows[i] if i < len(rows) else {}
        try:
            width = float(row.get("WIDTH", "") or 0)
        except ValueError:
            width = 0.0
        if not width:
            kind = (row.get("ROADTYPE") or row.get("TYPE") or "").lower()
            width = 10.0 if "main" in kind else 4.5 if "track" in kind else 7.0
        for part in rec:
            if len(part) < 2:
                continue
            p = np.array(part, dtype=np.float64)
            p[:, 0] -= shift
            d = np.diff(p, axis=0)
            length = np.hypot(d[:, 0], d[:, 1])
            keep = length > 1e-3
            if not keep.any():
                continue
            a, d, length = p[:-1][keep], d[keep], length[keep]
            side = np.stack([-d[:, 1], d[:, 0]], 1) / length[:, None] * (width / 2)
            b = a + d
            corners = np.stack([a - side, a + side, b - side, b + side], 1)  # (n, 4, 2): east, north
            quads.append(corners)
            report.roads += len(a)
    if not quads:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64)
    c = np.concatenate(quads).reshape(-1, 2)
    up = height_at(heights, spacing, c[:, 0], c[:, 1]) + 0.15
    pos = _to_level(c[:, 0], up, c[:, 1])
    base = np.arange(len(c) // 4)[:, None] * 4
    # (left a, right a, left b, right b): wound so the face points up in the level's frame
    faces = np.concatenate([base + [0, 2, 1], base + [1, 2, 3]])
    return pos, faces


def structure_boxes(game: Game, world: World, wrp: Wrp, trees: bool, report: Report):
    models = wrp.models
    boxes: dict[int, tuple[np.ndarray, np.ndarray] | None] = {}
    folder_of = {}
    for i, path in enumerate(models):
        low = "\\" + path.lower().lstrip("\\")
        if not trees and any(v in low for v in VEGETATION):
            boxes[i] = None
            folder_of[i] = "vegetation (without --trees)"
            continue
        try:
            data = game.fs.read(path.lstrip("\\"))
        except KeyError:
            boxes[i] = None
            folder_of[i] = "model not in the install"
            continue
        except (PboError, Encrypted):
            boxes[i] = None
            folder_of[i] = "model not readable"
            continue
        b = p3d.bounds(data)
        boxes[i] = b
        if b is None:
            folder_of[i] = "model size not readable"
    objs = wrp.objects
    report.objects = len(objs)
    keep = np.array([boxes.get(int(m)) is not None for m in objs["model"]], dtype=bool)
    for m in objs["model"][~keep]:
        report.skip(folder_of.get(int(m), "model not readable"))
    objs = objs[keep]
    if not len(objs):
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64)
    lo = np.stack([boxes[int(m)][0] for m in objs["model"]])
    hi = np.stack([boxes[int(m)][1] for m in objs["model"]])
    bits = np.array([[(i >> k) & 1 for k in range(3)] for i in range(8)], dtype=np.float32)  # (8, 3)
    local = lo[:, None, :] + bits[None] * (hi - lo)[:, None, :]  # (n, 8, 3) model frame
    m = objs["m"].reshape(-1, 4, 3)  # aside, up, dir, position (Arma frame: x east, y up, z north)
    world_pts = np.einsum("nkc,ncd->nkd", local, m[:, :3, :]) + m[:, None, 3, :]
    pos = _to_level(world_pts[..., 0], world_pts[..., 1], world_pts[..., 2]).reshape(-1, 3)
    faces = (_BOX[None] + 8 * np.arange(len(objs))[:, None, None]).reshape(-1, 3)
    report.structures = len(objs)
    return pos, faces


def export(game: Game, world: World, out_dir: Path, *, max_grid: int = 4097, trees: bool = False,
           structures: bool = True, roads: bool = True, log=print) -> tuple[Report, list[Path], Wrp]:
    from heat3d_gamefiles.glb import Part, write_glb

    started = time.time()
    report = Report(world.id)
    log(f"{world.id}: reading {world.wrp}")
    wrp = read_wrp(game.fs.read(world.wrp))
    report.notes += wrp.notes
    report.size, spacing = wrp.size, wrp.spacing
    step = max(1, -(-(wrp.map_size[0]) // (max_grid - 1)))
    heights = wrp.heights
    h = heights[::step, ::step]
    report.spacing = spacing * step
    rows, cols = h.shape
    east = np.arange(cols) * spacing * step
    north = np.arange(rows) * spacing * step
    grid = _to_level(np.broadcast_to(east[None, :], h.shape), h, np.broadcast_to(north[:, None], h.shape)).reshape(-1, 3)
    index = np.arange(rows * cols).reshape(rows, cols)
    a, b, c, d = index[:-1, :-1], index[:-1, 1:], index[1:, :-1], index[1:, 1:]
    ground = np.concatenate([np.stack([a, b, c], -1).reshape(-1, 3), np.stack([b, d, c], -1).reshape(-1, 3)])
    report.ground = len(ground)
    width = max(east[-1], 1e-6)
    depth = max(north[-1], 1e-6)
    uv = np.stack([np.broadcast_to(east[None, :], h.shape).ravel() / width,
                   1 - np.broadcast_to(north[:, None], h.shape).ravel() / depth], 1).astype(np.float32)
    positions, uvs = [grid], [uv]
    parts = [Part(f"ground:{world.id}", ground, colour=(0.46, 0.52, 0.32), texture=ground_colours(h, spacing * step))]
    offset = len(grid)

    def add(name, pos, faces, colour):
        nonlocal offset
        if not len(faces):
            return 0
        positions.append(pos)
        uvs.append(np.zeros((len(pos), 2), np.float32))
        parts.append(Part(f"{name}:{world.id}", faces + offset, colour=colour))
        offset += len(pos)
        return len(faces)

    sea_pos, sea_faces = _sea(heights, spacing, per=8 * step)
    report.water = add("water", sea_pos, sea_faces, (0.22, 0.40, 0.55))
    if roads:
        add("road", *road_ribbons(game, world, heights, spacing, wrp.size, report), (0.30, 0.30, 0.30))
    if structures:
        log(f"{world.id}: sizing {len(wrp.models):,} models for {len(wrp.objects):,} placed objects")
        add("structure", *structure_boxes(game, world, wrp, trees, report), (0.75, 0.74, 0.70))
    out_dir.mkdir(parents=True, exist_ok=True)
    glb = out_dir / f"{world.id}.glb"
    write_glb(glb, np.concatenate(positions), parts=parts, uvs=np.concatenate(uvs),
              extras={"game": "arma3", "world": world.id, "frame": "x east, y up, z south (metres)"})
    info = out_dir / f"{world.id}.json"
    info.write_text(json.dumps({
        "format": "3dheat-level-info", "version": 1, "game": "arma3", "level": world.id, "name": world.name or world.id,
        "worldName": world.id, "dlc": world.dlc_name, "size": report.size, "spacing": report.spacing,
        "frame": {"x": "east", "y": "up", "z": "south", "origin": "south-west corner", "units": "m"},
        "source": f"heat3d-arma3-maps {__version__}",
    }, indent=1), encoding="utf-8")
    log(f"{world.id}: written in {time.time() - started:.0f} s")
    return report, [glb, info], wrp

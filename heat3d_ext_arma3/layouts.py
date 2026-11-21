"""
A world's places as `<World>.layouts.json`: what its `CfgWorlds` class says.

* Towns and other named places: `class Names`, each with a name, a position (x east, y north), a type and radii.
  Capitals, cities and villages are `town`; named local places that are military (bases, outposts, camps,
  checkpoints, by name) are `base` kind `military`; hills, mounts, bays and the rest are `place` with their kind.
* Airfields: the world's main runway (`ilsPosition`, `ilsDirection`) and every `SecondaryAirports` class: an
  `airfield` at the threshold with its heading, and the runway as an `objective` kind `runway`.

One layout, `world`: an Arma terrain has no game mode of its own; missions bring their objectives. Positions are
the level's frame (x east, y up = the ground's height there, z south), the same as `<World>.glb`.
"""

from __future__ import annotations

import math
import re

import numpy as np

from . import __version__
from .config import CfgClass
from .game import Game, World

TOWN = {"namecitycapital": "capital", "namecity": "city", "namevillage": "village"}
MILITARY = re.compile(r"milit|\bbase\b|outpost|\bcamp\b|barrack|checkpoint|firebase|\bfob\b|airbase|army|garrison", re.I)


def _xy(v) -> tuple[float, float] | None:
    if isinstance(v, list) and len(v) >= 2 and all(isinstance(x, (int, float)) for x in v[:2]):
        return float(v[0]), float(v[1])
    return None


def _point(east: float, north: float, heights: np.ndarray | None, spacing: float) -> list[float]:
    up = 0.0
    if heights is not None:
        from .level import height_at

        up = float(height_at(heights, spacing, np.array([east]), np.array([north]))[0])
    return [round(east, 2), round(up, 2), round(-north, 2)]


def _heading(direction) -> float | None:
    """Compass heading (degrees) of an `ilsDirection` (x east, y up, z north), the way an aircraft lands."""
    if not (isinstance(direction, list) and len(direction) >= 3):
        return None
    dx, dz = float(direction[0]), float(direction[2])
    if dx == 0 and dz == 0:
        return None
    # ilsDirection points from the threshold out along the approach: landing runs the other way
    return round((math.degrees(math.atan2(-dx, -dz)) + 360) % 360, 1)


def _airport(game: Game, cls: CfgClass, name: str, heights, spacing) -> list[dict]:
    pos = _xy(cls.get("ilsPosition"))
    if pos is None:
        return []
    heading = _heading(cls.get("ilsDirection"))
    p = _point(pos[0], pos[1], heights, spacing)
    out = [{"type": "airfield", "team": None, "name": name, "position": p}]
    if heading is not None:
        out[0]["heading"] = heading
        # a runway line 1.5 km long from the threshold, the way the aircraft lands
        rad = math.radians(heading)
        end = (pos[0] + 1500 * math.sin(rad), pos[1] + 1500 * math.cos(rad))
        out.append({"type": "objective", "kind": "runway", "team": None, "name": f"{name} runway",
                    "position": p, "points": [p, _point(end[0], end[1], heights, spacing)], "heading": heading})
    return out


def build(game: Game, world: World, heights: np.ndarray | None = None, spacing: float = 1.0) -> dict:
    objectives: list[dict] = []
    counts: dict[str, int] = {}
    cfg = world.config
    if cfg is not None:
        names = cfg.child("Names")
        for place in (names.children() if names else []):
            pos = _xy(place.get("position"))
            if pos is None:
                continue
            kind = str(place.get("type", "")).lower()
            label = game.text(place.get("name", "")) or place.name
            radius = max(float(place.get("radiusA", 0) or 0), float(place.get("radiusB", 0) or 0))
            entry = {"name": label, "position": _point(pos[0], pos[1], heights, spacing), "kind": kind}
            if radius > 0:
                entry["radius"] = round(radius, 1)
            if kind in TOWN:
                entry.update(type="town", kind=TOWN[kind])
            elif kind in ("namelocal", "strongpointarea", "strategic") and MILITARY.search(label + " " + place.name):
                entry.update(type="base", kind="military", team=None)
            else:
                entry["type"] = "place"
            counts[entry["type"]] = counts.get(entry["type"], 0) + 1
            objectives.append(entry)
        main = _airport(game, cfg, world.name or world.id, heights, spacing)
        objectives += main
        sec = cfg.child("SecondaryAirports")
        for i, ap in enumerate(sec.children() if sec else []):
            objectives += _airport(game, ap, f"{world.name or world.id} airfield {i + 2}", heights, spacing)
        counts["airfield"] = sum(1 for o in objectives if o["type"] == "airfield")
    return {
        "format": "3dheat-layouts", "version": 1, "game": "arma3", "level": world.id,
        "source": f"heat3d-arma3-maps {__version__}", "units": "m", "up": "y",
        "layouts": [{"id": "world", "name": world.name or world.id, "mode": "world", "aliases": [world.id],
                     "objectives": objectives, "zones": [], "spawns": []}],
        "counts": counts,
    }

"""
Arma 3's vehicles as a local model pack for the 3DHeat viewer (`model-pack.json`, one `.glb` per model and look,
`hit-parts.json`), read from the install's own binarised models (`odol.py`) and configuration.

Per vehicle class (`CfgVehicles`, public, a tank, car, helicopter, plane, boat or static weapon):

* the shape is the class's `model`; its first three visual levels of detail of different detail (finest,
  middle, coarsest) become `lod0`, `lod1` and `lod2`;
* what the class shows by default is what is drawn: parts its `hide` animations hide at their sources' starting
  values (`AnimationSources` `initPhase`, else 0) are left out, as are muzzle flashes, proxies (crew, flags, lights)
  and hidden selections the class gives no texture;
* parts by the skeleton bone each vertex follows: `turret`/`gun` from the turret's `body` and `gun` animations
  (yaw and pitch joints about their axes from model.cfg as the binariser stored them; further turrets with
  weapons of their own `turret.N`/`gun.N`, a turret on a turret moves with it), `rotor.N` (a helicopter's rotors,
  spinning about their axes), `gear` (wheels, tracks and suspension; an aircraft's landing gear folds away when
  its animations follow the gear), `aerial` (antennas), the rest `hull`;
* plain colours: each face takes its texture's average colour (the `AVGC` tag every `.paa` carries, or the
  colour of a procedural texture), the class's own textures for its hidden selections;
* true size, in the figure frame (x forward, y up, z right, metres) about the vehicle's own origin, the point the
  game places (its land contact for ground vehicles);
* the damage model under `dm`: per hit point (`HitPoints` of the class and its turrets) the Fire Geometry
  component its `armorComponent` names, else spheres of its `radius` about its points in the Hit-points level,
  with its hit points, armour factor, pass-through and the model part it moves with; and the Fire Geometry's
  plates by penetration material (thickness where the material says it, `armour_plate_40mm`).

Classes that share a model and look share one file (`aliases`). `name` is the real vehicle a class stands for
(catalogue `key`); `names` holds its display name and the names recordings give it, so other games' units of the
same vehicle can borrow the model and Arma 3 recordings find it.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .odol import HIDE, ROTATION, ROTATION_X, ROTATION_Y, ROTATION_Z, Model, OdolError, read_model

GAME = "arma3"
FORMAT_VERSION = 1

VEHICLE_SIMS = {"tankx", "carx", "helicopterrtd", "helicopterx", "airplanex", "shipx", "submarinex", "motorcycle"}
STATIC_KINDS = {"static", "static-mg", "static-at", "static-aa", "mortar", "artillery"}

#: Catalogue class -> (pack domain, type words the viewer places a figure by).
TYPE_WORDS = {
    "mbt": ("ground", "tank"), "light-tank": ("ground", "light tank"), "tank-destroyer": ("ground", "tank destroyer"),
    "ifv": ("ground", "ifv"), "apc": ("ground", "apc"), "arv": ("ground", "armoured recovery vehicle"),
    "spaa": ("ground", "spaa"), "spg": ("ground", "self-propelled artillery"), "mlrs": ("ground", "mlrs rocket"),
    "car": ("ground", "car"), "truck": ("ground", "truck"), "ugv": ("ground", "unmanned ground vehicle"),
    "static": ("ground", "static"), "static-mg": ("ground", "static mg"), "static-at": ("ground", "static at"),
    "static-aa": ("ground", "static anti-air"), "mortar": ("ground", "static mortar"),
    "artillery": ("ground", "static artillery"),
    "heli-transport": ("heli", "helicopter transport"), "heli-attack": ("heli", "helicopter attack"),
    "plane": ("air", "plane transport"), "fighter": ("air", "fighter"), "attack": ("air", "attack"),
    "uav": ("air", "uav"), "tiltrotor": ("air", "tiltrotor transport"),
    "boat": ("sea", "boat"), "submarine": ("sea", "submarine"), "destroyer": ("sea", "destroyer"),
    "carrier": ("sea", "carrier"),
}

#: Arma 3's fictional vehicles by a word of their display name, as recordings (OCAP) name the real vehicle - the
#: names the viewer's recording reader gives (`armaUnits.ts`), so a recording's unit finds this pack's model.
RECORDED_AS = [
    (r"\bslammer\b", "Merkava Mk.4", ""), (r"\bvarsuk\b|\bangara\b", "T-14", ""), (r"\bkuma\b", "Leopard 2A6", ""),
    (r"\brhino\b", "Centauro II", ""), (r"\bnyx\b", "Wiesel 2", ""), (r"\bmarshall\b", "Patria AMV", ""),
    (r"\bgorgon\b", "Pandur II", ""), (r"\bmora\b", "ASCOD", ""), (r"\bmarid\b", "BTR-90", ""),
    (r"\btigris\b", "2S6 Tunguska", ""), (r"\bscorcher\b", "M109", ""), (r"\bsochor\b", "2S19 Msta", ""),
    (r"\bsandstorm\b", "M270 MLRS", ""), (r"\bhunter\b", "M-ATV", "ground"), (r"\bstrider\b", "Fennek", ""),
    (r"\bhemtt\b", "HEMTT", ""), (r"\bghost ?hawk\b", "UH-60M", ""), (r"\bblackfoot\b", "RAH-66 Comanche", ""),
    (r"\bkajman\b", "Mi-28N", ""), (r"\borca\b", "Ka-60 Kasatka", ""), (r"\bhummingbird\b", "MH-6 Little Bird", ""),
    (r"\bpawnee\b", "AH-6 Little Bird", ""), (r"\bhellcat\b", "AW159 Wildcat", ""), (r"\bmohawk\b", "AW101 Merlin", ""),
    (r"\bhuron\b", "CH-47F", ""), (r"\bblackfish\b", "V-22 Osprey", ""), (r"\bwipeout\b", "A-10C", ""),
    (r"\bneophron\b", "Yak-130", ""), (r"\bbuzzard\b", "L-159", ""), (r"\bblack wasp\b", "F-35A", ""),
    (r"\bshikra\b", "Su-57", ""), (r"\bgryphon\b", "JAS 39 Gripen", ""), (r"\bgreyhawk\b", "MQ-1 Predator", ""),
    (r"\bsentinel\b", "X-47B", ""),
]

GEAR_GROUND = re.compile(r"^(kol[lp]?\d|kolo|kolol|kolop|podkol|wheel|track|pas[lp]?$|pas_|idler|sprocket|hub|"
                         r"damper|suspension|stopa|koleso|kola)")
GEAR_AIR = re.compile(r"^(gear|wheel|podvozek|kolo|kola|tlumic|skid)")
ROTOR = re.compile(r"rotor|vrtule|blade|propel")
AERIAL = re.compile(r"^(antenna|anten|aerial)")
TRACK_SELECTION = re.compile(r"^(pas|track|wheel|kolo|kola)")
FLASH = re.compile(r"^(zasleh|muzzle_?flash|muzzleflash)")

#: The kind of region a hit point is, by its class name.
HIT_GROUP = [
    (r"engine", "engine"), (r"fuel", "fuel"), (r"track", "track"), (r"wheel", "wheel"), (r"(com)?turret", "turret"),
    (r"gun", "gun"), (r"rotor", "rotor"), (r"glass", "glass"), (r"avionics", "avionics"), (r"(aileron|elevator|rudder)", "control"),
    (r"(era|slat)", "addon"), (r"hull|body", "hull"),
]


@dataclass
class Unit:
    id: str
    shape: str
    name: str
    kind: str
    domain: str
    key: str
    side: str
    cfg: object


@dataclass
class Built:
    lods: list[dict]  # per level: part -> (vertices (n,3), triangles (m,3), colours (n,4) u8)
    joints: dict  # part -> extras
    dm: list  # (name, vertices, triangles, extras)
    box: tuple
    notes: list[str] = field(default_factory=list)
    source_lods: list[int] = field(default_factory=list)


def _float(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# ---- the vehicles ------------------------------------------------------------------------------------------------
def units(game) -> list[Unit]:
    """The catalogue's vehicles a model pack draws, with their configuration class."""
    from .catalog import vehicles

    cv = game.config.child("CfgVehicles")
    out = []
    for row in vehicles(game):
        if row["id"].lower().startswith("land_") or not row.get("shape"):
            continue
        cls = cv.child(row["id"]) if cv else None
        if cls is None:
            continue
        sim = str(cls.get("simulation", "") or "").lower()
        # (the core config that sets some base classes' simulation is not among the archives read: no simulation
        # with a vehicle kind is a vehicle)
        if sim not in VEHICLE_SIMS and row["class"] not in STATIC_KINDS and (sim or row["class"] not in TYPE_WORDS):
            continue
        # floating props (cones, barriers, lights) are ships to the game: nobody drives them and they carry no weapon
        if cls.get("hasDriver", 1) == 0 and not any(t.weapons for t in turrets(cls)):
            continue
        out.append(Unit(row["id"], row["shape"], row["name"], row["class"], row["domain"], row["key"], row["side"], cls))
    return out


def shape_path(shape: str) -> str:
    p = shape.strip("\\").replace("/", "\\").lower()
    return p if p.endswith(".p3d") else p + ".p3d"


def recorded_names(display: str, domain: str) -> list[str]:
    out = []
    for pat, real, only in RECORDED_AS:
        if re.search(pat, display, re.I) and (not only or only == domain):
            out.append(real)
    return out


# ---- configuration: what a class shows, its turrets and hit points ----------------------------------------------
def source_values(cls) -> dict[str, float]:
    src = cls.child("AnimationSources")
    out: dict[str, float] = {}
    for c in (src.children() if src else []):
        out[c.name.lower()] = _float(c.get("initPhase", 0.0))
    return out


def hidden_bones(model: Model, lod: int, values: dict[str, float]) -> set[int]:
    """The skeleton bones the model's hide animations hide at the sources' starting values, with every bone under them."""
    out: set[int] = set()
    row = model.anim_bones[lod] if lod < len(model.anim_bones) else []
    for i, a in enumerate(model.anims):
        if a.type != HIDE or i >= len(row) or row[i][0] < 0:
            continue
        v = values.get(a.source.lower(), 0.0)
        span = a.max_value - a.min_value
        phase = min(1.0, max(0.0, (v - a.min_value) / span)) if span else (1.0 if v >= a.max_value else 0.0)
        hide, unhide = a.params[0], a.params[1]
        if phase >= hide and not (unhide >= 0 and unhide > hide and phase >= unhide):
            out |= model.descendants(row[i][0])
    return out


@dataclass
class Turret:
    path: list[str]
    body: str
    gun: str
    weapons: list[str]
    primary: bool
    hitpoints: list
    yaw: tuple | None
    pitch: tuple | None
    src_body: str = ""
    src_gun: str = ""


def turrets(cls) -> list[Turret]:
    out: list[Turret] = []

    def walk(c, path):
        ts = c.child("Turrets")
        for t in (ts.children() if ts else []):
            body = str(t.get("body", "") or ("mainTurret" if t.name.lower() == "mainturret" else ""))
            gun = str(t.get("gun", "") or ("mainGun" if t.name.lower() == "mainturret" else ""))
            weapons = [w for w in (t.get("weapons") or []) if isinstance(w, str)]
            hp = t.child("HitPoints")
            yaw = (_float(t.get("minTurn", -360)), _float(t.get("maxTurn", 360)))
            pitch = (_float(t.get("minElev", -4)), _float(t.get("maxElev", 20)))
            out.append(Turret(path + [t.name], body, gun, weapons, t.get("primaryGunner") == 1,
                              hp.children() if hp else [], yaw, pitch,
                              str(t.get("animationSourceBody", "") or body), str(t.get("animationSourceGun", "") or gun)))
            walk(t, path + [t.name])

    walk(cls, [])
    return out


def hitpoints(cls) -> list:
    hp = cls.child("HitPoints")
    rows = list(hp.children()) if hp else []
    for t in turrets(cls):
        rows += t.hitpoints
    seen, out = set(), []
    for h in rows:
        if h.name.lower() not in seen:
            seen.add(h.name.lower())
            out.append(h)
    return out


# ---- colours -----------------------------------------------------------------------------------------------------
_PROC = re.compile(r"#\(\w+,\d+,\d+,\d+\)color\(([^)]*)\)", re.I)


class Colours:
    """Average colours of textures, read from their `AVGC` tag (never decoding the image), cached."""

    def __init__(self, fs) -> None:
        self.fs = fs
        self.cache: dict[str, tuple | None] = {}

    def __call__(self, tex: str) -> tuple | None:
        t = (tex or "").strip().lower()
        if not t:
            return None
        if t in self.cache:
            return self.cache[t]
        col = None
        m = _PROC.match(t)
        if m:
            parts = [p.strip() for p in m.group(1).split(",")]
            try:
                r, g, b = (float(parts[i]) for i in range(3))
                a = float(parts[3]) if len(parts) > 3 else 1.0
                col = (r, g, b, a)
            except (ValueError, IndexError):
                col = None
        else:
            head = self._head(t.lstrip("\\"))
            i = head.find(b"GGATCGVA") if head else -1
            if i >= 0 and i + 16 <= len(head):
                b_, g_, r_, a_ = head[i + 12:i + 16]
                col = (r_ / 255, g_ / 255, b_ / 255, a_ / 255)
        self.cache[t] = col
        return col

    def _head(self, path: str) -> bytes:
        found = self.fs.index.get(path)
        if found is None:
            return b""
        pbo, key = found
        e = pbo.entries[key]
        if e.packed or e.encrypted:
            try:
                return pbo.read(key)[:256]
            except Exception:  # noqa: BLE001 - a colour is a nicety
                return b""
        with pbo.path.open("rb") as fh:
            fh.seek(e.offset)
            return fh.read(min(256, e.size))


# ---- building one model ------------------------------------------------------------------------------------------
def to_figure(v: np.ndarray, centre: np.ndarray) -> np.ndarray:
    """Model frame (binarised: x left, y up, z back, about the bounding centre) -> figure frame (x forward, y up, z
    right) about the vehicle's own origin (where the binariser's centre came from)."""
    p = v + centre
    return np.stack([-p[:, 2], p[:, 1], -p[:, 0]], axis=1).astype(np.float32)


def _dir(d) -> np.ndarray:
    d = np.asarray(d, np.float64)
    return np.array([-d[2], d[1], -d[0]])


def _choose_lods(model: Model) -> list[int]:
    vis = [i for i in model.visual() if model.resolutions[i] < 900]
    if not vis:
        raise OdolError("no visual level of detail")
    if len(vis) >= 3:
        return [vis[0], vis[min(2, len(vis) - 2)], vis[-1]]
    return vis + [vis[-1]] * (3 - len(vis))


def _pick_anim(model: Model, row, counts: dict[int, int], name: str, source: str, types: tuple) -> int:
    """The animation a turret's `body` or `gun` names - or, when that one moves nothing this level draws (a model
    with separate visible parts), the one of `<name>_vis` or of the same source that moves the most vertices."""
    def moved(i: int) -> int:
        if i < 0 or i >= len(row) or row[i][0] < 0:
            return -1
        return sum(counts.get(b, 0) for b in model.descendants(row[i][0]))

    first = model.anim(name) if name else -1
    vis = model.anim(f"{name}_vis") if name else -1
    if moved(vis) > 0:
        return vis
    if moved(first) > 0 or not counts:
        return first
    best, most = first, moved(first)
    for i, a in enumerate(model.anims):
        if a.type not in types:
            continue
        if a.name.lower() in (f"{name}_vis".lower(), f"{name}vis".lower()) or (source and a.source.lower() == source.lower()):
            m = moved(i)
            if m > most:
                best, most = i, m
    return best


def bone_parts(model: Model, lod: int, unit: Unit, ts: list[Turret], vb: np.ndarray | None = None) -> tuple[dict[int, str], dict, list[str]]:
    """Per skeleton bone the part it moves with, the joints' extras (in the model frame), and notes."""
    row = model.anim_bones[lod] if lod < len(model.anim_bones) else []
    counts: dict[int, int] = {}
    if vb is not None and len(vb):
        b_, c_ = np.unique(vb[vb >= 0], return_counts=True)
        counts = dict(zip(b_.tolist(), c_.tolist()))
    yaw_types = (ROTATION, ROTATION_Y)
    pitch_types = (ROTATION, ROTATION_X)
    notes: list[str] = []
    explicit: dict[int, str] = {}
    joints: dict[str, dict] = {}
    air = unit.domain in ("air", "heli") or TYPE_WORDS.get(unit.kind, ("", ""))[0] in ("air", "heli")
    # turrets: the primary first, then the others with weapons of their own
    order = sorted(ts, key=lambda t: (not t.primary, len(t.path)))
    n = 1
    main_bone = -1
    for t in order:
        bi = _pick_anim(model, row, counts, t.body, t.src_body, yaw_types)
        if bi < 0 or bi >= len(row) or row[bi][0] < 0:
            continue
        bone = row[bi][0]
        if bone in explicit:
            continue
        real = [w for w in t.weapons if not re.match(r"(?i)^(fakeweapon|smokelauncher|cmflare|laserdesignator)", w)]
        nested = main_bone >= 0 and bone in model.descendants(main_bone)
        if n > 1 and (nested or not real):
            continue  # a commander's cupola on the main turret moves with it; a turret without weapons stays hull
        suffix = "" if n == 1 else f".{n}"
        tname, gname = "turret" + suffix, "gun" + suffix
        axis = row[bi][1]
        if axis is not None:
            joints[tname] = {"joint": "yaw", "pivot": axis[0], "limits": t.yaw, "turret": "/".join(t.path)}
        explicit[bone] = tname
        if n == 1:
            main_bone = bone
        gi = _pick_anim(model, row, counts, t.gun, t.src_gun, pitch_types)
        if 0 <= gi < len(row) and row[gi][0] >= 0 and row[gi][0] != bone:
            explicit[row[gi][0]] = gname
            if row[gi][1] is not None:
                joints[gname] = {"joint": "pitch", "pivot": row[gi][1][0], "parent": tname, "limits": t.pitch}
        n += 1
    # rotors: a helicopter's (or a propeller's) rotors, each the top-most bone its spinning animation turns (a full
    # turn from the rotor sources), about the axis the binariser stored for it
    rotors = 0
    if air and unit.kind not in ("fighter", "attack"):  # a jet's turning parts are engine fans, drawn as they stand
        spin: dict[int, tuple] = {}
        for i, a in enumerate(model.anims):
            if i >= len(row) or row[i][0] < 0 or row[i][1] is None or a.type not in (ROTATION, ROTATION_X, ROTATION_Y, ROTATION_Z):
                continue
            if abs(a.params[1] - a.params[0]) < 3.0 or a.source.lower().startswith(("damage", "hit")):
                continue
            b = row[i][0]
            if not ROTOR.search(model.skeleton[b][0]) and not a.source.lower().startswith(("rotor", "propel")):
                continue
            pos, d = row[i][1]
            d = {ROTATION_X: (1, 0, 0), ROTATION_Y: (0, 1, 0), ROTATION_Z: (0, 0, 1)}.get(a.type, d)
            spin.setdefault(b, (pos, d))
        for b in sorted(spin):
            k, nested = model.skeleton[b][1], False
            while k:
                kb = model.bone_index(k)
                if kb in spin:
                    nested = True
                    break
                k = model.skeleton[kb][1] if kb >= 0 else ""
            if nested or b in explicit:
                continue
            rotors += 1
            pname = f"rotor.{rotors}"
            explicit[b] = pname
            joints[pname] = {"spin": "rotor", "pivot": spin[b][0], "axis": spin[b][1]}
    gear_src = set()
    for i, a in enumerate(model.anims):
        if i < len(row) and row[i][0] >= 0 and a.source.lower() in ("gear", "gear_1", "geardoors"):
            gear_src |= model.descendants(row[i][0])
    parts: dict[int, str] = {}
    gear_re = GEAR_AIR if air else GEAR_GROUND
    for b in range(len(model.skeleton)):
        k, part = b, "hull"
        seen = 0
        while k >= 0 and seen < 64:
            name = model.skeleton[k][0]
            if k in explicit:
                part = explicit[k]
                break
            if AERIAL.match(name):
                part = "aerial"
                break
            if gear_re.match(name) and not name.startswith("drivewheel") and (not air or k in gear_src or b in gear_src):
                part = "gear"
                break
            parent = model.skeleton[k][1]
            k = model.bone_index(parent) if parent else -1
            seen += 1
        parts[b] = part
    if air and any(p == "gear" for p in parts.values()):
        joints["gear"] = {"retract": True}
    return parts, joints, notes


def _weld(v: np.ndarray, tri: np.ndarray, col: np.ndarray):
    if not len(tri):
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64), np.zeros((0, 4), np.uint8)
    used = np.unique(tri.reshape(-1))
    key = np.concatenate([np.round(v[used] * 1e4).astype(np.int64), col[used].astype(np.int64)], axis=1)
    _u, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    remap = np.full(len(v), -1, np.int64)
    remap[used] = inv.reshape(-1)
    t = remap[tri]
    t = t[(t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])]
    return v[used[first]], t, col[used[first]]


def build(model: Model, unit: Unit, colours: Colours, hide_tex: dict[str, str]) -> Built:
    """One class's look of a model: its parts per level, joints and damage model, in the figure frame."""
    ts = turrets(unit.cfg)
    values = source_values(unit.cfg)
    chosen = _choose_lods(model)
    lods = []
    joints: dict[str, dict] = {}
    notes: list[str] = []
    for slot, li in enumerate(chosen):
        L = model.lod(li)
        parts_of_bone, j, n = bone_parts(model, li, unit, ts, L.bones)
        if slot == 0:
            joints, notes = j, n
        hidden = hidden_bones(model, li, values)
        vb = L.bones
        hide_v = np.isin(vb, list(hidden)) if hidden else np.zeros(len(vb), bool)
        # faces: drop hidden ones, muzzle flashes and hidden selections without a texture
        fsec = L.face_section()
        sec_tex = [L.textures[s.texture] if 0 <= s.texture < len(L.textures) else "" for s in L.sections]
        # untextured sections without a material: the proxies' marker triangles (crew, flags, lights, flashes)
        drop_sec = np.array([s.texture < 0 and s.material < 0 for s in L.sections], bool)
        gear_sec = np.zeros(len(L.sections), bool)
        for sname, sel in L.selections.items():
            if not len(sel.sections):
                continue
            ok = sel.sections[sel.sections < len(L.sections)]
            if FLASH.match(sname) or sname.startswith("proxy:"):
                drop_sec[ok] = True
            if TRACK_SELECTION.match(sname) and not sname.endswith("_hit"):
                gear_sec[ok] = True
            if sname in hide_tex:
                tex = hide_tex[sname]
                if tex == "":
                    drop_sec[ok] = True
                else:
                    for s in ok:
                        sec_tex[s] = tex
        tri_list, tri_face = [], []
        for k, f in enumerate(L.faces):
            tri_list.append(f[:3])
            tri_face.append(k)
            if len(f) == 4:
                tri_list.append((f[0], f[2], f[3]))
                tri_face.append(k)
        tri = np.array(tri_list, np.int64).reshape(-1, 3)
        tface = np.array(tri_face, np.int64)
        keep = ~hide_v[tri].any(axis=1)
        sec_of_tri = fsec[tface] if len(tface) else np.zeros(0, np.int64)
        keep &= ~((sec_of_tri >= 0) & drop_sec[np.maximum(sec_of_tri, 0)])
        # colour per section
        sec_col = []
        for s, tex in enumerate(sec_tex):
            c = colours(tex)
            if c is None:
                m = L.sections[s].material
                c = tuple(L.materials[m].diffuse) if 0 <= m < len(L.materials) else (0.5, 0.5, 0.5, 1.0)
                if c[:3] == (1.0, 1.0, 1.0):
                    c = (0.45, 0.45, 0.42, 1.0)
            if c[3] < 0.05:
                drop_sec[s] = True
            sec_col.append(c)
        keep &= ~((sec_of_tri >= 0) & drop_sec[np.maximum(sec_of_tri, 0)])
        vcol = np.full((len(L.vertices), 4), 128, np.uint8)
        if len(sec_col):
            cols = (np.clip(np.array(sec_col), 0, 1) * 255).round().astype(np.uint8)
            tc = cols[np.maximum(sec_of_tri, 0)]
            tc[sec_of_tri < 0] = 128
            vcol[tri[keep].reshape(-1)] = np.repeat(tc[keep], 3, axis=0)
        # part per triangle: the part of its first vertex's bone
        bpart = np.array([parts_of_bone.get(int(b), "hull") if b >= 0 else "hull" for b in range(len(model.skeleton))] + ["hull"])
        vpart = bpart[np.where(vb >= 0, vb, len(model.skeleton))]
        tpart = vpart[tri[:, 0]] if len(tri) else np.zeros(0, str)
        if len(tri):
            on_gear_sec = (sec_of_tri >= 0) & gear_sec[np.maximum(sec_of_tri, 0)] & (tpart == "hull")
            tpart = np.where(on_gear_sec, "gear", tpart)
        fig = to_figure(L.vertices, model.centre)
        parts = {}
        for p in sorted(set(tpart[keep].tolist())):
            sel = keep & (tpart == p)
            v, t, c = _weld(fig, tri[sel], vcol)
            if len(t):
                parts[p] = (v, t, c)
        lods.append(parts)
    # winding: outward in the figure frame (positive volume), the binarised frame is the other hand
    vol = 0.0
    for v, t, _c in lods[0].values():
        a, b, c = v[t[:, 0]].astype(np.float64), v[t[:, 1]].astype(np.float64), v[t[:, 2]].astype(np.float64)
        vol += float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum())
    if vol < 0:
        for parts in lods:
            for p, (v, t, c) in list(parts.items()):
                parts[p] = (v, t[:, ::-1].copy(), c)
    # too many vertices for a 16-bit figure part: take the next coarser level as the finest
    while any(len(v) > 65535 for v, _t, _c in lods[0].values()) and len(lods) > 1:
        notes.append(f"finest level over 65,535 vertices a part; level {chosen[1]} drawn instead")
        lods.pop(0)
        chosen.pop(0)
        lods.append(lods[-1])
        chosen.append(chosen[-1])
    # joints into the figure frame
    out_joints: dict[str, dict] = {}
    c0 = model.centre
    for p, j in joints.items():
        if p not in lods[0] and p != "gear":
            continue
        e = dict(j)
        if "pivot" in e:
            e["pivot"] = [round(float(x), 4) for x in to_figure(np.array([e["pivot"]], np.float32), c0)[0]]
        if "axis" in e:
            ax = _dir(e["axis"])
            ax /= max(1e-9, np.linalg.norm(ax))
            e["axis"] = [round(float(x), 4) for x in ax]
            v = lods[0][p][0].astype(np.float64)
            rel = v - np.array(e["pivot"])
            dist = np.linalg.norm(rel - np.outer(rel @ ax, ax), axis=1)
            e["diameter"] = round(float(2 * dist.max()), 3) if len(dist) else 0.0
        if p == "gear" and "gear" in lods[0]:
            v = lods[0]["gear"][0]
            e["pivot"] = [round(float((v[:, 0].min() + v[:, 0].max()) / 2), 4), round(float(v[:, 1].max()), 4),
                          round(float((v[:, 2].min() + v[:, 2].max()) / 2), 4)]
        elif p == "gear":
            continue
        out_joints[p] = e
    dm = damage_model(model, unit, ts)
    allv = np.concatenate([v for v, _t, _c in lods[0].values()]) if lods[0] else np.zeros((1, 3), np.float32)
    box = (allv.min(0), allv.max(0))
    return Built(lods, out_joints, dm, box, notes, chosen)


def _octahedra(points: np.ndarray, r: float) -> tuple[np.ndarray, np.ndarray]:
    base = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], np.float32) * r
    faces = np.array([[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4], [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]], np.int64)
    v = (points[:, None, :] + base[None]).reshape(-1, 3)
    f = (faces[None] + 6 * np.arange(len(points))[:, None, None]).reshape(-1, 3)
    return v.astype(np.float32), f


def _hit_group(name: str) -> str:
    n = name.lower()
    for pat, g in HIT_GROUP:
        if re.search(pat, n):
            return g
    return "other"


def damage_model(model: Model, unit: Unit, ts: list[Turret]) -> list:
    """`dm` parts: per hit point its zone, and the Fire Geometry's plates by penetration material."""
    fi, hi = model.find("fire geometry"), model.find("hit points")
    if fi is None:
        return []
    F = model.lod(fi)
    H = model.lod(hi) if hi is not None else None
    parts_of_bone, _j, _n = bone_parts(model, fi, unit, ts, F.bones)
    tri = F.triangles()
    if not len(tri):
        return []
    tface = np.concatenate([[k] if len(f) == 3 else [k, k] for k, f in enumerate(F.faces)]).astype(np.int64)
    fig = to_figure(F.vertices, model.centre)
    vb = F.bones
    vpart = np.array([parts_of_bone.get(int(b), "hull") if b >= 0 else "hull" for b in vb] or ["hull"])
    armor = max(model.armor, _float(unit.cfg.get("armor", 0.0)))
    used = np.zeros(len(F.faces), bool)
    for sname, sel in F.selections.items():  # the proxies' marker triangles (crew, cargo) are no plates
        if sname.startswith("proxy:") and len(sel.faces):
            used[sel.faces[sel.faces < len(F.faces)]] = True
    out = []

    def faces_of(sel) -> np.ndarray:
        if len(sel.faces):
            return sel.faces[sel.faces < len(F.faces)]
        if len(sel.sections):
            fs = [np.arange(F.sections[s].face_from, F.sections[s].face_to) for s in sel.sections if s < len(F.sections)]
            return np.concatenate(fs) if fs else np.zeros(0, np.int64)
        return np.zeros(0, np.int64)

    def part_of(verts: np.ndarray) -> str:
        if not len(verts):
            return "hull"
        vals, counts = np.unique(vpart[verts], return_counts=True)
        return str(vals[np.argmax(counts)])

    for h in hitpoints(unit.cfg):
        a = _float(h.get("armor", 1.0), 1.0)
        extras = {"group": _hit_group(h.name), "hp": round(a * armor if a > 0 else -a, 1), "armor": round(a, 3),
                  "radius": round(_float(h.get("radius", 0.0)), 3), "passThrough": round(_float(h.get("passThrough", 0.0)), 3),
                  "selection": str(h.get("name", ""))}
        comp = str(h.get("armorComponent", "") or "").lower()
        sel = F.selections.get(comp) if comp else None
        faces = faces_of(sel) if sel is not None else np.zeros(0, np.int64)
        if len(faces):
            used[faces] = True
            ft = np.isin(tface, faces)
            t = tri[ft]
            verts = np.unique(t)
            remap = np.full(len(fig), -1, np.int64)
            remap[verts] = np.arange(len(verts))
            extras.update(part=part_of(verts), component=comp)
            out.append((h.name, fig[verts], remap[t], extras))
            continue
        pts_sel = H.selections.get(str(h.get("name", "")).lower()) if H is not None else None
        if pts_sel is not None and len(pts_sel.vertices):
            pv = pts_sel.vertices[pts_sel.vertices < len(H.vertices)][:400]
            pts = to_figure(H.vertices[pv], model.centre)
            r = max(0.05, extras["radius"])
            v, f = _octahedra(pts, r)
            hp_parts = parts_of_bone
            vals = [hp_parts.get(int(b), "hull") if b >= 0 else "hull" for b in H.bones[pv]]
            part = max(set(vals), key=vals.count) if vals else "hull"
            extras.update(part=part, zone="spheres")
            out.append((h.name, v, f, extras))
    # plates: the rest of the Fire Geometry by penetration material and part
    fsec = F.face_section()
    mat_of_face = np.array([F.sections[s].material if s >= 0 else -1 for s in fsec], np.int64)
    rest = ~used[tface]
    tmat = mat_of_face[tface]
    tpart = vpart[tri[:, 0]]
    for m in sorted(set(tmat[rest].tolist())):
        for p in sorted(set(tpart[rest & (tmat == m)].tolist())):
            sel = rest & (tmat == m) & (tpart == p)
            t = tri[sel]
            verts = np.unique(t)
            remap = np.full(len(fig), -1, np.int64)
            remap[verts] = np.arange(len(verts))
            mname = F.materials[m].name if 0 <= m < len(F.materials) else ""
            cls = mname.replace("/", "\\").rsplit("\\", 1)[-1].removesuffix(".rvmat") or "unknown"
            extras = {"part": p, "group": "armour" if "armour" in cls or "armor" in cls else "shell", "class": cls}
            mm = re.search(r"(\d+(?:\.\d+)?)\s*mm", cls)
            if mm:
                extras["mm"] = float(mm.group(1))
            out.append((f"{cls}.{p}", fig[verts], remap[t], extras))
    return out


# ---- writing --------------------------------------------------------------------------------------------------
class _Bin:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.size = 0
        self.views: list[dict] = []
        self.accessors: list[dict] = []

    def add(self, data: bytes, target: int) -> int:
        pad = (-self.size) % 4
        if pad:
            self.chunks.append(b"\0" * pad)
            self.size += pad
        self.views.append({"buffer": 0, "byteOffset": self.size, "byteLength": len(data), "target": target})
        self.chunks.append(data)
        self.size += len(data)
        return len(self.views) - 1

    def mesh(self, v: np.ndarray, f: np.ndarray, c: np.ndarray | None = None) -> dict:
        v = np.ascontiguousarray(v, np.float32)
        self.accessors.append({"bufferView": self.add(v.tobytes(), 34962), "componentType": 5126, "count": len(v),
                               "type": "VEC3", "min": v.min(0).tolist(), "max": v.max(0).tolist()})
        attrs = {"POSITION": len(self.accessors) - 1}
        if c is not None:
            c = np.ascontiguousarray(c, np.uint8)
            self.accessors.append({"bufferView": self.add(c.tobytes(), 34962), "componentType": 5121, "normalized": True,
                                   "count": len(c), "type": "VEC4"})
            attrs["COLOR_0"] = len(self.accessors) - 1
        idx = np.ascontiguousarray(f.reshape(-1), np.uint16 if len(v) < 65536 else np.uint32)
        self.accessors.append({"bufferView": self.add(idx.tobytes(), 34963),
                               "componentType": 5123 if idx.dtype == np.uint16 else 5125, "count": len(idx), "type": "SCALAR"})
        return {"primitives": [{"attributes": attrs, "indices": len(self.accessors) - 1, "mode": 4}]}


PART_ORDER = {"hull": 0, "aerial": 1, "gear": 2, "turret": 3, "gun": 4, "rotor": 5}


def _rank(name: str):
    base, _, k = name.partition(".")
    return (int(k) if k.isdigit() else 0, PART_ORDER.get(base, 9), name)


def write_glb(path: Path, b: Built, meta: dict, asset: dict) -> int:
    bin_ = _Bin()
    meshes: list[dict] = []
    nodes: list[dict] = [{"name": "unit", "children": [], "extras": meta}]

    def node(name, parent, v=None, f=None, extras=None, c=None) -> int:
        n: dict = {"name": name}
        if v is not None and len(f):
            meshes.append(bin_.mesh(v, f, c))
            n["mesh"] = len(meshes) - 1
        if extras:
            n["extras"] = extras
        nodes.append(n)
        nodes[parent].setdefault("children", []).append(len(nodes) - 1)
        return len(nodes) - 1

    for k, parts in enumerate(b.lods):
        li = node(f"lod{k}", 0, extras={"sourceLod": b.source_lods[k] if k < len(b.source_lods) else None,
                                         "triangles": sum(len(t) for _v, t, _c in parts.values())})
        for p in sorted(parts, key=_rank):
            v, t, c = parts[p]
            node(p, li, v, t, b.joints.get(p), c)
    if b.dm:
        di = node("dm", 0)
        for name, v, t, extras in b.dm:
            node(f"dm:{name}", di, v, t, extras)
    gltf = {"asset": {"version": "2.0", "generator": asset.get("source", ""), "extras": asset}, "scene": 0,
            "scenes": [{"nodes": [0]}], "nodes": nodes, "meshes": meshes, "buffers": [{"byteLength": bin_.size}],
            "bufferViews": bin_.views, "accessors": bin_.accessors}
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    body = b"".join(bin_.chunks)
    body += b"\0" * ((-len(body)) % 4)
    total = 12 + 8 + len(js) + 8 + len(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".glb.part")
    with tmp.open("wb") as fh:
        fh.write(struct.pack("<III", 0x46546C67, 2, total))
        fh.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        fh.write(struct.pack("<II", len(body), 0x004E4942) + body)
    tmp.replace(path)
    return total


def hidden_textures(cls) -> dict[str, str]:
    sels = [s for s in (cls.get("hiddenSelections") or []) if isinstance(s, str)]
    texs = [t for t in (cls.get("hiddenSelectionsTextures") or []) if isinstance(t, str)]
    return {s.lower(): texs[i].strip("\\").lower() for i, s in enumerate(sels) if i < len(texs)}


def look_key(unit: Unit, model_hash: str) -> str:
    """What makes two classes' files the same: the model, what their sources show and their textures."""
    ts = turrets(unit.cfg)
    payload = [model_hash, sorted(source_values(unit.cfg).items()), sorted(hidden_textures(unit.cfg).items()),
               [(t.path, t.body, t.gun, t.primary, bool(t.weapons)) for t in ts],
               [(h.name, h.get("armorComponent"), h.get("armor"), h.get("name")) for h in hitpoints(unit.cfg)],
               unit.domain, unit.kind, FORMAT_VERSION]
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def file_stem(unit: Unit) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", unit.id.lower())


def coverage(us: list[Unit], index: dict) -> dict:
    by_kind: dict[str, dict] = {}
    have = index.get("units", {})
    for u in us:
        k = by_kind.setdefault(u.kind, {"vehicles": 0, "models": 0})
        k["vehicles"] += 1
        if u.id in have:
            k["models"] += 1
    return {"vehicles": len(us), "models": sum(1 for u in us if u.id in have), "kinds": by_kind,
            "missing": sorted(u.id for u in us if u.id not in have)}


def export_pack(game, out: Path, source: str, wanted: list[str] | None = None, force: bool = False,
                progress=None, log=print) -> dict:
    """Writes (or brings up to date) the pack in `out`; returns a report."""
    t0 = time.monotonic()
    us = units(game)
    if wanted:
        w = {x.lower() for x in wanted}
        us = [u for u in us if u.id.lower() in w or u.shape.lower().rstrip(".p3d") in w]
    out.mkdir(parents=True, exist_ok=True)
    index_path = out / "model-pack.json"
    index = {"format": "3dheat-model-pack", "version": 1, "game": GAME, "gameBuild": game.install.build,
             "source": source, "units": {}}
    if index_path.is_file():
        try:
            old = json.loads(index_path.read_text(encoding="utf-8"))
            if old.get("format") == "3dheat-model-pack":
                index["units"] = old.get("units", {})
        except (OSError, ValueError):
            pass
    colours = Colours(game.fs)
    models: dict[str, tuple[Model, str] | Exception] = {}
    files: dict[str, str] = {}  # look key -> file
    for e in index["units"].values():
        if e.get("key") and e.get("file"):
            files.setdefault(e["key"], e["file"])
    report = {"ok": [], "unchanged": [], "failed": {}, "bytes": 0}
    hit_doc: dict[str, dict] = {}
    names: set[str] = set()
    if progress:
        progress.start(len(us))
    for n, u in enumerate(us):
        try:
            sp = shape_path(u.shape)
            if sp not in models:
                try:
                    data = game.fs.read(sp)
                    models[sp] = (read_model(data), hashlib.sha1(data).hexdigest()[:16])
                except Exception as exc:  # noqa: BLE001 - one model failing never stops the others
                    models[sp] = exc
            got = models[sp]
            if isinstance(got, Exception):
                raise got
            model, mhash = got
            key = look_key(u, mhash)
            hps = hitpoints(u.cfg)
            for h in hps:
                names.add(h.name)
            have = index["units"].get(u.id)
            if (have and not force and have.get("key") == key and have.get("source") == source
                    and (out / have["file"]).is_file()):
                report["unchanged"].append(u.id)
                if progress:
                    progress.map(n, u.id, True, "unchanged")
                hit_doc[u.id] = have.get("hits", {})
                continue
            if key in files and (out / files[key]).is_file() and not force and files[key] in {
                    e.get("file") for e in index["units"].values() if e.get("source") == source}:
                fname = files[key]
                meta = next(e for e in index["units"].values() if e.get("file") == fname)
                meta = {k: v for k, v in meta.items() if k not in ("aliases", "name", "names", "displayName", "side")}
            else:
                b = build(model, u, colours, hidden_textures(u.cfg))
                fname = f"{file_stem(u)}.glb"
                lo, hi = b.box
                domain, words = TYPE_WORDS.get(u.kind, (u.domain or "ground", u.kind))
                if "quadcopter" in u.key.lower():
                    words = "uav quadcopter"
                meta = {"unit": u.id, "kind": u.kind, "domain": domain, "type": words, "model": u.shape,
                        "anchor": "centre" if domain in ("air", "heli") else "ground",
                        "box": [round(float(x), 4) for x in (*lo, *hi)],
                        "pivots": {p: j["pivot"] for p, j in b.joints.items() if "pivot" in j and p.split(".")[0] in ("turret", "gun")},
                        "length": round(float(hi[0] - lo[0]), 3), "width": round(float(hi[2] - lo[2]), 3),
                        "height": round(float(hi[1] - lo[1]), 3), "parts": sorted(b.lods[0]),
                        "tris": [sum(len(t) for _v, t, _c in parts.values()) for parts in b.lods],
                        "sourceLods": b.source_lods, "dm": len(b.dm), "notes": b.notes,
                        "mass": round(float(model.mass), 1)}
                if b.joints:
                    meta["joints"] = {p: {k: v for k, v in j.items() if k != "limits"} for p, j in b.joints.items()}
                t1 = b.joints.get("turret", {}).get("limits")
                g1 = b.joints.get("gun", {}).get("limits")
                if t1:
                    meta["yawLimits"] = [float(t1[0]), float(t1[1])]
                if g1:
                    meta["pitchLimits"] = [float(g1[0]), float(g1[1])]
                mm = [d[3]["mm"] for d in b.dm if "mm" in d[3]]
                if mm:
                    meta["armourMm"] = [min(mm), max(mm)]
                for p, j in b.joints.items():
                    j.pop("limits", None)
                    j.pop("turret", None)
                size = write_glb(out / fname, b, meta, {"source": source, "version": 1, "game": GAME,
                                                        "gameBuild": game.install.build, "unit": u.id, "scale": 1})
                report["bytes"] += size
                files[key] = fname
            entry = {k: v for k, v in meta.items() if k != "unit"}
            real = recorded_names(u.name, entry.get("domain", ""))
            extra = [x for x in [u.name, *real] if x and x != u.key]
            entry.update({"file": fname, "key": key, "gameBuild": game.install.build, "source": source,
                          "name": u.key, "displayName": u.name, "side": u.side})
            if extra:
                entry["names"] = list(dict.fromkeys(extra))
            entry["hits"] = {h.name: {"selection": str(h.get("name", "")), "component": str(h.get("armorComponent", "") or ""),
                                      "armor": round(_float(h.get("armor", 1.0), 1.0), 3),
                                      "radius": round(_float(h.get("radius", 0.0)), 3),
                                      "passThrough": round(_float(h.get("passThrough", 0.0)), 3),
                                      "group": _hit_group(h.name)} for h in hps}
            hit_doc[u.id] = entry["hits"]
            index["units"][u.id] = entry
            report["ok"].append(u.id)
            if progress:
                progress.map(n, u.id, True, fname)
            if log and (n % 10 == 0 or n == len(us) - 1):
                log(f"[{n + 1}/{len(us)}] {u.id} ({u.kind}) tris {entry['tris']} dm {entry['dm']}  {time.monotonic() - t0:.0f} s")
        except Exception as exc:  # noqa: BLE001 - one vehicle failing never stops the others
            report["failed"][u.id] = f"{type(exc).__name__}: {exc}"
            if progress:
                progress.map(n, u.id, False, str(exc))
            if log:
                log(f"  {u.id}: FAILED {exc}")
    # every class sharing a file lists the others; recordings' names of the vehicle find it as ids too
    by_file: dict[str, list[str]] = {}
    for uid, e in index["units"].items():
        by_file.setdefault(e["file"], []).append(uid)
    for uid, e in index["units"].items():
        al = set(by_file[e["file"]]) - {uid}
        al |= {x.lower() for x in e.get("names", [])}
        e["aliases"] = sorted(al)
    tmp = index_path.with_suffix(".json.part")
    tmp.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(index_path)
    hits = {"format": "3dheat-hit-parts", "version": 1, "game": GAME, "gameBuild": game.install.build, "source": source,
            "names": sorted(names | {h for e in index["units"].values() for h in e.get("hits", {})}), "units": hit_doc}
    tmp = out / "hit-parts.json.part"
    tmp.write_text(json.dumps(hits, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    tmp.replace(out / "hit-parts.json")
    report["coverage"] = coverage(units(game) if wanted else us, index)
    report["seconds"] = round(time.monotonic() - t0, 1)
    return report


def check(game, out: Path, source: str) -> dict:
    """What the pack in `out` holds against the install, writing nothing: per vehicle current, outdated (a game
    update changed its model or class, or an older tool version wrote it) or missing."""
    us = units(game)
    index_path = out / "model-pack.json"
    index = {"units": {}}
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            index = {"units": {}}
    have = index.get("units", {})
    hashes: dict[str, str | None] = {}
    outdated, missing = [], []
    for u in us:
        sp = shape_path(u.shape)
        if sp not in hashes:
            try:
                hashes[sp] = hashlib.sha1(game.fs.read(sp)).hexdigest()[:16]
            except Exception:  # noqa: BLE001 - a model that does not read is missing from the pack
                hashes[sp] = None
        e = have.get(u.id)
        if e is None or not (out / e.get("file", "")).is_file():
            missing.append(u.id)
        elif hashes[sp] is None or e.get("key") != look_key(u, hashes[sp]) or e.get("source") != source:
            outdated.append(u.id)
    cov = coverage(us, {"units": {k: v for k, v in have.items() if k not in missing}})
    return {"game": GAME, "pack": str(out), "gameBuild": game.install.build, "packBuild": index.get("gameBuild"),
            "source": source, "packSource": index.get("source"), "vehicles": cov["vehicles"], "models": cov["models"],
            "kinds": cov["kinds"], "missing": sorted(missing), "outdated": sorted(outdated)}

"""
Binarised models (`.p3d`, ODOL version 73 as Arma 3 ships them): levels of detail with their vertices, faces,
sections, named selections and skinning, and the model's skeleton and animations - what a model pack needs.

Layout as read here (little-endian; `z` a zero-terminated string; a *compressed array* is a 32-bit count, then,
when the count is not zero, a flag byte - 0 raw, 2 an LZO1X stream of count x element bytes; a *condensed array*
is a count and a fill byte - 1 one element standing for all, 0 a compressed array's flag and data):

* head: `ODOL`, version, (from 59) an application id, (from 58) a prefix, the level count and their resolutions;
* model info: flags, spheres, hints, aiming centre, colours, view density, bounding box, (70, 71) two factors,
  (52) the visual box, bounding, geometry and mass centres, the inverse inertia, five flags, four thermal values,
  two more, three shadow fields, `animated`; the skeleton (name, discrete flag, bones as name and parent); the
  map type, a mass array, mass, its inverse, armour, its inverse, explosion shielding; the indices of the special
  levels (memory, geometry, simple geometry, physics, fire, view, view pilot, view gunner, one more, view cargo,
  land contact, roadway, paths, hit points); the minimum shadow level, blend flag, property class and damage,
  frequent flag, a further number and three shadow level tables;
* animations: per class its type (0-3 rotations, 4-7 translations, 8 direct, 9 hide), name, source, value and
  phase ranges, three numbers and its parameters; then per level the bones each animation moves and its axis;
* the levels' start and end offsets (the levels are stored back to front), their default face data;
* a level: proxies, the sub-skeleton (its bones as skeleton bones and back), vertex count, face area, hints, box,
  textures, materials (rvmat stages and colours), edges, faces (3 or 4 vertex indices, 32-bit), sections (face
  ranges in 16/20-byte face units, texture and material, and the eleven numbers an optic's picture-in-picture section
  carries), named selections (faces, sections, vertices, weights),
  properties, frames, colours, flags, then the vertex arrays: clip flags, UV sets, positions, normals, tangents,
  bone references (per vertex up to four sub-skeleton bones with weights) and neighbours.

Everything is checked as it is read (counts against what the file can hold, a level ending where its end offset
says); a model that does not read raises `OdolError`. Coordinates are the model's own: x right, y up, z forward,
metres, about the bounding centre the binariser moved the model to (`Model.centre` is where its origin was).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

from .compress import CompressionError, lzo1x_decode

ROTATION, ROTATION_X, ROTATION_Y, ROTATION_Z = 0, 1, 2, 3
TRANSLATION, TRANSLATION_X, TRANSLATION_Y, TRANSLATION_Z = 4, 5, 6, 7
DIRECT, HIDE = 8, 9

#: The special levels' resolutions (the levels other than the visual ones).
SPECIAL = {
    1e13: "geometry", 2e13: "buoyancy", 4e13: "physx", 1e15: "memory", 2e15: "land contact", 3e15: "roadway",
    4e15: "paths", 5e15: "hit points", 6e15: "view geometry", 7e15: "fire geometry", 8e15: "view cargo geometry",
    9e15: "view cargo fire geometry", 1e16: "view commander", 1.1e16: "view commander geometry",
    1.2e16: "view commander fire geometry", 1.3e16: "view pilot geometry", 1.4e16: "view pilot fire geometry",
    1.5e16: "view gunner geometry", 1.6e16: "view gunner fire geometry", 1.7e16: "sub parts",
    1.8e16: "shadow volume view cargo", 1.9e16: "shadow volume view pilot", 2e16: "shadow volume view gunner",
    2.1e16: "wreck",
}
LOD_SLOTS = ("memory", "geometry", "geometry simple", "physx", "fire geometry", "view geometry", "view pilot geometry",
             "view gunner geometry", "unknown", "view cargo geometry", "land contact", "roadway", "paths", "hit points")


class OdolError(ValueError):
    pass


def lod_kind(resolution: float) -> str:
    """What a level is: `visual` (resolutions under 1,000), `view` (the crew's views), `shadow`, or a special one."""
    if resolution < 1000:
        return "visual"
    if resolution < 10000:
        return "view"
    if resolution < 20000:
        return "shadow"
    for r, name in SPECIAL.items():
        if abs(resolution - r) <= r * 1e-3:
            return name
    return "other"


class _Reader:
    def __init__(self, data: bytes, at: int = 0) -> None:
        self.d = data
        self.o = at
        self.n = len(data)

    def take(self, fmt: str):
        try:
            v = struct.unpack_from("<" + fmt, self.d, self.o)
        except struct.error:
            raise OdolError(f"model ends early at {self.o:,}") from None
        self.o += struct.calcsize("<" + fmt)
        return v if len(v) > 1 else v[0]

    def u32(self) -> int:
        return self.take("I")

    def count(self, size: int = 1, limit: int = 50_000_000) -> int:
        c = self.take("I")
        if c > limit or (size and self.o + min(c * size, 1) > self.n and c):
            raise OdolError(f"implausible count {c:,} at {self.o - 4:,}")
        return c

    def z(self) -> str:
        e = self.d.find(b"\0", self.o)
        if e < 0:
            raise OdolError(f"string runs off the end at {self.o:,}")
        v = self.d[self.o:e].decode("utf-8", "replace")
        self.o = e + 1
        return v

    def raw(self, n: int) -> bytes:
        if self.o + n > self.n:
            raise OdolError(f"model ends early at {self.o:,} ({n:,} bytes wanted)")
        b = self.d[self.o:self.o + n]
        self.o += n
        return b

    def _payload(self, c: int, size: int) -> bytes:
        flag = self.take("B")
        n = c * size
        if flag == 0:
            return self.raw(n)
        if flag == 2:
            try:
                out, used = lzo1x_decode(self.d, n, self.o)
            except CompressionError as exc:
                raise OdolError(f"array at {self.o:,}: {exc}") from None
            self.o += used
            return out
        raise OdolError(f"unknown array packing {flag} at {self.o - 1:,}")

    def compressed(self, size: int) -> bytes:
        c = self.count(0)
        return self._payload(c, size) if c else b""

    def condensed(self, size: int) -> bytes:
        c = self.count(0)
        fill = self.take("B")
        if fill:
            return self.raw(size) * c
        return self._payload(c, size) if c else b""


@dataclass
class Anim:
    type: int
    name: str
    source: str
    min_value: float
    max_value: float
    min_phase: float
    max_phase: float
    params: tuple  # rotations: angle0, angle1; translations: offset0, offset1; hide: hide value, unhide value

    @property
    def kind(self) -> str:
        if self.type <= ROTATION_Z:
            return "rotation"
        if self.type <= TRANSLATION_Z:
            return "translation"
        return "direct" if self.type == DIRECT else "hide"


@dataclass
class Selection:
    name: str
    faces: np.ndarray  # face indices
    sections: np.ndarray
    vertices: np.ndarray
    weights: np.ndarray


@dataclass
class Section:
    face_from: int  # face indices, [from, to)
    face_to: int
    texture: int
    material: int
    special: int


@dataclass
class Material:
    name: str
    diffuse: tuple
    emissive: tuple
    stages: list[str]


@dataclass
class Lod:
    index: int
    resolution: float
    kind: str
    vertices: np.ndarray  # (n, 3) float32, model frame
    faces: list[tuple]  # vertex index tuples (3 or 4)
    sections: list[Section]
    selections: dict[str, Selection]  # by lower-case name
    textures: list[str]
    materials: list[Material]
    proxies: list[tuple]  # (model, 4x3 transform, selection index)
    bones: np.ndarray  # (n,) the skeleton bone each vertex follows most, -1 none
    properties: dict[str, str]

    def triangles(self, faces=None) -> np.ndarray:
        """The faces (all, or those given) as triangles, (m, 3) vertex indices; quads split along their first diagonal."""
        out = []
        for k in (range(len(self.faces)) if faces is None else faces):
            f = self.faces[k]
            out.append(f[:3])
            if len(f) == 4:
                out.append((f[0], f[2], f[3]))
        return np.array(out, dtype=np.int64).reshape(-1, 3)

    def face_section(self) -> np.ndarray:
        """The section each face is in (-1 none)."""
        out = np.full(len(self.faces), -1, np.int32)
        for i, s in enumerate(self.sections):
            out[s.face_from:s.face_to] = i
        return out


@dataclass
class Model:
    version: int
    prefix: str
    resolutions: list[float]
    centre: np.ndarray  # where the binariser moved the model's origin from (its bounding centre)
    box: tuple  # (min, max) of the geometry, model frame
    visual_box: tuple
    mass: float
    armor: float
    skeleton: list[tuple[str, str]]  # (bone, parent), lower case
    anims: list[Anim]
    anim_bones: list[list[tuple[int, tuple | None]]]  # per level, per animation: (skeleton bone, (axis pos, axis dir))
    slots: dict[str, int]  # special level -> index
    starts: list[int]
    ends: list[int]
    data: bytes = field(repr=False, default=b"")
    _lods: dict[int, Lod] = field(default_factory=dict, repr=False)

    def kinds(self) -> list[str]:
        return [lod_kind(r) for r in self.resolutions]

    def visual(self) -> list[int]:
        return [i for i, r in enumerate(self.resolutions) if r < 1000]

    def find(self, kind: str) -> int | None:
        i = self.slots.get(kind, -1)
        if i is not None and 0 <= i < len(self.resolutions):
            return i
        for k, r in enumerate(self.resolutions):
            if lod_kind(r) == kind:
                return k
        return None

    def lod(self, i: int) -> Lod:
        if i not in self._lods:
            self._lods[i] = _read_lod(self, i)
        return self._lods[i]

    def bone_index(self, name: str) -> int:
        n = name.lower()
        for i, (b, _p) in enumerate(self.skeleton):
            if b == n:
                return i
        return -1

    def descendants(self, bone: int) -> set[int]:
        """A bone and every bone under it."""
        kids: dict[str, list[int]] = {}
        for i, (_b, p) in enumerate(self.skeleton):
            kids.setdefault(p, []).append(i)
        out, todo = set(), [bone]
        while todo:
            b = todo.pop()
            if b in out or b < 0:
                continue
            out.add(b)
            todo += kids.get(self.skeleton[b][0], [])
        return out

    def anim(self, name: str) -> int:
        n = name.lower()
        for i, a in enumerate(self.anims):
            if a.name.lower() == n:
                return i
        return -1


def read_model(data: bytes) -> Model:
    if data[:4] != b"ODOL":
        raise OdolError("not a binarised model (ODOL)")
    r = _Reader(data, 4)
    version = r.u32()
    if not 70 <= version <= 75:
        raise OdolError(f"ODOL version {version} not read (73 is Arma 3's)")
    if version >= 59:
        r.u32()  # application id
    prefix = r.z() if version >= 58 else ""
    nlods = r.count(4, 256)
    if not nlods:
        raise OdolError("a model without levels")
    res = list(r.take(f"{nlods}f")) if nlods > 1 else [r.take("f")]
    # model info
    r.take("I2f3I")  # flags, spheres, remarks, hints
    r.take("3f2If")  # aiming centre, colours, view density
    bmin = r.take("3f")
    bmax = r.take("3f")
    if version >= 70:
        r.take("f")
    if version >= 71:
        r.take("f")
    vmin, vmax = (r.take("3f"), r.take("3f")) if version >= 52 else (bmin, bmax)
    centre = np.array(r.take("3f"), np.float32)
    r.take("3f3f9f")  # geometry centre, mass centre, inverse inertia
    r.take("4B")  # auto centre, lock auto centre, can occlude, can be occluded
    if version >= 73:
        r.take("B")  # AI covers
    r.take("4f2fBiBf")  # thermal values, mass factor, body temperature, alpha, shadow fields, shadow offset
    r.take("B")  # animated
    skeleton: list[tuple[str, str]] = []
    name = r.z()
    if name:
        r.take("B")  # discrete
        nb = r.count(2, 65536)
        for _ in range(nb):
            skeleton.append((r.z().lower(), r.z().lower()))
        r.z()  # obsolete pivots model name
    r.take("B")  # map type
    masses = r.count(4)
    if masses:
        r._payload(masses, 4)
    mass, _inv_mass, armor, _inv_armor = r.take("4f")
    if version >= 72:
        r.take("f")  # explosion shielding
    slots_raw = r.take("14b")
    slots = {k: v for k, v in zip(LOD_SLOTS, slots_raw) if v >= 0}
    r.take("I")  # minimum shadow level
    r.take("B")  # can blend
    r.z()  # property class
    r.z()  # property damage
    r.take("B")  # frequent
    r.take("I")
    r.raw(12 * nlods)  # shadow level preferences
    anims: list[Anim] = []
    anim_bones: list[list[tuple[int, tuple | None]]] = [[] for _ in range(nlods)]
    if r.take("B"):
        na = r.count(16, 100_000)
        for _ in range(na):
            t = r.u32()
            if t > HIDE:
                raise OdolError(f"animation type {t} not known")
            nm, src = r.z(), r.z()
            vals = r.take("4f")
            r.take("3I")
            params = r.take("8f") if t == DIRECT else r.take("2f")
            anims.append(Anim(t, nm, src, *vals, params))
        nres = r.count(4, 256)
        for _ in range(nres):  # per level, per bone: the animations it takes
            for _b in range(r.count(4)):
                c = r.count(4)
                if c:
                    r.raw(4 * c)
        for li in range(nres):
            row = []
            for a in anims:
                b = r.take("i")
                axis = None
                if b != -1 and a.type not in (DIRECT, HIDE):
                    v = r.take("6f")
                    axis = (v[:3], v[3:])
                row.append((b, axis))
            if li < nlods:
                anim_bones[li] = row
    starts = list(r.take(f"{nlods}I")) if nlods > 1 else [r.take("I")]
    ends = list(r.take(f"{nlods}I")) if nlods > 1 else [r.take("I")]
    for s, e in zip(starts, ends):
        if not 0 < s <= e <= len(data):
            raise OdolError(f"level offsets out of the file ({s:,}..{e:,})")
    return Model(version, prefix, res, centre, (np.array(bmin), np.array(bmax)), (np.array(vmin), np.array(vmax)),
                 mass, armor, skeleton, anims, anim_bones, slots, starts, ends, data)


def _stage(r: _Reader, mv: int) -> str:
    if mv >= 5:
        r.u32()
    t = r.z()
    if mv >= 8:
        r.u32()
    if mv >= 11:
        r.take("B")
    return t


def _read_lod(model: Model, i: int) -> Lod:
    r = _Reader(model.data, model.starts[i])
    ver = model.version
    proxies = []
    for _ in range(r.count(64, 100_000)):
        pm = r.z()
        tr = r.take("12f")
        seq, sel, bone = r.take("3i")
        if ver >= 40:
            r.take("i")
        proxies.append((pm, tr, sel, bone))
    nsub = r.count(4)
    sub = np.frombuffer(r.raw(4 * nsub), "<u4").astype(np.int64) if nsub else np.zeros(0, np.int64)
    for _ in range(r.count(4)):
        c = r.count(4)
        if c:
            r.raw(4 * c)
    nverts = r.u32()
    r.take("f")  # face area
    r.take("2I")  # hints
    r.take("10f")  # box, centre, radius
    textures = [r.z() for _ in range(r.count(1, 100_000))]
    materials = []
    for _ in range(r.count(8, 100_000)):
        mname = r.z()
        mv = r.u32()
        if mv > 32:
            raise OdolError(f"material version {mv} not read")
        cols = [r.take("4f") for _ in range(6)]  # emissive, ambient, diffuse, forced diffuse, specular, specular 2
        r.take("f4I")
        if mv == 3:
            r.take("B")
        if mv >= 6:
            r.z()
        if mv >= 4:
            r.take("2I")
        nst = r.count(4, 1000)
        ntg = r.count(4, 1000) if mv > 7 else 0
        stages = [_stage(r, mv) for _ in range(nst)]
        for _ in range(ntg):
            r.take("I12f")
        if mv >= 10:
            stages.append(_stage(r, mv))
        materials.append(Material(mname, cols[2], cols[0], stages))
    r.compressed(2 if ver < 69 else 4)  # edges: source model indices
    r.compressed(2 if ver < 69 else 4)  # edges: vertex indices
    nfaces = r.count(4)
    r.take("I")  # offset to the sections
    r.take("H")
    faces = []
    starts = np.zeros(nfaces + 1, np.int64)  # each face's offset in 16/20-byte face units
    idx = "I" if ver >= 69 else "H"
    size = 4 if ver >= 69 else 2
    unit = 0
    for k in range(nfaces):
        c = r.take("B")
        if c not in (3, 4):
            raise OdolError(f"a face of {c} vertices at {r.o - 1:,}")
        f = r.take(f"{c}{idx}")
        faces.append(f)
        starts[k] = unit
        unit += 4 + size * c if ver >= 69 else 2 + size * c
    starts[nfaces] = unit
    sections = []
    for _ in range(r.count(20, 1_000_000)):
        lo, hi = r.take("2I")
        r.take("3I")  # minimum bone, bone count, common point flags
        tex = r.take("h")
        special = r.u32()
        mat = r.take("i")
        if mat == -1:
            r.z()
        nst = r.count(4, 1000)
        if nst:
            r.raw(4 * nst)
        if ver >= 67:
            extra = r.count(44, 1000)  # (optics' picture-in-picture sections) eleven numbers each, not used here
            if extra:
                r.raw(44 * extra)
        f0 = int(np.searchsorted(starts, lo))
        f1 = int(np.searchsorted(starts, hi))
        sections.append(Section(f0, f1, tex, mat, special))
    selections = {}
    wide = 4 if ver >= 69 else 2
    for _ in range(r.count(8, 1_000_000)):
        sname = r.z()
        sf = r.compressed(wide)
        r.take("I")
        r.take("B")  # sectional
        ss = r.compressed(4)
        sv = r.compressed(wide)
        sw = r.compressed(1)
        dt = "<u4" if wide == 4 else "<u2"
        selections[sname.lower()] = Selection(sname, np.frombuffer(sf, dt).astype(np.int64),
                                              np.frombuffer(ss, "<u4").astype(np.int64),
                                              np.frombuffer(sv, dt).astype(np.int64), np.frombuffer(sw, "<u1"))
    props = {}
    for _ in range(r.count(2, 100_000)):
        k, v = r.z(), r.z()
        props[k.lower()] = v
    for _ in range(r.count(8, 100_000)):  # frames
        r.take("f")
        nb = r.count(12)
        if nb:
            r.raw(12 * nb)
    r.take("3I")  # icon colour, selected colour, special
    r.take("B")  # simple bone references
    r.take("I")  # size of what follows
    if ver >= 50:
        r.condensed(4)  # clip flags
    r.take("4f")
    r.condensed(4)  # first UV set
    nuv = r.count(1, 64)
    for _ in range(max(0, nuv - 1)):
        r.take("4f")
        r.condensed(4)
    pos = r.compressed(12)
    verts = np.frombuffer(pos, "<f4").reshape(-1, 3).astype(np.float32)
    if len(verts) != nverts:
        raise OdolError(f"level {i}: {len(verts):,} positions for {nverts:,} vertices")
    r.condensed(4)  # normals
    r.compressed(8)  # tangents
    refs = r.compressed(12)
    bones = np.full(len(verts), -1, np.int64)
    if refs:
        br = np.frombuffer(refs, "<u1").reshape(-1, 12)
        if len(br) == len(verts):
            n = br[:, 0]
            # the heaviest of the (up to four) bones; one listed bone counts whole
            pairs = br[:, 4:12].reshape(-1, 4, 2).astype(np.int64)
            w = pairs[:, :, 1].copy()
            w[np.arange(4)[None, :] >= n[:, None]] = -1
            best = pairs[np.arange(len(br)), np.argmax(w, axis=1), 0]
            ok = (n > 0) & (best < len(sub))
            bones[ok] = sub[best[ok]]
    r.compressed(32)  # neighbours
    if ver >= 67:
        r.take("I")
    if ver >= 68:
        r.take("B")
    if r.o != model.ends[i]:
        raise OdolError(f"level {i} ends at {r.o:,}, its table says {model.ends[i]:,}")
    nv = len(verts)
    for f in faces:
        if max(f) >= nv:
            raise OdolError(f"level {i}: a face points past its {nv:,} vertices")
    return Lod(i, model.resolutions[i], lod_kind(model.resolutions[i]), verts, faces, sections, selections, textures,
               materials, proxies, bones, props)

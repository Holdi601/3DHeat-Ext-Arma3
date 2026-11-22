"""A made-up binarised model (ODOL version 73) for the tests: written the way `odol.py` reads one, no game file in it
(a section with the extra numbers optics' sections carry included).

The model: a hull box, a turret on it (bone `otocvez`, turned by `mainTurret`), a gun on the turret (`otochlaven`,
raised by `mainGun`), a wheel (`koll1`) and bags (`bags`, hidden unless the `showBags` source is 1), facing -z as
binarised models do; a Fire Geometry with the hull's hit component and a 40 mm plate; a Hit-points level with the
engine's points; a land contact level one metre under the origin.
"""

from __future__ import annotations

import struct

from heat3d_ext_arma3.compress import lzo1x_literal

CENTRE = (0.0, 1.0, 0.0)  # where the binariser's centre was: the model's own origin is one metre under it


def _z(s: str) -> bytes:
    return s.encode() + b"\0"


def _box(lo, hi):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (3, 6, 2), (3, 7, 6), (1, 2, 6), (1, 6, 5),
         (0, 4, 7), (0, 7, 3)]
    return v, f


def _carr(data: bytes, count: int, lzo: bool = False) -> bytes:
    if not count:
        return struct.pack("<I", 0)
    if lzo:
        return struct.pack("<IB", count, 2) + lzo1x_literal(data)
    return struct.pack("<IB", count, 0) + data


def _cond_fill(count: int, elem: bytes) -> bytes:
    return struct.pack("<IB", count, 1) + elem


def _material(name: str, stage_tex: str) -> bytes:
    out = _z(name) + struct.pack("<I", 11)
    out += struct.pack("<24f", *([0, 0, 0, 0] + [1] * 4 + [1] * 4 + [0] * 4 + [0.1] * 4 + [0.1] * 4))
    out += struct.pack("<f4I", 50.0, 0, 0, 1, 1) + _z("") + struct.pack("<2I", 1, 0)
    out += struct.pack("<2I", 1, 1)  # stages, texgens
    out += struct.pack("<I", 3) + _z(stage_tex) + struct.pack("<IB", 0, 0)
    out += struct.pack("<I12f", 0, *([0.0] * 12))
    out += struct.pack("<I", 3) + _z("") + struct.pack("<IB", 0, 0)
    return out


def _lod(verts, faces, bones, sections, selections, textures=(), materials=(), sub=(), props=()) -> bytes:
    """`sections`: (first face, end face, texture, material); `selections`: (name, faces, sections, vertices)."""
    out = struct.pack("<I", 0)  # proxies
    out += struct.pack("<I", len(sub)) + b"".join(struct.pack("<I", s) for s in sub)
    out += struct.pack("<I", 0)  # bone links
    out += struct.pack("<I", len(verts)) + struct.pack("<f2I", 1.0, 0, 0) + struct.pack("<10f", *([0.0] * 10))
    out += struct.pack("<I", len(textures)) + b"".join(_z(t) for t in textures)
    out += struct.pack("<I", len(materials)) + b"".join(materials)
    out += struct.pack("<II", 0, 0)  # edges
    face_bytes = b"".join(struct.pack("<B", len(f)) + struct.pack(f"<{len(f)}I", *f) for f in faces)
    out += struct.pack("<IIH", len(faces), 0, 0) + face_bytes
    starts = [0]
    for f in faces:
        starts.append(starts[-1] + 4 + 4 * len(f))
    out += struct.pack("<I", len(sections))
    for a, b, tex, mat, *extra in sections:
        out += struct.pack("<5I", starts[a], starts[b], 0, 0, 0) + struct.pack("<hIi", tex, 0, mat)
        if mat == -1:
            out += _z("")
        out += struct.pack("<I", 0) + struct.pack("<I", len(extra)) + b"".join(struct.pack("<11f", *e) for e in extra)
    out += struct.pack("<I", len(selections))
    for name, fs, ss, vs in selections:
        out += _z(name) + _carr(b"".join(struct.pack("<I", x) for x in fs), len(fs)) + struct.pack("<IB", 0, 1 if ss else 0)
        out += _carr(b"".join(struct.pack("<I", x) for x in ss), len(ss))
        out += _carr(b"".join(struct.pack("<I", x) for x in vs), len(vs)) + _carr(b"\xff" * len(vs), len(vs))
    out += struct.pack("<I", len(props)) + b"".join(_z(k) + _z(v) for k, v in props)
    out += struct.pack("<I", 0)  # frames
    out += struct.pack("<3IBI", 0, 0, 0, 1, 0)
    n = len(verts)
    out += _cond_fill(n, struct.pack("<I", 0))  # clip flags
    out += struct.pack("<4f", 0, 0, 1, 1) + _cond_fill(n, struct.pack("<hh", 0, 0)) + struct.pack("<I", 1)
    out += _carr(b"".join(struct.pack("<3f", *v) for v in verts), n, lzo=True)
    out += _cond_fill(n, struct.pack("<I", 0))  # normals
    out += struct.pack("<I", 0)  # tangents
    refs = b""
    for b in bones:
        refs += struct.pack("<B3x", 1 if b >= 0 else 0) + (struct.pack("<BB6x", b, 255) if b >= 0 else b"\0" * 8)
    out += _carr(refs, n)
    out += struct.pack("<I", 0)  # neighbours
    out += struct.pack("<IB", 0, 0)
    return out


def build_model() -> bytes:
    skeleton = [("otocvez", ""), ("otochlaven", "otocvez"), ("koll1", ""), ("bags", "")]
    # visual level: hull, turret, gun (pointing to -z: forward), wheel, bags; a proxy's marker triangle
    parts = [((-1.5, -1.0, -3.0), (1.5, 0.5, 3.0), -1), ((-1.0, 0.5, -1.0), (1.0, 1.3, 1.0), 0),
             ((-0.1, 0.8, -4.5), (0.1, 1.0, -1.0), 1), ((1.5, -1.0, -2.0), (1.8, -0.2, -1.2), 2),
             ((-1.2, 0.5, 1.5), (1.2, 1.0, 2.5), 3)]
    verts, faces, bones = [], [], []
    for lo, hi, bone in parts:
        v, f = _box(lo, hi)
        base = len(verts)
        verts += v
        faces += [tuple(base + i for i in t) for t in f]
        bones += [bone] * len(v)
    base = len(verts)
    verts += [(0, 3, 0), (1, 3, 0), (0, 4, 0)]
    bones += [-1, -1, -1]
    faces.append((base, base + 1, base + 2))
    nf = len(faces)
    visual = _lod(verts, faces, bones, [(0, nf - 1, 0, 0), (nf - 1, nf, -1, -1, [0.5] * 11)],
                  [("proxy:\\a3\\data_f\\proxies\\crew\\driver.001", [], [1], [])],
                  textures=["a3\\armor_f\\data\\body_co.paa"],
                  materials=[_material("a3\\armor_f\\data\\body.rvmat", "a3\\armor_f\\data\\body_nohq.paa")], sub=[0, 1, 2, 3])
    # fire geometry: the hull's hit component (an inner box) and an outer 40 mm plate
    hv, hf = _box((-0.5, -0.5, -0.5), (0.5, 0.0, 0.5))
    pv, pf = _box((-1.5, -1.0, -3.0), (1.5, 0.5, 3.0))
    fv = hv + pv
    ff = hf + [tuple(8 + i for i in t) for t in pf]
    fire = _lod(fv, ff, [-1] * len(fv), [(0, 12, -1, 0), (12, 24, -1, 1)],
                [("hit_hull", list(range(12)), [], list(range(8)))],
                materials=[_material("a3\\data_f\\penetration\\armour.rvmat", ""),
                           _material("a3\\data_f\\penetration\\armour_plate_40mm.rvmat", "")])
    hitpts = _lod([(0, 0, 2.0), (0, 0.2, 2.2)], [], [-1, -1], [], [("hit_engine_point", [], [], [0, 1])])
    contact = _lod([(1.5, -1.0, -2.5), (-1.5, -1.0, -2.5), (1.5, -1.0, 2.5), (-1.5, -1.0, 2.5)], [], [-1] * 4, [], [])
    lods = [(1.0, visual), (7e15, fire), (5e15, hitpts), (2e15, contact)]
    n = len(lods)
    head = b"ODOL" + struct.pack("<II", 73, 107410) + _z("\\a3\\test") + struct.pack("<I", n)
    head += struct.pack(f"<{n}f", *[r for r, _ in lods])
    info = struct.pack("<I2f3I", 0, 5.0, 5.0, 0, 0, 0) + struct.pack("<3f2If", 0, 0, 0, 0, 0, 0)
    info += struct.pack("<6f", -1.8, -1.0, -4.5, 1.8, 4.0, 3.0) + struct.pack("<2f", 1, 1)
    info += struct.pack("<6f", -1.8, -1.0, -4.5, 1.8, 1.3, 3.0) + struct.pack("<3f", *CENTRE)
    info += struct.pack("<15f", *([0.0] * 15)) + struct.pack("<5B", 1, 0, 0, 1, 0)
    info += struct.pack("<4f2fBiBf", 0, 0, 0, 0, 1, 1, 0, 1, 1, 0) + struct.pack("<B", 1)
    info += _z("test") + struct.pack("<BI", 1, len(skeleton)) + b"".join(_z(b) + _z(p) for b, p in skeleton) + _z("")
    info += struct.pack("<BI", 0, 0) + struct.pack("<4f", 60000.0, 1 / 60000, 200.0, 0.005) + struct.pack("<f", 1.0)
    slots = [-1] * 14
    slots[4], slots[10], slots[13] = 1, 3, 2  # fire geometry, land contact, hit points
    info += struct.pack("<14b", *slots) + struct.pack("<IB", 0, 0) + _z("") + _z("") + struct.pack("<BI", 0, 0)
    info += b"\xff" * (12 * n)
    anims = [(2, "mainTurret", "mainTurret", (0.0, 0.0, 0.0)), (1, "mainGun", "mainGun", (0.0, 0.9, -1.0)),
             (9, "hideBags", "showBags", None)]
    abody = struct.pack("<BI", 1, len(anims))
    for t, name, src, _p in anims:
        abody += struct.pack("<I", t) + _z(name) + _z(src) + struct.pack("<4f", 0, 1, 0, 1) + struct.pack("<3I", 0, 0, 0)
        abody += struct.pack("<2f", 0.0, 1.0) if t == 9 else struct.pack("<2f", -1.0, 1.0)
    abody += struct.pack("<I", n) + struct.pack("<I", 0) * n  # bones to animations (none listed)
    bone_of = {"mainTurret": 0, "mainGun": 1, "hideBags": 3}
    for _ in range(n):
        for t, name, _src, p in anims:
            abody += struct.pack("<i", bone_of[name])
            if t != 9:
                abody += struct.pack("<6f", *p, 1.0, 0.0, 0.0)
    table_at = len(head) + len(info) + len(abody)
    offset = table_at + 8 * n + n
    starts, ends, body = [], [], b""
    for _r, data in lods:
        starts.append(offset + len(body))
        body += data
        ends.append(offset + len(body))
    return head + info + abody + struct.pack(f"<{n}I", *starts) + struct.pack(f"<{n}I", *ends) + b"\x01" * n + body


def build_paa(rgba=(200, 100, 50, 255)) -> bytes:
    """The head of a texture with its average colour tag (the image itself is never read)."""
    r, g, b, a = rgba
    return struct.pack("<H", 0xFF01) + b"GGATCGVA" + struct.pack("<I", 4) + bytes([b, g, r, a]) + b"\0" * 32

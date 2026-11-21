"""
ESRI shapefiles (`.shp` + `.dbf`, a public format): the road network Arma 3 terrains carry as
`data\\roads\\roads.shp`. Polylines (types 3, 13, 23) and their attribute rows; nothing else is needed.
"""

from __future__ import annotations

import struct


def read_polylines(shp: bytes) -> list[list[list[tuple[float, float]]]]:
    """Every record's parts, each a list of (x, y) points."""
    if len(shp) < 100 or struct.unpack_from(">i", shp, 0)[0] != 9994:
        raise ValueError("not a shapefile")
    out = []
    at = 100
    while at + 8 <= len(shp):
        _number, words = struct.unpack_from(">ii", shp, at)
        at += 8
        end = at + words * 2
        (kind,) = struct.unpack_from("<i", shp, at)
        if kind in (3, 13, 23):
            n_parts, n_points = struct.unpack_from("<ii", shp, at + 36)
            parts = list(struct.unpack_from(f"<{n_parts}i", shp, at + 44))
            pts_at = at + 44 + 4 * n_parts
            xy = struct.unpack_from(f"<{2 * n_points}d", shp, pts_at)
            points = list(zip(xy[0::2], xy[1::2]))
            bounds = parts + [n_points]
            out.append([points[bounds[i] : bounds[i + 1]] for i in range(n_parts)])
        else:
            out.append([])
        at = end
    return out


def read_dbf(dbf: bytes) -> list[dict[str, str]]:
    if len(dbf) < 32:
        return []
    count, header, length = struct.unpack_from("<IHH", dbf, 4)
    fields = []
    at = 32
    while at < header - 1 and dbf[at] != 0x0D:
        name = dbf[at : at + 11].split(b"\0")[0].decode("latin-1").strip().upper()
        size = dbf[at + 16]
        fields.append((name, size))
        at += 32
    rows = []
    for i in range(count):
        rec = header + i * length
        if rec + length > len(dbf):
            break
        p = rec + 1
        row = {}
        for name, size in fields:
            row[name] = dbf[p : p + size].decode("latin-1").strip()
            p += size
        rows.append(row)
    return rows


def write_shapefile(lines: list[list[tuple[float, float]]], rows: list[dict[str, str]]) -> tuple[bytes, bytes]:
    """A polyline shapefile and its table, for tests."""
    recs = bytearray()
    for i, line in enumerate(lines):
        xs, ys = [p[0] for p in line], [p[1] for p in line]
        body = struct.pack("<i4dii", 3, min(xs), min(ys), max(xs), max(ys), 1, len(line)) + struct.pack("<i", 0)
        body += b"".join(struct.pack("<2d", *p) for p in line)
        recs += struct.pack(">ii", i + 1, len(body) // 2) + body
    head = struct.pack(">i5ii", 9994, 0, 0, 0, 0, 0, (100 + len(recs)) // 2) + struct.pack("<ii4d4d", 1000, 3, 0, 0, 0, 0, 0, 0, 0, 0)
    names = list(rows[0]) if rows else []
    widths = {n: max(len(n), *(len(r.get(n, "")) for r in rows)) for n in names}
    header_len = 32 + 32 * len(names) + 1
    rec_len = 1 + sum(widths.values())
    dbf = bytearray(struct.pack("<B3BIHH20x", 3, 126, 1, 1, len(rows), header_len, rec_len))
    for n in names:
        dbf += n.encode().ljust(11, b"\0") + b"C" + b"\0" * 4 + bytes([widths[n], 0]) + b"\0" * 14
    dbf += b"\x0d"
    for r in rows:
        dbf += b" " + b"".join(r.get(n, "").encode().ljust(widths[n]) for n in names)
    return bytes(head + recs), bytes(dbf)

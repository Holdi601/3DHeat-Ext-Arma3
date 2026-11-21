"""
WRP, Arma 3's binarised terrain (`OPRW`, versions 24 and later), openly documented by the community.

What is read, and how each part is found and checked:

* the header: `OPRW`, version, (from 25) an application id, the texture-layer grid, the height grid and the layer
  cell size; accepted only when both grids are powers of two and agree on the terrain's size;
* the quad trees (geography, sound, materials) and the mountain list: read to step past them, the mountains checked
  to lie on the terrain;
* the packed arrays that follow: each an LZO1X stream of a known length (a layer or height grid of bytes, words or
  floats); the heights are the first array of height-grid floats, accepted only when every value is a plausible
  height above or below sea level;
* the model list: a count and that many `.p3d` paths;
* the placed objects: records of object id, model index and a 4x3 transform (axes and position, metres, x east,
  y up, z north), found as the longest run of records whose model index is in the list, whose axes are unit-ish and
  whose position lies on the terrain.

A file that fails any check raises `WrpError` saying which part; nothing is guessed silently.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

from .compress import CompressionError, lzo1x_decode


class WrpError(ValueError):
    pass


@dataclass
class Wrp:
    version: int
    layer_size: tuple[int, int]
    map_size: tuple[int, int]
    layer_cell: float
    heights: np.ndarray  # float32 [rows = north, cols = east], row 0 at the south edge
    models: list[str]
    objects: np.ndarray  # structured: id, model, m (12 floats)
    notes: list[str] = field(default_factory=list)

    @property
    def size(self) -> float:
        """The terrain's side in metres."""
        return self.layer_size[0] * self.layer_cell

    @property
    def spacing(self) -> float:
        """Metres between two height samples."""
        return self.size / self.map_size[0]


def _pow2(n: int) -> bool:
    return 16 <= n <= 1 << 16 and n & (n - 1) == 0


def read_header(data: bytes) -> tuple[int, int, tuple[int, int], tuple[int, int], float]:
    """(version, offset after the header, layer grid, height grid, layer cell size)."""
    if data[:4] != b"OPRW":
        if data[:4] == b"8WVR":
            raise WrpError("an editable terrain (8WVR), not a binarised one (OPRW); not read")
        raise WrpError("not a binarised terrain (OPRW)")
    (version,) = struct.unpack_from("<I", data, 4)
    if version < 20:
        raise WrpError(f"OPRW version {version}: older than Arma 3; not read")
    for start in ((12, 8) if version >= 25 else (8, 12)):
        lx, ly, mx, my = struct.unpack_from("<4I", data, start)
        (cell,) = struct.unpack_from("<f", data, start + 16)
        if all(_pow2(v) for v in (lx, ly, mx, my)) and lx == ly and mx == my and 0.5 <= cell <= 512:
            size = lx * cell
            if 100 <= size <= 200_000 and 0.25 <= size / mx <= 512:
                return version, start + 20, (lx, ly), (mx, my), cell
    raise WrpError(f"OPRW version {version}: no plausible grid sizes in the header")


def _quadtree(data: bytes, at: int) -> int:
    """Offset after a quad tree: a flag byte, then a node (16-bit mask, 16 children: node or 4-byte leaf) or a leaf."""
    flag = data[at]
    at += 1
    if not flag:
        return at + 4
    stack = [0]
    (mask,) = struct.unpack_from("<H", data, at)
    at += 2
    masks = [mask]
    while stack:
        i = stack[-1]
        if i == 16:
            stack.pop()
            masks.pop()
            if stack:
                stack[-1] += 1
            continue
        if masks[-1] & (1 << i):
            (mask,) = struct.unpack_from("<H", data, at)
            at += 2
            stack.append(0)
            masks.append(mask)
            if len(stack) > 16:
                raise WrpError("quad tree nested too deep")
        else:
            at += 4
            stack[-1] += 1
    return at


def _plausible_heights(raw: bytes, count: int) -> np.ndarray | None:
    h = np.frombuffer(raw, dtype="<f4", count=count)
    if not np.isfinite(h).all():
        return None
    lo, hi = float(h.min()), float(h.max())
    if lo < -3000 or hi > 9000 or hi - lo < 0.01:
        return None
    return h


def _packed(data: bytes, at: int, length: int) -> tuple[bytes, int] | None:
    """An array of `length` bytes at `at`: stored when shorter than 1024 bytes, LZO1X otherwise; None if not."""
    if length < 1024:
        return data[at : at + length], at + length
    for skip in (0, 1):  # newer versions put a compression flag byte first
        try:
            out, used = lzo1x_decode(data, length, at + skip)
            return out, at + skip + used
        except CompressionError:
            continue
    return None


def _find_heights(data: bytes, at: int, layer: int, grid: int, notes: list[str]) -> tuple[np.ndarray, int]:
    """Step over the packed arrays after the quad trees to the height grid."""
    layer_n, grid_n = layer * layer, grid * grid
    sizes = [grid_n * 4, layer_n, grid_n, layer_n * 2, grid_n * 2, layer_n * 4]
    for _array in range(8):
        for length in dict.fromkeys(sizes):
            got = _packed(data, at, length)
            if got is None:
                continue
            raw, nxt = got
            if length == grid_n * 4:
                h = _plausible_heights(raw, grid_n)
                if h is not None:
                    return h.reshape(grid, grid).astype(np.float32), nxt
            notes.append(f"stepped over a packed array of {length:,} bytes")
            at = nxt
            break
        else:
            raise WrpError(f"no packed array of a known size at {at:#x}; the heights were not found")
    raise WrpError("the heights were not found within eight packed arrays")


def _find_models(data: bytes, at: int) -> tuple[list[str], int]:
    """The model list: a 32-bit count and that many zero-terminated `.p3d` paths."""
    search = at
    while True:
        hit = data.find(b".p3d\0", search)
        if hit < 0:
            hit = data.find(b".P3D\0", search)
            if hit < 0:
                raise WrpError("no model list (.p3d paths) after the heights")
        start = hit
        while start > at and 0x20 <= data[start - 1] < 0x7F:
            start -= 1
        if start - 4 >= at:
            (count,) = struct.unpack_from("<I", data, start - 4)
            if 0 < count < 1_000_000:
                models, p = [], start
                for _ in range(count):
                    end = data.find(b"\0", p)
                    if end < 0:
                        break
                    name = data[p:end].decode("latin-1")
                    if not name.lower().endswith(".p3d"):
                        break
                    models.append(name)
                    p = end + 1
                if len(models) == count:
                    return models, p
        search = hit + 5


def _find_objects(data: bytes, at: int, n_models: int, size: float, notes: list[str]) -> np.ndarray:
    """The longest run of placed-object records after the model list (60- or 56-byte records)."""
    best = (0, 0, 0)  # run length, start offset, stride
    view = np.frombuffer(data, dtype=np.uint8)
    margin = max(200.0, size * 0.05)
    for stride in (60, 56):
        for phase in range(stride):  # what precedes the records has any length
            start = at + phase
            count = (len(data) - start) // stride
            if count <= 0:
                continue
            rec = view[start : start + count * stride].reshape(count, stride)
            model = rec[:, 4:8].copy().view("<u4").ravel()
            m = rec[:, 8:56].copy().view("<f4").reshape(count, 12)
            with np.errstate(all="ignore"):
                axes = np.sqrt((m[:, :9].reshape(count, 3, 3) ** 2).sum(-1))
                ok = (
                    (model < n_models)
                    & np.isfinite(m).all(1)
                    & (axes > 0.05).all(1)
                    & (axes < 50).all(1)
                    & (m[:, 9] > -margin) & (m[:, 9] < size + margin)
                    & (m[:, 11] > -margin) & (m[:, 11] < size + margin)
                    & (m[:, 10] > -3000) & (m[:, 10] < 9000)
                )
            if not ok.any():
                continue
            # longest run of consecutive valid records
            edges = np.diff(np.concatenate([[0], ok.astype(np.int8), [0]]))
            starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
            lengths = ends - starts
            k = int(lengths.argmax())
            if lengths[k] > best[0]:
                best = (int(lengths[k]), start + int(starts[k]) * stride, stride)
    run, start, stride = best
    if run == 0:
        raise WrpError("no placed objects found after the model list")
    dtype = np.dtype({"names": ["id", "model", "m"], "formats": ["<u4", "<u4", ("<f4", 12)], "offsets": [0, 4, 8], "itemsize": stride})
    objects = np.frombuffer(data, dtype=dtype, count=run, offset=start)
    notes.append(f"{run:,} placed objects in {stride}-byte records at {start:#x}")
    out = np.zeros(run, dtype=[("id", "<u4"), ("model", "<u4"), ("m", "<f4", 12)])
    out["id"], out["model"], out["m"] = objects["id"], objects["model"], objects["m"]
    return out


def read_wrp(data: bytes) -> Wrp:
    notes: list[str] = []
    version, at, (lx, ly), (mx, my), cell = read_header(data)
    size = lx * cell
    try:
        at = _quadtree(data, at)  # geography
        at = _quadtree(data, at)  # sound map
        (n_mountains,) = struct.unpack_from("<I", data, at)
        at += 4
        if n_mountains > 1_000_000:
            raise WrpError(f"{n_mountains:,} mountains: the quad trees were not read right")
        peaks = np.frombuffer(data, dtype="<f4", count=n_mountains * 3, offset=at).reshape(-1, 3)
        if n_mountains and not ((peaks[:, 0] > -1) & (peaks[:, 0] < size + 1) & (peaks[:, 2] > -1) & (peaks[:, 2] < size + 1)).all():
            raise WrpError("the mountains do not lie on the terrain: the quad trees were not read right")
        at += n_mountains * 12
        at = _quadtree(data, at)  # materials
    except (struct.error, IndexError, ValueError) as exc:
        if isinstance(exc, WrpError):
            raise
        raise WrpError(f"the quad trees end early: {exc}") from None
    heights, at = _find_heights(data, at, lx, mx, notes)
    models, at = _find_models(data, at)
    objects = _find_objects(data, at, len(models), size, notes)
    return Wrp(version, (lx, ly), (mx, my), cell, heights, models, objects, notes)


# --------------------------------------------------------------------------
# a synthetic terrain, for tests


def write_wrp(heights: np.ndarray, layer: int, cell: float, models: list[str], objects: list[tuple[int, int, list[float]]],
              version: int = 25) -> bytes:
    from .compress import lzo1x_literal

    grid = heights.shape[0]
    out = bytearray(b"OPRW" + struct.pack("<I", version))
    if version >= 25:
        out += struct.pack("<I", 107410)
    out += struct.pack("<4If", layer, layer, grid, grid, cell)
    # geography: a node with two child nodes, the rest leaves; sound: a single leaf
    out += b"\x01" + struct.pack("<H", 0b101)
    for i in range(16):
        if i in (0, 2):
            out += struct.pack("<H", 0) + b"\0\0\0\0" * 16
        else:
            out += b"\0\0\0\0"
    out += b"\x00" + b"\0\0\0\0"
    out += struct.pack("<I", 1) + struct.pack("<3f", cell, 10.0, cell)
    out += b"\x00" + b"\0\0\0\0"  # materials
    out += lzo1x_literal(bytes(layer * layer))  # a byte layer map
    out += lzo1x_literal(heights.astype("<f4").tobytes())
    out += struct.pack("<I", 0)  # no materials list
    out += struct.pack("<I", len(models)) + b"".join(m.encode() + b"\0" for m in models)
    out += b"\0" * 12  # unrelated data between the list and the objects
    for oid, model, m in objects:
        out += struct.pack("<II12fI", oid, model, *m, 0)
    out += b"\xff" * 16
    return bytes(out)

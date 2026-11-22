"""The tiled terrain (terrain.py) on made-up heights: pyramid, apron, encoding, classes, roads, object records."""

import json
import math

import numpy as np

from heat3d_ext_arma3 import terrain as T


def _grid(rows=300, cols=260, spacing=10.0):
    z, x = np.mgrid[0:rows, 0:cols].astype(np.float64)
    return (40 * np.sin(x / 23) + 30 * np.cos(z / 17) + 0.1 * x - 5).astype(np.float32)


def _write(tmp_path, grid, **kw):
    return T.write_terrain(tmp_path / "W", grid, 10.0, origin=(0.0, -(grid.shape[0] - 1) * 10.0),
                           info={"game": "test", "level": "W"}, log=lambda *_: None, **kw)


def test_pyramid_and_tiles(tmp_path):
    g = _grid()
    done = _write(tmp_path, g, sea=True)
    doc = json.loads((tmp_path / "W.terrain.json").read_text())
    data = (tmp_path / "W.terrain.bin").read_bytes()
    assert doc["format"] == "3dheat-terrain" and doc["version"] == 1 and doc["side"] == 133
    assert doc["samples"] == [260, 300] and len(data) == doc["bytes"]
    # 299 cells pad to 512 = 128 x 2^2: levels 0..2, 16 + 4 + 1 tiles
    assert [lv["tiles"] for lv in doc["levels"]] == [4, 2, 1] and done["tiles"] == 21
    assert [lv["spacing"] for lv in doc["levels"]] == [10.0, 20.0, 40.0]
    padded = T.pad(g)
    for lvl, (tx, tz) in [(0, (0, 0)), (0, (1, 2)), (1, (1, 1)), (2, (0, 0))]:
        h, c = T.read_tile(doc, data, lvl, tx, tz)
        step = 1 << lvl
        level = padded[::step, ::step]
        n = level.shape[0] - 1
        for a, b in [(0, 0), (2, 2), (66, 40), (132, 132)]:
            i = min(max(tx * 128 + a - 2, 0), n)
            j = min(max(tz * 128 + b - 2, 0), n)
            entry = doc["levels"][lvl]["index"][tz * doc["levels"][lvl]["tiles"] + tx]
            assert abs(h[b, a] - level[j, i]) <= entry[3] * 0.51 + 1e-6
        assert c.shape == (133, 133) and c.max() < len(T.CLASSES)
    # a coarse sample is the fine one under it: the levels meet without cracks
    h0, _ = T.read_tile(doc, data, 0, 0, 0)
    h1, _ = T.read_tile(doc, data, 1, 0, 0)
    assert abs(h1[2 + 10, 2 + 7] - h0[2 + 20, 2 + 14]) < 0.02
    assert doc["water"] == {"y": 0.0}
    assert doc["extent"] == [0.0, -2990.0, 2590.0, 0.0]


def test_classes_and_roads(tmp_path):
    g = np.full((129, 129), 10.0, np.float32)
    g[:, :20] = -3.0
    roads = np.array([[300.0, -600.0, 900.0, -600.0, 8.0]])
    done = _write(tmp_path, g, roads=roads)
    doc = json.loads((tmp_path / "W.terrain.json").read_text())
    h, c = T.read_tile(doc, (tmp_path / "W.terrain.bin").read_bytes(), 0, 0, 0)
    names = doc["classes"]["names"]
    assert c[2 + 60, 2 + 5] == names.index("sea floor")
    assert c[2 + 100, 2 + 60] == names.index("low")
    # the road at z = -600 is row 128 - 60 = 68 (rows run south from z = -1280)
    assert c[2 + 68, 2 + 60] == names.index("road") and c[2 + 75, 2 + 60] != names.index("road")
    assert done["road samples"] > 50
    assert len(doc["classes"]["colours"]) == len(names)


def test_object_records(tmp_path):
    g = np.zeros((257, 257), np.float32)
    yaw = math.radians(30)
    c, s = math.cos(yaw), math.sin(yaw)
    axes = np.array([[[c * 2, 0, -s * 2], [0, 2, 0], [s * 2, 0, c * 2]]])  # Ry(30 deg), scale 2
    rec = T.instance_records(np.array([[1500.0, 12.5, -900.0]]), axes, np.array([0], np.uint16))
    assert rec[0]["yaw"] == round(65536 / 12) and rec[0]["scale"] == 8192 and rec[0]["ux"] == 0
    recs = np.concatenate([rec, T.instance_records(np.array([[100.0, 0.0, -2500.0]]), axes, np.array([1], np.uint16))])
    models = [{"path": "a.p3d", "box": [-1, 0, -2, 1, 3, 2]}, {"path": "b.p3d", "box": [0, 0, 0, 1, 1, 1]}]
    done = _write(tmp_path, g, objects=(recs, models, 1000.0))
    doc = json.loads((tmp_path / "W.terrain.json").read_text())
    inst = doc["instances"]
    assert done["objects"] == 2 and inst["count"] == 2 and inst["cells"] == [3, 3] and inst["record"] == 20
    data = (tmp_path / "W.terrain.bin").read_bytes()
    by_cell = {(cx, cz): (off, n) for cx, cz, off, n in inst["index"]}
    # z = -900 is 1660 m south of the north edge (z = -2560): cell row 1; x 1500: column 1
    off, n = by_cell[(1, 1)]
    got = np.frombuffer(data, T.RECORD, count=n, offset=off)
    assert n == 1 and got[0]["x"] == 1500 and got[0]["model"] == 0 and got[0]["y"] == 12.5
    off, n = by_cell[(0, 0)]
    assert np.frombuffer(data, T.RECORD, count=n, offset=off)[0]["model"] == 1

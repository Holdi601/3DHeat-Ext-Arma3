"""The readers on made-up data: every format written here by the tests' own writers, then read back."""

import hashlib
import os
import struct

import numpy as np
import pytest

from heat3d_ext_arma3 import compress, config, p3d, pbo, shp, wrp


def test_lzss_round_trip():
    data = b"abcabcabcabcabc hello hello hello" * 20 + bytes(range(256))
    packed = compress.lzss_encode(data)
    assert len(packed) < len(data)
    out, used = compress.lzss_decode(packed, len(data))
    assert out == data and used == len(packed)


def test_lzss_bad_checksum():
    packed = bytearray(compress.lzss_encode(b"hello world, hello world"))
    packed[-1] ^= 0xFF
    with pytest.raises(compress.CompressionError):
        compress.lzss_decode(bytes(packed), 24)


@pytest.mark.parametrize("n", [0, 1, 3, 4, 200, 238, 239, 273, 5000])
def test_lzo_literal_round_trip(n):
    data = os.urandom(n)
    stream = compress.lzo1x_literal(data) + b"trailing"
    out, used = compress.lzo1x_decode(stream, n)
    assert out == data and used == len(stream) - len(b"trailing")


def test_lzo_match():
    # "abcd" as literals, then a match of 4 at distance 4 (an M2 instruction), then the end marker
    stream = bytes([17 + 4]) + b"abcd" + bytes([108, 0]) + b"\x11\x00\x00"
    out, used = compress.lzo1x_decode(stream, 8)
    assert out == b"abcdabcd" and used == len(stream)


def test_lzo_wrong_length():
    with pytest.raises(compress.CompressionError):
        compress.lzo1x_decode(compress.lzo1x_literal(b"x" * 100), 99)


def test_pbo_round_trip(tmp_path):
    path = tmp_path / "test.pbo"
    files = {"config.bin": b"\0raP" + bytes(20), "data\\big.txt": b"repeat " * 500}
    pbo.write_pbo(path, files, {"prefix": "a3\\test_f", "product": "test"}, pack=("data\\big.txt",))
    p = pbo.read_pbo(path)
    assert p.prefix == "a3\\test_f"
    assert p.read("DATA/big.txt") == files["data\\big.txt"]
    assert p.entries["data\\big.txt"].packed
    assert p.sha1 == hashlib.sha1(path.read_bytes()[:-21]).digest()
    fs = pbo.FileSystem([p])
    assert "a3\\test_f\\config.bin" in fs
    assert fs.find(".txt") == ["a3\\test_f\\data\\big.txt"]


def test_ebo_left_alone(tmp_path):
    path = tmp_path / "secret.ebo"
    path.write_bytes(b"anything")
    with pytest.raises(pbo.Encrypted):
        pbo.read_pbo(path)
    fs = pbo.FileSystem.of_folders([tmp_path])
    assert str(path) in fs.skipped and not fs.pbos
    assert path.read_bytes() == b"anything"  # untouched


def test_rap_read_and_inheritance():
    tree = {
        "class CfgPatches": {"class A3_Test": {"requiredAddons": ["A3_Base"], "units": ["B_Test_F"]}},
        "class CfgVehicles": {
            "class All": {"scope": 0, "side": 3},
            "class Car": {},
            "class Car_F: All": {"simulation": "carx", "class Turrets": {"class MainTurret": {"gunnerName": "gunner"}}},
            "class B_Test_F: Car_F": {"scope": 2, "displayName": "$STR_test_car", "speed": 1.5, "arr": [1, "two", [3.0]]},
        },
    }
    root = config.read_rap(config.write_rap(tree))
    car = root.path("CfgVehicles", "B_Test_F")
    assert car.get("scope") == 2 and car.get("side") == 3 and car.get("simulation") == "carx"
    assert car.get("speed") == pytest.approx(1.5)
    assert car.get("arr") == [1, "two", [3.0]]
    assert car.ancestors() == ["B_Test_F", "Car_F", "All"]
    assert car.child("Turrets").child("MainTurret").get("gunnerName") == "gunner"
    strings = config.read_stringtable(b'<Project><Package><Key ID="STR_test_car"><Original>Testwagen</Original>'
                                      b"<English>Test car</English></Key></Package></Project>")
    assert config.localise(car.get("displayName"), strings) == "Test car"


def test_merge_follows_required_addons():
    base = config.read_rap(config.write_rap({"class CfgPatches": {"class A3_Base": {}},
                                             "class CfgWorlds": {"class Altis": {"description": "base", "mapSize": 100}}}))
    patch = config.read_rap(config.write_rap({"class CfgPatches": {"class A3_Patch": {"requiredAddons": ["A3_Base"]}},
                                              "class CfgWorlds": {"class Altis": {"description": "patched"}}}))
    root = config.merged([("patch", patch), ("base", base)])  # given out of order
    altis = root.path("CfgWorlds", "Altis")
    assert altis.get("description") == "patched" and altis.get("mapSize") == 100


def _terrain(grid=64, layer=32, cell=10.0):
    size = layer * cell
    yy, xx = np.mgrid[0:grid, 0:grid] * (size / grid)
    h = 60 * np.exp(-(((xx - size / 2) ** 2 + (yy - size / 2) ** 2) / (size / 4) ** 2)) - 6
    return h.astype(np.float32), size


def _object(oid, model, east, up, north, yaw=0.0):
    c, s = np.cos(yaw), np.sin(yaw)
    return (oid, model, [c, 0, -s, 0, 1, 0, s, 0, c, east, up, north])


@pytest.mark.parametrize("version", [24, 25])
def test_wrp_synthetic(version):
    h, size = _terrain()
    objects = [_object(i, i % 2, 20 + i, 1, 30 + 2 * i, 0.3 * i) for i in range(40)]
    data = wrp.write_wrp(h, 32, 10.0, ["a3\\s\\house.p3d", "a3\\plants_f\\tree.p3d"], objects, version=version)
    t = wrp.read_wrp(data)
    assert t.map_size == (64, 64) and t.size == pytest.approx(320) and t.spacing == pytest.approx(5)
    assert np.allclose(t.heights, h)
    assert t.models == ["a3\\s\\house.p3d", "a3\\plants_f\\tree.p3d"]
    assert len(t.objects) == 40 and (t.objects["model"] == np.arange(40) % 2).all()
    assert np.allclose(t.objects["m"][5, 9:], [25, 1, 40])


def test_wrp_rejects_other_files():
    with pytest.raises(wrp.WrpError):
        wrp.read_wrp(b"8WVR" + bytes(100))
    with pytest.raises(wrp.WrpError):
        wrp.read_wrp(b"OPRW" + struct.pack("<I", 24) + bytes(100))


@pytest.mark.parametrize("version", [50, 68, 73])
def test_odol_bounds(version):
    lo, hi = p3d.bounds(p3d.write_odol((-2, 0, -4), (2, 6, 4), version=version))
    assert np.allclose(lo, (-2, 0, -4)) and np.allclose(hi, (2, 6, 4))


def test_mlod_bounds():
    pts = np.array([[-1, 0, -2, 0], [3, 4, 5, 0]], dtype="<f4")
    data = b"MLOD" + struct.pack("<II", 257, 1) + b"P3DM" + struct.pack("<6I", 28, 256, 2, 0, 0, 0) + pts.tobytes()
    lo, hi = p3d.bounds(data)
    assert np.allclose(lo, (-1, 0, -2)) and np.allclose(hi, (3, 4, 5))
    assert p3d.bounds(b"ODOL" + bytes(40)) is None


def test_shapefile_round_trip():
    lines = [[(200010.0, 20.0), (200100.0, 20.0)], [(200050.0, 10.0), (200050.0, 90.0), (200060.0, 95.0)]]
    s, d = shp.write_shapefile(lines, [{"ID": "1", "WIDTH": "8"}, {"ID": "2", "WIDTH": ""}])
    got = shp.read_polylines(s)
    assert [list(map(tuple, part)) for rec in got for part in rec] == lines
    assert shp.read_dbf(d) == [{"ID": "1", "WIDTH": "8"}, {"ID": "2", "WIDTH": ""}]

"""The commands end to end on a made-up Arma 3 install (no game files): find, status, world, all, catalog."""

import json

import numpy as np
import pytest

from heat3d_ext_arma3 import cli, config, p3d, pbo, shp, wrp
from heat3d_ext_arma3.library import ensure

from .test_formats import _object, _terrain


def _install(root, world_version=25):
    addons = root / "Addons"
    addons.mkdir(parents=True)
    h, size = _terrain()
    objects = [_object(i, i % 2, 40 + 5 * (i % 10), 10, 40 + 5 * (i // 10)) for i in range(30)]
    terrain = wrp.write_wrp(h, 32, 10.0, ["\\a3\\structures_f\\house.p3d", "a3\\plants_f\\tree.p3d"], objects, version=world_version)
    roads, table = shp.write_shapefile([[(200010.0, 160.0), (200300.0, 160.0)]], [{"ID": "1", "WIDTH": "8"}])
    world_cfg = config.write_rap({
        "class CfgPatches": {"class A3_Map_Test": {"requiredAddons": []}},
        "class CfgWorlds": {
            "class CAWorld": {},
            "class Test: CAWorld": {
                "description": "$STR_test_world", "worldName": "\\a3\\map_test\\test.wrp",
                "ilsPosition": [100.0, 100.0], "ilsDirection": [0.0, 0.08, 1.0],
                "class SecondaryAirports": {"class Strip1": {"ilsPosition": [250.0, 60.0], "ilsDirection": [1.0, 0.08, 0.0]}},
                "class Names": {
                    "class Town1": {"name": "Testville", "position": [160.0, 160.0], "type": "NameCity", "radiusA": 100.0, "radiusB": 80.0},
                    "class Mil1": {"name": "Military base", "position": [50.0, 250.0], "type": "NameLocal"},
                    "class Hill1": {"name": "", "position": [150.0, 150.0], "type": "Hill"},
                },
            },
        },
    })
    pbo.write_pbo(addons / "map_test.pbo", {
        "test.wrp": terrain, "config.bin": world_cfg, "data\\roads\\roads.shp": roads, "data\\roads\\roads.dbf": table,
        "stringtable.xml": b'<Project><Package><Key ID="STR_test_world"><English>Test Island</English></Key></Package></Project>',
    }, {"prefix": "a3\\map_test"})
    pbo.write_pbo(addons / "structures_f.pbo", {"house.p3d": p3d.write_odol((-4, 0, -5), (4, 6, 5))}, {"prefix": "a3\\structures_f"})
    vehicles = config.write_rap({
        "class CfgPatches": {"class A3_Test_Vehicles": {"requiredAddons": ["A3_Map_Test"]}},
        "class CfgFactionClasses": {"class BLU_F": {"displayName": "NATO", "side": 1}},
        "class CfgVehicles": {
            "class All": {}, "class AllVehicles: All": {}, "class Land: AllVehicles": {},
            "class LandVehicle: Land": {}, "class Tank: LandVehicle": {"simulation": "tankx"},
            "class Tank_F: Tank": {}, "class MBT_01_base_F: Tank_F": {"scope": 0},
            "class B_MBT_01_cannon_F: MBT_01_base_F": {"scope": 2, "displayName": "M2A1 Slammer", "side": 1,
                                                        "faction": "BLU_F", "model": "\\A3\\armor_f\\slammer.p3d"},
            "class Man: Land": {"simulation": "soldier"}, "class B_Soldier_F: Man": {"scope": 2, "displayName": "Rifleman"},
        },
        "class CfgWeapons": {"class Rifle_Base_F": {"type": 1}, "class arifle_MX_F: Rifle_Base_F": {"scope": 2, "displayName": "MX 6.5 mm"}},
    })
    pbo.write_pbo(addons / "vehicles_f.pbo", {"config.bin": vehicles}, {"prefix": "a3\\vehicles_f"})
    (root / "Expansion" / "Addons").mkdir(parents=True)
    (root / "Expansion" / "Addons" / "locked.ebo").write_bytes(b"encrypted")
    return h


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_no_install(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ARMA3_PATH", raising=False)
    code, _out, err = _run(capsys, "find", "--install", str(tmp_path / "nothing"), "--levels", str(tmp_path / "lv"))
    assert "--install" in err
    if code == 0:
        pytest.skip("a real Arma 3 install was found")
    assert code == 2


def test_end_to_end(tmp_path, capsys):
    game, levels = tmp_path / "Arma 3", tmp_path / "levels"
    _install(game)
    common = ["--install", str(game), "--levels", str(levels)]
    code, out, _ = _run(capsys, "find", *common)
    assert code == 0 and "new" in out and "Test" in out and "Apex" in out
    code, out, _ = _run(capsys, "status", "--json", *common)
    doc = json.loads(out)
    assert [(w["id"], w["state"]) for w in doc["worlds"]] == [("Test", "new")]
    # the shape the extractors manager reads
    g = doc["games"][0]
    assert g["game"] == "arma3" and g["found"] is True and g["counts"]["new"] == 1
    assert g["maps"] == [{"id": "Test", "title": "Test Island", "state": "new"}]
    assert any(s["path"].endswith("locked.ebo") for s in doc["skipped"])
    code, out, _ = _run(capsys, "worlds", *common)
    assert "Test Island" in out
    if not ensure():
        pytest.skip("3DHeat GameFiles not available")
    code, out, err = _run(capsys, "all", *common)
    assert code == 0, err
    assert (levels / "Test.glb").stat().st_size > 1000
    info = json.loads((levels / "Test.json").read_text())
    assert info["worldName"] == "Test" and info["frame"]["z"] == "south"
    lay = json.loads((levels / "Test.layouts.json").read_text())
    objs = lay["layouts"][0]["objectives"]
    town = next(o for o in objs if o["type"] == "town")
    assert town["name"] == "Testville" and town["radius"] == 100 and town["position"][2] == -160
    assert any(o["type"] == "base" and o["kind"] == "military" for o in objs)
    assert sum(o["type"] == "airfield" for o in objs) == 2
    assert "structure" in out and "15 boxes" in out  # the trees are left out without --trees
    code, out, _ = _run(capsys, "status", "--json", *common)
    assert json.loads(out)["worlds"][0]["state"] == "current"
    # a game update changes the terrain's archive: status calls it changed
    h = _terrain()[0] + 1
    data = wrp.write_wrp(h, 32, 10.0, ["\\a3\\structures_f\\house.p3d"], [_object(0, 0, 50, 10, 50)])
    old = pbo.read_pbo(game / "Addons" / "map_test.pbo")
    files = {e.name: old.read(e.name) for e in old.entries.values()}
    files["test.wrp"] = data
    pbo.write_pbo(game / "Addons" / "map_test.pbo", files, old.properties)
    code, out, _ = _run(capsys, "status", "--json", *common)
    assert json.loads(out)["worlds"][0]["state"] == "changed"
    manifest = json.loads((levels / "manifest.json").read_text())
    entry = manifest["levels"]["Test"]
    assert entry["extractor"].startswith("heat3d-arma3-maps") and entry["sources"][0]["hash"]


def test_extract_progress_json(tmp_path, capsys, monkeypatch):
    if not ensure():
        pytest.skip("3DHeat GameFiles not available")
    game, levels = tmp_path / "Arma 3", tmp_path / "lv"
    _install(game)
    monkeypatch.setenv("HEAT3D_LEVELS", str(levels))  # as the manager starts it
    code, out, _ = _run(capsys, "extract", "--game", "arma3", "--new", "--progress-json", "--install", str(game))
    events = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert code == 0
    assert events[0] == {"event": "start", "total": 1}
    assert events[1]["event"] == "map" and events[1]["id"] == "Test" and events[1]["state"] == "ok"
    assert events[-1] == {"event": "done", "ok": 1, "failed": 0}
    assert (levels / "arma3" / "Test.glb").is_file()
    code, out, _ = _run(capsys, "status", "--json", "--install", str(game))
    m = json.loads(out)["games"][0]["maps"][0]
    assert m["state"] == "current" and m["extracted_version"]
    code, out, _ = _run(capsys, "extract", "nowhere", "--progress-json", "--install", str(game))
    assert code == 2 and '"error"' in out


def test_status_json_without_install(tmp_path, capsys):
    code, out, _ = _run(capsys, "status", "--json", "--install", str(tmp_path / "none"), "--levels", str(tmp_path))
    doc = json.loads(out)
    if doc["games"][0]["found"]:
        pytest.skip("a real Arma 3 install was found")
    assert code == 0 and doc["games"][0]["maps"] == []


def test_level_geometry(tmp_path, capsys):
    if not ensure():
        pytest.skip("3DHeat GameFiles not available")
    from heat3d_ext_arma3.game import Game
    from heat3d_ext_arma3.install import Install
    from heat3d_ext_arma3.level import export

    root = tmp_path / "Arma 3"
    h = _install(root)
    g = Game.open(Install(root, "1", "test"))
    report, files, terrain = export(g, g.find_world("Test"), tmp_path / "out", log=lambda *_: None)
    assert report.water > 0 and report.roads == 1 and report.structures == 15
    assert report.skipped == {"vegetation (without --trees)": 15}
    assert np.allclose(terrain.heights, h)


def test_catalog(tmp_path, capsys):
    game, levels = tmp_path / "Arma 3", tmp_path / "levels"
    _install(game)
    code, out, _ = _run(capsys, "catalog", "--install", str(game), "--levels", str(levels))
    assert code == 0
    items = json.loads((levels / "catalog.json").read_text())["items"]
    by_id = {i["id"]: i for i in items}
    slammer = by_id["B_MBT_01_cannon_F"]
    assert slammer["name"] == "M2A1 Slammer" and slammer["key"] == "Merkava Mk IV"
    assert slammer["side"] == "west" and slammer["nation"] == "NATO" and slammer["class"] == "mbt" and slammer["domain"] == "ground"
    assert "B_Soldier_F" not in by_id  # men are not vehicles
    assert by_id["arifle_MX_F"]["class"] == "rifle"
    assert by_id["BLU_F"]["kind"] == "faction" and by_id["BLU_F"]["side"] == "west"


def test_identify(tmp_path, capsys):
    game = tmp_path / "Arma 3"
    _install(game)
    code, out, _ = _run(capsys, "identify", str(game / "Addons" / "map_test.pbo"))
    assert code == 0 and "a3\\map_test" in out
    code, out, _ = _run(capsys, "identify", str(game / "Expansion" / "Addons" / "locked.ebo"))
    assert code == 0 and "encrypted" in out

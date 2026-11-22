"""The binarised model reader and the model pack, on a made-up model (`odol_fixture.py`; no game file)."""

import json
import struct

import numpy as np
import pytest

from heat3d_ext_arma3 import config, models, odol, pbo

from .odol_fixture import build_model, build_paa
from .test_cli import _install, _run

VEHICLE = {
    "scope": 2, "displayName": "M2A1 Slammer", "side": 1, "faction": "BLU_F", "model": "\\A3\\armor_f\\slammer.p3d",
    "armor": 200.0,
    "class HitPoints": {
        "class HitHull": {"armor": 1.8, "name": "hit_hull_point", "armorComponent": "hit_hull", "radius": 0.3,
                          "passThrough": 1.0},
        "class HitEngine": {"armor": 0.4, "name": "hit_engine_point", "radius": 0.3, "passThrough": 0.2},
    },
    "class Turrets": {"class MainTurret": {"weapons": ["cannon_120mm"], "primaryGunner": 1, "minElev": -7.0,
                                           "maxElev": 20.0,
                                           "class HitPoints": {"class HitTurret": {"armor": 0.3, "name": "x"}}}},
    "class AnimationSources": {"class showBags": {"source": "user", "initPhase": 0.0}},
}


def _cfg(vehicle=VEHICLE):
    root = config.read_rap(config.write_rap({"class CfgVehicles": {
        "class All": {}, "class Tank: All": {"simulation": "tankx"}, "class B_Test_F: Tank": vehicle}}))
    return root.path("CfgVehicles", "B_Test_F")


def _unit(cls):
    return models.Unit("B_Test_F", "a3\\armor_f\\slammer", "M2A1 Slammer", "mbt", "ground", "Merkava Mk IV", "west", cls)


def test_read_model():
    m = odol.read_model(build_model())
    assert m.version == 73 and len(m.resolutions) == 4
    assert [odol.lod_kind(r) for r in m.resolutions] == ["visual", "fire geometry", "hit points", "land contact"]
    assert m.find("fire geometry") == 1 and m.find("land contact") == 3
    assert m.skeleton[1] == ("otochlaven", "otocvez") and m.descendants(0) == {0, 1}
    assert [a.name for a in m.anims] == ["mainTurret", "mainGun", "hideBags"] and m.anims[2].type == odol.HIDE
    assert m.anim_bones[0][1][0] == 1 and m.anim_bones[0][2][1] is None
    lod = m.lod(0)
    assert len(lod.vertices) == 43 and len(lod.faces) == 61 and lod.textures == ["a3\\armor_f\\data\\body_co.paa"]
    assert lod.bones[8] == 0 and lod.bones[0] == -1  # the turret's first vertex follows otocvez
    assert lod.materials[0].stages[0] == "a3\\armor_f\\data\\body_nohq.paa"
    assert [(s.face_from, s.face_to) for s in lod.sections] == [(0, 60), (60, 61)]
    fire = m.lod(1)
    assert list(fire.selections["hit_hull"].faces) == list(range(12))
    assert fire.materials[1].name.endswith("armour_plate_40mm.rvmat")
    assert len(m.lod(2).selections["hit_engine_point"].vertices) == 2


def test_read_model_rejects():
    with pytest.raises(odol.OdolError):
        odol.read_model(b"MLOD" + b"\0" * 64)
    data = bytearray(build_model())
    data[4:8] = struct.pack("<I", 49)
    with pytest.raises(odol.OdolError):
        odol.read_model(bytes(data))
    with pytest.raises(odol.OdolError):  # cut short: a level's end lies past the file
        odol.read_model(build_model()[:-20])


def test_build_parts_joints_and_frame():
    m = odol.read_model(build_model())
    b = models.build(m, _unit(_cfg()), lambda tex: (0.8, 0.4, 0.2, 1.0), {})
    parts = b.lods[0]
    assert sorted(parts) == ["gear", "gun", "hull", "turret"]  # the bags are hidden by default, the proxy left out
    hull = parts["hull"][0]
    # the figure frame: x forward (the binarised model's -z), y up from the origin (one metre under the centre)
    assert hull[:, 0].max() == pytest.approx(3.0) and hull[:, 1].min() == pytest.approx(0.0)
    assert parts["gun"][0][:, 0].max() == pytest.approx(4.5)
    assert parts["gear"][0][:, 2].min() == pytest.approx(-1.8)  # x left in the model: z right in the figure
    assert b.joints["turret"]["joint"] == "yaw" and b.joints["turret"]["pivot"] == [0.0, 1.0, 0.0]
    assert b.joints["gun"] == {"joint": "pitch", "pivot": [1.0, 1.9, 0.0], "parent": "turret", "limits": (-7.0, 20.0)}
    assert (parts["hull"][2][:, :3] == [204, 102, 51]).all()
    # outward faces: the hull's volume is positive in the figure frame
    v, t, _c = parts["hull"]
    a, bb, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    assert np.einsum("ij,ij->i", a, np.cross(bb, c)).sum() > 0
    dm = {name: extras for name, _v, _t, extras in b.dm}
    assert dm["HitHull"]["component"] == "hit_hull" and dm["HitHull"]["hp"] == 360.0
    assert dm["HitEngine"]["zone"] == "spheres" and dm["HitEngine"]["group"] == "engine"
    assert dm["armour_plate_40mm.hull"]["mm"] == 40.0
    shown = dict(VEHICLE, **{"class AnimationSources": {"class showBags": {"source": "user", "initPhase": 1.0}}})
    b2 = models.build(m, _unit(_cfg(shown)), lambda tex: None, {})
    assert len(b2.lods[0]["hull"][0]) > len(parts["hull"][0])  # the bags shown


def test_hidden_bones_rules():
    m = odol.read_model(build_model())
    assert models.hidden_bones(m, 0, {}) == {3}
    assert models.hidden_bones(m, 0, {"showbags": 1.0}) == set()


def _model_install(root):
    _install(root)
    pbo.write_pbo(root / "Addons" / "armor_f.pbo", {"slammer.p3d": build_model(), "data\\body_co.paa": build_paa()},
                  {"prefix": "a3\\armor_f"})
    vehicles = config.write_rap({
        "class CfgPatches": {"class A3_Test_Armor": {"requiredAddons": ["A3_Test_Vehicles"]}},
        "class CfgVehicles": {"class B_MBT_01_cannon_F: MBT_01_base_F": {
            "hiddenSelections": ["camo1"], "hiddenSelectionsTextures": ["\\a3\\armor_f\\data\\body_co.paa"],
            "class HitPoints": VEHICLE["class HitPoints"], "class Turrets": VEHICLE["class Turrets"]}},
    })
    pbo.write_pbo(root / "Addons" / "armor_f_cfg.pbo", {"mbt_01\\config.bin": vehicles}, {"prefix": "a3\\armor_f_cfg"})


def _glb(path):
    b = path.read_bytes()
    n = struct.unpack_from("<I", b, 12)[0]
    return json.loads(b[20:20 + n])


def test_models_end_to_end(tmp_path, capsys, monkeypatch):
    game, out = tmp_path / "Arma 3", tmp_path / "levels" / "model-packs" / "arma3"
    _model_install(game)
    common = ["--install", str(game), "--out", str(out)]
    code, text, _ = _run(capsys, "models", "--check", "--json", *common)
    doc = json.loads(text)
    assert code == 1 and doc["vehicles"] == 1 and doc["missing"] == ["B_MBT_01_cannon_F"]
    code, text, err = _run(capsys, "models", "--json", *common)
    assert code == 0, err
    rep = json.loads(text)
    assert rep["written"] == 1 and rep["coverage"]["models"] == 1 and not rep["failed"]
    pack = json.loads((out / "model-pack.json").read_text(encoding="utf-8"))
    assert pack["format"] == "3dheat-model-pack" and pack["game"] == "arma3"
    e = pack["units"]["B_MBT_01_cannon_F"]
    assert e["name"] == "Merkava Mk IV" and "merkava mk.4" in e["aliases"] and e["names"][0] == "M2A1 Slammer"
    assert e["domain"] == "ground" and e["anchor"] == "ground" and e["parts"] == ["gear", "gun", "hull", "turret"]
    assert e["joints"]["turret"]["joint"] == "yaw" and e["pitchLimits"] == [-7.0, 20.0]
    g = _glb(out / e["file"])
    names = [n["name"] for n in g["nodes"]]
    assert names[0] == "unit" and {"lod0", "lod1", "lod2", "dm", "dm:HitHull"} <= set(names)
    assert models.Colours(pbo.FileSystem.of_folders([game / "Addons"]))("a3\\armor_f\\data\\body_co.paa") == pytest.approx(
        (200 / 255, 100 / 255, 50 / 255, 1.0))  # the texture's average colour tag
    hits = json.loads((out / "hit-parts.json").read_text(encoding="utf-8"))
    assert hits["format"] == "3dheat-hit-parts" and "HitHull" in hits["names"]
    code, text, _ = _run(capsys, "models", "--check", "--json", *common)
    assert code == 0 and json.loads(text)["models"] == 1
    # the extractors manager's verb, one JSON line a vehicle; a current model is left as it is
    monkeypatch.setenv("HEAT3D_LEVELS", str(tmp_path / "levels"))
    code, text, _ = _run(capsys, "extract", "--models", "--progress-json", "--install", str(game))
    events = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
    assert code == 0 and events[0] == {"event": "start", "total": 1}
    assert events[1]["state"] == "ok" and events[1]["detail"] == "unchanged" and events[-1]["ok"] == 1

"""The content hooks (catalogue, model pack, item ids) and the app's record of terrains, on a made-up install."""

import json

from heat3d_ext_arma3 import LEVEL_FORMAT, __version__, cli, pbo, standalone_spec as spec
from heat3d_ext_arma3.app.manifest import Manifest
from heat3d_ext_arma3.cli import _installed
from heat3d_ext_arma3.game import Game
from heat3d_ext_arma3.install import find_install
from heat3d_ext_arma3.manifest import record
from heat3d_ext_arma3.track import adopt, track

from .test_cli import _install


def _game(root):
    return Game.open(find_install(root)[0])


def test_contents_and_items(tmp_path):
    root = tmp_path / "Arma 3"
    _install(root)
    contents = {c.id: c for c in spec._contents(root)}
    assert set(contents) == {"catalog", "models"}
    assert contents["catalog"].kind == "catalog" and contents["models"].kind == "models"
    # every readable archive is a source (its signature when it has one); the encrypted one is not
    names = {p.name for p in contents["catalog"].sources}
    assert {"map_test.pbo", "vehicles_f.pbo"} <= names and "locked.ebo" not in names
    (root / "Addons" / "vehicles_f.pbo.a3.bisign").write_bytes(b"sig")
    assert "vehicles_f.pbo.a3.bisign" in {p.name for p in spec._contents(root)[0].sources}
    items = spec._catalog_items(root)
    assert "B_MBT_01_cannon_F" in items["vehicle"] and "arifle_MX_F" in items["weapon"]
    assert "ItemMap" in items["equipment"] and "B_AssaultPack_rgr" in items["backpack"]
    assert "B_65x39_Caseless" in items["ammo"] and "30Rnd_65x39_caseless_mag" in items["magazine"]
    g = spec.SPEC.games[0]
    assert g.contents is spec._contents and g.catalog_items is spec._catalog_items and g.adopt is spec._adopt


def test_extract_catalog_content(tmp_path):
    root, out = tmp_path / "Arma 3", tmp_path / "levels" / "arma3"
    _install(root)
    cat = next(c for c in spec._contents(root) if c.id == "catalog")
    files = spec._extract_content(root, cat, out, print)
    assert files == [out / "catalog.json"]
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    assert any(i["kind"] == "equipment" for i in doc["items"])
    assert spec._models_folder(out) == tmp_path / "levels" / "model-packs" / "arma3"


def _fake_extract(root, folder):
    """A terrain as `world` leaves it in manifest.json, without building the level (that needs GameFiles)."""
    game = _game(root)
    row = _installed(game)[0]
    folder.mkdir(parents=True, exist_ok=True)
    glb = folder / f"{row['id']}.glb"
    glb.write_bytes(b"glTF")
    record(folder, "arma3", "heat3d-arma3-maps", "0.6.0", LEVEL_FORMAT, row["id"], build=game.install.build,
           sources=row["sources"], files=[glb])
    return game, row["id"]


def test_track_and_adopt(tmp_path, capsys):
    root, folder = tmp_path / "Arma 3", tmp_path / "levels" / "arma3"
    _install(root)
    game, wid = _fake_extract(root, folder)
    m = next(x for x in spec._maps(root) if x.id == wid)
    assert adopt(root, m, folder) == ("0.6.0", "current")
    assert track(game, folder, log=lambda s: None) == {wid: "current"}
    entry = Manifest(folder, "heat3d-arma3").entries[wid]
    assert entry["extractor_version"] == "0.6.0" and entry["outputs"] == [f"arma3/{wid}.glb"]
    assert track(game, folder, log=lambda s: None) == {}  # recorded once
    # a game update after the extract: adopt and the record both say changed
    old = pbo.read_pbo(root / "Addons" / "map_test.pbo")
    files = {e.name: old.read(e.name) for e in old.entries.values()}
    files["stringtable.xml"] = files["stringtable.xml"] + b" "
    pbo.write_pbo(root / "Addons" / "map_test.pbo", files, old.properties)
    assert adopt(root, m, folder) == ("0.6.0", "changed")
    (folder / ".heat3d-extract.json").unlink()
    assert track(_game(root), folder, log=lambda s: None) == {wid: "changed"}
    from heat3d_ext_arma3.app.manifest import game_status

    st = game_status(spec.SPEC.games[0], root, folder.parent, "heat3d-arma3")
    assert [(s["id"], s["state"]) for s in st["maps"]] == [(wid, "changed")]


def test_world_export_records_for_the_app(tmp_path, capsys, monkeypatch):
    """`world` writes the app's record beside its own (here with the level itself stubbed out)."""
    root, folder = tmp_path / "Arma 3", tmp_path / "levels" / "arma3"
    _install(root)

    class Report:
        ground = water = roads = structures = objects = 0
        size = spacing = 1.0
        terrain = None
        skipped: dict = {}
        notes: list = []

    class Wrp:
        heights, spacing = None, 1.0

    def export(game, world, out, **kw):
        out.mkdir(parents=True, exist_ok=True)
        glb = out / f"{world.id}.glb"
        glb.write_bytes(b"glTF")
        return Report(), [glb], Wrp()

    monkeypatch.setattr("heat3d_ext_arma3.level.export", export)
    monkeypatch.setattr("heat3d_ext_arma3.layouts.build", lambda *a: {"counts": {}})
    assert cli.main(["all", "--install", str(root), "--levels", str(folder)]) == 0
    entries = Manifest(folder, "heat3d-arma3").entries
    assert list(entries) == ["Test"] and entries["Test"]["extractor_version"] == __version__

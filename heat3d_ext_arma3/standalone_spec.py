"""
The standalone app's description of this tool (app/spec.py): the Arma 3 install it reads, its terrains as maps (new
ones after an update or a new DLC are found by app/manifest.py), and how one terrain becomes a level with its
layouts; its other content (the catalogue, the vehicle model pack) and the catalogue's item ids, so new
vehicles, weapons and equipment after an update are noticed. `python -m heat3d_ext_arma3.standalone_spec` runs the app from a checkout; the release executables run
the same.
"""

from __future__ import annotations

from pathlib import Path

from . import __version__
from .app.spec import Check, GameSpec, Input, MapSource, Spec, Task


def _find(given: str | None) -> Path | None:
    from .install import find_install

    install, _looked = find_install(given)
    return install.root if install else None


def _game(root: Path):
    from .game import Game
    from .install import find_install

    install, _ = find_install(root)
    return Game.open(install) if install else None


def _maps(root: Path) -> list[MapSource]:
    game = _game(root)
    if game is None:
        return []
    return [MapSource(w.id, w.name or w.id, [p.path for p in w.sources(game.fs)], note=w.dlc_name) for w in game.worlds()]


def _extract(root: Path, m: MapSource, out: Path, log) -> list[Path]:
    from . import cli

    code = cli.main(["world", m.id, "--install", str(root), "--levels", str(out)])
    if code != 0:
        raise RuntimeError(f"heat3d-arma3-maps world exited {code}")
    return [p for p in (out / f"{m.id}.glb", out / f"{m.id}.json", out / f"{m.id}.layouts.json") if p.is_file()]


def _build(root: Path) -> str | None:
    from .install import find_install

    install, _ = find_install(root)
    return install.build if install else None


def _models_folder(levels: Path) -> Path:
    """Model packs sit beside the games' level folders: `<levels>/model-packs/arma3`."""
    base = levels.parent if levels.name.lower() == "arma3" else levels
    return base / "model-packs" / "arma3"


# ------------------------------------------------------------------ content (catalogue, model pack)

CONTENT = {"catalog": ("catalog", "Vehicles, weapons, equipment and soldiers"), "models": ("models", "Vehicle models")}


def _content_source(cid: str, title: str, sources: list[Path], kind: str) -> MapSource:
    """A MapSource of another kind than a map (the kit's `kind` field; set by hand on a kit that predates it)."""
    try:
        return MapSource(cid, title, sources, kind=kind)  # type: ignore[call-arg]
    except TypeError:
        m = MapSource(cid, title, sources)
        m.kind = kind  # type: ignore[attr-defined]
        return m


def _signatures(game) -> list[Path]:
    """What the catalogue and the model pack are read from, cheaply: every readable archive's signature file
    (a few hundred bytes that change with the archive), the archive itself where it has none."""
    out = []
    for pbo in game.fs.pbos:
        sigs = sorted(pbo.path.parent.glob(pbo.path.name + ".*.bisign"))
        out.append(sigs[0] if sigs else pbo.path)
    return sorted(out, key=lambda p: str(p).lower())


def _contents(root: Path) -> list[MapSource]:
    game = _game(root)
    if game is None:
        return []
    sources = _signatures(game)
    return [_content_source(cid, title, sources, kind) for cid, (kind, title) in CONTENT.items()]


def _extract_content(root: Path, m: MapSource, out: Path, log) -> list[Path]:
    from . import cli

    if m.id == "catalog":
        target = Path(out) / "catalog.json"
        code = cli.main(["catalog", "--install", str(root), "--levels", str(out), "-o", str(target)])
        if code != 0:
            raise RuntimeError(f"heat3d-arma3-maps catalog exited {code}")
        return [target]
    if m.id == "models":
        folder = _models_folder(Path(out))
        code = cli.main(["models", "--install", str(root), "--out", str(folder)])
        if code != 0:
            raise RuntimeError(f"heat3d-arma3-maps models exited {code}")
        return [p for p in (folder / "model-pack.json",) if p.is_file()]
    raise KeyError(f"no such content: {m.id}")


def _catalog_items(root: Path) -> dict[str, list[str]]:
    """Every catalogue id of the install by its kind (vehicle, weapon, ammo, magazine, equipment, backpack ...)."""
    from . import catalog

    game = _game(root)
    if game is None:
        return {}
    out: dict[str, list[str]] = {}
    for item in catalog.build(game):
        out.setdefault(item["kind"], []).append(item["id"])
    return {k: sorted(v) for k, v in sorted(out.items())}


def _adopt(root: Path, m: MapSource, out: Path):
    from .track import adopt

    return adopt(root, m, out)


def _game_spec(**hooks) -> GameSpec:
    """The game, with the content hooks set whether or not this copy of the kit declares them yet."""
    known = {k: v for k, v in hooks.items() if k in GameSpec.__dataclass_fields__}
    g = GameSpec(**known)
    for k, v in hooks.items():
        if k not in known:
            setattr(g, k, v)
    return g


def _checks(found: dict, levels: Path) -> list[Check]:
    root = found.get("arma3")
    if root is None:
        return []
    game = _game(root)
    n = len(game.terrains()) if game else 0
    skipped = len(game.skipped) if game else 0
    return [Check("terrains", "Terrains in the install", n > 0,
                  f"{n} found" + (f"; {skipped} encrypted or unreadable archives left alone" if skipped else ""))]


SPEC = Spec(
    id="heat3d-arma3",
    name="3DHeat Arma 3",
    repo="Holdi601/3DHeat-Ext-Arma3",
    version=__version__,
    tools={"heat3d-arma3-maps": "heat3d_ext_arma3.cli:main"},
    default_tool="heat3d-arma3-maps",
    description="Arma 3 terrains, their places, the game's catalogue and its vehicles' models for the 3DHeat viewer",
    module="heat3d_ext_arma3.standalone_spec",
    games=[_game_spec(
        key="arma3", label="Arma 3", find=_find, subdir="arma3", build=_build,
        maps=_maps, extract=_extract,
        existing_outputs=lambda out, m: [out / f"{m.id}.glb"],
        output_version="0.1.0",
        platforms=("windows-x64", "linux-x64"),
        contents=_contents, extract_content=_extract_content, catalog_items=_catalog_items, adopt=_adopt,
    )],
    checks=_checks,
    tasks=[Task(
        id="models", label="Vehicle models",
        inputs=[Input("out", "Model pack folder", default=lambda levels: str(_models_folder(levels)),
                      hint="model-packs/arma3 in the viewer's levels folder; only vehicles new or changed are built")],
        argv=lambda v, levels: ["heat3d-arma3-maps", "models", "--out", v.get("out") or str(_models_folder(levels))],
        help="Every vehicle of your install in its own shape and true size, with its turret, gun, rotors and hit "
             "zones, for 3DHeat to draw in place of a stand-in figure. Stays on this computer.",
        action="Build the models")],
    min_free_gb=4.0,
    notice="Not affiliated with or endorsed by Bohemia Interactive.",
)

if __name__ == "__main__":
    from .app.entry import main

    raise SystemExit(main(SPEC))

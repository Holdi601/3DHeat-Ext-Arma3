"""
The standalone app's description of this tool (app/spec.py): the Arma 3 install it reads, its terrains as maps (new
ones after an update or a new DLC are found by app/manifest.py), and how one terrain becomes a level with its
layouts. `python -m heat3d_ext_arma3.standalone_spec` runs the app from a checkout; the release executables run
the same.
"""

from __future__ import annotations

from pathlib import Path

from . import __version__
from .app.spec import Check, GameSpec, MapSource, Spec


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
    description="Arma 3 terrains, their places and the game's catalogue as 3D levels for the 3DHeat viewer",
    module="heat3d_ext_arma3.standalone_spec",
    games=[GameSpec(
        key="arma3", label="Arma 3", find=_find, subdir="arma3", build=_build,
        maps=_maps, extract=_extract,
        existing_outputs=lambda out, m: [out / f"{m.id}.glb"],
        output_version="0.1.0",
        platforms=("windows-x64", "linux-x64"),
    )],
    checks=_checks,
    min_free_gb=4.0,
    notice="Not affiliated with or endorsed by Bohemia Interactive.",
)

if __name__ == "__main__":
    from .app.entry import main

    raise SystemExit(main(SPEC))

"""
The extractors manager's record of what is extracted (`<levels>/arma3/.heat3d-extract.json`, app/manifest.py),
kept in step with this tool's own `manifest.json`.

The standalone app records a terrain when it extracts one. Terrains written by `heat3d-arma3-maps world` or `all`
before there was such a record are known only to `manifest.json`; without a record the app would call them
`outdated` and offer to write them again. Here:

* `kit_record` adds the app's record after this tool wrote a terrain itself (`world`, `all`, `extract`);
* `adopt` says, for a terrain the app has no record of, what this tool's own manifest knows: made by which
  version, and whether it is still current for the install (the same archives, the current level format);
* `track` writes the app's record for every terrain `manifest.json` proves current (the `track` verb), and marks the
  ones whose archives changed since as `changed`. It writes only that record, never a level.
"""

from __future__ import annotations

from pathlib import Path

SPEC_ID = "heat3d-arma3"
#: A source fingerprint no file has: an entry carrying it reads as `changed` to the app.
BEFORE = "unknown: extracted before this record existed"


def _version(extractor: str) -> str:
    """`heat3d-arma3-maps 0.6.0` -> `0.6.0`."""
    return (extractor or "").split()[-1] if extractor else ""


def _map_source(world, game):
    from .app.spec import MapSource

    return MapSource(world.id, world.name or world.id, [p.path for p in world.sources(game.fs)], note=world.dlc_name)


def kit_record(game, world, folder: Path, files: list[Path], version: str) -> None:
    """The app's record of one terrain this tool just wrote. Never fails the export it follows."""
    try:
        from .app.manifest import Manifest

        man = Manifest(folder, SPEC_ID)
        man.record(game.install.root, _map_source(world, game), files, version, game.install.build, folder.parent)
        man.save()
    except Exception:  # noqa: BLE001 - the level is written; the record is a convenience
        pass


def own_states(game, folder: Path) -> dict[str, dict]:
    """This tool's own status rows by terrain id (`state`, `extractor`)."""
    from . import LEVEL_FORMAT
    from .cli import _installed
    from .manifest import status

    return {r["id"]: r for r in status(folder, _installed(game), LEVEL_FORMAT)}


def adopt(root: Path, m, out: Path) -> tuple[str, str] | None:
    """For a terrain the app has no record of: (version that made it, "current" | "changed"), or None."""
    from .game import Game
    from .install import find_install

    install, _ = find_install(root)
    if install is None:
        return None
    row = own_states(Game.open(install), Path(out)).get(m.id)
    if not row or not row.get("extractor"):
        return None
    if row["state"] == "current":
        return _version(row["extractor"]), "current"
    if row["state"] == "changed":
        return _version(row["extractor"]), "changed"
    return None


def track(game, folder: Path, log=print) -> dict[str, str]:
    """The app's record for every terrain `manifest.json` knows and the app does not: what each became."""
    from .app.manifest import Manifest
    from .manifest import load

    own = load(folder)["levels"]
    man = Manifest(folder, SPEC_ID)
    rows = own_states(game, folder)
    done: dict[str, str] = {}
    for world in game.worlds():
        row, entry = rows.get(world.id), own.get(world.id)
        if world.id in man.entries or not row or not entry:
            continue
        files = [folder / f for f in entry.get("files", [])]
        version = _version(entry.get("extractor", ""))
        if row["state"] == "current":
            man.record(game.install.root, _map_source(world, game), files, version, entry.get("gameBuild"),
                       folder.parent)
        elif row["state"] == "changed":
            man.record(game.install.root, _map_source(world, game), files, version, entry.get("gameBuild"),
                       folder.parent)
            for src in man.entries[world.id]["sources"].values():
                src.update(size=-1, mtime_ns=-1, sha256=BEFORE)
        else:
            continue
        done[world.id] = row["state"]
        log(f"  {row['state']:<9} {world.id}")
    if done:
        man.save()
    return done

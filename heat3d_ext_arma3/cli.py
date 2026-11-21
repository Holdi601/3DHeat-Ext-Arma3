"""
heat3d-arma3-maps                      find the game, its DLCs and terrains, and say what is not extracted yet
heat3d-arma3-maps status [--json]      every terrain: new / changed / outdated / missing / current / removed
heat3d-arma3-maps worlds               the terrains, with their world names (what recordings call them)
heat3d-arma3-maps world <world> [...]  one terrain as a level (<World>.glb, .json, .layouts.json)
heat3d-arma3-maps all [...]            every terrain status calls new, changed, outdated or missing
heat3d-arma3-maps layouts <world>      only the places (towns, military, airfields)
heat3d-arma3-maps catalog [-o FILE]    vehicles, weapons and factions as catalog.json
heat3d-arma3-maps identify <file>      what an Arma 3 file is (PBO, config, terrain, model)

heat3d-arma3-maps extract [ids] [--new] [--changed] [--outdated] [--progress-json]
                                       what the 3DHeat extractors manager runs

Common options: --install FOLDER (the Arma 3 folder, when it is not found by itself), --mod FOLDER (also read a mod
folder; repeatable), --levels FOLDER (where levels go; default $HEAT3D_LEVELS/arma3, else ./arma3).

Exit status: 0 done; 1 reading failed; 2 nothing to read (no install, no such world); the message says what to do.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import LEVEL_FORMAT, __version__

PROG = "heat3d-arma3-maps"
GAME = "arma3"


def levels_folder(args) -> Path:
    if getattr(args, "levels", None):
        return Path(args.levels)
    root = os.environ.get("HEAT3D_LEVELS", "").strip()
    return Path(root, GAME) if root else Path(GAME)


def _open(args):
    from .game import Game
    from .install import find_install

    install, looked = find_install(args.install)
    if install is None:
        print("Arma 3 not found. Looked at:", file=sys.stderr)
        for line in looked:
            print(f"  {line}", file=sys.stderr)
        print("Install Arma 3 through Steam, or pass --install <Arma 3 folder>.", file=sys.stderr)
        return None
    return Game.open(install, [Path(m) for m in args.mod or []])


def _installed(game, with_config: bool = True) -> list[dict]:
    rows = []
    for w in game.worlds(with_config=with_config):
        rows.append({"id": w.id, "wrp": w.wrp, "dlc": w.dlc_name, "name": w.name,
                     "sources": [{"path": str(p.path), "hash": p.fingerprint} for p in w.sources(game.fs)]})
    return rows


def cmd_status(args) -> int:
    from .manifest import manager_status, status

    folder = levels_folder(args)
    if args.json:
        from .install import find_install

        if find_install(args.install)[0] is None:  # the extractors manager wants an answer, not an error
            print(json.dumps(manager_status(GAME, "Arma 3", None, [], __version__, folder), indent=1))
            return 0
    game = _open(args)
    if game is None:
        return 2
    rows = status(folder, _installed(game, with_config=not args.fast), LEVEL_FORMAT)
    skipped = [{"path": p, "why": w} for p, w in sorted(game.skipped.items())]
    if args.json:
        doc = manager_status(GAME, "Arma 3", game.install.build, rows, __version__, folder)
        doc.update(install=str(game.install.root), extractor=f"{PROG} {__version__}", levelFormat=LEVEL_FORMAT,
                   worlds=rows, skipped=skipped)
        print(json.dumps(doc, indent=1))
        return 0
    print(f"Arma 3 at {game.install.root} (build {game.install.build}); levels in {folder}")
    for r in rows:
        print(f"  {r['state']:<9} {r['id']:<28} {r.get('dlc', ''):<22} {r['why']}")
    for s in skipped:
        print(f"  skipped   {Path(s['path']).name}: {s['why']}")
    return 0


def cmd_find(args) -> int:
    from .install import find_tools
    from .manifest import status

    game = _open(args)
    if game is None:
        return 2
    folder = levels_folder(args)
    print(f"Arma 3 at {game.install.root} (found via {game.install.source}, build {game.install.build})")
    dlcs = sorted({d for d, _ in game.install.data_folders()})
    from .install import DLC_NAMES

    print("  data: " + ", ".join(DLC_NAMES.get(d, d) for d in dlcs))
    tools = find_tools()
    print(f"  Arma 3 Tools: {tools if tools else 'not installed (not needed)'}")
    print(f"  {len(game.fs.pbos):,} archives read, {len(game.skipped)} left alone (encrypted or unreadable)")
    rows = status(folder, _installed(game), LEVEL_FORMAT)
    todo = [r for r in rows if r["state"] in ("new", "changed", "outdated", "missing")]
    print(f"  {len(rows)} terrains; levels in {folder}")
    for r in rows:
        print(f"    {r['state']:<9} {r['id']:<28} {r.get('dlc', '')}")
    if todo:
        print(f"{len(todo)} to extract: {PROG} all")
    else:
        print("every terrain is extracted and current")
    if not (folder / "catalog.json").exists():
        print(f"no catalogue yet: {PROG} catalog")
    return 0


def cmd_worlds(args) -> int:
    game = _open(args)
    if game is None:
        return 2
    for w in game.worlds():
        print(f"  {w.id:<28} {w.name:<28} {w.dlc_name:<22} {w.wrp}")
    return 0


def _export_one(game, world, args, folder: Path) -> int:
    from . import layouts as lay
    from .level import export
    from .manifest import record
    from .wrp import WrpError

    try:
        report, files, wrp = export(game, world, folder, max_grid=args.max_grid, trees=args.trees,
                                    structures=not args.no_structures, roads=not args.no_roads)
    except (WrpError, KeyError, ValueError) as exc:
        print(f"{world.id}: not exported: {exc}", file=sys.stderr)
        return 1
    doc = lay.build(game, world, wrp.heights, wrp.spacing)
    lpath = folder / f"{world.id}.layouts.json"
    lpath.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    files.append(lpath)
    record(folder, GAME, PROG, __version__, LEVEL_FORMAT, world.id, build=game.install.build,
           sources=[{"path": str(p.path), "hash": p.fingerprint} for p in world.sources(game.fs)], files=files,
           worldName=world.id, wrp=world.wrp, dlc=world.dlc_name, name=world.name)
    print(f"{world.id} -> {files[0]}")
    print(f"  ground     {report.ground:>10,} triangles, {report.size:,.0f} m square, {report.spacing:g} m apart")
    print(f"  water      {report.water:>10,} triangles")
    print(f"  roads      {report.roads:>10,} segments")
    print(f"  structure  {report.structures:>10,} boxes of {report.objects:,} placed objects")
    print(f"  places     {doc['counts']}")
    if report.skipped:
        print("  left out   " + ", ".join(f"{k}: {v:,}" for k, v in sorted(report.skipped.items())))
    for n in report.notes:
        print(f"  note       {n}")
    return 0


def cmd_world(args) -> int:
    game = _open(args)
    if game is None:
        return 2
    try:
        world = game.find_world(args.world)
    except LookupError as exc:
        print(exc, file=sys.stderr)
        return 2
    return _export_one(game, world, args, levels_folder(args))


def cmd_all(args) -> int:
    from .manifest import status

    game = _open(args)
    if game is None:
        return 2
    folder = levels_folder(args)
    rows = status(folder, _installed(game, with_config=True), LEVEL_FORMAT)
    todo = {r["id"] for r in rows if args.force or r["state"] in ("new", "changed", "outdated", "missing")}
    worst = 0
    for world in game.worlds():
        if world.id in todo:
            worst = max(worst, _export_one(game, world, args, folder))
    if not todo:
        print("every terrain is extracted and current")
    return worst


def cmd_extract(args) -> int:
    """The extractors manager's verb: maps by state (--new, --changed, --outdated) or by id, one JSON line each."""
    from .manifest import Progress, pick, status

    progress = Progress(args.progress_json)
    if args.game_key and args.game_key != GAME:
        progress.fail(f"no game {args.game_key!r} here; this tool reads {GAME}")
        return 2
    game = _open(args)
    if game is None:
        progress.fail("Arma 3 not found")
        return 2
    folder = levels_folder(args)
    rows = status(folder, _installed(game), LEVEL_FORMAT)
    try:
        chosen = pick(rows, args.maps, new=args.new, changed=args.changed, outdated=args.outdated)
    except LookupError as exc:
        progress.fail(str(exc))
        return 2
    worlds = {w.id: w for w in game.worlds()}
    progress.start(len(chosen))
    for i, map_id in enumerate(chosen):
        try:
            code = _export_one(game, worlds[map_id], args, folder)
            progress.map(i, map_id, code == 0, "" if code == 0 else "not exported (see the message above)")
        except Exception as exc:  # noqa: BLE001 - one map failing never stops the others
            progress.map(i, map_id, False, str(exc))
    return progress.done()


def cmd_layouts(args) -> int:
    from . import layouts as lay
    from .wrp import WrpError, read_wrp

    game = _open(args)
    if game is None:
        return 2
    try:
        world = game.find_world(args.world)
    except LookupError as exc:
        print(exc, file=sys.stderr)
        return 2
    heights, spacing = None, 1.0
    try:
        wrp = read_wrp(game.fs.read(world.wrp))
        heights, spacing = wrp.heights, wrp.spacing
    except WrpError as exc:
        print(f"heights not read ({exc}); places at height 0", file=sys.stderr)
    folder = levels_folder(args)
    folder.mkdir(parents=True, exist_ok=True)
    doc = lay.build(game, world, heights, spacing)
    out = folder / f"{world.id}.layouts.json"
    out.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    print(f"{world.id}: {doc['counts']} -> {out}")
    return 0


def cmd_catalog(args) -> int:
    import time

    from . import catalog

    game = _open(args)
    if game is None:
        return 2
    t0 = time.monotonic()
    items = catalog.build(game)
    out = Path(args.out) if args.out else levels_folder(args) / "catalog.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {"format": "3dheat-catalog", "version": 1, "game": GAME, "gameBuild": game.install.build,
           "source": f"{PROG} {__version__}", "items": items}
    tmp = out.with_suffix(".json.part")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    kinds: dict[str, int] = {}
    for it in items:
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    print(f"{len(items)} items {kinds} in {time.monotonic() - t0:.0f} s -> {out}")
    for path, why in sorted(game.config_notes.items())[:10]:
        print(f"  {Path(path).name}: {why}")
    return 0


def identify(path: Path) -> str | None:
    from . import p3d
    from .config import ConfigError, read_rap
    from .pbo import Encrypted, PboError, read_pbo
    from .wrp import WrpError, read_header

    head = path.open("rb").read(64)
    lines = [f"{path.name}  ({path.stat().st_size:,} bytes)"]
    if path.suffix.lower() == ".ebo":
        return "\n".join(lines + ["  encrypted archive (.ebo): not read"])
    if head[:4] == b"\0raP":
        try:
            root = read_rap(path.read_bytes())
        except ConfigError as exc:
            return "\n".join(lines + [f"  rapified config, not readable: {exc}"])
        return "\n".join(lines + ["  rapified config", "    classes    " + ", ".join(c.name for c in root.classes.values())[:300]])
    if head[:4] in (b"OPRW", b"8WVR"):
        try:
            v, _at, layer, grid, cell = read_header(path.read_bytes())
        except WrpError as exc:
            return "\n".join(lines + [f"  terrain, {exc}"])
        return "\n".join(lines + [f"  binarised terrain (OPRW v{v})", f"    heights    {grid[0]} x {grid[1]}, {layer[0] * cell / grid[0]:g} m apart",
                                  f"    size       {layer[0] * cell:,.0f} m square"])
    if head[:4] in (b"ODOL", b"MLOD"):
        b = p3d.bounds(path.read_bytes())
        size = "size not readable" if b is None else "size " + " x ".join(f"{v:.2f}" for v in (b[1] - b[0])) + " m"
        return "\n".join(lines + [f"  model ({head[:4].decode()}), {size}"])
    try:
        pbo = read_pbo(path)
    except Encrypted as exc:
        return "\n".join(lines + [f"  {exc}"])
    except (PboError, OSError):
        return None
    lines.append("  PBO archive")
    lines.append(f"    prefix     {pbo.prefix}")
    lines.append(f"    files      {len(pbo.entries):,}, {sum(e.packed for e in pbo.entries.values()):,} packed")
    if pbo.encrypted_entries:
        lines.append(f"    encrypted  {len(pbo.encrypted_entries):,} entries (not read)")
    lines.append(f"    sha1       {pbo.sha1.hex() if pbo.sha1 else 'none'}")
    return "\n".join(lines)


def cmd_identify(args) -> int:
    if not args.path.exists():
        print(f"no such file: {args.path}", file=sys.stderr)
        return 2
    found = identify(args.path)
    if found is None:
        print("not an Arma 3 file", file=sys.stderr)
        return 2
    print(found)
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=PROG, description="Arma 3 terrains, their places and the catalogue from your own install into 3DHeat levels.")
    ap.add_argument("--version", action="version", version=__version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--install", help="the Arma 3 folder, when it is not found by itself")
    common.add_argument("--mod", action="append", help="also read this mod folder (repeatable)")
    common.add_argument("--levels", help="where levels go (default $HEAT3D_LEVELS/arma3, else ./arma3)")
    sub = ap.add_subparsers(dest="verb")
    p = sub.add_parser("find", parents=[common], help="find the game and say what is not extracted yet")
    p.set_defaults(run=cmd_find)
    p = sub.add_parser("status", parents=[common], help="every terrain: new, changed, outdated, missing, current, removed")
    p.add_argument("--json", action="store_true", help="as JSON, for the extractors manager")
    p.add_argument("--fast", action="store_true", help="without reading the configs (world ids are file names)")
    p.set_defaults(run=cmd_status)
    p = sub.add_parser("worlds", parents=[common], help="the terrains and their world names")
    p.set_defaults(run=cmd_worlds)
    export_opts = argparse.ArgumentParser(add_help=False)
    export_opts.add_argument("--max-grid", type=int, default=4097, help="most height samples per side (default 4097)")
    export_opts.add_argument("--trees", action="store_true", help="also place vegetation (as boxes)")
    export_opts.add_argument("--no-structures", action="store_true", help="leave out the placed objects")
    export_opts.add_argument("--no-roads", action="store_true", help="leave out the road network")
    p = sub.add_parser("world", parents=[common, export_opts], help="one terrain as a level")
    p.add_argument("world", help="its world name, as `worlds` lists it")
    p.set_defaults(run=cmd_world)
    p = sub.add_parser("all", parents=[common, export_opts], help="every terrain not extracted or not current")
    p.add_argument("--force", action="store_true", help="every terrain, current or not")
    p.set_defaults(run=cmd_all)
    p = sub.add_parser("extract", parents=[common, export_opts], help="maps by state or id, for the extractors manager")
    p.add_argument("maps", nargs="*", help="map ids, as status lists them")
    p.add_argument("--game", dest="game_key", default=None, help="only this game (the extractors manager names it)")
    p.add_argument("--new", action="store_true")
    p.add_argument("--changed", action="store_true")
    p.add_argument("--outdated", action="store_true")
    p.add_argument("--progress-json", action="store_true", help="one JSON line per event (start, map, done)")
    p.set_defaults(run=cmd_extract)
    p = sub.add_parser("layouts", parents=[common], help="only a world's places as <World>.layouts.json")
    p.add_argument("world")
    p.set_defaults(run=cmd_layouts)
    p = sub.add_parser("catalog", parents=[common], help="vehicles, weapons and factions as catalog.json")
    p.add_argument("-o", "--out", help="the .json (default <levels>/catalog.json)")
    p.set_defaults(run=cmd_catalog)
    p = sub.add_parser("identify", help="what an Arma 3 file is")
    p.add_argument("path", type=Path)
    p.set_defaults(run=cmd_identify)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.verb is None:
        args = parser().parse_args(["find", *(argv if argv is not None else sys.argv[1:])])
    if args.verb in ("world", "all"):
        from .library import MISSING, ensure

        if not ensure():
            print(MISSING, file=sys.stderr)
            return 2
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())

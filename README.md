# 3DHeat Arma 3: Arma 3 terrains as 3D levels

**What is it?** This tool turns the terrains of your own Arma 3 install - the
base game's and those of the DLCs you have - into 3D levels for the
[3DHeat](https://github.com/Holdi601/3DHeat) viewer: the ground and the sea,
the road network, every placed building and object as a box of its own size,
the towns, military areas and airfields as a layout, the game's vehicles,
weapons and factions as a catalogue, and every vehicle's own model as a local
model pack.

**Why would I use it?** So recordings of Arma 3 missions (OCAP and others)
have the ground, the roads and the buildings under them, in the game's own
metres, and units are named and sorted the way the game names them.

**Example:** `heat3d-arma3-maps` -> where the game is, which DLCs it has, and
which terrains are not extracted yet; `heat3d-arma3-maps all` extracts them.

Each game reader is its own small project with its own licence file, releases
and issue tracker, so it can be installed, updated, maintained - or withdrawn
on a rights holder's request - on its own, without affecting the viewer or the
other tools. The levels it writes stay on your machine.

## Quick start

You need: Windows or Linux, [Python](https://www.python.org/downloads/) 3.10 or
later, and Arma 3 installed through Steam (found by itself in every Steam
library, also the Linux, Flatpak and Snap Steam folders a Proton install lives
in). Arma 3 Tools are not needed.

```bash
python -m pip install -e .        # from a checkout: installs heat3d-arma3-maps
heat3d-arma3-maps                 # finds the game and says what is missing
heat3d-arma3-maps all             # every terrain not extracted yet, or changed since
heat3d-arma3-maps catalog         # vehicles, weapons, factions, ammunition, magazines, vests, headgear, soldiers
heat3d-arma3-maps models          # every vehicle's model, for 3DHeat to draw
```

Levels go to `$HEAT3D_LEVELS/arma3/` when `HEAT3D_LEVELS` is set (the viewer's
levels folder), else to `arma3/` where you run it; `--levels FOLDER` says
where. A game outside the Steam libraries needs `--install "<Arma 3 folder>"`.

## The standalone program

No Python needed: the releases have `heat3d-arma3-<version>-windows-x64.zip` and `-linux-x64.tar.gz` (check them
against `SHA256SUMS`). Started without arguments, `heat3d-arma3` opens a window that walks through four
steps (find the game, choose the terrains, extract with progress and time left, done: open in 3DHeat)
in the 3DHeat look, dark or light, and offers updates (it asks first). On Windows it opens only its
window, with no console window; in a terminal it prints as usual, and `heat3d-arma3-cli.cmd` in the same
folder makes the terminal wait for it. `heat3d-arma3 status --json`, `extract --new` and `--check --json`
are for scripts; the 3DHeat app's Extractors panel installs, updates and runs it.

## Commands

| Command | Does |
|---|---|
| *(none)* or `find` | where the game is, its DLCs, every terrain and whether it is extracted; what to run next |
| `status [--json]` | every terrain: `new`, `changed` (a game or DLC update changed its archives), `outdated` (written by an older level format), `missing` (files gone), `current`, `removed` |
| `worlds` | the terrains with their world names (what the game and every recording call them), display names and DLC |
| `world <world>` | one terrain as `<World>.terrain.json` + `.terrain.bin` (tiled, streamed), `<World>.glb`, `<World>.json` and `<World>.layouts.json` |
| `all [--force]` | every terrain `status` does not call current |
| `layouts <world>` | only the places, as `<World>.layouts.json` |
| `catalog [-o FILE]` | vehicles, weapons, factions, ammunition, magazines, vests, headgear, uniforms and soldiers as `catalog.json` |
| `identify <file>` | what an Arma 3 file is: archive, config, terrain or model |
| `models [classes] [--force]` | the vehicles' models as a local model pack (only those new or changed since) |
| `models --check [--json]` | how many vehicles the pack has, per kind; which are missing or out of date |
| `extract --models [--progress-json]` | the same for the 3DHeat extractors manager, one JSON line a vehicle |

| `world` / `all` option | Does |
|---|---|
| `--max-grid N` | the glb's most height samples a side (default 2049 beside the tiled terrain, 4097 without it) |
| `--no-terrain` | leave out the tiled terrain (then the glb is the level, at up to 4097 samples a side) |
| `--trees` | also place vegetation (as boxes) |
| `--no-structures`, `--no-roads` | leave out the placed objects, the road network |
| `--mod FOLDER` | also read a mod folder (repeatable), for terrains a mod brings |

Exit status: 0 done; 1 reading failed; 2 nothing to read (no install, no such
world); the message says what to do.

## What comes out

| File | What it is |
|---|---|
| `<World>.terrain.json`, `<World>.terrain.bin` | the terrain tiled for streaming: every height sample in a pyramid of levels (7.5, 15, 30 m ... apart on Altis), 128 x 128 cells a tile, each tile one byte range; what the ground is per sample (beach, low ground, slopes, rock, high ground, sea floor, roads); every placed object as a 20-byte record per square of about a kilometre. The viewer draws this one when it is there: the ground near the camera in full detail, coarser further off, loaded as you look round. Format: `heat3d_ext_arma3/terrain.py` |
| `<World>.glb` | `ground:<World>` (the height grid, coloured by beach, low ground, slopes, rock, high ground), `water:<World>` (the sea), `road:<World>` (the road network), `structure:<World>` (placed objects as boxes) |
| `<World>.json` | the world's name, display name, DLC, size, sample spacing and frame |
| `<World>.layouts.json` | towns, military areas, other named places and airfields ([layouts](https://holdi601.github.io/3DHeat/level-layouts.html)) |
| `manifest.json` | every level written: id, game build, the fingerprints of the archives it came from, extractor and level-format version, files |
| `catalog.json` | class name, display name, side, faction, kind, domain, model and the real vehicle each stands for; ammunition with its damage numbers (`hit`, `caliber`, `typicalSpeed`), magazines (ammunition, rounds, muzzle speed), vests, headgear and uniforms with their armour per hit point (`protection`), soldiers with their default vest, headgear and uniform |
| `model-packs/arma3/model-pack.json`, `*.glb` | every vehicle's model (see below) |
| `model-packs/arma3/hit-parts.json` | the hit points' names, and per vehicle each hit point's selection, armour factor, radius and pass-through |

**Frame:** metres, x east, y up, z south, origin the terrain's south-west
corner: Arma's own world metres with north turned to -z, so a recording's
positions (x east, y north) land on the level as they are.

**World names:** `<World>` is the `CfgWorlds` class of the terrain - `Altis`,
`Stratis`, `Malden`, `Tanoa`, `Enoch` (Livonia), `VR` and the DLC terrains by
their own names. That is the `worldName` an OCAP recording carries, so the
viewer finds the level of a recording by its name.

## Vehicle models

`models` writes `model-packs/arma3/` in the viewer's levels folder
(`$HEAT3D_LEVELS`, or `--out`): `model-pack.json` and one `.glb` per vehicle
and look, in the layout of the viewer's model packs. Tanks, APCs, cars,
trucks, helicopters, planes, drones, boats and static weapons - every public
vehicle class of the install - each:

- in its own shape at true size, metres, about the point the game places it
  (its land contact for ground vehicles), x forward, y up, z right;
- in three levels of detail, taken from the model's own visual levels;
- as the class shows it by default: parts its hide animations hide at their
  starting values, muzzle flashes and crew stand-ins are left out;
- with its moving parts: the turret and gun (yaw and pitch joints about the
  axes model.cfg gives them), further armed turrets numbered, rotors spinning
  about their axes, wheels, tracks and landing gear (an aircraft's folding
  away in flight), antennas;
- in plain colours: each surface its texture's average colour, the class's
  own textures for its camouflage;
- with its damage model: each hit point (`HitHull`, `HitEngine`,
  `HitLTrack`, `HitTurret`, `HitGun`...) as the Fire Geometry component it
  names (or as spheres of its radius about its points), with its hit points,
  armour factor and the part it moves with; and the Fire Geometry's plates by
  penetration material, with the thickness where the material says it.

Each entry names the real vehicle the class stands for (`name`, the
catalogue's `key`) and the names recordings give it, so the viewer draws an
Arma 3 recording's vehicles with their own models, and another game's unit
of the same vehicle can borrow one. Classes of one model and look share a
file. A game update that changes a model or class rebuilds only those
(`models --check` says which).

The models are read from your install by this project's own reader of the
binarised model format (version 73); Arma 3 Tools are not needed.

## What it reads

The PBO archives of your own install (base game, DLC folders, and mod folders
you name), read only: their configuration (`config.bin`), string tables, the
binarised terrain (`.wrp`), the road network (`roads.shp`), the binarised
models (`.p3d`) and the average-colour tag at the head of each texture
(`.paa`; the images themselves are not read). Encrypted archives (`.ebo`) and encrypted entries are left
alone and listed; signatures are not touched. Nothing is written into the
game's folder.

Nothing of the game is in this repository or downloaded by it: no game assets,
no game code, no game files. The levels and models it writes stay on your machine, to view,
not to share.

3DHeat Arma 3 is not affiliated with or endorsed by Bohemia Interactive. Arma 3
and related names and trademarks belong to their owners. Rights holders: open
an issue on this project's issue tracker - we respond quickly and remove
anything on request.

## Tests

```bash
python -m pip install -e .[test]
python -m pytest
```

On made-up data written by the tests themselves (archives, configs, a terrain,
models, a road network): no game files are needed or used.

## Licence

MIT ([`LICENSE`](LICENSE)).

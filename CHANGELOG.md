# Changelog

## 0.7.0

- What's new: the catalogue also lists what a soldier carries: binoculars, rangefinders, laser designators, night
  vision, map, compass, watch, radio, GPS, UAV terminals, first aid kits, medikits, toolkits and mine detectors
  (`kind` `equipment`), weapon attachments - suppressors, optics, side rails, bipods (`attachment`), backpacks with
  their load and what they assemble (`backpack`) and facewear (`glasses`). Grenades, mines and explosives stay
  magazines with their ammunition.
- The extractors manager notices new content: the catalogue and the vehicle model pack are content of their own
  (new after an update, current otherwise), and it compares the catalogue's item ids with the install's, so new
  vehicles, weapons and equipment after an update are found.
- Terrains extracted from the command line (`world`, `all`) are recorded for the extractors manager too; `track`
  records terrains extracted before that, so the window no longer calls current terrains "made by an older
  version". It writes only that record, never a level.
- The game build is the Steam build id also when the Arma 3 folder is given (it was the program's date then).

## 0.6.0

- What's new: big terrains load in seconds. Each terrain is also written tiled (`<World>.terrain.json` +
  `<World>.terrain.bin`): the heights as a pyramid of levels in tiles, what the ground is per sample (roads
  included), and every placed object as a small record per square kilometre. 3DHeat draws Altis from it near the
  camera in full detail and coarser further off, loading the tiles as you look round, instead of reading a 1 GB
  level whole. The glb beside it is at most 2049 samples a side now (`--max-grid` as before, `--no-terrain` for the
  old single level); the level format is 2, so `status` calls levels written before outdated.
- Vehicle models: display names without stray spaces, and every vehicle found by the name the game shows; a
  proxy's marker triangle inside a textured section (the Orca's tail rotor) is left out.

## 0.5.0

- What's new: your Arma 3 vehicles in their own shape in 3DHeat. `heat3d-arma3-maps models` (or "Vehicle models" in
  the window) writes every vehicle of your install - tanks, APCs, cars, helicopters, planes, boats, static weapons -
  as a local model pack: true size, turret and gun on their joints, rotors, wheels and tracks, plain colours, and
  the hit zones of each hit point. `models --check --json` says what the pack covers; `extract --models
  --progress-json` builds it for the extractors manager.
- A reader of binarised models (ODOL version 73): levels of detail, faces, sections, selections, skinning,
  skeleton and the model.cfg animations.
- The catalogue calls the ED-1 robots unmanned ground vehicles (they were tanks).

## 0.4.0

- The catalogue (`catalog`) also lists the ammunition with the game's damage numbers (`hit`, the penetration
  factor `caliber`, `typicalSpeed`, indirect damage), the magazines (display and short name, ammunition, rounds,
  muzzle speed), vests, headgear and uniforms with their armour per hit point (`armor`, `passThrough`) and the
  soldiers with their default vest, headgear and uniform and the body's own armour, all read from your install.

## 0.3.0

- Its own icon: the 3DHeat cube with a text badge for its game (letters in 3DHeat's colours), as the
  program, window, taskbar and docs icon.
- A new window: the 3DHeat look in dark and light (follows the system, switchable), the logo and
  icon, sharp at any display scaling. Four steps: find the game (each requirement with its fix),
  choose (maps ticked when there is something to do, filters, search; jobs with their fields
  inline), work (progress, time left, cancel), done (open in 3DHeat, open the output folder, and
  for a failure what went wrong and what to do, copy details, report a problem).
- No console window on Windows: the program is a window program. From a terminal it still prints
  as before; `heat3d-arma3-cli.cmd` beside it waits for it in a terminal and passes its exit code.
- The update offer in the same look: Update now / Later / Skip this version, and "Update
  automatically" from the dialog.
- CI draws every page and dialog of the window (on Linux under a virtual display) and checks the
  Windows build is a window program.

## 0.2.0

- Checked on a real Arma 3 install (Altis, Stratis, Malden, Tanoa, Livonia, VR): terrains, roads, placed objects,
  places and the catalogue read from the game's own files.
- The standalone app (a window, `--check --json`, `status --json`, `extract`) and Windows and Linux builds, as every
  3DHeat extractor has them; draft releases only.

## 0.1.0

- PBO, rapified config, string table, OPRW terrain, ODOL/MLOD bounding box and road shapefile readers; `find`,
  `status --json`, `worlds`, `world`, `all`, `extract`, `layouts`, `catalog`, `identify`.

# Changelog

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

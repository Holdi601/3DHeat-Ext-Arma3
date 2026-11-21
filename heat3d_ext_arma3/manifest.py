"""
The levels written so far, as `<levels>/<game>/manifest.json`, and what `status` makes of it.

Each level records its id, the game build it was read from, the fingerprints of the archives it was made from,
the extractor and level-format version that wrote it, when, and its files. `status` compares that with what is
installed now:

* `new`: an installed world with no level yet;
* `changed`: its archives differ from those the level was made from (a game or DLC update);
* `outdated`: written by an older level format of this tool (a newer one reads more);
* `missing`: recorded, but a file of it is gone;
* `current`: nothing to do;
* `removed`: a level whose world is no longer installed (the level is kept).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

NAME = "manifest.json"


def load(folder: Path) -> dict:
    try:
        doc = json.loads((folder / NAME).read_text(encoding="utf-8"))
        if isinstance(doc, dict) and isinstance(doc.get("levels"), dict):
            return doc
    except (OSError, ValueError):
        pass
    return {"format": "3dheat-level-manifest", "version": 1, "levels": {}}


def _rel(folder: Path, p: Path) -> str:
    try:
        return p.resolve().relative_to(folder.resolve()).as_posix()
    except ValueError:
        return str(p)


def record(folder: Path, game: str, tool: str, version: str, level_format: int, level: str, *, build: str,
           sources: list[dict], files: list[Path], **extra) -> dict:
    doc = load(folder)
    doc.update(game=game, tool=tool)
    entry = {
        "id": level, "gameBuild": build, "sources": sources, "extractor": f"{tool} {version}",
        "levelFormat": level_format, "exported": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": [_rel(folder, p) for p in files],
    }
    entry.update(extra)
    doc["levels"][level] = entry
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (NAME + ".part")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(folder / NAME)
    return entry


def status(folder: Path, installed: list[dict], level_format: int) -> list[dict]:
    """One row per installed world (`id`, `sources`) and per recorded level no longer installed."""
    doc = load(folder)
    levels = doc["levels"]
    rows = []
    for world in installed:
        entry = levels.get(world["id"])
        row = {k: v for k, v in world.items() if k != "sources"}
        if entry is None:
            row.update(state="new", why="no level yet")
        else:
            had = {s["path"].lower(): s.get("hash") for s in entry.get("sources", [])}
            now = {s["path"].lower(): s.get("hash") for s in world["sources"]}
            missing = [f for f in entry.get("files", []) if not (folder / f).exists()]
            if had != now:
                changed = sorted(set(had) ^ set(now) | {p for p in had.keys() & now.keys() if had[p] != now[p]})
                row.update(state="changed", why="archives differ: " + ", ".join(Path(p).name for p in changed[:4]))
            elif int(entry.get("levelFormat", 0)) < level_format:
                row.update(state="outdated", why=f"level format {entry.get('levelFormat', 0)} < {level_format} ({entry.get('extractor', '')})")
            elif missing:
                row.update(state="missing", why="files gone: " + ", ".join(missing))
            else:
                row.update(state="current", why=f"{entry.get('extractor', '')}, {entry.get('exported', '')}")
            row["exported"] = entry.get("exported", "")
        rows.append(row)
    have = {w["id"] for w in installed}
    for level, entry in levels.items():
        if level not in have:
            rows.append({"id": level, "state": "removed", "why": "world no longer installed (level kept)",
                         "exported": entry.get("exported", "")})
    return rows

"""
Finding Arma 3 (and Arma 3 Tools) on this machine, read only.

Looked for, in order: `--install` / `ARMA3_PATH`; the Steam libraries (every `libraryfolders.vdf` of every Steam root:
the registry's, the usual Windows folders, and on Linux the native, Flatpak and Snap Steam folders that Proton games
live in); the game's own registry entry. A folder only counts when it holds the game's data (`Addons` with PBOs).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ARMA3_APP = 107410
TOOLS_APP = 233800
FOLDER = "Arma 3"

#: The game's DLC data folders and what they are (anything else holding `Addons` is listed by its folder name).
DLC_NAMES = {
    "": "Arma 3", "curator": "Zeus", "kart": "Karts", "heli": "Helicopters", "mark": "Marksmen", "expansion": "Apex",
    "jets": "Jets", "argo": "Malden 2035", "orange": "Laws of War", "tacops": "Tac-Ops", "tank": "Tanks",
    "enoch": "Contact", "contact": "Contact (platform)", "aow": "Art of War", "gm": "Global Mobilization", "vn": "S.O.G. Prairie Fire",
    "csla": "CSLA Iron Curtain", "ws": "Western Sahara", "spe": "Spearhead 1944", "rf": "Reaction Forces",
    "ef": "Expeditionary Forces",
}


@dataclass
class Install:
    root: Path
    build: str  # Steam build id, or the date of the game's program
    source: str  # how it was found

    def data_folders(self, mods: list[Path] | None = None) -> list[tuple[str, Path]]:
        """(dlc id, Addons folder) for the base game, every DLC folder and any mod folders given."""
        out = []
        for folder in [self.root, *sorted(p for p in self.root.iterdir() if p.is_dir())]:
            if folder.name.startswith("!") or folder.name.lower() in ("keys", "missions", "mpmissions", "battleye", "dta"):
                continue
            addons = next((folder / n for n in ("Addons", "addons") if (folder / n).is_dir()), None)
            if addons is not None:
                out.append(("" if folder == self.root else folder.name.lower(), addons))
        for mod in mods or []:
            addons = next((mod / n for n in ("Addons", "addons") if (mod / n).is_dir()), mod)
            out.append((f"mod:{mod.name}", addons))
        return out


def steam_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg

            for hive, key, value in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
            ):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        roots.append(Path(winreg.QueryValueEx(k, value)[0]))
                except OSError:
                    pass
        except ImportError:
            pass
        roots += [Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Program Files\Steam")]
    else:
        home = Path.home()
        roots += [
            home / ".steam" / "steam", home / ".steam" / "root", home / ".local" / "share" / "Steam",
            home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
            home / "snap" / "steam" / "common" / ".local" / "share" / "Steam",
        ]
    seen, out = set(), []
    for r in roots:
        try:
            key = str(r.resolve()).lower()
        except OSError:
            continue
        if key not in seen and r.is_dir():
            seen.add(key)
            out.append(r)
    return out


def libraries() -> list[Path]:
    libs: list[Path] = []
    for root in steam_roots():
        libs.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            libs.append(Path(m.group(1).replace("\\\\", "\\")))
    seen, out = set(), []
    for lib in libs:
        key = str(lib).lower().rstrip("\\/")
        if key not in seen and (lib / "steamapps").is_dir():
            seen.add(key)
            out.append(lib)
    return out


def _manifest(lib: Path, app: int) -> dict[str, str]:
    try:
        text = (lib / "steamapps" / f"appmanifest_{app}.acf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return {k.lower(): v for k, v in re.findall(r'"(\w+)"\s+"([^"]*)"', text)}


def steam_app(app: int, folder: str) -> tuple[Path, str] | None:
    for lib in libraries():
        info = _manifest(lib, app)
        path = lib / "steamapps" / "common" / info.get("installdir", folder)
        if path.is_dir():
            return path, info.get("buildid", "")
    return None


def _registry() -> Path | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        for key in (r"SOFTWARE\WOW6432Node\Bohemia Interactive\arma 3", r"SOFTWARE\Bohemia Interactive\arma 3"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                    return Path(winreg.QueryValueEx(k, "main")[0])
            except OSError:
                continue
    except ImportError:
        pass
    return None


def _is_game(path: Path) -> bool:
    addons = path / "Addons"
    if not addons.is_dir():
        addons = path / "addons"
    return addons.is_dir() and any(addons.glob("*.pbo"))


def _program_date(root: Path) -> str:
    import time

    for exe in ("arma3_x64.exe", "arma3.exe", "arma3.x86_64", "arma3server_x64.exe"):
        p = root / exe
        if p.is_file():
            return time.strftime("%Y-%m-%d", time.gmtime(p.stat().st_mtime))
    return "unknown"


def find_install(given: str | Path | None = None) -> tuple[Install | None, list[str]]:
    """The install, and what was looked at (for the message when there is none)."""
    looked: list[str] = []
    for label, path in (("--install", given), ("ARMA3_PATH", os.environ.get("ARMA3_PATH"))):
        if path:
            p = Path(path)
            looked.append(f"{label} {p}")
            if _is_game(p):
                return Install(p, _program_date(p), label), looked
    found = steam_app(ARMA3_APP, FOLDER)
    looked.append("Steam libraries: " + (", ".join(str(lib) for lib in libraries()) or "none found"))
    if found and _is_game(found[0]):
        return Install(found[0], found[1] or _program_date(found[0]), "Steam"), looked
    reg = _registry()
    if reg is not None:
        looked.append(f"registry entry {reg}" + ("" if reg.is_dir() else " (that folder is gone)"))
        if _is_game(reg):
            return Install(reg, _program_date(reg), "registry"), looked
    return None, looked


def find_tools() -> Path | None:
    found = steam_app(TOOLS_APP, "Arma 3 Tools")
    return found[0] if found else None

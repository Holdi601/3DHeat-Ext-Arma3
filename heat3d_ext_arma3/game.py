"""
An Arma 3 install as the game loads it: every PBO of the base game, its DLCs (and mods given), their merged
configuration and string tables, and the terrains among them.

A terrain is a `.wrp` in a PBO. Its world is the `CfgWorlds` class whose `worldName` names that file; that class
name is what the game (and every recording made in it, OCAP's `worldName` too) calls the world, and it is the
level's id. A terrain no config names keeps its file name.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import CfgClass, ConfigError, localise, merged, read_rap, read_stringtable
from .install import DLC_NAMES, Install
from .pbo import Encrypted, FileSystem, Pbo, PboError


@dataclass
class World:
    id: str  # the CfgWorlds class (OCAP worldName), or the .wrp stem
    wrp: str  # virtual path of the terrain
    pbo: Pbo
    dlc: str
    name: str = ""  # display name
    config: CfgClass | None = None

    @property
    def dlc_name(self) -> str:
        return DLC_NAMES.get(self.dlc, self.dlc)

    def sources(self, fs: FileSystem) -> list[Pbo]:
        """The PBOs this world's level is made of: the terrain's, plus the one its roads come from."""
        out = [self.pbo]
        roads = roads_path(self)
        owner = fs.owner(roads) if roads else None
        if owner is not None and owner is not self.pbo:
            out.append(owner)
        return out


def roads_path(world: World) -> str | None:
    folder = world.wrp.rsplit("\\", 1)[0]
    return f"{folder}\\data\\roads\\roads.shp"


@dataclass
class Game:
    install: Install
    fs: FileSystem
    dlc_of: dict[str, str]  # PBO path -> dlc id
    skipped: dict[str, str] = field(default_factory=dict)
    _config: CfgClass | None = None
    _strings: dict[str, str] | None = None
    config_notes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def open(cls, install: Install, mods: list[Path] | None = None) -> "Game":
        pbos, skipped, dlc_of = [], {}, {}
        for dlc, folder in install.data_folders(mods):
            for path in sorted(folder.glob("*.*bo")):
                if path.suffix.lower() not in (".pbo", ".ebo"):
                    continue
                try:
                    from .pbo import read_pbo

                    pbo = read_pbo(path)
                except Encrypted as exc:
                    skipped[str(path)] = str(exc)
                    continue
                except (PboError, OSError) as exc:
                    skipped[str(path)] = f"not readable: {exc}"
                    continue
                if pbo.encrypted_entries:
                    skipped[str(path)] = f"{len(pbo.encrypted_entries)} encrypted entries left unread"
                pbos.append(pbo)
                dlc_of[str(path)] = dlc
        return cls(install, FileSystem(pbos), dlc_of, skipped)

    # ---- configuration ----------------------------------------------------
    @property
    def config(self) -> CfgClass:
        if self._config is None:
            configs = []
            for pbo in self.fs.pbos:
                # An archive holds a config.bin at its root and often one per subfolder (one per vehicle).
                names = [k for k in pbo.entries if k == "config.bin" or k.endswith("\\config.bin")]
                if not names and any(k == "config.cpp" or k.endswith("\\config.cpp") for k in pbo.entries):
                    self.config_notes[str(pbo.path)] = "text config (config.cpp) not read"
                for name in sorted(names, key=lambda k: (k.count("\\"), k)):
                    try:
                        configs.append((f"{pbo.path}:{name}", read_rap(pbo.read(name))))
                    except (ConfigError, PboError) as exc:
                        self.config_notes[f"{pbo.path}:{name}"] = f"config not readable: {exc}"
            self._config = merged(configs)
        return self._config

    @property
    def strings(self) -> dict[str, str]:
        if self._strings is None:
            self._strings = {}
            for pbo in self.fs.pbos:
                for name in pbo.names():
                    if name.lower().endswith("stringtable.xml"):
                        try:
                            self._strings.update(read_stringtable(pbo.read(name)))
                        except (PboError, Encrypted):
                            pass
        return self._strings

    def text(self, value) -> str:
        return localise(value, self.strings)

    # ---- terrains ----------------------------------------------------------
    def terrains(self) -> list[tuple[str, Pbo]]:
        """(virtual path, PBO) of every `.wrp`, without reading any config: what `status` needs."""
        return [(p, self.fs.index[p][0]) for p in self.fs.find(".wrp")]

    def worlds(self, with_config: bool = True) -> list[World]:
        names: dict[str, CfgClass] = {}
        if with_config:
            cfg = self.config.child("CfgWorlds")
            for w in (cfg.children() if cfg else []):
                wn = w.values.get("worldname")
                if isinstance(wn, str) and wn.lower().endswith(".wrp"):
                    names[wn.strip("\\").lower()] = w
        out = []
        for path, pbo in self.terrains():
            stem = path.rsplit("\\", 1)[-1][:-4]
            w = names.get(path)
            world = World(w.name if w else stem, path, pbo, self.dlc_of.get(str(pbo.path), ""), config=w)
            world.name = self.text(w.get("description")) if w else ""
            out.append(world)
        return sorted(out, key=lambda w: w.id.lower())

    def find_world(self, wanted: str, with_config: bool = True) -> World:
        worlds = self.worlds(with_config)
        for w in worlds:
            if wanted.lower() in (w.id.lower(), w.wrp.rsplit("\\", 1)[-1][:-4].lower(), w.name.lower()):
                return w
        raise LookupError(f"no world {wanted!r}; there are: " + ", ".join(w.id for w in worlds))


def build_stamp(install: Install) -> str:
    return install.build or time.strftime("%Y-%m-%d")

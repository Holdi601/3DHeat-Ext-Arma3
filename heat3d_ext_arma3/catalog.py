"""
Arma 3's catalogue as `catalog.json`: every public vehicle, weapon and faction of the configuration.

* Vehicles: `CfgVehicles` classes with `scope = 2` that are vehicles (land, air, sea, static weapons; men are left
  out), each with its class name (the id recordings use), display name, side (`west`, `east`, `guer`, `civ`), faction
  and its display name, kind (from the class's `simulation` and its parent classes), domain, the model it is drawn
  with and the real vehicle it stands for (`key`): a neutral name, the real counterpart where the game's vehicle is
  a fictional version of one.
* Weapons: `CfgWeapons` classes with `scope = 2` that are hand weapons (rifles, machine guns, pistols, launchers)
  and `scope >= 1` vehicle weapons with a display name.
* Factions: `CfgFactionClasses` with a display name, and their side.
"""

from __future__ import annotations

import re

from .config import CfgClass
from .game import Game

SIDE = {0: "east", 1: "west", 2: "guer", 3: "civ"}

#: Fictional vehicles of the base game and its DLCs by class-name stem, with the real vehicle each is modelled on
#: (the nearest counterpart where it is a blend of several).
REAL = {
    "mbt_01_arty": ("Sholef", "spg"), "mbt_01_mlrs": ("M270 MLRS", "mlrs"), "mbt_01_tusk": ("Merkava Mk IV", "mbt"),
    "mbt_01": ("Merkava Mk IV", "mbt"), "mbt_02_arty": ("2S19 Msta-S", "spg"), "mbt_02": ("T-14 Armata", "mbt"),
    "mbt_03": ("Leopard 2A4", "mbt"), "mbt_04": ("T-14 Armata", "mbt"), "afv_wheeled_01": ("Centauro II", "tank-destroyer"),
    "apc_tracked_01_aa": ("Namer", "spaa"), "apc_tracked_01_crv": ("Namer", "arv"), "apc_tracked_01": ("Namer", "apc"),
    "apc_wheeled_01": ("Patria AMV", "ifv"), "apc_tracked_02_aa": ("2K22 Tunguska", "spaa"),
    "apc_tracked_02": ("BMP-3", "ifv"), "apc_wheeled_02": ("Otokar Arma", "apc"), "apc_wheeled_03": ("Pandur II", "ifv"),
    "apc_tracked_03": ("FV510 Warrior", "ifv"), "lt_01": ("Wiesel 2", "light-tank"), "mrap_01": ("M-ATV", "car"),
    "mrap_02": ("Punisher", "car"), "mrap_03": ("Fennek", "car"), "truck_01": ("HEMTT", "truck"),
    "truck_02_mrl": ("BM-21 Grad", "mlrs"), "truck_02": ("KamAZ-4310", "truck"), "truck_03": ("Typhoon-K", "truck"),
    "heli_light_01": ("MH-6 Little Bird", "heli-transport"), "heli_attack_01": ("RAH-66 Comanche", "heli-attack"),
    "heli_attack_02": ("Mi-28N", "heli-attack"), "heli_transport_01": ("UH-60 Black Hawk", "heli-transport"),
    "heli_transport_02": ("AW101 Merlin", "heli-transport"), "heli_transport_03": ("CH-47 Chinook", "heli-transport"),
    "heli_transport_04": ("Mi-26", "heli-transport"), "heli_light_02": ("Ka-60", "heli-transport"),
    "heli_light_03": ("AW159 Wildcat", "heli-transport"), "plane_cas_01": ("A-10 Thunderbolt II", "attack"),
    "plane_cas_02": ("Yak-130", "attack"), "plane_fighter_01": ("F-35C", "fighter"), "plane_fighter_02": ("Su-57", "fighter"),
    "plane_fighter_03": ("L-159 ALCA", "attack"), "plane_fighter_04": ("JAS 39 Gripen", "fighter"),
    "vtol_01": ("V-22 Osprey", "tiltrotor"), "uav_02": ("MQ-1 Predator", "uav"), "uav_05": ("X-47B", "uav"),
    "uav_04": ("CH-4", "uav"), "uav_01": ("quadcopter drone", "uav"), "uav_06": ("quadcopter drone", "uav"),
    "boat_armed_01": ("CB90", "boat"), "boat_transport_01": ("rigid inflatable boat", "boat"),
    "sdv_01": ("SEAL Delivery Vehicle", "submarine"), "destroyer_01": ("Arleigh Burke-class destroyer", "destroyer"),
    "carrier_01": ("Gerald R. Ford-class carrier", "carrier"), "offroad_01": ("Toyota Hilux", "car"),
    "offroad_02": ("Jeep Wrangler", "car"), "quadbike_01": ("quad bike", "car"), "van_01": ("Mercedes-Benz Vario", "truck"),
    "van_02": ("Ford Transit", "car"), "hatchback_01": ("Volkswagen Golf", "car"), "suv_01": ("Chevrolet Tahoe", "car"),
    "lsv_01": ("Polaris MRZR", "car"), "lsv_02": ("Ural Typhoon light", "car"), "ugv_01": ("unmanned ground vehicle", "ugv"),
    "tractor_01": ("tractor", "car"), "kart_01": ("go-kart", "car"),
}
_STEM = re.compile(r"^(?:[BbOoIiCc]_(?:[TtGgRrEe]_)?)?(.+?)(?:_F)?$")

SIM_KIND = {"tankx": ("ground", "tracked"), "carx": ("ground", "wheeled"), "helicopterrtd": ("air", "heli-transport"),
            "helicopterx": ("air", "heli-transport"), "airplanex": ("air", "plane"), "shipx": ("sea", "boat"),
            "submarinex": ("sea", "submarine"), "motorcycle": ("ground", "car")}
ANCESTOR_KIND = [  # first match up the class chain wins
    ("staticmortar", "mortar"), ("staticaaweapon", "static-aa"), ("staticatweapon", "static-at"),
    ("staticcannon", "artillery"), ("staticmgweapon", "static-mg"), ("staticweapon", "static"),
    ("ugv_01_base_f", "ugv"), ("uav", "uav"), ("tank", "mbt"), ("wheeled_apc_f", "apc"), ("apc_tracked", "ifv"),
    ("truck_f", "truck"), ("mrap", "car"), ("car", "car"), ("helicopter", "heli-transport"), ("plane", "plane"),
    ("ship", "boat"),
]
SUBCAT_KIND = {"edsubcat_tanks": "mbt", "edsubcat_apcs": "apc", "edsubcat_aas": "spaa", "edsubcat_artillery": "spg",
               "edsubcat_drones": "uav", "edsubcat_boats": "boat", "edsubcat_submersibles": "submarine",
               "edsubcat_planes": "plane", "edsubcat_helicopters": "heli-transport", "edsubcat_cars": "car",
               "edsubcat_turrets": "static"}
DOMAIN_OF = {"heli-transport": "air", "heli-attack": "air", "plane": "air", "fighter": "air", "attack": "air",
             "uav": "air", "tiltrotor": "air", "boat": "sea", "submarine": "sea", "destroyer": "sea", "carrier": "sea"}


def _real(cls: CfgClass) -> tuple[str, str] | None:
    for c in cls.chain():
        m = _STEM.match(c.name)
        stem = (m.group(1) if m else c.name).lower()
        for key in sorted(REAL, key=len, reverse=True):
            if stem.startswith(key):
                return REAL[key]
    return None


def _kind(cls: CfgClass) -> tuple[str, str]:
    sim = str(cls.get("simulation", "")).lower()
    domain, kind = SIM_KIND.get(sim, ("", ""))
    names = [n.lower() for n in cls.ancestors()]
    for stem, k in ANCESTOR_KIND:
        if any(stem in n for n in names):
            kind = k
            break
    sub = str(cls.get("editorSubcategory", "")).lower()
    if sub in SUBCAT_KIND and kind in ("", "car", "tracked", "wheeled", "plane", "heli-transport", "boat"):
        kind = SUBCAT_KIND[sub]
    if kind == "heli-transport" and (cls.get("isUav") == 1):
        kind = "uav"
    if kind in ("tracked", "wheeled", ""):
        kind = "mbt" if kind == "tracked" and "tank" in " ".join(names) else ("apc" if kind else "vehicle")
    return DOMAIN_OF.get(kind, domain or ("ground")), kind


def _is_man(cls: CfgClass) -> bool:
    return any(n.lower() in ("camanbase", "man") for n in cls.ancestors()) or str(cls.get("simulation", "")).lower() == "soldier"


def vehicles(game: Game) -> list[dict]:
    cfg = game.config.child("CfgVehicles")
    factions = game.config.child("CfgFactionClasses")
    out = []
    for cls in (cfg.children() if cfg else []):
        if cls.get("scope") != 2 or _is_man(cls):
            continue
        sim = str(cls.get("simulation", "")).lower()
        names = " ".join(n.lower() for n in cls.ancestors())
        if sim not in SIM_KIND and "staticweapon" not in names and "allvehicles" not in names:
            continue
        if "parachute" in sim or "parachute" in names:
            continue
        domain, kind = _kind(cls)
        real = _real(cls)
        if real and kind not in ("static", "static-mg", "static-at", "static-aa", "mortar", "artillery"):
            key, kind = real
            domain = DOMAIN_OF.get(kind, domain)
        name = game.text(cls.get("displayName", "")) or cls.name
        faction = str(cls.get("faction", ""))
        fcls = factions.child(faction) if factions and faction else None
        side = cls.get("side")
        out.append({
            "id": cls.name, "kind": "vehicle", "name": name, "class": kind, "domain": domain,
            "side": SIDE.get(side if isinstance(side, int) else -1, ""), "faction": faction,
            "nation": game.text(fcls.get("displayName", "")) if fcls else faction,
            "key": real[0] if real else name, "shape": str(cls.get("model", "")).strip("\\"),
        })
    return out


def weapons(game: Game) -> list[dict]:
    cfg = game.config.child("CfgWeapons")
    out = []
    for cls in (cfg.children() if cfg else []):
        wtype = cls.get("type")
        scope = cls.get("scope")
        name = game.text(cls.get("displayName", ""))
        chain = [n.lower() for n in cls.ancestors()]
        names = " ".join(chain)
        if not isinstance(wtype, int):
            # The slot type is not always set along the chain the configs give; the base classes say it as well.
            wtype = (1 if any(n in ("rifle", "rifle_base_f", "riflecore") for n in chain)
                     else 2 if any(n in ("pistol", "pistol_base_f", "pistolcore") for n in chain)
                     else 4 if any(n in ("launcher", "launcher_base_f", "launchercore") for n in chain)
                     else 65536 if any(n in ("cannoncore", "mgun", "rocketpods", "missilelauncher") for n in chain)
                     else None)
        if wtype == 65536:
            if not (isinstance(scope, int) and scope >= 1 and name):
                continue
            wclass = "vehicle-weapon"
        elif scope == 2 and wtype in (1, 2, 4):
            if "itemcore" in names or "binocular" in names:
                continue
            wclass = {2: "pistol", 4: "launcher"}.get(wtype, "rifle")
            if wtype == 1 and ("rifle_long_base_f" in names or "lmg" in names or "mmg" in names):
                wclass = "lmg" if ("lmg" in names or "mmg" in names) else "dmr"
            if wtype == 1 and "smg" in names:
                wclass = "smg"
        else:
            continue
        out.append({"id": cls.name, "kind": "weapon", "name": name or cls.name, "class": wclass, "key": name or cls.name})
    return out


def factions(game: Game) -> list[dict]:
    cfg = game.config.child("CfgFactionClasses")
    out = []
    for cls in (cfg.children() if cfg else []):
        name = game.text(cls.get("displayName", ""))
        side = cls.get("side")
        if not name or not isinstance(side, int) or side not in SIDE:
            continue
        out.append({"id": cls.name, "kind": "faction", "name": name, "class": "faction", "side": SIDE[side], "key": name})
    return out


def build(game: Game) -> list[dict]:
    return sorted(vehicles(game) + weapons(game) + factions(game), key=lambda i: (i["kind"], i["id"].lower()))

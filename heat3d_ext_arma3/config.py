"""
Arma 3's configuration: the rapified (binarised) `config.bin` of every PBO, merged into one class tree.

Rapified format (openly documented): `\\0raP`, two numbers (0, 8), the offset of the enum table, then the root class
body. A class body is its parent class's name (zero-terminated, empty for none), a compressed entry count (7 bits a
byte, lowest first, high bit = more) and the entries, each a type byte:

* 0 class: name, 32-bit offset of its body;
* 1 value: subtype (0 string, 1 float, 2 int, 4 expression string, 6 int64), name, value;
* 2 array: name, array; 5 array append (`+=`): 32-bit flags, name, array;
* 3 extern class declaration: name; 4 delete: name.

An array is a compressed count and elements, each a type byte (0 string, 1 float, 2 int, 3 nested array,
4 expression, 6 int64) and its value.

Configs merge in load order (`CfgPatches` `requiredAddons` first): a class seen again is extended, its values
replaced. A value a class lacks is looked up along its parent classes, a parent named in the same scope or above.
"""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


class ConfigError(ValueError):
    pass


@dataclass(eq=False)
class CfgClass:
    name: str
    base: str = ""
    values: dict = field(default_factory=dict)  # lower-case name -> value
    classes: dict = field(default_factory=dict)  # lower-case name -> CfgClass
    parent: "CfgClass | None" = None
    defined: bool = True  # False for an extern declaration only

    # ---- lookups along the parents ----------------------------------------
    def base_class(self) -> "CfgClass | None":
        if not self.base:
            return None
        want = self.base.lower()
        scope = self.parent
        while scope is not None:
            for owner in _chain(scope):
                found = owner.classes.get(want)
                if found is not None and found is not self:
                    return found
            scope = scope.parent
        return None

    def chain(self) -> list["CfgClass"]:
        return _chain(self)

    def get(self, key: str, default=None):
        key = key.lower()
        for c in _chain(self):
            if key in c.values:
                return c.values[key]
        return default

    def child(self, name: str) -> "CfgClass | None":
        name = name.lower()
        for c in _chain(self):
            found = c.classes.get(name)
            if found is not None and found.defined:
                return found
        return None

    def children(self) -> list["CfgClass"]:
        """This class's subclasses, own and inherited (own first)."""
        seen: dict[str, CfgClass] = {}
        for c in _chain(self):
            for k, v in c.classes.items():
                if k not in seen and v.defined:
                    seen[k] = v
        return list(seen.values())

    def ancestors(self) -> list[str]:
        return [c.name for c in _chain(self)]

    def path(self, *names: str) -> "CfgClass | None":
        c: CfgClass | None = self
        for n in names:
            if c is None:
                return None
            c = c.child(n)
        return c


def _chain(c: CfgClass) -> list[CfgClass]:
    out, seen = [], set()
    cur: CfgClass | None = c
    while cur is not None and id(cur) not in seen and len(out) < 64:
        out.append(cur)
        seen.add(id(cur))
        cur = cur.base_class()
    return out


# --------------------------------------------------------------------------
# rapified reader


class _Rap:
    def __init__(self, data: bytes) -> None:
        if data[:4] != b"\0raP":
            raise ConfigError("not a rapified config")
        self.d = data

    def cstr(self, at: int) -> tuple[str, int]:
        end = self.d.find(b"\0", at)
        if end < 0:
            raise ConfigError("config ends inside a name")
        return self.d[at:end].decode("utf-8", "replace"), end + 1

    def cint(self, at: int) -> tuple[int, int]:
        value, shift = 0, 0
        while True:
            b = self.d[at]
            at += 1
            value |= (b & 0x7F) << shift
            if not b & 0x80:
                return value, at
            shift += 7
            if shift > 35:
                raise ConfigError("compressed number too long")

    def scalar(self, kind: int, at: int):
        if kind in (0, 4):
            return self.cstr(at)
        if kind == 1:
            return struct.unpack_from("<f", self.d, at)[0], at + 4
        if kind == 2:
            return struct.unpack_from("<i", self.d, at)[0], at + 4
        if kind == 6:
            return struct.unpack_from("<q", self.d, at)[0], at + 8
        raise ConfigError(f"unknown value type {kind}")

    def array(self, at: int) -> tuple[list, int]:
        count, at = self.cint(at)
        out = []
        for _ in range(count):
            kind = self.d[at]
            at += 1
            if kind == 3:
                value, at = self.array(at)
            else:
                value, at = self.scalar(kind, at)
            out.append(value)
        return out, at

    def body(self, at: int, cls: CfgClass, depth: int = 0) -> None:
        if depth > 64:
            raise ConfigError("classes nested too deep")
        cls.base, at = self.cstr(at)
        count, at = self.cint(at)
        for _ in range(count):
            kind = self.d[at]
            at += 1
            if kind == 0:
                name, at = self.cstr(at)
                (offset,) = struct.unpack_from("<I", self.d, at)
                at += 4
                sub = CfgClass(name, parent=cls)
                self.body(offset, sub, depth + 1)
                cls.classes[name.lower()] = sub
            elif kind == 1:
                sub_kind = self.d[at]
                name, at = self.cstr(at + 1)
                cls.values[name.lower()], at = self.scalar(sub_kind, at)
            elif kind == 2:
                name, at = self.cstr(at)
                cls.values[name.lower()], at = self.array(at)
            elif kind == 3:
                name, at = self.cstr(at)
                cls.classes.setdefault(name.lower(), CfgClass(name, parent=cls, defined=False))
            elif kind == 4:
                name, at = self.cstr(at)
                cls.classes.setdefault(name.lower(), CfgClass(name, parent=cls, defined=False)).values["__delete__"] = True
            elif kind == 5:
                at += 4
                name, at = self.cstr(at)
                items, at = self.array(at)
                cls.values.setdefault("__append__", {})[name.lower()] = items
            else:
                raise ConfigError(f"unknown entry type {kind}")


def read_rap(data: bytes) -> CfgClass:
    """The class tree of one rapified config."""
    rap = _Rap(data)
    root = CfgClass("")
    try:
        rap.body(16, root)
    except (IndexError, struct.error):
        raise ConfigError("config ends early") from None
    return root


# --------------------------------------------------------------------------
# rapified writer (for tests)


def _wcint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _warray(values: list) -> bytes:
    out = bytearray(_wcint(len(values)))
    for v in values:
        if isinstance(v, list):
            out += b"\x03" + _warray(v)
        elif isinstance(v, str):
            out += b"\x00" + v.encode() + b"\0"
        elif isinstance(v, float):
            out += b"\x01" + struct.pack("<f", v)
        else:
            out += b"\x02" + struct.pack("<i", v)
    return bytes(out)


def write_rap(tree: dict) -> bytes:
    """A rapified config of `tree`: `{"class Name: Base": {...}, "key": value, "arr": [..]}`, for tests."""
    bodies: list[tuple[int, bytes]] = []  # placeholders patched after layout
    out = bytearray(b"\0raP" + struct.pack("<III", 0, 8, 0))

    def encode(node: dict, base: str) -> tuple[bytearray, list]:
        # entries first, with class offsets patched in later
        entries = bytearray(base.encode() + b"\0" + _wcint(len(node)))
        pending = []
        for key, value in node.items():
            if key.startswith("class "):
                spec = key[6:]
                name, _, parent = (s.strip() for s in spec.partition(":"))
                entries += b"\x00" + name.encode() + b"\0"
                pending.append((len(entries), value, parent))
                entries += b"\0\0\0\0"
            elif isinstance(value, list):
                entries += b"\x02" + key.encode() + b"\0" + _warray(value)
            elif isinstance(value, str):
                entries += b"\x01\x00" + key.encode() + b"\0" + value.encode() + b"\0"
            elif isinstance(value, float):
                entries += b"\x01\x01" + key.encode() + b"\0" + struct.pack("<f", value)
            else:
                entries += b"\x01\x02" + key.encode() + b"\0" + struct.pack("<i", value)
        return entries, pending

    def place(node: dict, base: str) -> int:
        at = len(out)
        body, pending = encode(node, base)
        out.extend(body)
        for rel, value, parent in pending:
            child_at = place(value, parent)
            struct.pack_into("<I", out, at + rel, child_at)
        return at

    place(tree, "")
    struct.pack_into("<I", out, 12, len(out))
    out += b"\0\0\0\0"  # an empty enum table
    return bytes(out)


# --------------------------------------------------------------------------
# merging


def merge(into: CfgClass, src: CfgClass) -> None:
    """Extend `into` by `src`, the way a later addon's config extends an earlier one."""
    if src.defined:
        if src.base or not into.defined:
            into.base = src.base
        into.defined = True
    appends = src.values.get("__append__", {})
    for k, v in src.values.items():
        if k != "__append__":
            into.values[k] = v
    for k, items in appends.items():
        into.values[k] = list(into.values.get(k, []) or []) + list(items)
    for k, sub in src.classes.items():
        if sub.values.get("__delete__"):
            into.classes.pop(k, None)
            continue
        have = into.classes.get(k)
        if have is None:
            have = CfgClass(sub.name, parent=into, defined=False)
            into.classes[k] = have
        merge(have, sub)


def load_order(configs: list[tuple[str, CfgClass]]) -> list[tuple[str, CfgClass]]:
    """Configs sorted so an addon comes after the addons its `CfgPatches` `requiredAddons` names."""
    provides: dict[str, int] = {}
    needs: list[set[str]] = []
    for i, (_src, root) in enumerate(configs):
        patches = root.classes.get("cfgpatches")
        need: set[str] = set()
        if patches:
            for p in patches.classes.values():
                provides[p.name.lower()] = i
                for r in p.values.get("requiredaddons", []) or []:
                    if isinstance(r, str):
                        need.add(r.lower())
        needs.append(need)
    order: list[int] = []
    state = [0] * len(configs)  # 0 new, 1 visiting, 2 done

    def visit(i: int) -> None:
        stack = [(i, iter(sorted(needs[i])))]
        state[i] = 1
        while stack:
            node, it = stack[-1]
            for r in it:
                j = provides.get(r)
                if j is not None and state[j] == 0:
                    state[j] = 1
                    stack.append((j, iter(sorted(needs[j]))))
                    break
            else:
                stack.pop()
                state[node] = 2
                order.append(node)

    for i in range(len(configs)):
        if state[i] == 0:
            visit(i)
    return [configs[i] for i in order]


def merged(configs: list[tuple[str, CfgClass]]) -> CfgClass:
    root = CfgClass("")
    for _src, cfg in load_order(configs):
        merge(root, cfg)
    return root


# --------------------------------------------------------------------------
# string tables

_STR = re.compile(r"^\$(str_\w+)$", re.I)


def read_stringtable(data: bytes) -> dict[str, str]:
    """English texts by lower-case key from a `stringtable.xml` (Original where no English)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {}
    out: dict[str, str] = {}
    for key in root.iter("Key"):
        kid = (key.get("ID") or "").lower()
        if not kid:
            continue
        text = key.findtext("English") or key.findtext("Original") or ""
        if text:
            out[kid] = text
    return out


def localise(value, strings: dict[str, str]) -> str:
    """A `$STR_...` reference read from the string tables, anything else as text."""
    if not isinstance(value, str):
        return "" if value is None else str(value)
    m = _STR.match(value.strip())
    if not m:
        return value
    return strings.get(m.group(1).lower(), "")

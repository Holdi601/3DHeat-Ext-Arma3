"""
PBO, the archive Arma 3 keeps its data in (openly documented format).

A PBO is a header of entries - name (zero-terminated), packing method, unpacked size, reserved, time stamp, packed
size, five little-endian 32-bit numbers - ended by an entry with an empty name. The first entry may be a `Vers`
entry followed by zero-terminated key/value pairs (`prefix`, `product`, `version`...) up to an empty key. The data
of every entry follows in header order. Arma 3 ends the file with a zero byte and a 20-byte SHA-1 of what comes
before; it is the PBO's own fingerprint and the source hash of what is read from it.

Packing methods: 0 stored, `Cprs` LZSS-packed, `Vers` the property entry, `Enco`/`Encr` encrypted. Encrypted
entries and encrypted archives (`.ebo`) are never opened: they are listed as such and left alone. Signatures
(`.bisign` beside the PBO) are only ever read, never touched; nothing here writes into the game.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .compress import CompressionError, lzss_decode

STORED = 0
CPRS = 0x43707273  # 'Cprs'
VERS = 0x56657273  # 'Vers'
ENCO = 0x456E636F  # 'Enco'
ENCR = 0x456E6372  # 'Encr'


class PboError(ValueError):
    pass


class Encrypted(PboError):
    """The entry or archive is encrypted; it is not read."""


@dataclass
class Entry:
    name: str  # as stored, with backslashes
    method: int
    original: int
    timestamp: int
    size: int
    offset: int = 0  # of its data in the file

    @property
    def packed(self) -> bool:
        return self.method == CPRS or (self.method == STORED and self.original not in (0, self.size))

    @property
    def encrypted(self) -> bool:
        return self.method in (ENCO, ENCR)

    @property
    def length(self) -> int:
        return self.original if self.packed else self.size


@dataclass
class Pbo:
    path: Path
    properties: dict[str, str]
    entries: dict[str, Entry]  # by lower-case name with backslashes
    data_end: int
    sha1: bytes | None  # the archive's own trailing hash, when it has one
    notes: list[str] = field(default_factory=list)

    @property
    def prefix(self) -> str:
        return self.properties.get("prefix", self.path.stem).strip("\\").lower()

    @property
    def fingerprint(self) -> str:
        """The archive's trailing SHA-1, or (without one) a SHA-1 of its header: what `status` compares."""
        if self.sha1:
            return self.sha1.hex()
        with self.path.open("rb") as handle:
            return hashlib.sha1(handle.read(min(self.data_end, 1 << 20))).hexdigest()

    @property
    def encrypted_entries(self) -> list[str]:
        return [e.name for e in self.entries.values() if e.encrypted]

    def names(self) -> list[str]:
        return [e.name for e in self.entries.values()]

    def __contains__(self, name: str) -> bool:
        return _key(name) in self.entries

    def read(self, name: str) -> bytes:
        entry = self.entries.get(_key(name))
        if entry is None:
            raise KeyError(name)
        if entry.encrypted:
            raise Encrypted(f"{entry.name} in {self.path.name} is encrypted; not read")
        with self.path.open("rb") as handle:
            handle.seek(entry.offset)
            raw = handle.read(entry.size)
        if len(raw) != entry.size:
            raise PboError(f"{entry.name} in {self.path.name} ends early")
        if not entry.packed:
            return raw
        try:
            data, _used = lzss_decode(raw, entry.original)
        except CompressionError as exc:
            raise PboError(f"{entry.name} in {self.path.name}: {exc}") from None
        return data


def _key(name: str) -> str:
    return name.replace("/", "\\").lower().lstrip("\\")


def _cstr(data: bytes, at: int) -> tuple[str, int]:
    end = data.find(b"\0", at)
    if end < 0:
        raise PboError("header ends inside a name")
    return data[at:end].decode("utf-8", "replace"), end + 1


def read_pbo(path: Path | str) -> Pbo:
    """The directory of a PBO (no entry data is read). `.ebo` files raise `Encrypted`."""
    path = Path(path)
    if path.suffix.lower() == ".ebo":
        raise Encrypted(f"{path.name} is an encrypted archive; not read")
    size = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(min(size, 1 << 22))
        at = 0
        properties: dict[str, str] = {}
        entries: dict[str, Entry] = {}
        order: list[Entry] = []
        first = True
        while True:
            while True:
                try:
                    name, nxt = _cstr(head, at)
                    if nxt + 20 > len(head):
                        raise PboError("header ends early")
                    break
                except PboError:
                    if len(head) >= size:
                        raise PboError(f"{path.name}: not a PBO (header ends early)") from None
                    head += handle.read(1 << 22)
            at = nxt
            method, original, _reserved, timestamp, data_size = struct.unpack_from("<5I", head, at)
            at += 20
            if not name:
                if first and method == VERS:
                    while True:
                        key, at = _cstr(head, at)
                        if not key:
                            break
                        value, at = _cstr(head, at)
                        properties[key.lower()] = value
                    first = False
                    continue
                break  # the end of the header
            first = False
            if method not in (STORED, CPRS, ENCO, ENCR):
                raise PboError(f"{path.name}: unknown packing method {method:#x} for {name!r}")
            entry = Entry(name, method, original, timestamp, data_size)
            order.append(entry)
            entries[_key(name)] = entry
    offset = at
    for entry in order:
        entry.offset = offset
        offset += entry.size
    if offset > size:
        raise PboError(f"{path.name}: entries run past the end of the file ({offset:,} > {size:,})")
    sha1 = None
    if size - offset == 21:
        with path.open("rb") as handle:
            handle.seek(offset)
            tail = handle.read(21)
        if tail[0] == 0:
            sha1 = tail[1:]
    pbo = Pbo(path, properties, entries, offset, sha1)
    if any(k in properties for k in ("encryption", "obfuscated")):
        pbo.notes.append("marked as encrypted or obfuscated in its properties")
    return pbo


def write_pbo(path: Path, files: dict[str, bytes], properties: dict[str, str] | None = None, pack: tuple = ()) -> None:
    """A PBO of `files` (names packed with LZSS where listed in `pack`), with the Arma 3 trailing SHA-1: for tests."""
    from .compress import lzss_encode

    head = bytearray()
    body = bytearray()
    head += b"\0" + struct.pack("<5I", VERS, 0, 0, 0, 0)
    for key, value in (properties or {}).items():
        head += key.encode() + b"\0" + value.encode() + b"\0"
    head += b"\0"
    for name, data in files.items():
        if name in pack:
            packed = lzss_encode(data)
            head += name.encode() + b"\0" + struct.pack("<5I", CPRS, len(data), 0, 0, len(packed))
            body += packed
        else:
            head += name.encode() + b"\0" + struct.pack("<5I", STORED, 0, 0, 0, len(data))
            body += data
    head += b"\0" + struct.pack("<5I", 0, 0, 0, 0, 0)
    content = bytes(head + body)
    path.write_bytes(content + b"\0" + hashlib.sha1(content).digest())


class FileSystem:
    """Every PBO of an install as one tree of virtual paths (`<prefix>\\<name>`), the way the game sees them."""

    def __init__(self, pbos: list[Pbo], skipped: dict[str, str] | None = None) -> None:
        self.pbos = pbos
        self.skipped = skipped or {}  # path -> why it was not read
        self.index: dict[str, tuple[Pbo, str]] = {}
        for pbo in pbos:
            for key in pbo.entries:
                self.index[f"{pbo.prefix}\\{key}"] = (pbo, key)

    @classmethod
    def of_folders(cls, folders: list[Path]) -> "FileSystem":
        pbos, skipped = [], {}
        for folder in folders:
            for path in sorted(folder.glob("*.[pe]bo")):
                try:
                    pbos.append(read_pbo(path))
                except Encrypted as exc:
                    skipped[str(path)] = str(exc)
                except (PboError, OSError) as exc:
                    skipped[str(path)] = f"not readable: {exc}"
        return cls(pbos, skipped)

    def __contains__(self, path: str) -> bool:
        return _key(path) in self.index

    def read(self, path: str) -> bytes:
        found = self.index.get(_key(path))
        if found is None:
            raise KeyError(path)
        pbo, key = found
        return pbo.read(key)

    def owner(self, path: str) -> Pbo | None:
        found = self.index.get(_key(path))
        return found[0] if found else None

    def find(self, suffix: str, under: str = "") -> list[str]:
        suffix, under = suffix.lower(), _key(under)
        return [p for p in self.index if p.endswith(suffix) and p.startswith(under)]

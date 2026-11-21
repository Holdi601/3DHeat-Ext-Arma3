"""
The two compressions Arma 3's files use, both openly documented.

* LZSS, the Real Virtuality flavour: a flag byte whose bits (lowest first) say literal byte (1) or back-reference
  (0, two bytes: 12-bit distance, 4-bit length + 3), followed by a 4-byte sum of the output bytes. Packed PBO entries
  (`Cprs`) and older binarised files use it.
* LZO1X, the standard algorithm (lzo1x_decompress_safe), for the arrays of binarised terrains and models.

Both decoders take the expected output length, say how many input bytes they used, and raise `CompressionError`
on anything inconsistent rather than returning a wrong result.
"""

from __future__ import annotations

import struct


class CompressionError(ValueError):
    pass


# --------------------------------------------------------------------------
# LZSS


def lzss_decode(data: bytes | memoryview, out_len: int, start: int = 0, checksum: bool = True) -> tuple[bytes, int]:
    """(output, bytes used) for the LZSS stream at `start` that unpacks to `out_len` bytes."""
    out = bytearray()
    i = start
    n = len(data)
    try:
        while len(out) < out_len:
            flags = data[i]
            i += 1
            for bit in range(8):
                if len(out) >= out_len:
                    break
                if flags & (1 << bit):
                    out.append(data[i])
                    i += 1
                    continue
                b1, b2 = data[i], data[i + 1]
                i += 2
                rpos = b1 | ((b2 & 0xF0) << 4)
                rlen = (b2 & 0x0F) + 3
                pos = len(out) - rpos
                if rpos == 0:
                    raise CompressionError("LZSS back-reference of distance 0")
                for k in range(rlen):
                    if len(out) >= out_len:
                        break
                    p = pos + k
                    out.append(out[p] if p >= 0 else 0x20)
    except IndexError:
        raise CompressionError("LZSS stream ends early") from None
    if checksum:
        if i + 4 > n:
            raise CompressionError("LZSS checksum missing")
        (want,) = struct.unpack_from("<I", data, i)
        i += 4
        if (sum(out) & 0xFFFFFFFF) != want:
            raise CompressionError("LZSS checksum does not match")
    return bytes(out), i - start


def lzss_encode(data: bytes, checksum: bool = True) -> bytes:
    """A valid LZSS stream for `data` (greedy, small window): for tests and round trips."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        flag_at = len(out)
        out.append(0)
        flags = 0
        for bit in range(8):
            if i >= n:
                break
            best_len, best_dist = 0, 0
            lo = max(0, i - 4095)
            for j in range(lo, i):
                length = 0
                while length < 18 and i + length < n and data[j + length] == data[i + length]:
                    length += 1
                if length > best_len:
                    best_len, best_dist = length, i - j
            if best_len >= 3:
                out.append(best_dist & 0xFF)
                out.append(((best_dist >> 4) & 0xF0) | (best_len - 3))
                i += best_len
            else:
                flags |= 1 << bit
                out.append(data[i])
                i += 1
        out[flag_at] = flags
    if checksum:
        out += struct.pack("<I", sum(data) & 0xFFFFFFFF)
    return bytes(out)


# --------------------------------------------------------------------------
# LZO1X

_LOOP, _FIRST_LITERAL_RUN, _MATCH, _MATCH_DONE, _MATCH_NEXT = range(5)


def lzo1x_decode(src: bytes | memoryview, out_len: int, start: int = 0) -> tuple[bytes, int]:
    """(output, bytes used) for the LZO1X stream at `start`; its end marker must come exactly at `out_len` bytes."""
    out = bytearray()
    ip = start

    def copy_match(m: int, length: int) -> None:
        if m < 0:
            raise CompressionError("LZO match before the start of the output")
        if len(out) + length > out_len:
            raise CompressionError("LZO output longer than expected")
        end = m + length
        if end <= len(out):
            out.extend(out[m:end])
        else:
            for k in range(length):
                out.append(out[m + k])

    def literals(count: int) -> None:
        nonlocal ip
        if len(out) + count > out_len:
            raise CompressionError("LZO output longer than expected")
        chunk = src[ip : ip + count]
        if len(chunk) != count:
            raise CompressionError("LZO stream ends early")
        out.extend(chunk)
        ip += count

    try:
        t = src[ip]
        if t > 17:
            ip += 1
            t -= 17
            if t < 4:
                state = _MATCH_NEXT
            else:
                literals(t)
                state = _FIRST_LITERAL_RUN
        else:
            state = _LOOP
        while True:
            if state == _LOOP:
                t = src[ip]
                ip += 1
                if t >= 16:
                    state = _MATCH
                    continue
                if t == 0:
                    while src[ip] == 0:
                        t += 255
                        ip += 1
                    t += 15 + src[ip]
                    ip += 1
                literals(t + 3)
                state = _FIRST_LITERAL_RUN
                continue
            if state == _FIRST_LITERAL_RUN:
                t = src[ip]
                ip += 1
                if t >= 16:
                    state = _MATCH
                    continue
                m = len(out) - (1 + 0x0800) - (t >> 2) - (src[ip] << 2)
                ip += 1
                copy_match(m, 3)
                state = _MATCH_DONE
                continue
            if state == _MATCH:
                if t >= 64:
                    m = len(out) - 1 - ((t >> 2) & 7) - (src[ip] << 3)
                    ip += 1
                    copy_match(m, (t >> 5) + 1)
                elif t >= 32:
                    t &= 31
                    if t == 0:
                        while src[ip] == 0:
                            t += 255
                            ip += 1
                        t += 31 + src[ip]
                        ip += 1
                    m = len(out) - 1 - ((src[ip] | (src[ip + 1] << 8)) >> 2)
                    ip += 2
                    copy_match(m, t + 2)
                elif t >= 16:
                    m = len(out) - ((t & 8) << 11)
                    t &= 7
                    if t == 0:
                        while src[ip] == 0:
                            t += 255
                            ip += 1
                        t += 7 + src[ip]
                        ip += 1
                    m -= (src[ip] | (src[ip + 1] << 8)) >> 2
                    ip += 2
                    if m == len(out):
                        break  # the end marker
                    copy_match(m - 0x4000, t + 2)
                else:
                    m = len(out) - 1 - (t >> 2) - (src[ip] << 2)
                    ip += 1
                    copy_match(m, 2)
                state = _MATCH_DONE
                continue
            if state == _MATCH_DONE:
                t = src[ip - 2] & 3
                if t == 0:
                    state = _LOOP
                    continue
                state = _MATCH_NEXT
            # _MATCH_NEXT: one to three literals, then the next instruction
            literals(t)
            t = src[ip]
            ip += 1
            state = _MATCH
    except IndexError:
        raise CompressionError("LZO stream ends early") from None
    if len(out) != out_len:
        raise CompressionError(f"LZO stream ends at {len(out):,} bytes, {out_len:,} expected")
    return bytes(out), ip - start


def lzo1x_literal(data: bytes) -> bytes:
    """A valid LZO1X stream holding `data` as literals (no matches): for tests and round trips."""
    n = len(data)
    end = b"\x11\x00\x00"
    if n == 0:
        return end
    if n <= 238:
        return bytes([17 + n]) + data + end
    t = n - 18
    k, rem = divmod(t, 255)
    if rem == 0:
        k, rem = k - 1, 255
    return b"\x00" + b"\x00" * k + bytes([rem]) + data + end

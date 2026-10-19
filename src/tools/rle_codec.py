"""Stock Complex LCD RLEReversed image codec (magic E0 21 A5 5A).

Layout derived from the stock resource.bin image blocks
(cross-checked against this device's stock face, not guessed):

  Image / ImageList header (12 bytes):
    +0  u8  sign      0x00 = BGRA32, 0x10 = indexed8 (1024-byte BGRA palette + indices)
    +1  u8  flags     singles: 0x04 when compressed; lists: frame count
    +2  u8  listComp  lists: 0x04 when compressed (length table present)
    +4  u16 width
    +6  u16 height
    +8  u32 payload length (bytes after this header)

  Compressed payload:
    +0  4   magic E0 21 A5 5A
    +4  u32 (rawLength << 4) | bytesPerPixel   (bpp in low nibble)
    +8  …   RLE bytes
              bpp==1 / sign 0x10 → RLE v2 over palette+indices
              bpp==4 / sign 0x00 → RLE v1 over BGRA pixels
"""
from __future__ import annotations

import struct
from typing import List, Optional, Tuple

from PIL import Image

COMPRESSED_MAGIC = bytes((0xE0, 0x21, 0xA5, 0x5A))
IMAGE_HEADER = 12
PALETTE_BYTES = 256 * 4
MAX_RUN = 0x7F


def encode_rle_v1(data: bytes, bpp: int) -> bytes:
    if bpp < 1 or len(data) % bpp:
        raise ValueError(f"RLE v1 length {len(data)} not divisible by bpp {bpp}")
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        count = 1
        while (
            count < MAX_RUN
            and i + (count + 1) * bpp <= n
            and data[i : i + bpp] == data[i + count * bpp : i + (count + 1) * bpp]
        ):
            count += 1
        out.append(count)
        out.extend(data[i : i + bpp])
        i += count * bpp
    return bytes(out)


def decode_rle_v1(encoded: bytes, expected: int, bpp: int) -> bytes:
    out = bytearray()
    i = 0
    while i < len(encoded) and len(out) < expected:
        control = encoded[i]
        i += 1
        count = control & MAX_RUN
        if count == 0:
            raise ValueError("RLE v1 zero-length packet")
        if (control & 0x80) == 0:
            if i + bpp > len(encoded):
                raise ValueError("Truncated RLE v1 repeat")
            pix = encoded[i : i + bpp]
            i += bpp
            out.extend(pix * count)
        else:
            lit = count * bpp
            if i + lit > len(encoded):
                raise ValueError("Truncated RLE v1 literal")
            out.extend(encoded[i : i + lit])
            i += lit
    if len(out) != expected:
        raise ValueError(f"RLE v1 produced {len(out)}; expected {expected}")
    return bytes(out)


def encode_rle_v2(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        repeat = 1
        while repeat < MAX_RUN and i + repeat < n and data[i + repeat] == data[i]:
            repeat += 1
        if repeat >= 3:
            out.append(repeat)
            out.append(data[i])
            i += repeat
            continue
        lit_start = i
        i += repeat
        while i < n and (i - lit_start) < MAX_RUN:
            nxt = 1
            while nxt < 3 and i + nxt < n and data[i + nxt] == data[i]:
                nxt += 1
            if nxt >= 3:
                break
            i += min(nxt, MAX_RUN - (i - lit_start))
        lit_len = i - lit_start
        out.append(0x80 | lit_len)
        out.extend(data[lit_start:i])
    return bytes(out)


def decode_rle_v2(encoded: bytes, expected: int) -> bytes:
    out = bytearray()
    i = 0
    while i < len(encoded) and len(out) < expected:
        control = encoded[i]
        i += 1
        count = control & MAX_RUN
        if count == 0:
            raise ValueError("RLE v2 zero-length packet")
        if control & 0x80:
            if i + count > len(encoded):
                raise ValueError("Truncated RLE v2 literal")
            out.extend(encoded[i : i + count])
            i += count
        else:
            if i >= len(encoded):
                raise ValueError("Truncated RLE v2 repeat")
            out.extend(bytes([encoded[i]]) * count)
            i += 1
    if len(out) != expected:
        raise ValueError(f"RLE v2 produced {len(out)}; expected {expected}")
    return bytes(out)


def rgba_to_bgra(rgba: bytes) -> bytes:
    out = bytearray(len(rgba))
    for o in range(0, len(rgba), 4):
        out[o] = rgba[o + 2]
        out[o + 1] = rgba[o + 1]
        out[o + 2] = rgba[o]
        out[o + 3] = rgba[o + 3]
    return bytes(out)


def bgra_to_rgba(bgra: bytes) -> bytes:
    return rgba_to_bgra(bgra)  # same swap


def try_indexed(rgba: bytes) -> Optional[Tuple[bytes, int]]:
    """Return (palette1024 + indices, ncolors) if ≤256 unique RGBA colours."""
    palette: dict[int, int] = {}
    indices = bytearray(len(rgba) // 4)
    for pix, o in enumerate(range(0, len(rgba), 4)):
        key = (rgba[o + 3] << 24) | (rgba[o] << 16) | (rgba[o + 1] << 8) | rgba[o + 2]
        idx = palette.get(key)
        if idx is None:
            if len(palette) == 256:
                return None
            idx = len(palette)
            palette[key] = idx
        indices[pix] = idx
    raw = bytearray(PALETTE_BYTES + len(indices))
    for key, idx in palette.items():
        off = idx * 4
        raw[off] = key & 0xFF
        raw[off + 1] = (key >> 8) & 0xFF
        raw[off + 2] = (key >> 16) & 0xFF
        raw[off + 3] = (key >> 24) & 0xFF
    raw[PALETTE_BYTES:] = indices
    return bytes(raw), len(palette)


def compressed_payload(raw: bytes, bpp: int, indexed: bool) -> bytes:
    enc = encode_rle_v2(raw) if indexed or bpp == 1 else encode_rle_v1(raw, bpp)
    return COMPRESSED_MAGIC + struct.pack("<I", (len(raw) << 4) | bpp) + enc


def encode_image(rgba: bytes, w: int, h: int, prefer_32bit: bool = True) -> Tuple[bytes, dict]:
    """Encode one image. Prefer full 32-bit RLE; indexed8 only when ≤256 colours (lossless)."""
    if len(rgba) != w * h * 4:
        raise ValueError("RGBA size mismatch")
    # Prefer 32-bit RLE; indexed8 only when ≤256 colours (lossless) and smaller.
    cand_32 = compressed_payload(rgba_to_bgra(rgba), 4, False)
    hdr32 = bytearray(IMAGE_HEADER)
    hdr32[0] = 0x00
    hdr32[1] = 0x04
    struct.pack_into("<HH I", hdr32, 4, w, h, len(cand_32))
    block32 = bytes(hdr32) + cand_32

    idx = try_indexed(rgba)
    if idx is None:
        meta = {"sign": 0, "mode": "bgra32-rle", "colors": None, "payload": len(cand_32)}
        return block32, meta

    raw_idx, ncolors = idx
    cand_idx = compressed_payload(raw_idx, 1, True)
    hdr_idx = bytearray(IMAGE_HEADER)
    hdr_idx[0] = 0x10
    hdr_idx[1] = 0x04
    struct.pack_into("<HH I", hdr_idx, 4, w, h, len(cand_idx))
    block_idx = bytes(hdr_idx) + cand_idx

    if prefer_32bit:
        # Prefer 32-bit unless indexed is strictly smaller (still lossless)
        if len(block_idx) < len(block32):
            return block_idx, {"sign": 0x10, "mode": "indexed8-rle", "colors": ncolors, "payload": len(cand_idx)}
        return block32, {"sign": 0, "mode": "bgra32-rle", "colors": ncolors, "payload": len(cand_32)}

    return block_idx, {"sign": 0x10, "mode": "indexed8-rle", "colors": ncolors, "payload": len(cand_idx)}


def encode_image_list(frames_rgba: List[bytes], w: int, h: int, prefer_32bit: bool = True) -> Tuple[bytes, dict]:
    if not frames_rgba or len(frames_rgba) > 255:
        raise ValueError("image list count")
    for fr in frames_rgba:
        if len(fr) != w * h * 4:
            raise ValueError("frame size")

    def pack(sign: int, payloads: List[bytes]) -> bytes:
        # Stock Complex LCD (resource.bin list@121446): u32@+8 = sum(frame
        # payload sizes) only; the leading count*u32 length table is extra and
        # the definition/table length is 12 + 4*count + sum(payloads).
        table = b"".join(struct.pack("<I", len(p)) for p in payloads)
        body = table + b"".join(payloads)
        hdr = bytearray(IMAGE_HEADER)
        hdr[0] = sign
        hdr[1] = len(payloads)
        hdr[2] = 0x04
        struct.pack_into("<HH I", hdr, 4, w, h, sum(len(p) for p in payloads))
        return bytes(hdr) + body

    payloads_32 = [compressed_payload(rgba_to_bgra(fr), 4, False) for fr in frames_rgba]
    block32 = pack(0x00, payloads_32)

    payloads_idx = []
    for fr in frames_rgba:
        idx = try_indexed(fr)
        if idx is None:
            payloads_idx = None
            break
        payloads_idx.append(compressed_payload(idx[0], 1, True))

    if payloads_idx is None:
        return block32, {
            "sign": 0,
            "mode": "bgra32-rle-list",
            "frames": len(frames_rgba),
            "payload": len(block32) - 12,
        }

    block_idx = pack(0x10, payloads_idx)
    if prefer_32bit and len(block_idx) >= len(block32):
        return block32, {
            "sign": 0,
            "mode": "bgra32-rle-list",
            "frames": len(frames_rgba),
            "payload": len(block32) - 12,
        }
    return block_idx, {
        "sign": 0x10,
        "mode": "indexed8-rle-list",
        "frames": len(frames_rgba),
        "payload": len(block_idx) - 12,
    }


def _decode_raw_pixels(raw: bytes, sign: int, w: int, h: int, bpp: int) -> bytes:
    n = w * h
    rgba = bytearray(n * 4)
    if sign == 0x10 or bpp == 1:
        if len(raw) < PALETTE_BYTES + n:
            raise ValueError("indexed payload short")
        for p in range(n):
            po = raw[PALETTE_BYTES + p] * 4
            o = p * 4
            rgba[o] = raw[po + 2]
            rgba[o + 1] = raw[po + 1]
            rgba[o + 2] = raw[po]
            rgba[o + 3] = raw[po + 3]
        return bytes(rgba)
    if bpp == 4:
        if len(raw) < n * 4:
            raise ValueError("bgra payload short")
        for p in range(n):
            i = p * 4
            rgba[i] = raw[i + 2]
            rgba[i + 1] = raw[i + 1]
            rgba[i + 2] = raw[i]
            rgba[i + 3] = raw[i + 3]
        return bytes(rgba)
    raise ValueError(f"unsupported bpp {bpp}")


def decode_payload(buf: bytes, off: int, length: int, sign: int, w: int, h: int) -> Tuple[bytes, bool]:
    if length >= 8 and buf[off : off + 4] == COMPRESSED_MAGIC:
        info = struct.unpack_from("<I", buf, off + 4)[0]
        bpp = info & 0x0F
        expected = info >> 4
        enc = buf[off + 8 : off + length]
        if sign == 0x10 or bpp == 1:
            raw = decode_rle_v2(enc, expected)
        else:
            raw = decode_rle_v1(enc, expected, bpp)
        return _decode_raw_pixels(raw, sign, w, h, bpp), True
    raw = buf[off : off + length]
    bpp = 1 if sign == 0x10 else 4
    return _decode_raw_pixels(raw, sign, w, h, bpp), False


def decode_image_block(buf: bytes, off: int) -> Tuple[Image.Image, dict]:
    sign = buf[off]
    w, h = struct.unpack_from("<HH", buf, off + 4)
    plen = struct.unpack_from("<I", buf, off + 8)[0]
    rgba, compressed = decode_payload(buf, off + IMAGE_HEADER, plen, sign, w, h)
    im = Image.frombytes("RGBA", (w, h), rgba)
    return im, {
        "sign": sign,
        "w": w,
        "h": h,
        "payload": plen,
        "compressed": compressed,
        "total": IMAGE_HEADER + plen,
        "flags": buf[off + 1],
    }


def decode_image_list_block(buf: bytes, off: int) -> Tuple[List[Image.Image], dict]:
    sign = buf[off]
    count = buf[off + 1]
    compressed = buf[off + 2] in (4, 8)
    w, h = struct.unpack_from("<HH", buf, off + 4)
    total = struct.unpack_from("<I", buf, off + 8)[0]
    if compressed:
        lengths = [struct.unpack_from("<I", buf, off + IMAGE_HEADER + i * 4)[0] for i in range(count)]
        po = off + IMAGE_HEADER + count * 4
    else:
        lengths = [total // count] * count
        po = off + IMAGE_HEADER
    frames = []
    for ln in lengths:
        rgba, _ = decode_payload(buf, po, ln, sign, w, h)
        frames.append(Image.frombytes("RGBA", (w, h), rgba))
        po += ln
    return frames, {
        "sign": sign,
        "count": count,
        "w": w,
        "h": h,
        "payload": total,
        "compressed": compressed,
        "total": IMAGE_HEADER + total,
    }


def pil_rgba_bytes(im: Image.Image) -> bytes:
    return im.convert("RGBA").tobytes()

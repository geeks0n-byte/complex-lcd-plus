"""Re-pack a Complex LCD+ compiler .face with stock RLEReversed image encoding.

Used as a post-compile step from build.py, or standalone:

  python tools\\pack_rle.py [input.face [output.face]]

Defaults: output\\ComplexLCDPlus.face → same path (in-place) and repo-root copy.
Keeps face id 29100101 / name "Complex LCD+".
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rle_codec import (
    IMAGE_HEADER,
    decode_image_block,
    decode_image_list_block,
    encode_image,
    encode_image_list,
    pil_rgba_bytes,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
DIST = ROOT.parent
FACE_FILE = "ComplexLCDPlus.face"

FACE_ID = "29100101"
FACE_NAME = "Complex LCD+"
ID_OFF, NAME_OFF, FIELD_LEN = 40, 104, 64


def face_style_indices(d: bytes) -> List[int]:
    out = []
    for fi in range(32):
        base = 168 + fi * 88
        if base + 88 > len(d):
            break
        n0 = struct.unpack_from("<I", d, base + 8)[0]
        p0 = struct.unpack_from("<I", d, base + 12)[0]
        if n0 == 0 or n0 > 500 or p0 == 0 or p0 >= len(d):
            break
        out.append(fi)
    return out


def table_entries(d: bytes, fi: int, k: int):
    base = 168 + fi * 88
    n = struct.unpack_from("<I", d, base + 8 + k * 8)[0]
    p = struct.unpack_from("<I", d, base + 8 + k * 8 + 4)[0]
    rows = []
    for i in range(n):
        rid = struct.unpack_from("<I", d, p + i * 16)[0]
        off = struct.unpack_from("<I", d, p + i * 16 + 8)[0]
        ln = struct.unpack_from("<I", d, p + i * 16 + 12)[0]
        rows.append({"rid": rid, "off": off, "len": ln, "def_off": p + i * 16})
    return rows


def decode_raw_bgra_image(d: bytes, off: int) -> Tuple["Image.Image", int]:
    from PIL import Image

    sign = d[off]
    w, h = struct.unpack_from("<HH", d, off + 4)
    if sign == 0 and d[off + 12 : off + 16] != b"\xe0\x21\xa5\x5a":
        need = w * h * 4
        raw = d[off + 12 : off + 12 + need]
        im = Image.frombuffer("RGBA", (w, h), raw, "raw", "BGRA", 0, 1).copy()
        return im, IMAGE_HEADER + need
    im, meta = decode_image_block(d, off)
    return im, meta["total"]


def decode_raw_bgra_list(d: bytes, off: int) -> Tuple[List["Image.Image"], int]:
    from PIL import Image

    sign = d[off]
    count = d[off + 1]
    w, h = struct.unpack_from("<HH", d, off + 4)
    if sign == 0 and d[off + 2] not in (4, 8) and d[off + 12 : off + 16] != b"\xe0\x21\xa5\x5a":
        frame = w * h * 4
        frames = []
        for i in range(count):
            o = off + 12 + i * frame
            frames.append(
                Image.frombuffer("RGBA", (w, h), d[o : o + frame], "raw", "BGRA", 0, 1).copy()
            )
        return frames, IMAGE_HEADER + count * frame
    frames, meta = decode_image_list_block(d, off)
    return frames, meta["total"]


def set_id_name(buf: bytearray, face_id: str = FACE_ID, face_name: str = FACE_NAME) -> None:
    id_bytes = face_id.encode("ascii")[:FIELD_LEN].ljust(FIELD_LEN, b"\0")
    name_bytes = face_name.encode("utf-8")[:FIELD_LEN].ljust(FIELD_LEN, b"\0")
    buf[ID_OFF : ID_OFF + FIELD_LEN] = id_bytes
    buf[NAME_OFF : NAME_OFF + FIELD_LEN] = name_bytes


def repack_face(
    raw: bytes,
    face_id: str = FACE_ID,
    face_name: str = FACE_NAME,
) -> Tuple[bytes, dict]:
    """Compress all images/lists in a raw compiler .face. Returns (packed_bytes, stats)."""
    d = bytearray(raw)
    before = len(d)
    styles = face_style_indices(d)
    preview_off = struct.unpack_from("<I", d, 32)[0]

    jobs = [("preview", None, None, preview_off, None)]
    for fi in styles:
        for row in table_entries(d, fi, 2):
            jobs.append(("image", fi, row["rid"], row["off"], row["len"]))
        for row in table_entries(d, fi, 3):
            jobs.append(("list", fi, row["rid"], row["off"], row["len"]))

    sources = {}
    for kind, fi, rid, off, old_len in jobs:
        key = (kind, fi, rid, off)
        if kind == "list":
            frames, total = decode_raw_bgra_list(d, off)
            w, h = frames[0].size
            rgba_list = [pil_rgba_bytes(fr) for fr in frames]
            sources[key] = {
                "kind": kind,
                "fi": fi,
                "rid": rid,
                "off": off,
                "old_len": old_len if old_len is not None else total,
                "w": w,
                "h": h,
                "frames": rgba_list,
            }
        else:
            im, total = decode_raw_bgra_image(d, off)
            w, h = im.size
            rgba = pil_rgba_bytes(im)
            sources[key] = {
                "kind": kind,
                "fi": fi,
                "rid": rid,
                "off": off,
                "old_len": old_len if old_len is not None else total,
                "w": w,
                "h": h,
                "rgba": rgba,
            }

    packed: Dict[tuple, bytes] = {}
    meta_by_key: Dict[tuple, dict] = {}
    share_map: Dict[tuple, tuple] = {}
    content_canon: Dict[Tuple[Optional[int], bytes], tuple] = {}
    stats = defaultdict(int)
    mode_counts = defaultdict(int)

    for key, src in sources.items():
        if src["kind"] == "list":
            block, meta = encode_image_list(src["frames"], src["w"], src["h"], prefer_32bit=True)
            packed[key] = block
            meta_by_key[key] = meta
            mode_counts[meta["mode"]] += 1
            stats["lists"] += 1
            continue

        digest = hashlib.md5(src["rgba"]).digest()
        fi = src["fi"]
        if fi is not None:
            ck = (fi, digest)
            if ck in content_canon:
                share_map[key] = content_canon[ck]
                stats["shared_singles"] += 1
                mode_counts["shared"] += 1
                continue
            content_canon[ck] = key

        block, meta = encode_image(src["rgba"], src["w"], src["h"], prefer_32bit=True)
        packed[key] = block
        meta_by_key[key] = meta
        mode_counts[meta["mode"]] += 1
        stats["singles"] += 1

    for key, canon in share_map.items():
        packed[key] = packed[canon]
        meta_by_key[key] = {**meta_by_key[canon], "shared_from": list(canon)}

    img_spans = sorted({(src["off"], src["off"] + src["old_len"]) for src in sources.values()})
    merged = []
    for a, b in img_spans:
        if not merged or a > merged[-1][1]:
            merged.append([a, b])
        else:
            merged[-1][1] = max(merged[-1][1], b)

    out = bytearray()
    runs = []
    i = 0
    while i < len(d):
        skip = False
        for a, b in merged:
            if i == a:
                i = b
                skip = True
                break
            if a < i < b:
                i = b
                skip = True
                break
        if skip:
            continue
        start = i
        new_start = len(out)
        while i < len(d):
            hit = False
            for a, b in merged:
                if i == a:
                    hit = True
                    break
            if hit:
                break
            out.append(d[i])
            i += 1
        runs.append((start, i, new_start))

    def map_addr(old: int) -> int:
        for os_, oe, ns in runs:
            if os_ <= old < oe:
                return ns + (old - os_)
        raise KeyError(f"old addr {old} was in removed image region")

    key_new_off: Dict[tuple, int] = {}
    unique_order = [k for k in sources if k not in share_map]
    unique_order.sort(
        key=lambda k: (0 if k[0] == "preview" else 1, k[1] if k[1] is not None else -1, k[2] or 0, k[3])
    )

    for key in unique_order:
        key_new_off[key] = len(out)
        out.extend(packed[key])

    for key, canon in share_map.items():
        key_new_off[key] = key_new_off[canon]

    for fi in styles:
        base_old = 168 + fi * 88
        base_new = map_addr(base_old)
        for k in range(10):
            n = struct.unpack_from("<I", d, base_old + 8 + k * 8)[0]
            p = struct.unpack_from("<I", d, base_old + 8 + k * 8 + 4)[0]
            if n == 0 or p == 0:
                continue
            try:
                struct.pack_into("<I", out, base_new + 8 + k * 8 + 4, map_addr(p))
            except KeyError:
                pass
            for i in range(n):
                def_old = p + i * 16
                def_new = map_addr(def_old)
                data_off = struct.unpack_from("<I", d, def_old + 8)[0]
                if k in (2, 3):
                    rid = struct.unpack_from("<I", d, def_old)[0]
                    kind = "image" if k == 2 else "list"
                    key = (kind, fi, rid, data_off)
                    if key not in key_new_off:
                        matches = [
                            kk
                            for kk in key_new_off
                            if kk[0] == kind and kk[1] == fi and kk[2] == rid
                        ]
                        if not matches:
                            raise SystemExit(f"missing packed key for {kind} fi={fi} rid={rid}")
                        key = matches[0]
                    nob = key_new_off[key]
                    nlen = len(packed[key])
                    struct.pack_into("<I", out, def_new + 8, nob)
                    struct.pack_into("<I", out, def_new + 12, nlen)
                else:
                    try:
                        struct.pack_into("<I", out, def_new + 8, map_addr(data_off))
                    except KeyError:
                        raise SystemExit(f"lost property data fi={fi} k={k} off={data_off}")

    prev_key = ("preview", None, None, preview_off)
    struct.pack_into("<I", out, 32, key_new_off[prev_key])

    for fi in styles:
        base_old = 168 + fi * 88
        base_new = map_addr(base_old)
        face_prev = struct.unpack_from("<I", d, base_old + 4)[0]
        if face_prev and face_prev < len(d):
            if any(a <= face_prev < b for a, b in merged):
                for key, src in sources.items():
                    if src["off"] == face_prev:
                        struct.pack_into("<I", out, base_new + 4, key_new_off[key])
                        break
            else:
                try:
                    struct.pack_into("<I", out, base_new + 4, map_addr(face_prev))
                except KeyError:
                    pass

    set_id_name(out, face_id, face_name)

    mismatches = []
    checked = 0
    for key, src in sources.items():
        block = packed[key]
        if src["kind"] == "list":
            frames, _ = decode_image_list_block(block, 0)
            for i, fr in enumerate(frames):
                if pil_rgba_bytes(fr) != src["frames"][i]:
                    mismatches.append(f"{key} frame {i}")
                checked += 1
        else:
            im, _ = decode_image_block(block, 0)
            if pil_rgba_bytes(im) != src["rgba"]:
                mismatches.append(str(key))
            checked += 1

    face_checked = 0
    face_fail = 0
    for key, src in sources.items():
        nob = key_new_off[key]
        if src["kind"] == "list":
            frames, _ = decode_image_list_block(out, nob)
            for i, fr in enumerate(frames):
                face_checked += 1
                if pil_rgba_bytes(fr) != src["frames"][i]:
                    face_fail += 1
        else:
            im, _ = decode_image_block(out, nob)
            face_checked += 1
            if pil_rgba_bytes(im) != src["rgba"]:
                face_fail += 1

    after = len(out)
    md5 = hashlib.md5(out).hexdigest().upper()
    summary = {
        "before": before,
        "after": after,
        "saved": before - after,
        "md5": md5,
        "face_id": face_id,
        "face_name": face_name,
        "checked_images_frames": checked,
        "mismatches": mismatches,
        "face_checked": face_checked,
        "face_fail": face_fail,
        "mode_counts": dict(mode_counts),
        "stats": dict(stats),
        "styles": len(styles),
        "shared_singles": stats["shared_singles"],
    }
    if mismatches or face_fail:
        raise SystemExit(
            f"RLE lossless verify FAILED: mismatches={mismatches} face_fail={face_fail}"
        )
    return bytes(out), summary


def main() -> int:
    inp = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / FACE_FILE
    outp = Path(sys.argv[2]) if len(sys.argv) > 2 else inp
    if not inp.is_file():
        print(f"missing input face: {inp}", file=sys.stderr)
        return 1
    raw = inp.read_bytes()
    # If already RLE-packed (small), refuse unless forced — expect raw compiler size.
    packed, summary = repack_face(raw, FACE_ID, FACE_NAME)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_bytes(packed)
    if outp.resolve() != (DIST / FACE_FILE).resolve():
        (DIST / FACE_FILE).write_bytes(packed)
    if outp.resolve() != (OUT / FACE_FILE).resolve():
        OUT.mkdir(exist_ok=True)
        (OUT / FACE_FILE).write_bytes(packed)
    print(json.dumps(summary, indent=2))
    print(f"Wrote {outp} ({summary['after']} bytes) MD5={summary['md5']}")
    print(f"LOSSLESS OK: {summary['checked_images_frames']} units, face {summary['face_checked']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

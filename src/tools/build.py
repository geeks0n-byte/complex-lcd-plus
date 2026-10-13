#!/usr/bin/env python3
"""Complex LCD+ : clean EasyFace (Gen2, P67 336x480) rebuild of the stock Complex LCD face.

One command rebuild:   python tools\\build.py            (generate project + previews + compile)
                       python tools\\build.py --no-compile (generate only; works on any OS)

Inputs (read-only):  stock\\resources\\manifest.xml + PNGs (untouched stock unpack)
Outputs:             project\\{active,A,B,C}.fprj + project\\images\\*  (regenerated from scratch)
                     previews\\preview_<style>.png, previews\\taps_overlay.png
                     output\\ComplexLCDPlus.face
"""
import colorsys
import hashlib
import math
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
STOCK = ROOT / "stock" / "resources"
PROJ = ROOT / "project"
IMG = PROJ / "images"
AOD = PROJ / "AOD"
AOD_IMG = AOD / "images"
PREV = ROOT / "previews"
OUT = ROOT / "output"
COMPILER = Path(r"C:\Users\Giga\Documents\Projects\miband\tools\easyface\Compiler.exe")
ASSETS = ROOT / "tools" / "assets"     # pre-rendered label (see tools/make_assets.py)
DIST = ROOT.parent                     # repo root ComplexLCDPlus.face

FACE_ID = "29100101"
DISPLAY_NAME = "Complex LCD+"
FACE_FILE = "ComplexLCDPlus.face"
DEVICE_TYPE = "567"
ID_OFF, NAME_OFF, FIELD_LEN = 40, 104, 64

# AOD (always-on): dim grey digits on black. Compiler packs project/AOD/{AOD.fprj,images}/ with the main face.
AOD_GREY = (168, 168, 168)          # digit ink (dim LCD on black)
AOD_DATE_GREY = (120, 120, 120)     # secondary date / weekday row
AOD_HH_Y = 170                      # big HH:MM vertical centre-ish
AOD_DATE_Y = 270
AOD_WEEK_GAP = 10                   # space between date row and short weekday below
AOD_SAMPLE = dict(hour=10, minute=9, day=16, month=8, weekday=0)  # weekday 0=Sun .. 6=Sat

# style key -> (fprj stem, title, panel tint at stock panel grey L=139, None = untouched stock)
# Only the hue and saturation of a tint are used: every recoloured element is brought to the stock
# luminance of that element (see match_lum()).
STYLES = [
    ("orig",   "active", "Original LCD",   None),
    ("green",  "A",      "Phosphor Green", (112, 170, 100)),
    ("amber",  "B",      "Amber",          (206, 146, 62)),
    ("ice",    "C",      "Ice Blue",       (112, 164, 200)),
    ("red",    "D",      "Signal Red",     (204, 100, 96)),
    # all 8 back in v2.4 (on request). Size note: Compiler.exe stores every image of every style as raw
    # 32-bit pixels (no compressor for DeviceType 567, no shared images between styles), ~1.6 MB per style.
    ("teal",   "E",      "Teal",           (96, 180, 166)),
    ("violet", "F",      "Violet",         (152, 122, 204)),
    ("pink",   "G",      "Rose Pink",      (206, 120, 168)),
    # ("yellow", "H", "Lemon Yellow", (198, 188, 92)) tried and dropped: at the stock panel luminance a
    # yellow hue turns olive/khaki, muddy next to Amber and Phosphor Green
]

# ---------------------------------------------------------------- stock manifest
MAN = (STOCK / "manifest.xml").read_text(encoding="utf-8")
IMAGES = dict(re.findall(r'<Image name="(\w+)" src="([^"]+)"', MAN))
ARRAYS = {n: re.findall(r'src="([^"]+)"', body)
          for n, body in re.findall(r'<ImageArray name="(\w+)"[^>]*>(.*?)</ImageArray>', MAN, re.S)}


def stock_img(src):
    return Image.open(STOCK / src).convert("RGBA")


def put(im, name):
    im.save(IMG / name)
    return name


def export_single(key, name):
    return put(stock_img(IMAGES[key]), name)


def export_array(key, prefix):
    names = []
    for i, src in enumerate(ARRAYS[key]):
        names.append(put(stock_img(src), f"{prefix}_{i:02d}.png"))
    return names


# ---------------------------------------------------------------- recolor
def smooth(e0, e1, x):
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


DIAL_C, DIAL_R = (90, 117), 77     # analog dial (numerals, ticks, rings, crosshair, rim), stock pivot
DOT_SRC, DOT_DST = (9, 250), (160, 14)   # stock decorative dot -> OS notification-dot spot (top centre)
SEC_SHIFT = 0    # stock position (7 would put the seconds on the HH:MM baseline; reverted at user request)


def vivid(tint):
    """Saturated, full-brightness version of a style tint (used for hands and dial markings)."""
    h, sat, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in tint))
    r, g, b = colorsys.hsv_to_rgb(h, max(sat, 0.65), 1.0)
    return (r * 255, g * 255, b * 255)


# ---- luminance matching: a recoloured pixel gets the relative luminance (sRGB, Rec.709 weights) of
# the stock grey pixel it replaces, so every element keeps its stock brightness in every style.
def _lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rel_lum(rgb):
    r, g, b = (_lin(float(c)) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def lstar(rgb):
    Y = rel_lum(rgb)
    return 116 * Y ** (1 / 3) - 16 if Y > 216 / 24389 else Y * 24389 / 27


SAT_MIN = 0.30   # floor when a target is too bright for the scheme saturation (white hands)
_MATCH = {}


def match_lum(tint, grey, accent):
    """Colour with the hue of `tint`, the scheme saturation (tint saturation for the LCD panel,
    max(sat, 0.65) for accents on the black bezel), and the luminance of stock grey `grey`. Value is
    solved first; if even value 1 is too dark, saturation is lowered (not below SAT_MIN); a target still
    out of reach stays at (SAT_MIN, value 1) and is reported in the brightness table."""
    key = (tint, round(grey, 2), accent)
    if key in _MATCH:
        return _MATCH[key]
    h, sat, _ = colorsys.rgb_to_hsv(*(c / 255.0 for c in tint))
    if accent:
        sat = max(sat, 0.65)
    target = rel_lum((grey, grey, grey))
    col = lambda s, v: tuple(c * 255.0 for c in colorsys.hsv_to_rgb(h, s, v))
    if grey <= 0:
        out = (0.0, 0.0, 0.0)
    elif rel_lum(col(sat, 1.0)) >= target:
        lo, hi = 0.0, 1.0
        for _ in range(40):
            m = (lo + hi) / 2
            lo, hi = (m, hi) if rel_lum(col(sat, m)) < target else (lo, m)
        out = col(sat, (lo + hi) / 2)
    else:
        lo, hi = SAT_MIN, sat
        if rel_lum(col(SAT_MIN, 1.0)) < target:
            hi = SAT_MIN
        for _ in range(40):
            m = (lo + hi) / 2
            lo, hi = (lo, m) if rel_lum(col(m, 1.0)) < target else (m, hi)
        out = col(lo, 1.0)
    _MATCH[key] = out
    return out


def lum_lut(tint, accent):
    return [match_lum(tint, g, accent) for g in range(256)]


def mcol(tint, grey, accent=True):
    return tuple(round(c) for c in match_lum(tint, grey, accent)) + (255,)


def stock_levels(stock_bg):
    """Grey level of each recoloured element, measured from the stock pixels."""
    px = stock_bg.convert("RGB").load()
    pan, num, tick, ring = [], [], [], []
    for y in range(480):
        for x in range(336):
            r, g, b = px[x, y]
            if max(r, g, b) - min(r, g, b) > 24:
                continue
            L = (r + g + b) / 3.0
            d = ((x - DIAL_C[0]) ** 2 + (y - DIAL_C[1]) ** 2) ** 0.5
            if d >= DIAL_R:
                if 128 <= L <= 150:
                    pan.append(L)
            elif d >= 27:
                if L >= 200:
                    tick.append(L)
                elif 45 <= d < 67 and 160 <= L < 200:
                    num.append(L)
                elif d >= 70 and 50 <= L < 64:
                    ring.append(L)
    med = lambda v: sorted(v)[len(v) // 2]
    time_l = max(sum(px[x, y]) / 3.0 for y in range(TIME_BOX[1], TIME_BOX[3]) for x in range(TIME_BOX[0], TIME_BOX[2]))
    hand = max(sum(p[:3]) / 3.0 for p in Image.open(STOCK / IMAGES["Image4"]).convert("RGBA").getdata() if p[3])
    bar = [p for p in Image.open(STOCK / ARRAYS["ImageArray5"][0]).convert("RGBA").getdata() if p[3] > 200]
    return dict(panel=med(pan), numerals=med(num), ticks=med(tick), rings=med(ring), hands=hand,
                header=time_l, bar_unlit=med([sum(p[:3]) / 3.0 for p in bar]))


def brightness_table(stock_bg):
    """Per-element perceived lightness (CIE L*, from relative luminance) of stock vs every style,
    before (previous build's formulas) and after (luminance matched). Written to
    previews/brightness_table.md and printed."""
    lv = stock_levels(stock_bg)
    # (element, stock grey, accent?, old-formula colour or None)
    rows = [
        ("LCD panel", lv["panel"], False, lambda t: t),
        ("Dial numerals", lv["numerals"], True, lambda t: [c * lv["numerals"] / 255 for c in vivid(t)]),
        ("Dial ticks", lv["ticks"], True, lambda t: [c * lv["ticks"] / 255 for c in vivid(t)]),
        ("Dial rings / rim", lv["rings"], True, lambda t: [c * lv["rings"] / 255 for c in vivid(t)]),
        ("Hands (h/m/s)", lv["hands"], True, lambda t: vivid(t)),
        ("Headers TIME / STATS", lv["header"], True, lambda t: [c * 0.544 for c in vivid(t)]),
        ("ALARM label, walker, KM", 153, True, lambda t: [c * 0.544 for c in vivid(t)]),
        ("Alarm / distance digits", BEZEL_TEXT[0], True, lambda t: [c * 0.80 for c in vivid(t)]),
        ("Status icons lit", BEZEL_TEXT[0], True, lambda t: vivid(t)),
        ("Strip battery % + lit segments", 153, True, lambda t: [c * 0.544 for c in vivid(t)]),
        ("Strip battery unlit segments", 70, True, lambda t: [c * 0.25 for c in vivid(t)]),
    ]
    styled = [(k, title, t) for k, _, title, t in STYLES if t is not None]
    old_keys = ("green", "amber", "ice")
    head = "| Element | stock grey | stock L* | " + " | ".join(f"{title} (before -> after)" for _, title, _ in styled) + " |"
    out = ["# Per-element brightness: CIE L* (perceived lightness from relative luminance)", "",
           "Stock grey = level measured from the stock pixels (alarm digits / lit icons: the Original's 225, "
           "labels: stock header grey 153). Before = previous build (vivid tint scaled by fixed factors); "
           "after = this build (scheme hue/saturation at the stock element's luminance). "
           "Original LCD = stock values in every row. '*' = target brighter than the scheme hue can reach at "
           f"saturation >= {SAT_MIN}; shown value is the closest.", "", head,
           "|" + "---|" * (3 + len(styled))]
    worst_before, worst_after = 0.0, 0.0
    for name, g, accent, old in rows:
        ref = lstar((g, g, g))
        cells = []
        for k, title, t in styled:
            new = match_lum(t, g, accent)
            la = lstar(new)
            short = "*" if abs(la - ref) > 0.5 else ""
            worst_after = max(worst_after, abs(la - ref)) if not short else worst_after
            if k in old_keys:
                lb = lstar([min(255.0, c) for c in old(t)])
                worst_before = max(worst_before, abs(lb - ref))
                cells.append(f"{lb:.1f} -> {la:.1f}{short}")
            else:
                cells.append(f"new -> {la:.1f}{short}")
        out.append(f"| {name} | {g:.0f} | {ref:.1f} | " + " | ".join(cells) + " |")
    out += ["", "Unchanged in every style (identical pixels, so identical luminance): LCD digits (black), weather "
            "icons (stock colour art), status icons off (grey 78 rune), centre hub.", "",
            f"Largest |L* difference| before (Green/Amber/Ice): {worst_before:.1f}; after (rows "
            f"without '*'): {worst_after:.2f}."]
    (PREV / "brightness_table.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out).encode("ascii", "replace").decode("ascii"))


def colorize(im, tint):
    """Recolor a neutral (white/grey) image in the scheme hue, every pixel at its stock luminance."""
    im = im.convert("RGBA").copy()
    px = im.load()
    lut = lum_lut(tint, True)
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            t = lut[round((r + g + b) / 3.0)]
            px[x, y] = (round(t[0]), round(t[1]), round(t[2]), a)
    return im


def tint_unlit(im, tint):
    """Battery segment bar: grey 'off' segments in the panel hue at their stock luminance (91); black
    'on' segments unchanged."""
    im = im.convert("RGBA").copy()
    px = im.load()
    lut = lum_lut(tint, False)
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            L = (r + g + b) / 3.0
            if a == 0 or L < 30 or max(r, g, b) - min(r, g, b) > 24:
                continue
            k = smooth(30, 60, L)
            t = lut[round(L)]
            px[x, y] = (round(r + (t[0] - r) * k), round(g + (t[1] - g) * k), round(b + (t[2] - b) * k), a)
    return im


def in_dial(x, y):
    return (x - DIAL_C[0]) ** 2 + (y - DIAL_C[1]) ** 2 < DIAL_R ** 2


def recolor_panel(im, tint):
    """Tint the neutral-grey LCD panels and the analog dial numerals/ticks.
    Black bezel, dark dial plate and the red accent stay as they are."""
    im = im.copy()
    px = im.load()
    w, h = im.size
    acc, pan = lum_lut(tint, True), lum_lut(tint, False)   # dial markings / LCD panel, per stock grey
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if max(r, g, b) - min(r, g, b) > 24:   # saturated (red accent) -> keep
                continue
            L = (r + g + b) / 3.0
            if in_dial(x, y):
                # plate is L 40-48 (kept dark); rings/crosshair/rim are L 50-60, ticks/numerals brighter
                if (x - DIAL_C[0]) ** 2 + (y - DIAL_C[1]) ** 2 < 27 ** 2:
                    continue                   # central disc, crosshair and hub area: keep stock pixels
                k = smooth(46, 56, L)
                # every marking pixel keeps its stock luminance (numerals ~172, ticks ~218, rings ~56)
                tr, tg, tb = acc[round(L)]
            else:
                k = smooth(60, 112, L) * (1.0 - smooth(185, 225, L))
                tr, tg, tb = pan[round(L)]                 # panel ~139, shading kept pixel by pixel
            if k <= 0:
                continue
            px[x, y] = (round(r + (tr - r) * k), round(g + (tg - g) * k), round(b + (tb - b) * k), a)
    return im


def clean_background(im):
    """Erase the header labels (TIME, STEPS/BPM, BATTERY; TIME and STATS are put back per style by header_labels), the whole WORLD TIME strip (arrows + label)
    and the arrow glyph next to AM/PM, and move the decorative dot to the top centre. All except the
    arrow sit on pure black bezel (verified: the surrounding rows/borders are (0,0,0)), so they are
    filled with black. The arrow sits on the LCD
    panel, whose shading only varies horizontally, so each column is refilled from clean panel rows
    just above/below it."""
    im = im.copy()
    px = im.load()
    black = (0, 0, 0, 255)
    for x0, y0, x1, y1 in [(64, 6, 120, 27),      # TIME        (text bbox 67,9-117,24)
                           (188, 6, 316, 28),     # STEPS/BPM   (text bbox 191,9-313,25)
                           (0, 208, 336, 236),    # WORLD TIME strip: arrows + label (rows 213-231)
                           (40, 248, 125, 268)]:  # BATTERY     (text bbox 43,251-121,262; row 266 = its faint shadow)
        for y in range(y0, y1):
            for x in range(x0, x1):
                px[x, y] = black
    # Decorative 'LED' dot (16 px dark-grey disc, fill #1E1E1E, rim #343434, bbox 9,250-24,265 on pure
    # black; no manifest widget/data behind it). Move it to the top centre (bbox 160,14-175,29, centre
    # ~167.5,21.5) where the band OS draws its red unread-notification dot, so it acts as the socket.
    dot = im.crop((DOT_SRC[0], DOT_SRC[1], DOT_SRC[0] + 16, DOT_SRC[1] + 16))
    for y in range(DOT_SRC[1] - 2, DOT_SRC[1] + 18):
        for x in range(DOT_SRC[0] - 2, DOT_SRC[0] + 18):
            px[x, y] = black
    # (v2: the dot is no longer moved to the top centre - removed entirely at user request)
    # "SEC" label (glyph+shadow bbox ≈26-60 x 390-404) moves down SEC_SHIFT px together with the seconds digits,
    # so the seconds share the HH:MM baseline. Panel shading only varies horizontally, so each
    # column's vacated rows are refilled with that column's clean panel colour (rows 380-387).
    for x in range(24, 63):
        c = sorted(px[x, y] for y in range(380, 388))[4]
        col = [px[x, y] for y in range(389, 406)]
        for y in range(389, 406 + SEC_SHIFT):
            px[x, y] = col[y - SEC_SHIFT - 389] if 389 <= y - SEC_SHIFT < 406 else c
    sleep_label(im)
    km_label(im)
    for x in range(286, 318):                     # AM/PM-side arrow glyph (≈291-312 x 348-366)
        ref = sorted([px[x, y] for y in list(range(337, 342)) + list(range(372, 376))])
        c = ref[len(ref) // 2]
        for y in range(343, 371):
            px[x, y] = c
    return im



# ---------------------------------------------------------------- SLEEP box label
# Stock box labels are dark glyphs (+ a 2-row drop shadow) on the neutral LCD panel, glyph rows 144-156
# (KCAL | STAND) and 53-65 (STEP | BPM), shadow 2 rows below. A glyph is moved as its darkness relative
# to the clean panel of its own column (row 161 resp. 70), so the shading and shadow come along exactly.
LBL_ROWS = (143, 159)                     # KCAL/STAND glyph + shadow rows (exclusive end)
STEP_DY = 91                              # STEP row glyph tops (53) -> KCAL row glyph tops (144)
GLYPHS = {                                # letter -> (x0, x1 inclusive, source row offset, ref row)
    "S": (260, 268, 0, 161),              # S of STAND
    "L": (233, 240, 0, 161),              # L of KCAL
    "E": (218, 227, -STEP_DY, 70),        # E of STEP
    "P": (229, 239, -STEP_DY, 70),        # P of STEP
}
BOX_CX = 252                              # stat boxes: outer x 181-322 (centre 251.5), divider 253-255
SLEEP_CX = 288.5                          # centre of the stock STAND label (ink 260-317)
SLEEP_ERASE = (257, 320)                  # STAND ink + margins, right of the box divider (253-255)


KM_CX = 218                               # centre of the stock KCAL label (ink 196-240) -> now BPM
KM_ERASE = (190, 247)                     # KCAL ink + margins, left of the box divider (253-255)
UP_ROWS = (LBL_ROWS[0] - STEP_DY, LBL_ROWS[1] - STEP_DY)   # STEP/BPM glyph + shadow rows (52-67)
BPM_CX = 289                              # centre of the stock BPM label (ink 271-307) -> now KM
BPM_ERASE = (257, 320)                    # BPM ink + margins, right of the box divider


def _box_word(im, src, parts, cx, upper, erase, gap=2):
    """Erase one stat-box label and write a new word from stock box-label glyphs, copied as darkness
    relative to the clean panel of their own column (so shading and the 2-row drop shadow come along).
    parts: (x0, x1 inclusive, source row offset, source ref row); target = upper (STEP/BPM) or lower
    (KCAL/STAND) label row."""
    sp, px = src.load(), im.load()
    y0, y1 = UP_ROWS if upper else LBL_ROWS
    tref = 70 if upper else 161
    for y in range(y0, y1):
        for x in range(erase[0], erase[1] + 1):
            px[x, y] = sp[x, tref]
    total = sum(g[1] - g[0] + 1 for g in parts) + gap * (len(parts) - 1)
    x = round(cx - total / 2)
    for gx0, gx1, dy, ref in parts:
        for i in range(gx1 - gx0 + 1):
            for y in range(y0, y1):
                sx, sy, tx = gx0 + i, y + dy, x + i
                d = [sp[sx, ref][k] - sp[sx, sy][k] for k in range(3)]
                base = px[tx, tref]
                px[tx, y] = tuple(max(0, min(255, base[k] - d[k])) for k in range(3)) + base[3:]
        x += gx1 - gx0 + 1 + gap


def km_label(im):
    """1.0 swap: lower-left box label KCAL -> "BPM" (the stock B, P, M of the upper BPM label, moved down
    91 rows, stock 2 px gaps, centred on the KCAL centre x 218 like STEP above it); upper-right label
    BPM -> "KM" (stock KCAL K moved up 91 rows + stock BPM M, 2 px gap, centred on the BPM centre x 289,
    in line with SLEEP below). One pristine source copy for both, since each reads the other's glyphs."""
    src = im.copy()
    _box_word(im, src, [(271, 280, -STEP_DY, 70), (283, 292, -STEP_DY, 70), (295, 307, -STEP_DY, 70)],
              KM_CX, False, KM_ERASE)
    _box_word(im, src, [(196, 205, STEP_DY, 161), (295, 307, 0, 70)], BPM_CX, True, BPM_ERASE)
    return im


def sleep_label(im):
    """Replace the STAND label of the lower-right stat box with SLEEP built from stock box-label glyphs
    (S from STAND, L from KCAL, E and P from STEP), same size, weight, shade, shadow and 1 px letter gap."""
    src = im.copy()
    sp, px = src.load(), im.load()
    y0, y1 = LBL_ROWS
    for y in range(y0, y1):
        for x in range(SLEEP_ERASE[0], SLEEP_ERASE[1] + 1):
            px[x, y] = sp[x, 161]
    word = "SLEEP"
    widths = [GLYPHS[c][1] - GLYPHS[c][0] + 1 for c in word]
    total = sum(widths) + (len(word) - 1)
    x = round(SLEEP_CX - total / 2)
    for c, w in zip(word, widths):
        gx0, gx1, dy, ref = GLYPHS[c]
        for i in range(w):
            for y in range(y0, y1):
                sx, sy, tx = gx0 + i, y + dy, x + i
                d = [sp[sx, ref][k] - sp[sx, sy][k] for k in range(3)]
                base = px[tx, 161]
                px[tx, y] = tuple(max(0, min(255, base[k] - d[k])) for k in range(3)) + base[3:]
        x += w + 1
    return im


# ---------------------------------------------------------------- header labels
TIME_BOX = (64, 6, 120, 27)       # stock "TIME" header (glyph bbox 67,9-116,23 + AA/shadow), pure black around
STATS_POS = (220, 8)              # label_stats.png (62x18, rows 8-25): ink 220-281, centre x=250.5 (STEPS/BPM 191-311: 251)


def header_labels(im, stock_bg, tint):
    """Put the bezel header labels back into a style background: stock "TIME" over the dial and
    "STATS" (tools/assets/label_stats.png) over the two stat boxes. Original: TIME is the stock pixels
    copied back 1:1 and STATS is composited in stock header grey 153. Other styles: both labels in the
    ALARM-label colour (scheme hue at the luminance of stock header grey 153) with the stock
    anti-aliasing kept as alpha (alpha = L/153)."""
    im = im.copy()
    stats = Image.open(ASSETS / "label_stats.png").convert("RGBA")
    if tint is None:
        im.paste(stock_bg.crop(TIME_BOX), TIME_BOX[:2])
        im.alpha_composite(stats, STATS_POS)
        return im
    col = mcol(tint, 153)[:3]
    t = stock_bg.crop(TIME_BOX).convert("L").point(lambda v: 0 if v <= 2 else min(255, round(v * 255 / 153)))
    lab = Image.new("RGBA", t.size, col + (0,))
    lab.putalpha(t)
    im.alpha_composite(lab, TIME_BOX[:2])
    st = Image.new("RGBA", stats.size, col + (0,))
    st.putalpha(stats.split()[3])
    im.alpha_composite(st, STATS_POS)
    return im


# ---------------------------------------------------------------- new assets
BEZEL_TEXT = (225, 225, 225, 255)   # light digits on the black bezel (stock "WORLD" label brightness)


def fix_date_baseline(names):
    """Stock 20x28 date digits: 1, 4 and 7 end with a weaker anti-aliased bottom row than the other
    flat-bottomed digits (row 23 max alpha 116 vs 173 for '2'; bottom edge 23.45 vs 23.67 px).
    The old fix (duplicate row 22 into 23, shift the rest down) overshot: their bottom edge went to
    24.45 px, below even the round 0/8 overshoot (24.0), so a '1' looked taller than a '0' (month 10).
    Now only row 23 of 1/4/7 is strengthened toward row 22 so its peak alpha equals the '2' row 23
    (same flat-bottom edge 23.67 px, top 5.0 px for every digit); the stock shadow rows stay untouched.
    The battery-strip digits (num_bi_*) are derived from these, so they get the same fix."""
    ref = Image.open(IMG / names[2]).convert("RGBA").split()[3]
    target = max(ref.getpixel((x, 23)) for x in range(ref.width))
    for i in (1, 4, 7):
        im = Image.open(IMG / names[i]).convert("RGBA")
        px = im.load()
        a22 = max(px[x, 22][3] for x in range(im.width))
        a23 = max(px[x, 23][3] for x in range(im.width))
        f = (target - a23) / (a22 - a23)
        for x in range(im.width):
            r, g, b, a = px[x, 23]
            px[x, 23] = (r, g, b, round(a + (px[x, 22][3] - a) * f))
        im.save(IMG / names[i])


# Stock 7-segment sets where a digit is drawn off the shared segment grid (audit 1.0: every digit 0-9 of
# every set rendered from the compiled face; segment digits must be a subset of their '8'). In the HH:MM
# (ImageArray6) and temperature/alarm (ImageArray10) sets the '7' sits 1 px low and 1 px left of the
# '8' segments (top bar rows 3-6 vs 2-5); moved by (+1, -1) it matches them exactly.
SEG_FIX = {"ImageArray6": {7: (1, -1)}, "ImageArray10": {7: (1, -1)}}


def fix_segments(key, names):
    for i, (dx, dy) in SEG_FIX.get(key, {}).items():
        im = Image.open(IMG / names[i]).convert("RGBA")
        out = Image.new("RGBA", im.size, (0, 0, 0, 0))
        out.paste(im, (dx, dy))
        out.save(IMG / names[i])


def lcd_shadow_rgba(im, pad=(2, 0, 2, 4)):
    """Colour art (RGBA) -> the same art over the stock LCD drop shadow of its alpha: black, offset
    (0, +SHADOW_DY), blur SHADOW_SIGMA, opacity SHADOW_K, exactly as lcd_shadow() (its alpha is the same
    screen(G, S)). Canvas grows by `pad`; callers shift the position by -left/-top."""
    l, t, r, b = pad
    size = (im.width + l + r, im.height + t + b)
    sh = Image.new("L", size)
    sh.paste(im.split()[3], (l, t + SHADOW_DY))
    sh = sh.filter(ImageFilter.GaussianBlur(SHADOW_SIGMA)).point(lambda v: round(v * SHADOW_K))
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.putalpha(sh)
    out.alpha_composite(im.convert("RGBA"), (l, t))
    return out


def half_px_left(im):
    """Shift an image 0.5 px to the left (box filter: new[x] = (old[x] + old[x + 1]) / 2), on
    premultiplied colour so the black glyph and its shadow keep their colour."""
    im = im.convert("RGBA")
    src = im.load()
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    dst = out.load()
    for y in range(im.height):
        for x in range(im.width):
            p0 = src[x, y]
            p1 = src[x + 1, y] if x + 1 < im.width else (0, 0, 0, 0)
            a = (p0[3] + p1[3]) / 2
            if a:
                dst[x, y] = tuple(round((p0[c] * p0[3] + p1[c] * p1[3]) / 2 / a) for c in range(3)) + (round(a),)
    return out


SOCK = 22   # four sockets (BT, sleep, lock, charging) left of the date tab -> 22 px, 8 px gaps


def bt_badge(connected, lit):
    """24x24 bezel socket (same look as the stock LED dot: rim #343434, well #181818) with a Bluetooth
    rune. Connected: rune lit; disconnected: dim grey rune, no slash (same as the other off sockets)."""
    S = 4
    n = 24 * S
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([0, 0, n - 1, n - 1], fill=(52, 52, 52, 255))
    d.ellipse([2 * S, 2 * S, n - 1 - 2 * S, n - 1 - 2 * S], fill=(24, 24, 24, 255))
    col = lit if connected else (78, 78, 78, 255)
    cx = 12 * S
    pts = [(cx - 4 * S, 8.5 * S), (cx + 4 * S, 15.5 * S), (cx, 19 * S), (cx, 5 * S),
           (cx + 4 * S, 8.5 * S), (cx - 4 * S, 15.5 * S)]
    d.line(pts, fill=col, width=int(1.6 * S), joint="curve")
    return im.resize((SOCK, SOCK), Image.LANCZOS)


def status_socket(kind, on, lit):
    """Same socket as bt_badge() with a charging bolt or a padlock. on: icon lit (scheme colour /
    white); off: dim grey icon (no slash)."""
    S = 4
    n = 24 * S
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([0, 0, n - 1, n - 1], fill=(52, 52, 52, 255))
    d.ellipse([2 * S, 2 * S, n - 1 - 2 * S, n - 1 - 2 * S], fill=(24, 24, 24, 255))
    col = lit if on else (78, 78, 78, 255)
    if kind == "charge":
        d.polygon([(13 * S, 4.5 * S), (7 * S, 13 * S), (11.5 * S, 13 * S), (10.5 * S, 19.5 * S),
                   (17 * S, 10.5 * S), (12.5 * S, 10.5 * S)], fill=col)
    elif kind == "moon":   # sleep mode: crescent (disc minus an offset disc), tips pointing right
        d.ellipse([5.5 * S, 5 * S, 18.5 * S, 18 * S], fill=col)
        d.ellipse([9.6 * S, 3.2 * S, 21.6 * S, 15.2 * S], fill=(24, 24, 24, 255))
    else:   # lock
        d.rounded_rectangle([7 * S, 11 * S, 17 * S, 19 * S], radius=S, fill=col)
        d.arc([8.5 * S, 5.5 * S, 15.5 * S, 15 * S], 180, 360, fill=col, width=int(1.6 * S))
        d.line([(8.5 * S + 0.8 * S, 10.2 * S), (8.5 * S + 0.8 * S, 11.5 * S)], fill=col, width=int(1.6 * S))
        d.line([(15.5 * S - 0.8 * S, 10.2 * S), (15.5 * S - 0.8 * S, 11.5 * S)], fill=col, width=int(1.6 * S))
        d.ellipse([11 * S, 13.5 * S, 13 * S, 15.5 * S], fill=(24, 24, 24, 255))   # keyhole
    return im.resize((SOCK, SOCK), Image.LANCZOS)


def glyph_rows(names, th=128):
    """Union of solid (alpha >= th) rows over the digit frames 0-9: (top, bottom)."""
    tops, bots = [], []
    for f in names[:10]:
        a = Image.open(IMG / f).convert("RGBA").split()[3].point(lambda v: 255 if v >= th else 0)
        bb = a.getbbox()
        tops.append(bb[1]); bots.append(bb[3] - 1)
    return min(tops), max(bots)


# ---------------------------------------------------------------- v2 layout: LCD-panel glyphs and icons
# Small LCD labels (ALARM, UV) are built from the stock box-label glyphs (STEP | BPM row 53-65,
# KCAL | STAND row 144-156, cap 13 px, ~2 px stems - the same size and weight as the stock "SEC" label
# printed on the LCD panel). alpha = (110 - L) / (110 - 54) like tools/make_assets.py (110 drops the
# embossed inner shade), drawn in LCD black like SEC and the digits.
BOXG = {"K": (196, 205, 144), "C": (208, 218, 144), "A": (221, 231, 144), "L": (234, 240, 144),
        "N": (294, 303, 144), "D": (307, 317, 144), "S": (196, 204, 53), "T": (206, 216, 53),
        "E": (218, 226, 53), "P": (230, 239, 53), "B": (271, 280, 53), "M": (295, 307, 53)}
_LG = []


def box_alpha(c):
    if not _LG:
        _LG.append(stock_img(IMAGES["Image2"]).convert("L"))
    if c == "R":        # P (bowl + stem) + the lower arm of the box-label K as the leg
        p, k = box_alpha("P"), box_alpha("K")
        r = Image.new("L", (11, 13))
        r.paste(p, (0, 0))
        r.paste(ImageChops.lighter(r.crop((4, 8, 11, 13)), k.crop((3, 8, 10, 13))), (4, 8))
        return r
    if c == "F":        # E without its bottom bar
        e = box_alpha("E").copy()
        ImageDraw.Draw(e).rectangle((2, 11, 8, 12), fill=0)
        return e
    if c == "V":        # A upside down with the crossbar removed (leg pixels continued through rows 8-9)
        a = box_alpha("A").copy()
        px = a.load()
        for y in (8, 9):
            for x in range(a.width):
                px[x, y] = 0
        for y, (l0, r0) in ((8, (1, 8)), (9, (1, 8))):
            for x in (l0, l0 + 1, r0, r0 + 1):
                px[x, y] = 255
        return a.transpose(Image.FLIP_TOP_BOTTOM)
    if c == "U":        # drawn: two 2.1 px stems + round bottom, 10x13 like the box-label D/N
        SS = 16
        im = Image.new("L", (10 * SS, 13 * SS))
        d = ImageDraw.Draw(im)
        sw = 2.1 * SS
        d.ellipse((0, 3.2 * SS, 10 * SS - 1, 13 * SS - 1), fill=255)
        d.rectangle((0, 0, 10 * SS - 1, 8 * SS), fill=255)
        d.ellipse((sw, 3.2 * SS + sw, 10 * SS - 1 - sw, 13 * SS - 1 - sw), fill=0)
        d.rectangle((sw, 0, 10 * SS - 1 - sw, 8 * SS), fill=0)
        return im.resize((10, 13), Image.BOX)
    if c == "I":        # drawn: one 2.1 px stem, 13 rows (box-label stem weight)
        return _ss_draw(3, 13, lambda d, S: d.rectangle((0.45 * S, 0, 2.55 * S, 13 * S), fill=255))
    if c == "Q":        # drawn: O ring (2.1 px stroke, 11x13 like the box-label D/C) + a diagonal tail
        def q(d, S):
            sw = 2.1
            d.ellipse((0, 0, 11 * S, 13 * S), fill=255)
            d.ellipse((sw * S, sw * S, (11 - sw) * S, (13 - sw) * S), fill=0)
            d.line([(6.4 * S, 8.6 * S), (10.6 * S, 12.8 * S)], fill=255, width=round(2.2 * S))
        return _ss_draw(11, 13, q)
    x0, x1, row = BOXG[c]
    return _LG[0].crop((x0, row, x1 + 1, row + 13)).point(lambda v: max(0, min(255, round((110 - v) * 255 / 56))))


def lcd_word(text, gap=1, bold=0):
    """Word from box-label glyphs. bold = supersampled MaxFilter size (16x grid): 11 grows each stem by
    ~0.3 px per side, which matches the heavier black strokes of the stock LCD "SEC" label."""
    gl = [box_alpha(c) for c in text]
    out = Image.new("L", (sum(g.width for g in gl) + gap * (len(gl) - 1), 13))
    x = 0
    for g in gl:
        out.paste(ImageChops.lighter(out.crop((x, 0, x + g.width, 13)), g), (x, 0))
        x += g.width + gap
    if bold:
        SS = 16
        big = out.resize((out.width * SS, 13 * SS), Image.BICUBIC).filter(ImageFilter.MaxFilter(bold))
        out = big.resize(out.size, Image.BOX)
    return out


def lcd_word_scaled(text, h=15, gap=1, bold=11):
    """lcd_word at a larger cap: the 13-row box-label word scaled to h rows (16x supersampled), with the
    same MaxFilter emboldening as lcd_word(..., bold=11) so the stems keep the SEC-label weight."""
    w = lcd_word(text, gap)
    SS, nw = 16, round(w.width * h / 13)
    big = w.resize((nw * SS, h * SS), Image.BICUBIC).filter(ImageFilter.MaxFilter(bold))
    return big.resize((nw, h), Image.BOX)


def ink(alpha, col=(0, 0, 0)):
    im = Image.new("RGBA", alpha.size, tuple(col[:3]) + (0,))
    im.putalpha(alpha)
    return im


def _ss_draw(w, h, fn, SS=16):
    im = Image.new("L", (w * SS, h * SS))
    fn(ImageDraw.Draw(im), SS)
    return im.resize((w, h), Image.BOX)


def icon_bell(h=15):
    """Alarm bell, filled (like the drop / umbrella row icons): knob, domed body flaring to a rim bar,
    clapper below; h rows tall, width ~0.93 h."""
    w = round(h * 0.93)
    def f(d, S):
        W_, H_ = w * S, h * S
        u = H_ / 15.0
        d.ellipse((W_ / 2 - 1.1 * u, 0, W_ / 2 + 1.1 * u, 2.2 * u), fill=255)                # knob
        d.ellipse((0.17 * W_, 1.4 * u, 0.83 * W_, 9.0 * u), fill=255)                      # round dome
        d.rectangle((0.17 * W_, 5.2 * u, 0.83 * W_, 9.4 * u), fill=255)                    # straight sides
        d.polygon([(0.17 * W_, 8.6 * u), (0.83 * W_, 8.6 * u), (W_, 11.6 * u), (0, 11.6 * u)], fill=255)
        d.rectangle((0, 10.6 * u, W_ - 1, 11.6 * u), fill=255)                              # rim
        d.ellipse((W_ / 2 - 1.9 * u, 12.4 * u, W_ / 2 + 1.9 * u, H_ + 1.4 * u), fill=255)  # clapper (gap above)
    return _ss_draw(w, h, f)


def icon_drop(w=11, h=16):
    """Filled rain drop, point up, round bottom (bottom row = baseline)."""
    def f(d, S):
        r = w * S / 2
        cy = h * S - r
        d.ellipse((0, cy - r, w * S - 1, h * S - 1), fill=255)
        d.polygon([(w * S / 2, 0), (w * S * 0.05, cy - r * 0.25), (w * S * 0.95, cy - r * 0.25)], fill=255)
    return _ss_draw(w, h, f)


def _ss_draw_im(w, h, fn, S=16):
    """_ss_draw variant whose callback also gets the supersampled canvas (for masks)."""
    im = Image.new("L", (w * S, h * S))
    fn(ImageDraw.Draw(im), S, im)
    return im.resize((w, h), Image.BOX)

def icon_leaf(w=15, h=16):
    """Filled leaf (AQI): a lens of two circle arcs, tip up-right, with a cut-out mid vein and a short stem."""
    def f(d, S, im):
        L = Image.new("L", im.size); ld = ImageDraw.Draw(L)
        R = 9.4 * S
        # lens = intersection of two discs whose centres sit on either side of the leaf axis
        ax0, ay0, ax1, ay1 = 2.2 * S, 13.6 * S, 14.4 * S, 0.8 * S     # base -> tip
        mx, my = (ax0 + ax1) / 2, (ay0 + ay1) / 2
        dx, dy = ax1 - ax0, ay1 - ay0; ln = math.hypot(dx, dy); nx, ny = -dy / ln, dx / ln
        off = math.sqrt(max(R * R - (ln / 2) ** 2, 0))
        A = Image.new("L", im.size); B = Image.new("L", im.size)
        ImageDraw.Draw(A).ellipse((mx + nx * off - R, my + ny * off - R, mx + nx * off + R, my + ny * off + R), fill=255)
        ImageDraw.Draw(B).ellipse((mx - nx * off - R, my - ny * off - R, mx - nx * off + R, my - ny * off + R), fill=255)
        leaf = ImageChops.darker(A, B)
        vd = ImageDraw.Draw(leaf)
        vd.line([(ax0 + dx * 0.18, ay0 + dy * 0.18), (ax0 + dx * 0.80, ay0 + dy * 0.80)], fill=0, width=round(0.9 * S))
        im.paste(leaf, (0, 0))
        d.line([(0.6 * S, h * S - 0.6 * S), (ax0 + dx * 0.2, ay0 + dy * 0.2)], fill=255, width=round(1.8 * S))
    return _ss_draw_im(w, h, f)

def icon_sun(w=16, h=16):
    """Filled sun (UV): solid disc r 3.7 with 8 short round-capped rays (1.7 px)."""
    def f(d, S, im):
        cx, cy = w * S / 2, h * S / 2
        r = 3.7 * S
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
        for i in range(8):
            a = i * math.pi / 4
            r0, r1, sw = 5.6 * S, 7.4 * S, 1.75 * S
            x0, y0 = cx + r0 * math.cos(a), cy + r0 * math.sin(a); x1, y1 = cx + r1 * math.cos(a), cy + r1 * math.sin(a)
            d.line([(x0, y0), (x1, y1)], fill=255, width=round(sw))
            for x, y in ((x0, y0), (x1, y1)):
                d.ellipse((x - sw / 2, y - sw / 2, x + sw / 2, y + sw / 2), fill=255)
    return _ss_draw_im(w, h, f)


def small_percent():
    """Stock '%' (the 20x28 battery set, glyph 18x19) scaled to the 16-row small-digit glyph height."""
    a = stock_img(IMAGES["Image7"]).split()[3].crop((2, 5, 20, 24))
    SS, h = 16, 16
    w = round(a.width * h / a.height)
    return a.resize((w * SS, h * SS), Image.BICUBIC).resize((w, h), Image.BOX)


# Stock LCD drop shadow, measured on the stock digit sets (num_sec / num_t / num_d / num_s, SEC and the
# box labels): black, offset (0, +2), Gaussian blur sigma ~0.85, opacity ~0.144 (peak alpha ~37 under a
# horizontal edge, ~10 beside a stem, nothing above). Every stock LCD / stat-box glyph has it.
SHADOW_K, SHADOW_DY, SHADOW_SIGMA = 0.144, 2, 0.85


def lcd_shadow(a, pad=(2, 0, 2, 4)):
    """Glyph alpha (L) -> same glyph + the stock LCD drop shadow (black ink, so alpha = screen(G, S)).
    The canvas grows by `pad` (left, top, right, bottom); callers shift the position by -left/-top."""
    l, t, r, b = pad
    size = (a.width + l + r, a.height + t + b)
    g = Image.new("L", size)
    g.paste(a, (l, t))
    sh = Image.new("L", size)
    sh.paste(a, (l, t + SHADOW_DY))
    sh = sh.filter(ImageFilter.GaussianBlur(SHADOW_SIGMA)).point(lambda v: round(v * SHADOW_K))
    return ImageChops.screen(g, sh)


def deshadow(a):
    """Stock glyph alpha -> alpha without the soft drop shadow (alpha <= ~38), for light copies on the
    black bezel (the bezel's stock text, TIME/STATS and the dial numerals, has no shadow)."""
    return a.point(lambda v: v if v >= 80 else max(0, round((v - 38) * 80 / 42)))


# Middle-strip battery: icon | 20-segment bar | digits + '%'
BAR_N = 20
BAR_H = 14
BAR_SEG_GAP = 2.5
BI_DIG_H = BAR_H                            # % digits match bar / icon height (14)
# Compact battery icon (no digits inside): body + nub, fill tracks charge.
BI_BODY_W, BI_H, BI_NUB = 24, 14, 3
BI_W, BI_FILL_MAX, BI_FILL_STEP = BI_BODY_W + BI_NUB, BI_BODY_W - 6, 2


def batt_icon_small(fill_px, outline, fillc):
    """Strip battery icon left of the bar: body + nub; fill_px of BI_FILL_MAX inside the rim."""
    SS = 8
    im = Image.new("RGBA", (BI_W * SS, BI_H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, BI_BODY_W * SS - 1, BI_H * SS - 1), radius=3 * SS, fill=outline)
    d.rounded_rectangle((2 * SS, 2 * SS, (BI_BODY_W - 2) * SS - 1, (BI_H - 2) * SS - 1), radius=1.5 * SS,
                        fill=(0, 0, 0, 255))
    d.rounded_rectangle(((BI_BODY_W + 0.5) * SS, 4 * SS, BI_W * SS - 1, (BI_H - 4) * SS - 1), radius=1 * SS,
                        fill=outline)
    if fill_px:
        d.rectangle((3 * SS, 3 * SS, (3 + fill_px) * SS - 1, (BI_H - 3) * SS - 1), fill=fillc)
    return im.resize((BI_W, BI_H), Image.LANCZOS)


def batt_bar_frame(n_lit, bar_w, bar_h=BAR_H, unlit=(70, 70, 70, 255), lit=(153, 153, 153, 255)):
    """20-segment battery bar for the black bezel strip. Lit = header grey 153; unlit = dim 70
    (both scheme-tinted per style)."""
    SS = 8
    n = BAR_N
    gap = BAR_SEG_GAP
    seg_w = (bar_w - (n - 1) * gap) / n
    im = Image.new("RGBA", (max(1, round(bar_w * SS)), max(1, round(bar_h * SS))), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i in range(n):
        x0 = i * (seg_w + gap) * SS
        x1 = x0 + seg_w * SS
        col = lit if i < n_lit else unlit
        d.rounded_rectangle((x0, 0, x1 - 1, bar_h * SS - 1), radius=max(1, round(1.2 * SS)), fill=col)
    return im.resize((bar_w, bar_h), Image.LANCZOS)


def batt_digit(src_alpha, col, h=None, *, halo=0):
    """Stock date/battery digit (glyph rows 5-23, shadow removed) scaled to BI_DIG_H rows, emboldened
    2/16 px per side, in `col`. Optional 1 px black halo (halo=1) for contrast on busy fills."""
    h = h or BI_DIG_H
    a = src_alpha.crop((0, 5, src_alpha.width, 24))
    big = a.resize((a.width * 16, 19 * 16), Image.BICUBIC).filter(ImageFilter.MaxFilter(5))
    g = big.resize((round(a.width * h / 19), h), Image.BOX)
    pad = 1 if halo else 0
    can = Image.new("L", (g.width + 2 * pad, h + 2 * pad))
    can.paste(g, (pad, pad))
    out = Image.new("RGBA", can.size, (0, 0, 0, 0))
    if halo:
        out.putalpha(can.filter(ImageFilter.MaxFilter(3)))
    t = Image.new("RGBA", can.size, tuple(col[:3]) + (255,))
    t.putalpha(can)
    out.alpha_composite(t)
    return out


# ---------------------------------------------------------------- widget builders
XSI = 'xmlns:p3="http://www.w3.org/2001/XMLSchema-instance"'
W = []          # list of dicts, drawn in order (z-order)


def w_image(name, x, y, bitmap, size=None):
    if size is None:
        size = Image.open(IMG / bitmap).size
    W.append(dict(t="img", Name=name, X=x, Y=y, Width=size[0], Height=size[1], Bitmap=bitmap))


def w_num(name, anchor_x, y, src, bitmaps, digits, spacing, align, blank, sample):
    """anchor_x = stock manifest x (left edge / centre / right edge for align 0/1/2).
    Compiler.exe (DefaultPacker.SetElementProp) stores anchor = X + w*digits/2 (centre) or X + w*digits
    (right), ignoring spacing, so X is derived the same way to land exactly on the stock anchor."""
    dw, dh = Image.open(IMG / bitmaps[0]).size
    x = {0: anchor_x, 1: anchor_x - (dw * digits) // 2, 2: anchor_x - dw * digits}[align]
    W.append(dict(t="num", Name=name, X=x, Y=y, Width=dw, Height=dh, Digits=digits, anchor=anchor_x,
                  Alignment=align, Spacing=spacing, Blanking=1 if blank else 0, Value_Src=src,
                  BitmapList=bitmaps, sample=sample))


def w_list(name, x, y, src, pairs, sample):
    sz = Image.open(IMG / pairs[0][1]).size
    W.append(dict(t="list", Name=name, X=x, Y=y, Width=sz[0], Height=sz[1], Index_Src=src,
                  pairs=pairs, sample=sample))


def w_tap(key, x, y, w, h):
    # Like the official Xiaomi build of this face (stock resource.bin): every tap zone has its own fully
    # transparent bitmap of exactly the zone size. Compiler.exe takes the action from
    # Name.Split('[', ']')[1]; a key "x_tag" gives "btn_[x]_tag_RGBA".
    key = TAP_ALIAS.get(key, key)
    action, _, tag = key.partition("_")
    bmp = put(Image.new("RGBA", (w, h), (0, 0, 0, 0)), f"btn_{key}.png")
    W.append(dict(t="tap", Name=f"btn_[{action}]{'_' + tag if tag else ''}_RGBA", X=x, Y=y, Width=w, Height=h,
                  Bitmap=bmp))


# KM cell: Compiler.exe has no Workout/sport action, so the zone is packed with unused `breath` and
# rewritten by patch_taps() to sport (Workouts). From Desktop refs Roundabout/Stat rings:
#   jumpApp=sport  GMF 0100f710  -> EasyFace 0100f300  (byte2 7->3, byte3 clear 0x10)
# Same transform maps trainingStatus 01009713->01009303 (Fitness State) and sportsRecord
# 0100d711->0100d301 (history). Full-size bitmap.
TAP_ALIAS = {"distance": "breath_workout"}


SUB = {}   # per-style bitmap substitutions (original name -> style copy)


def S(f):
    return SUB.get(f, f)


def fprj_xml(style_bg, title, preview_name):
    out = ['<?xml version="1.0" encoding="utf-16"?>',
           f'<FaceProject DeviceType="{DEVICE_TYPE}" Name="{DISPLAY_NAME}" Id="{FACE_ID}">',
           f'<Screen Title="{title}" Bitmap="{preview_name}">']
    for d in W:
        common = f'Name="{d["Name"]}" X="{d["X"]}" Y="{d["Y"]}" Width="{d["Width"]}" Height="{d["Height"]}" Alpha="255" Visible_Src="0"'
        if d["t"] in ("img", "tap", "bg"):
            bmp = style_bg if d["t"] == "bg" else S(d["Bitmap"])
            out.append(f'<Widget p3:type="FaceWidgetImage" Shape="30" {common} Bitmap="{bmp}" {XSI} />')
        elif d["t"] == "num":
            out.append(f'<Widget p3:type="FaceWidgetDigitalNum" Shape="32" {common} Digits="{d["Digits"]}" '
                       f'Alignment="{d["Alignment"]}" Spacing="{d["Spacing"]}" Blanking="{d["Blanking"]}" '
                       f'Value_Src="{d["Value_Src"]}" BitmapList="{"|".join(S(f) for f in d["BitmapList"])}" {XSI} />')
        elif d["t"] == "list":
            bl = "|".join(f"({k}):{S(f)}" for k, f in d["pairs"])
            out.append(f'<Widget p3:type="FaceWidgetImageList" Shape="31" {common} Index_Src="{d["Index_Src"]}" '
                       f'DefaultIndex="0" BitmapList="{bl}" {XSI} />')
        elif d["t"] == "analog":
            out.append(f'<Widget p3:type="FaceWidgetAnalogClock" Shape="27" {common} HourHandCorrection_En="1" '
                       f'MinuteHandCorrection_En="0" BgImage_rotate_xc="{d["cx"]}" BgImage_rotate_yc="{d["cy"]}" '
                       f'HourHand_ImageName="{S(d["hour"][0])}" HourImage_rotate_xc="{d["hour"][1]}" HourImage_rotate_yc="{d["hour"][2]}" '
                       f'MinuteHand_Image="{S(d["minute"][0])}" MinuteImage_rotate_xc="{d["minute"][1]}" MinuteImage_rotate_yc="{d["minute"][2]}" '
                       f'SecondHand_Image="{S(d["second"][0])}" SecondImage_rotate_xc="{d["second"][1]}" SecondImage_rotate_yc="{d["second"][2]}" {XSI} />')
    out += ['</Screen>', '</FaceProject>', '']
    return "\r\n".join(out)


# ---------------------------------------------------------------- preview renderer
MODE = "floor"   # centre rounding: floor matches the stock preview pixel-for-pixel
SAMPLE = dict(hour=10, minute=9, second=24)


def _lv(v):
    """ImageList value of a sample: decimal-mode samples are text ("7.5"); compared in tenths."""
    return round(float(v) * 10) if isinstance(v, str) else v


def render(bg_name):
    can = Image.new("RGBA", (336, 480), (0, 0, 0, 255))
    for d in W:
        if d["t"] == "bg":
            can.alpha_composite(Image.open(IMG / bg_name).convert("RGBA"), (0, 0))
        elif d["t"] == "img":
            can.alpha_composite(Image.open(IMG / S(d["Bitmap"])).convert("RGBA"), (d["X"], d["Y"]))
        elif d["t"] == "list":
            v = d["sample"]
            f = S([f for k, f in d["pairs"] if k <= _lv(v)][-1])
            can.alpha_composite(Image.open(IMG / f).convert("RGBA"), (d["X"], d["Y"]))
        elif d["t"] == "num":
            # device semantics: shown digits (leading zeros dropped when Blanking=1) laid out with
            # Spacing, then left/centre/right-aligned on the anchor
            n, sp, dw = d["Digits"], d["Spacing"], d["Width"]
            v = d["sample"]
            if isinstance(v, str):                   # decimal mode (Digits=20): sample is the shown text
                txt = v
            else:
                txt = str(abs(v))[-n:]
                if not d["Blanking"]:
                    txt = txt.zfill(n)
                if v < 0:
                    txt = "-" + txt
            total = len(txt) * dw + (len(txt) - 1) * sp
            x0 = {0: d["anchor"], 1: d["anchor"] - (total // 2 if MODE == "floor" else (total + 1) // 2), 2: d["anchor"] - total}[d["Alignment"]]
            for i, ch in enumerate(txt):
                f = S(d["BitmapList"][10 if ch == "-" else 11 if ch == "." else int(ch)])
                can.alpha_composite(Image.open(IMG / f).convert("RGBA"), (x0 + i * (dw + sp), d["Y"]))
        elif d["t"] == "analog":
            cx, cy = d["X"] + d["cx"], d["Y"] + d["cy"]
            h, m, sec = SAMPLE["hour"], SAMPLE["minute"], SAMPLE["second"]
            for (f, px, py), ang in ((d["hour"], (h % 12 + m / 60) * 30), (d["minute"], m * 6 + sec / 10),
                                     (d["second"], sec * 6)):
                im = Image.open(IMG / S(f)).convert("RGBA")
                big = Image.new("RGBA", (400, 400))
                big.alpha_composite(im, (200 - px, 200 - py))
                big = big.rotate(-ang, resample=Image.BICUBIC, center=(200, 200))
                can.alpha_composite(big, (cx - 200, cy - 200))
    return can



# ---------------------------------------------------------------- compiled-face renderer / checker
def _face_tables(d, fi):
    dw = lambda o: struct.unpack_from("<I", d, o)[0]
    base = 168 + fi * 88

    def table(k):
        n, p = dw(base + 8 + k * 8), dw(base + 8 + k * 8 + 4)
        return {dw(p + i * 16): (dw(p + i * 16 + 8), dw(p + i * 16 + 12)) for i in range(n)}, \
            [dw(p + i * 16) for i in range(n)]
    return table


# ---------------------------------------------------------------- tap (action) records
# Compiler.exe v4.23 packs btn_[...] widgets for DeviceType 567 with RedmiWatch4ActionPacker: a 56-byte
# record = 2-byte ASCII id, jump code at +40 (4 bytes), image id at +48, then widget Width at +52 and
# Height at +54. Xiaomi's own build of this very face (stock/resource.bin, Mi Band 10 Pro, protocol
# 1.8.11) differs in two ways for the same targets:
#   * size words are Height at +52, Width at +54 (stock steps 70x60 zone -> 3c00 4600, weather 140x32
#     -> 2000 8c00), i.e. EasyFace swaps them;
#   * jump codes of the data pages carry a 0x10 flag in byte 3 (and weather uses type 01, not 05):
#       steps/kcal/stand  EasyFace 01 00 33 01 -> stock 01 00 33 11
#       heart rate        EasyFace 01 00 53 00 -> stock 01 00 53 10
#       weather           EasyFace 05 00 13 02 -> stock 01 00 13 12
# The app jumps (alarm, calendar, flashlight, stopwatch, timer) are not in the stock face; they keep
# EasyFace's codes (only the size order is fixed). patch_taps() rewrites the records after compiling.
TAP_CODES = {   # EasyFace 2-byte id -> (EasyFace code, code written to the face)
    b"1\0": ("01003301", "01003311"),   # steps
    b"2\0": ("01003301", "01003311"),   # calories
    b"3\0": ("01003301", "01003311"),   # standing (unused since SLEEP replaced STAND)
    b"8\0": ("0100c300", "0100c310"),   # sleep (0x10 flag like the stock data-page jumps)
    b"5\0": ("01005300", "01005310"),   # hrm
    b"10": ("05001302", "01001312"),     # weather
    b"25": ("01004300", "0100f300"),     # breath placeholder -> sport/Workouts (KM zone)
}


def tap_records(d):
    out = []
    for fi in face_style_indices(d):
        if is_aod_style(d, fi):
            continue
        recs, order = _face_tables(d, fi)(9)
        for rid in order:
            out.append((fi, rid, recs[rid][0]))
    return out


def patch_taps(raw):
    n = 0
    for fi, rid, o in tap_records(raw):
        code = bytes(raw[o + 40:o + 44]).hex()
        old_new = TAP_CODES.get(bytes(raw[o:o + 2]))
        if old_new and code == old_new[0]:
            raw[o + 40:o + 44] = bytes.fromhex(old_new[1])
        w, h = struct.unpack_from("<HH", raw, o + 52)
        struct.pack_into("<HH", raw, o + 52, h, w)          # official order: Height, Width
        n += 1
    return n


def _bgra(d, o, w, h):
    return Image.frombuffer("RGBA", (w, h), d[o:o + w * h * 4], "raw", "BGRA", 0, 1).copy()


def render_compiled(d, fi, samples):
    """Render style #fi straight from the compiled .face: element positions, widget records and the
    packed (raw BGRA) images, with the same digit/list/hand semantics as render(). Returns (image,
    list of digit-widget facts for the alignment report)."""
    table = _face_tables(d, fi)
    elems, order = table(0)
    imgs, _ = table(2)
    lists, _ = table(3)
    wids, _ = table(7)
    u16 = lambda o: struct.unpack_from("<H", d, o)[0]
    s16 = lambda o: struct.unpack_from("<h", d, o)[0]
    dw = lambda o: struct.unpack_from("<I", d, o)[0]

    def image(iid):
        o, _ = imgs[iid]
        return _bgra(d, o + 12, u16(o + 4), u16(o + 6))

    def frames(lid):
        o, _ = lists[lid]
        n, w, h = d[o + 1], u16(o + 4), u16(o + 6)
        return [_bgra(d, o + 12 + i * w * h * 4, w, h) for i in range(n)]

    can = Image.new("RGBA", (336, 480), (0, 0, 0, 255))
    facts = []
    for eid in order:
        eo, _ = elems[eid]
        tid, x, y = dw(eo), s16(eo + 4), s16(eo + 6)
        kind = tid >> 24
        if kind == 2:
            can.alpha_composite(image(tid), (x, y))
        elif kind != 7:
            continue                                         # 9 = tap/action (transparent)
        else:
            wo, wsz = wids[tid]
            src = d[wo:wo + 2].hex()
            flags = d[wo + 3]
            if flags == 0x30:                                # analog hand
                im = image(dw(wo + 8))
                rx, ry = u16(wo + 20), u16(wo + 22)
                hh, mm, ss = SAMPLE["hour"], SAMPLE["minute"], SAMPLE["second"]
                ang = {"0811": (hh % 12 + mm / 60) * 30, "1011": mm * 6 + ss / 10, "1811": ss * 6}[src]
                big = Image.new("RGBA", (400, 400))
                big.alpha_composite(im, (200 - rx, 200 - ry))
                big = big.rotate(-ang, resample=Image.BICUBIC, center=(200, 200))
                can.alpha_composite(big, (x + rx - 200, y + ry - 200))
            elif flags == 0x20:                              # image list
                fr = frames(dw(wo + 8))
                v = _lv(samples[src])
                if dw(wo + 12) == 512:
                    keys = [dw(wo + 16 + 4 * i) for i in range(len(fr))]
                    idx = max(i for i, k in enumerate(keys) if k <= v)
                else:
                    idx = v
                can.alpha_composite(fr[idx], (x, y))
            else:                                            # digital number
                fr = frames(dw(wo + 8))
                n, sp = d[wo + 2], struct.unpack_from("<b", d, wo + 13)[0]
                lead = bool(flags & 4)
                align = {1: 0, 2: 1, 0: 2}[flags & 3]        # packed code -> 0 left / 1 centre / 2 right
                dw_, dh = fr[0].size
                v = samples[src]
                if isinstance(v, str):
                    txt = v
                else:
                    txt = str(abs(v))[-n:]
                    if lead:
                        txt = txt.zfill(n)
                    if v < 0:
                        txt = "-" + txt
                total = len(txt) * dw_ + (len(txt) - 1) * sp
                x0 = {0: x, 1: x - total // 2, 2: x - total}[align]
                for i, ch in enumerate(txt):
                    can.alpha_composite(fr[10 if ch == "-" else 11 if ch == "." else int(ch)], (x0 + i * (dw_ + sp), y))
                tops, bots = [], []
                for f in fr[:10]:
                    bb = f.split()[3].point(lambda a: 255 if a >= 128 else 0).getbbox()
                    tops.append(bb[1]); bots.append(bb[3] - 1)
                facts.append(dict(src=src, x=x, y=y, w=dw_, h=dh, sp=sp, align=align, digits=n,
                                  top=y + min(tops), base=y + max(bots)))
    return can, facts


# ---------------------------------------------------------------- tap zones
TAP_PAD = 4          # padding around an element's ink
TAP_MIN = 44         # minimum zone side (comfortable finger target)
TAP_GAP = 16         # gaps up to this size between these neighbouring zones are closed (bezel between
TAP_CLOSE = {        # the dial and the stat boxes stays a gap; the 2x2 box grid is fixed equal cells)
    ("alarm", "weather"), ("weather", "timer"), ("alarm", "timer"),
    ("calendar", "weather"), ("stopwatch", "flashlight")}
BOX_UP, BOX_LO = (42, 116), (132, 206)    # stat boxes, rows (exclusive end); x 181-322, divider 253-255
# Equal 2x2 tap grid over the STATS boxes (same w/h each cell, shared edges). 75x80:
# left 179..254, right 254..329; upper 40..120, lower 120..200 (meets strip at 200).
STAT_TAP_LX, STAT_TAP_MX, STAT_TAP_RX = 179, 254, 329
STAT_TAP_YU, STAT_TAP_MY, STAT_TAP_YB = 40, 120, 200
STAT_TAP_W = STAT_TAP_MX - STAT_TAP_LX   # 75
STAT_TAP_H = STAT_TAP_MY - STAT_TAP_YU   # 80
# Middle strip (battery bar + %): flashlight tap band (x edges set from stopwatch/calendar in tap_zones).
STRIP_TAP_Y0, STRIP_TAP_Y1 = 200, 245    # h = 45; meets calendar at 245
DATE_TAB = (140, 247, 323, 291)           # weekday/date tab (slanted left edge 154@246 -> 133@270)
SEC_LABEL = (24, 389, 62, 406)            # "SEC" label baked into the background
STATIC_BOUNDS = {}                        # v2 LCD statics baked into the background (name -> ink bounds)


def _ink(f):
    return Image.open(IMG / f).convert("RGBA").split()[3].getbbox()


def widget_bounds(name, worst=None):
    """Ink bounds (x0, y0, x1, y1 exclusive) of widget `name`; numbers at their widest sample (`worst`
    = shown text), lists over all frames."""
    d = next(w for w in W if w["Name"] == name)
    if d["t"] in ("img", "tap"):
        b = _ink(d["Bitmap"])
        return (d["X"] + b[0], d["Y"] + b[1], d["X"] + b[2], d["Y"] + b[3])
    if d["t"] == "list":
        bs = [_ink(f) for _, f in d["pairs"] if _ink(f)]
        return (d["X"] + min(b[0] for b in bs), d["Y"] + min(b[1] for b in bs),
                d["X"] + max(b[2] for b in bs), d["Y"] + max(b[3] for b in bs))
    if d["t"] == "num":
        txt = worst if worst is not None else str(d["sample"])
        if worst is None and not d["Blanking"] and not isinstance(d["sample"], str):
            txt = txt.zfill(d["Digits"])
        dw, sp = d["Width"], d["Spacing"]
        total = len(txt) * dw + (len(txt) - 1) * sp
        x0 = {0: d["anchor"], 1: d["anchor"] - total // 2, 2: d["anchor"] - total}[d["Alignment"]]
        bs = [_ink(f) for f in d["BitmapList"][:10]]
        return (x0, d["Y"] + min(b[1] for b in bs), x0 + total, d["Y"] + max(b[3] for b in bs))
    raise ValueError(name)


def _union(rects):
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


def tap_zones():
    """Each zone = ink bounds of its element group + TAP_PAD (stat boxes / date tab: the panel itself),
    grown to TAP_MIN, clipped to the screen, then any overlap split at its middle.

    Post-normalize:
      * STATS 2x2 → equal cells (STAT_TAP_*), then workouts/sleep right = calendar right
      * flashlight: left = stopwatch left, right = calendar right, y = STRIP_TAP_*
      * calendar + weather → same left/right (one LCD column)
      * alarm right edge meets weather left; timer spans alarm-left .. weather-right
    """
    R = DIAL_R
    SB = STATIC_BOUNDS
    groups = [
        # dial = stopwatch, alarm = LCD next-alarm block, timer = whole digital clock
        ("stopwatch", [(DIAL_C[0] - R, DIAL_C[1] - R, DIAL_C[0] + R + 1, DIAL_C[1] + R + 1)], TAP_PAD),
        ("alarm", [SB["alarm_label"], SB["alarm_icon"]] + [widget_bounds(n) for n in ("alarm_hour", "alarm_colon", "alarm_minute")],
         TAP_PAD),
        # equal 2x2 (pad already baked into STAT_TAP_*); pad=0 so sizes stay identical
        ("steps", [(STAT_TAP_LX, STAT_TAP_YU, STAT_TAP_MX, STAT_TAP_MY)], 0),
        ("hrm", [(STAT_TAP_LX, STAT_TAP_MY, STAT_TAP_MX, STAT_TAP_YB)], 0),
        ("sleep", [(STAT_TAP_MX, STAT_TAP_MY, STAT_TAP_RX, STAT_TAP_YB)], 0),
        ("distance", [(STAT_TAP_MX, STAT_TAP_YU, STAT_TAP_RX, STAT_TAP_MY)], 0),
        ("weather", [widget_bounds("weather_icon"), widget_bounds("temp_c", "25"), widget_bounds("temp_degree"),
                     widget_bounds("temp_high", "-12"), widget_bounds("high_degree"), widget_bounds("high_arrow"),
                     widget_bounds("temp_low", "-12"), widget_bounds("low_degree"), widget_bounds("low_arrow"),
                     SB["uv_label"], widget_bounds("uv_index", "3"), SB["hum_icon"], widget_bounds("humidity", "65"),
                     widget_bounds("humidity_pct"), SB["aqi_label"], widget_bounds("aqi", "158")], TAP_PAD),
        ("calendar", [DATE_TAB], 2),
        ("flashlight", [(0, STRIP_TAP_Y0, 336, STRIP_TAP_Y1)], 0),  # x fixed later from stopwatch/calendar
        ("timer", [widget_bounds("seconds"), SEC_LABEL] + [widget_bounds(n) for n in ("hours", "time_colon", "minutes")],
         TAP_PAD),
    ]
    rects = []
    for name, rs, pad in groups:
        x0, y0, x1, y1 = _union(rs)
        x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
        if x1 - x0 < TAP_MIN:
            g = TAP_MIN - (x1 - x0); x0 -= g // 2; x1 += g - g // 2
        if y1 - y0 < TAP_MIN:
            g = TAP_MIN - (y1 - y0); y0 -= g // 2; y1 += g - g // 2
        rects.append([name, max(0, x0), max(0, y0), min(336, x1), min(480, y1)])

    by = {r[0]: r for r in rects}

    # --- calendar + weather share one column (same left/right) ---
    cal, wx = by["calendar"], by["weather"]
    col_l = min(cal[1], wx[1])
    col_r = max(cal[3], wx[3])
    cal[1], wx[1] = col_l, col_l
    cal[3], wx[3] = col_r, col_r

    # --- alarm meets weather on the shared vertical edge; timer spans both ---
    al, tm = by["alarm"], by["timer"]
    al[3] = wx[1]
    tm[1], tm[3] = al[1], wx[3]
    # shared floor between (alarm|weather) and timer
    floor = min(al[4], wx[4], tm[2])
    al[4], wx[4], tm[2] = floor, floor, floor

    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            ox = min(a[3], b[3]) - max(a[1], b[1])
            oy = min(a[4], b[4]) - max(a[2], b[2])
            if ox <= 0 or oy <= 0:
                continue
            if ox <= oy:          # split the narrower overlap at its middle
                lo, hi = (a, b) if a[1] < b[1] else (b, a)
                m = (max(a[1], b[1]) + min(a[3], b[3])) // 2
                lo[3], hi[1] = m, m
            else:
                lo, hi = (a, b) if a[2] < b[2] else (b, a)
                m = (max(a[2], b[2]) + min(a[4], b[4])) // 2
                lo[4], hi[2] = m, m
    # close small gaps between neighbours (so a tap between two targets still hits one): zones that
    # overlap on one axis and are <= TAP_GAP apart on the other meet in the middle of the gap
    def clash():
        return any(min(p[3], q[3]) > max(p[1], q[1]) and min(p[4], q[4]) > max(p[2], q[2])
                   for k, p in enumerate(rects) for q in rects[k + 1:])

    for i in range(len(rects)):
        for j in range(len(rects)):
            a, b = rects[i], rects[j]
            if i == j or (a[0], b[0]) not in TAP_CLOSE:
                continue
            for lo_i, hi_i, ov in ((3, 1, min(a[4], b[4]) > max(a[2], b[2])),
                                   (4, 2, min(a[3], b[3]) > max(a[1], b[1]))):
                if ov and 0 < b[hi_i] - a[lo_i] <= TAP_GAP:
                    keep = (a[lo_i], b[hi_i])
                    m = (a[lo_i] + b[hi_i]) // 2
                    a[lo_i], b[hi_i] = m, m
                    if clash():       # meeting in the middle collides: grow only one of the two instead
                        a[lo_i], b[hi_i] = keep[1], keep[1]
                        if clash():
                            a[lo_i], b[hi_i] = keep[0], keep[0]
                            if clash():
                                a[lo_i], b[hi_i] = keep

    # Re-assert stats grid, then lock shared vertical edges:
    #   flashlight left  = stopwatch left
    #   flashlight right = calendar right (= weather right)
    #   workouts/sleep right = calendar right
    for name, x0, y0, x1, y1 in (
        ("steps", STAT_TAP_LX, STAT_TAP_YU, STAT_TAP_MX, STAT_TAP_MY),
        ("distance", STAT_TAP_MX, STAT_TAP_YU, STAT_TAP_RX, STAT_TAP_MY),
        ("hrm", STAT_TAP_LX, STAT_TAP_MY, STAT_TAP_MX, STAT_TAP_YB),
        ("sleep", STAT_TAP_MX, STAT_TAP_MY, STAT_TAP_RX, STAT_TAP_YB),
    ):
        r = by[name]
        r[1], r[2], r[3], r[4] = x0, y0, x1, y1
    sw_l = by["stopwatch"][1]
    cal_r = by["calendar"][3]
    by["flashlight"][1], by["flashlight"][2] = sw_l, STRIP_TAP_Y0
    by["flashlight"][3], by["flashlight"][4] = cal_r, STRIP_TAP_Y1
    by["distance"][3] = cal_r
    by["sleep"][3] = cal_r

    return [(n, x0, y0, x1 - x0, y1 - y0) for n, x0, y0, x1, y1 in rects]


# ---------------------------------------------------------------- build
def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    compile_ = "--no-compile" not in sys.argv
    W.clear()
    if PROJ.exists():
        shutil.rmtree(PROJ)
    IMG.mkdir(parents=True)
    PREV.mkdir(exist_ok=True)

    # backgrounds (RGB565 via _RGB widget name)
    base_bg = clean_background(stock_img(IMAGES["Image2"]))
    stock_bg = stock_img(IMAGES["Image2"])

    hand_min = export_single("Image3", "hand_minute.png")
    hand_hour = export_single("Image4", "hand_hour.png")
    hand_sec = export_single("Image5", "hand_second.png")
    date_sep = export_single("Image6", "date_sep.png")
    colon = export_single("Image10", "time_colon.png")
    degree = export_single("Image11", "temp_degree.png")
    hub = export_single("Image12", "hub.png")

    d_small = export_array("ImageArray1", "num_s")                 # 14x26 health digits
    d_hr = export_array("ImageArray2", "num_hr")                   # + no-data dash
    week = export_array("ImageArray3", "week")
    d_date = export_array("ImageArray4", "num_d")                  # 20x28 date/battery digits
    fix_date_baseline(d_date)                                      # 1/4/7 bottom row matched to the flat-bottomed 2
    d_big = export_array("ImageArray6", "num_big")                 # 48x78
    fix_segments("ImageArray6", d_big)                             # '7' back on the segment grid
    d_sec = export_array("ImageArray7", "num_sec")                 # 26x42
    wx = export_array("ImageArray9", "wx")
    t11 = export_array("ImageArray10", "num_t")                    # stock 28x38 temperature digits + minus
    fix_segments("ImageArray10", t11)                              # '7' back on the segment grid
    # 11th bitmap of a DigitalNum = minus sign. Use 0 where the stock set has none.
    small11 = d_small + [d_small[0]]
    date11 = d_date + [d_date[0]]
    big11 = d_big + [d_big[0]]
    sec11 = d_sec + [d_sec[0]]
    s_top, s_bot = glyph_rows(d_small)                             # small digits: solid rows 6..21
    t_top, t_bot = glyph_rows(t11)                                 # temperature digits: 4..32
    pct_small = small_percent()                                    # 15x16 alpha
    PCT_W = pct_small.width

    # ---- row 1 (stock weather row): icon at the stock spot; main temp, then today's high/low, then icon
    WX_POS = (265, 293)                                            # stock 46x46 icon, ink x 267-310
    # Main temperature: stock 28x38 digits + stock degree, right-aligned (anchor = last cell's right edge,    # ink 4 px before; degree at anchor-3, ink anchor-3 .. anchor+7).
    # Feels-like (1831, never filled by Mi Fitness on the Band 10 Pro) replaced by TODAY'S HIGH / LOW
    # (1832 / 2032) as two stacked rows of the stock 14x26 box digits between the temperature and the icon:
    # value right-aligned, small degree ring at a fixed column; secondary grey (glyph at 50 % ink alpha,
    # stock drop shadow kept at full strength).
    FEEL_ALPHA = 0.5
    def grey_a(al):
        # alpha a -> a (a <= 40), 40 (40..80), a * 0.5 (> 80); continuous, monotone
        return al.point(lambda v: v if v <= 40 else max(40, round(v * FEEL_ALPHA)))
    def lighter(f, name):
        im = Image.open(IMG / f).convert("RGBA")
        r, g, b_, al = im.split()
        return put(Image.merge("RGBA", (r, g, b_, grey_a(al))), name)
    # minus for the box digits (stock set has none): a 2-row bar on the glyph's middle rows, ink cols 3-10,
    # drawn like a stock stroke (solid core, AA ends), plus the stock LCD drop shadow
    sw, sh_ = Image.open(IMG / d_small[0]).size
    mid = (s_top + s_bot) / 2
    mn = _ss_draw(sw, sh_, lambda d, S: d.rectangle((3.2 * S, (mid - 0.5) * S, 10.8 * S, (mid + 1.6) * S), fill=255))
    small_minus = put(ink(lcd_shadow(mn, pad=(0, 0, 0, 0))), "num_s_minus.png")
    hl11 = [lighter(f, f"num_hl_{i:02d}.png") for i, f in enumerate(d_small)] + [lighter(small_minus, "num_hl_10.png")]
    # small degree ring (stock degree scaled to the box-digit cap: 7 px, ~1.7 px stroke)
    def ring(d, S):
        d.ellipse((0.6 * S, 0.6 * S, 7.4 * S, 7.4 * S), fill=255)
        d.ellipse((2.3 * S, 2.3 * S, 5.7 * S, 5.7 * S), fill=0)
    def grey_glyph(a, name):
        return put(ink(grey_a(lcd_shadow(a))), name)
    hl_deg = grey_glyph(_ss_draw(8, 8, ring), "hl_degree.png")
    # Small up/down arrows (same secondary grey as high/low digits), left of each value.
    AW, AH = 7, 9
    def tri_up(d, S):
        d.polygon([(AW * S / 2, 0.8 * S), ((AW - 0.6) * S, (AH - 0.8) * S), (0.6 * S, (AH - 0.8) * S)], fill=255)
    def tri_dn(d, S):
        d.polygon([(0.6 * S, 0.8 * S), ((AW - 0.6) * S, 0.8 * S), (AW * S / 2, (AH - 0.8) * S)], fill=255)
    hl_up = grey_glyph(_ss_draw(AW, AH, tri_up), "hl_arrow_up.png")
    hl_dn = grey_glyph(_ss_draw(AW, AH, tri_dn), "hl_arrow_dn.png")
    # Order (left → right): main temp, arrows + high/low, weather icon — equal *ink* gaps.
    def _solid_bb(name, thr=80):
        a = Image.open(IMG / name).split()[3].point(lambda v: v if v >= thr else 0)
        return a.getbbox()
    G_ROW1 = 8
    HL_DW, HL_SP = 14, -3
    ARROW_GAP = 2
    # Solid ink edges (not widget boxes) so temp↔HL and HL↔icon air matches.
    wx_ink_l = WX_POS[0] + min(_solid_bb(f)[0] for f in wx)
    hl_db = _solid_bb(hl_deg)
    HL_DEG_X = wx_ink_l - G_ROW1 - hl_db[2]                        # degree widget X
    HL_DEG_INK = HL_DEG_X + 1
    HL_ANCHOR = HL_DEG_INK + 1                                     # digit ink ends 2 px before the ring
    HL_2_LEFT = HL_ANCHOR - (2 * HL_DW + HL_SP)
    AW_IMG = Image.open(IMG / hl_up).size[0]
    ARROW_X = HL_2_LEFT - ARROW_GAP - AW_IMG
    ab = _solid_bb(hl_up)
    hl_ink_l = ARROW_X + ab[0]
    td_bb = _solid_bb(degree)
    TDEG_X = hl_ink_l - G_ROW1 - td_bb[2]
    TEMP_ANCHOR = TDEG_X + 3
    # rows: two 16-row glyphs, 2 rows apart, centred on the main digits (glyph rows 302-330)
    HL_GAP = 2
    _mc = (298 + t_top + 298 + t_bot) / 2
    HI_TOP = round(_mc - (2 * (s_bot - s_top + 1) + HL_GAP) / 2)
    LO_TOP = HI_TOP + (s_bot - s_top + 1) + HL_GAP
    HI_Y, LO_Y = HI_TOP - s_top, LO_TOP - s_top
    # Centre each arrow in the solid height of its temp digits (ignore drop-shadow inflate).
    def _arrow_y(row_y, dig_files, arrow_name):
        thr = 80
        tops, bots = [], []
        for f in dig_files:
            a = Image.open(IMG / f).split()[3].point(lambda v: v if v >= thr else 0)
            bb = a.getbbox()
            tops.append(row_y + bb[1]); bots.append(row_y + bb[3])
        y0, y1 = min(tops), max(bots)
        aa = deshadow(Image.open(IMG / arrow_name).split()[3])
        abb = aa.point(lambda v: v if v >= thr else 0).getbbox()
        return round((y0 + y1) / 2 - (abb[1] + abb[3]) / 2)
    ARROW_UP_Y = _arrow_y(HI_Y, (hl11[2], hl11[8]), hl_up)   # sample "28"
    ARROW_DN_Y = _arrow_y(LO_Y, (hl11[1], hl11[7]), hl_dn)  # sample "17"
    print(f"weather row gaps: temp-HL={hl_ink_l - (TDEG_X + td_bb[2] - 1) - 1}  "
          f"HL-icon={wx_ink_l - (HL_DEG_X + hl_db[2] - 1) - 1} (target {G_ROW1})")


    # ---- row 2: humidity, air quality, UV on one baseline with the alarm digits
    B2 = 362
    S_Y = B2 - s_bot
    uv_lab = icon_sun()                                            # UV: filled sun, droplet height
    aqi_lab = icon_leaf()                                          # air quality: filled leaf, droplet height
    drop = icon_drop()
    # order: humidity (2831), air quality (5031), UV (7831). Fixed slots for worst case
    # (100% humidity / AQI 3 digits / UV 11), ROW2_GAP between slots; row right-aligned to x 321.
    ROW2_GAP, ROW2_END = 10, 322
    PCT_DX = 10 + 2                                                # '%' frame x after the first digit ink
    def pct_end(dx):                                               # ink end of a 3-digit value + '%'
        return dx + PCT_DX + 22 + PCT_W
    uv_w = uv_lab.width + 3 + 21                                   # sun + "11"
    aqi_w = aqi_lab.width + 3 + 32                                 # leaf + 3 digits (ink 10 + 2 x 11)
    hum_w = drop.width + 3 + PCT_DX + 22 + PCT_W                   # drop + "100%"
    span0 = hum_w + aqi_w + uv_w
    span = span0 + 2 * ROW2_GAP
    ROW2_X0 = ROW2_END - 1 - span
    print("row 2: x0", ROW2_X0, "gap", ROW2_GAP, "worst-case end", ROW2_X0 + span,
          "order humidity / AQI / UV")
    SLOT_HUM = ROW2_X0                                             # order: humidity, air quality, UV
    HUM_DX = SLOT_HUM + drop.width + 3
    SLOT_AQI = pct_end(HUM_DX) + ROW2_GAP                          # after worst-case "100%"
    AQI_DX = SLOT_AQI + aqi_lab.width + 3
    SLOT_UV = AQI_DX + 32 + ROW2_GAP                               # after 3-digit AQI
    UV_DX = SLOT_UV + uv_lab.width + 3
    assert UV_DX + 21 <= ROW2_END + 1, "row 2 worst case passes the LCD edge"
    # ---- alarm block (left of the LCD): ALARM label over HH:MM in the stock 7-segment temperature
    # digits (28x38, glyph 22x29, pitch 22), colon = two square segments like the main clock colon
    AL_INK_X = 24
    al_lab = lcd_word_scaled("ALARM", 15)                          # box-label lettering, cap 13 -> 15 rows
    AD_Y = B2 - t_bot                                              # digits baseline = row-2 baseline (362)
    AH_X = AL_INK_X - 3                                            # 7-segment digit ink starts at col 3
    COL_W = 4
    AC_X = AH_X + 22 + 25 + 3                                      # colon 3 px after the hour ink (col 24)
    AM_X = AC_X + COL_W + 3 - 3                                    # minute ink 3 px after the colon
    col_im = Image.new("L", (COL_W * 16, 38 * 16))
    cd = ImageDraw.Draw(col_im)
    gh = t_bot - t_top
    for fr in (0.27, 0.75):                                         # main clock colon proportions
        cy = (t_top + fr * gh + 0.5) * 16
        cd.rectangle((0, cy - 2 * 16, COL_W * 16 - 1, cy + 2 * 16), fill=255)
    al_colon = put(ink(lcd_shadow(col_im.resize((COL_W, 38), Image.BOX), pad=(2, 0, 2, 0))), "alarm_colon.png")
    AC_X -= 2                                                      # shadow margin left of the colon
    # label last ink row 7 px above the digit tops; a small LCD-black alarm bell of the label's cap height
    # after it. 1.0: 3 px more space between ALARM and the bell (bitmap offset 4 -> 7), and the ALARM + bell
    # group centred over the HH:MM ink (x 24-121) instead of left-aligned with the HH ink.
    _lb = al_lab.getbbox()
    ALL_Y = AD_Y + t_top - 7 - _lb[3]
    bell = icon_bell(_lb[3] - _lb[1])
    _bb = bell.getbbox()
    BELL_DX = 7                                                    # bell bitmap x after the label ink end
    AL_DIG_END = AM_X + 3 + 22 + 22                                # MM ink end (exclusive): 122
    grp_w = (_lb[2] - _lb[0]) + BELL_DX + _bb[2]
    grp_x0 = AL_INK_X + (AL_DIG_END - AL_INK_X - grp_w) // 2
    AL_X = grp_x0 - _lb[0]
    BELL_X, BELL_Y = AL_X + _lb[2] + BELL_DX, ALL_Y + _lb[1]
    print("alarm label: ink x", grp_x0, "-", AL_X + _lb[2] - 1, "bell ink x", BELL_X + _bb[0], "-",
          BELL_X + _bb[2] - 1, "ink gap", BELL_X + _bb[0] - (AL_X + _lb[2]), "digits ink x", AL_INK_X, "-", AL_DIG_END - 1)

    STATICS = [("alarm_label", AL_X, ALL_Y, al_lab), ("alarm_icon", BELL_X, BELL_Y, bell),
               ("uv_label", SLOT_UV, B2 + 1 - uv_lab.height, uv_lab), ("hum_icon", SLOT_HUM, B2 + 1 - drop.height, drop),
               ("aqi_label", SLOT_AQI, B2 + 1 - aqi_lab.height, aqi_lab)]
    STATIC_BOUNDS.clear()
    for n, x, y, a in STATICS:
        bb = a.getbbox()
        STATIC_BOUNDS[n] = (x + bb[0], y + bb[1], x + bb[2], y + bb[3])

    def lcd_bake(im):
        im = im.copy()
        for n, x, y, a in STATICS:                                 # with the stock LCD drop shadow
            sa = lcd_shadow(a)
            im.alpha_composite(ink(sa), (x - 2, y))
        return im

    base_bg = lcd_bake(base_bg)

    def pct_frames(prefix, alpha, step, col=(0, 0, 0)):
        """'%' that follows a left-aligned number: one frame per digit count (keys 0 / 10 / 100)."""
        out = []
        for i, key in enumerate((0, 10, 100)):
            fr = Image.new("RGBA", (alpha.width + 2 * step, alpha.height), tuple(col[:3]) + (0,))
            fr.alpha_composite(ink(alpha, col), (step * i, 0))
            out.append((key, put(fr, f"{prefix}_{i + 1}d.png")))
        return out

    pct_lcd = lcd_shadow(pct_small)                                # +2 px left margin -> x - 2
    hum_pct = pct_frames("hum_pct", pct_lcd, 11)

    # ---- bezel strip: battery icon | 20-seg bar | "NN%" (no MUSIC/CARDS/COMPASS).
    # Equal STRIP_GAP between icon→bar and bar→percentage. Digits are bar-height, left-aligned after
    # the bar; '%' ImageList tracks digit count. Group centred on the common 2-digit "NN%" width.
    STRIP_TEXT = (153, 153, 153, 255)
    STRIP_BAR_UNLIT = (70, 70, 70, 255)
    STRIP_GAP = 16                                                 # equal air between icon | bar | %
    bd_alpha = [deshadow(Image.open(IMG / f).convert("RGBA").split()[3]) for f in d_date]
    bdig = [put(batt_digit(a_, STRIP_TEXT), f"num_bi_{i:02d}.png") for i, a_ in enumerate(bd_alpha)]
    bpct_a = deshadow(stock_img(IMAGES["Image7"]).convert("RGBA").split()[3])
    bpct_im = batt_digit(bpct_a, STRIP_TEXT)
    bpct = put(bpct_im, "batt_pct.png")
    BD_W = Image.open(IMG / bdig[0]).width
    BP_W = bpct_im.width
    BP_GAP = 3                                                     # air between last digit ink and '%'
    # Pull % group left so bar→digit *solid* ink gap == STRIP_GAP (skip AA fringe cols).
    _ba = Image.open(IMG / bdig[0]).split()[3]
    DIG_PAD_L = next(x for x in range(_ba.width) if max(_ba.getpixel((x, y)) for y in range(_ba.height)) >= 80)
    _dig_r = next(x for x in range(_ba.width - 1, -1, -1)
                  if max(_ba.getpixel((x, y)) for y in range(_ba.height)) >= 80)
    DIGIT_GAP = 2                                                  # solid ink air between consecutive digits
    BD_PITCH = (_dig_r - DIG_PAD_L + 1) + DIGIT_GAP
    BD_SP = BD_PITCH - BD_W
    BATT_GROUP_W = 3 * BD_W + 2 * BD_SP + BP_GAP + BP_W           # reserved "100%"
    # Solid ink width of common "NN%" (2 digits + gap + '%'), not the widget box.
    _bp = bpct_im.split()[3]
    _pct_r = next(x for x in range(_bp.width - 1, -1, -1)
                  if max(_bp.getpixel((x, y)) for y in range(_bp.height)) >= 80)
    SAMPLE_PCT_INK_W = 2 * BD_PITCH + BP_GAP + _pct_r + 1         # cols from PCT_X through % ink
    # Center icon|bar|NN% on the full screen so left (screen→icon) == right (%→screen).
    BAR_W = 198
    sample_w = BI_W + 2 * STRIP_GAP + BAR_W - DIG_PAD_L + SAMPLE_PCT_INK_W
    if (336 - sample_w) % 2:                                       # keep L/R margins identical
        BAR_W -= 1
        sample_w -= 1
    ST_LEFT = (336 - sample_w) // 2
    full_w = BI_W + 2 * STRIP_GAP + BAR_W - DIG_PAD_L + BATT_GROUP_W
    assert ST_LEFT >= 0 and ST_LEFT + full_w <= 336, (ST_LEFT, full_w)
    assert BAR_W >= BAR_N * 4, BAR_W
    BI_X = ST_LEFT
    BAR_X = BI_X + BI_W + STRIP_GAP
    PCT_X = BAR_X + BAR_W + STRIP_GAP - DIG_PAD_L
    ST_RIGHT = ST_LEFT + full_w - 1
    ST_MID = (STRIP_TAP_Y0 + STRIP_TAP_Y1) // 2
    BAR_Y = BI_Y = BD_Y = ST_MID - BAR_H // 2                      # icon, bar, digits share one band
    _sample_r = PCT_X + SAMPLE_PCT_INK_W - 1
    print(f"battery center: sample_w {sample_w}  ST_LEFT {ST_LEFT}  pct@{PCT_X}  "
          f"ink {_sample_r}  screen margins {ST_LEFT}/{335 - _sample_r}")

    # battery_pct is right-aligned (Alignment=2) on the right edge of the 2-digit cells, so 1/2 digits keep
    # that right edge and a 3rd digit ("100") grows LEFT toward the bar; the '%' therefore stays fixed.
    BATT_NUM_R = PCT_X + 2 * BD_W + BD_SP                          # right-align anchor (2-digit right edge)

    def make_batt_pct_list(col, suffix=""):
        """'%' after the right-aligned battery_pct: fixed position (2-digit slot) for every key 0 / 10 / 100."""
        glyph = batt_digit(bpct_a, col)
        out = []
        max_w = 3 * BD_PITCH + BP_GAP + glyph.width
        for i, key in enumerate((0, 10, 100)):
            fr = Image.new("RGBA", (max_w, glyph.height), (0, 0, 0, 0))
            fr.alpha_composite(glyph, (2 * BD_PITCH + BP_GAP, 0))
            out.append((key, put(fr, f"batt_pct_{i + 1}d{suffix}.png")))
        return out

    bpct_list = make_batt_pct_list(STRIP_TEXT)
    bfill_keys = []
    for wpx in range(0, BI_FILL_MAX + 1, BI_FILL_STEP):
        key = 0 if wpx == 0 else math.ceil((wpx - BI_FILL_STEP / 2) * 100 / BI_FILL_MAX)
        bfill_keys.append((key, wpx))
    sbatt = [(k, put(batt_icon_small(w, STRIP_TEXT, STRIP_TEXT), f"sbatt_{w:02d}.png"))
             for k, w in bfill_keys]
    bbars = []
    for i in range(BAR_N + 1):
        key = i * (100 // BAR_N)
        bbars.append((key, put(batt_bar_frame(i, BAR_W), f"batt_bar_{i:02d}.png")))
    print(f"battery: icon x {BI_X}-{BI_X + BI_W - 1}  gap {STRIP_GAP}  "
          f"bar x {BAR_X}-{BAR_X + BAR_W - 1} ({BAR_W}px)  gap {STRIP_GAP}  "
          f"pct@{PCT_X} h={BI_DIG_H}")

    bgs = {}
    for key, stem, title, tint in STYLES:
        im = base_bg if tint is None else recolor_panel(base_bg, tint)
        im = header_labels(im, stock_bg, tint)
        bgs[key] = put(im.convert("RGB"), f"bg_{key}_RGB.png")   # 24-bit PNG, packed as RGB565

    # ---- status sockets left of the date tab: Bluetooth, sleep mode, screen lock, charging
    bt_off = put(bt_badge(False, None), "bt_0.png")
    bt_on = put(bt_badge(True, BEZEL_TEXT), "bt_1.png")
    slp_off = put(status_socket("moon", False, None), "slp_0.png")
    slp_on = put(status_socket("moon", True, BEZEL_TEXT), "slp_1.png")
    chg_off = put(status_socket("charge", False, None), "chg_0.png")
    chg_on = put(status_socket("charge", True, BEZEL_TEXT), "chg_1.png")
    lock_off = put(status_socket("lock", False, None), "lock_0.png")
    lock_on = put(status_socket("lock", True, BEZEL_TEXT), "lock_1.png")

    # ---------------- layout (stock manifest coordinates, stock z-order)
    W.append(dict(t="bg", Name="bg_RGB", X=0, Y=0, Width=336, Height=480))
    # hands: stock pivot = layout(84,78)+(6,39) = (90,117). Compiler.exe places the hand pivot at the
    # widget centre (X+Width/2, Y+Height/2) and ignores BgImage_rotate_*, so the box is 180x234 at (0,0).
    W.append(dict(t="analog", Name="analog_hands", X=0, Y=0, Width=180, Height=234, cx=90, cy=117,
                  hour=(hand_hour, 6, 38), minute=(hand_min, 6, 39), second=(hand_sec, 6, 38)))
    # stat boxes. Lower row: distance (1821) | sleep duration (0828), both in the firmware decimal mode
    # (Digits=20 + 12th bitmap = decimal point; Mi-Create #9 / EasyFace #26, the community S3 demo uses
    # exactly 1821 this way), centred on the stock KCAL / STAND anchors.
    dot = Image.new("RGBA", Image.open(IMG / d_small[0]).size, (0, 0, 0, 0))
    # Dot drawn like a stock stroke ("+##+": 2 solid px, 75-alpha edges, cf. the digit 1) in cell cols 5-8,
    # on the baseline: digit ink spans cell cols 2-11, pitch 11, so the free 12 px split 4 | 4 | 4.
    dd = ImageDraw.Draw(dot)
    for (x, y), a in {(6, 0): 255, (7, 0): 255, (6, 1): 255, (7, 1): 255,
                      (5, 0): 75, (8, 0): 75, (5, 1): 75, (8, 1): 75,
                      (6, -1): 75, (7, -1): 75, (6, 2): 75, (7, 2): 75,
                      (5, -1): 20, (8, -1): 20, (5, 2): 20, (8, 2): 20}.items():
        dd.point((x, s_bot - 1 + y), fill=(0, 0, 0, a))
    dot.putalpha(lcd_shadow(dot.split()[3], pad=(0, 0, 0, 0)))       # stock LCD drop shadow
    small12 = small11 + [put(dot, "num_s_dot.png")]
    # 1.0 swap: heart rate in the lower-left cell (under STEP, same centre 219), distance (KM, decimal)
    # in the upper-right cell (over SLEEP, same centre 290)
    w_num("sleep_hours", 290, 172, "0828", small12, 20, -3, 1, True, "7.5")
    # Single-0 cover for SLEEP: at value 0 the decimal DigitalNum draws "00" (0.0 with an invisible
    # dot). ImageList on the same source — frame (0) = opaque panel crop + one centred "0"; frame (1)
    # = fully transparent so normal values ("7.5") show through. Preview sample 7 picks the empty frame.
    SLEEP_COV_W = 14 * 3 + (-3) * 2   # width of "0.0" / "00" in this digit set
    SLEEP_COV_H = 26
    SLEEP_COV_X = 290 - SLEEP_COV_W // 2
    SLEEP_COV_Y = 172

    def make_sleep_cover(bg_rgb):
        cov = bg_rgb.crop((SLEEP_COV_X, SLEEP_COV_Y,
                           SLEEP_COV_X + SLEEP_COV_W, SLEEP_COV_Y + SLEEP_COV_H)).convert("RGBA")
        zero = Image.open(IMG / small12[0]).convert("RGBA")
        cov.alpha_composite(zero, ((SLEEP_COV_W - zero.width) // 2, 0))
        return cov

    sleep_cover_0 = put(make_sleep_cover(Image.open(IMG / bgs["orig"])), "sleep_cover_0.png")
    sleep_cover_empty = put(Image.new("RGBA", (SLEEP_COV_W, SLEEP_COV_H), (0, 0, 0, 0)),
                            "sleep_cover_empty.png")
    w_list("sleep_zero_cover", SLEEP_COV_X, SLEEP_COV_Y, "0828",
           [(0, sleep_cover_0), (1, sleep_cover_empty)], 7)
    w_num("distance", 290, 82, "1821", small12, 20, -3, 1, True, "4.2")
    w_num("heart_rate", 219, 172, "0822", d_hr, 3, -3, 1, True, 78)
    w_num("steps", 219, 82, "0821", small11, 5, -3, 1, True, 2560)
    w_list("weekday", 157, 257, "2012", list(enumerate(week)), 0)
    # date row DD-MM: stock flex row x=234 gap -6 (34 px per 2-digit number, 20 px separator), day first
    w_num("day", 234, 257, "1812", date11, 2, -6, 0, False, 16)
    # '-' centred in the gap between day and month ink. Stock ink 7.0-13.76 in its cell sat 0.55 px right
    # of centre for 0x months (gaps 5.35 | 4.24 at 00-00), and the narrow '1' (ink starts 3 px later
    # than '0') left a much wider right gap for months 10-12 (04-10: 4.49 | 7.24). The dash is an
    # ImageList on the month: months 1-9 -> half a pixel left, 10-12 -> one pixel right. Main face only
    # (the AOD keeps its own tight-cropped separator layout).
    sep_im = Image.open(IMG / date_sep).convert("RGBA")
    sep_m1 = Image.new("RGBA", sep_im.size, (0, 0, 0, 0))
    sep_m1.paste(sep_im.crop((0, 0, sep_im.width - 1, sep_im.height)), (1, 0))
    date_sep_l = [(0, put(half_px_left(sep_im), "date_sep_m0x.png")), (10, put(sep_m1, "date_sep_m1x.png"))]
    w_list("date_sep", 234 + 34 - 6, 257, "1012", date_sep_l, 8)
    w_num("month", 234 + 34 - 6 + 20 - 6, 257, "1012", date11, 2, -6, 0, False, 8)
    w_num("hours", 89, 377, "0811", big11, 2, 6, 0, False, 10)
    w_image("time_colon", 178, 377, colon)
    w_num("minutes", 213, 377, "1011", big11, 2, 4, 0, False, 9)
    w_num("seconds", 24, 406 + SEC_SHIFT, "1811", sec11, 2, 1, 0, False, 24)
    # weather row: main temp | ↑high/↓low | icon
    # stock LCD drop shadow on every weather icon frame (the only LCD element without it); the canvas
    # grows 2 px left/right and 4 px down, so X moves 2 px left and the art stays where it was
    WX_SH_PAD = (2, 0, 2, 4)
    wx_sh = [put(lcd_shadow_rgba(Image.open(IMG / f), WX_SH_PAD), f.replace("wx_", "wx_sh_")) for f in wx]
    w_list("weather_icon", WX_POS[0] - WX_SH_PAD[0], WX_POS[1] - WX_SH_PAD[1], "3031",
           list(zip((0, 1, 2, 4, 5, 6, 7, 8, 9, 13, 15, 16, 18, 19, 20, 29, 53, 99), wx_sh)), 1)
    w_num("temp_c", TEMP_ANCHOR, 298, "2031", t11, 3, -6, 2, True, 25)
    w_image("temp_degree", TDEG_X, 302, degree)
    w_image("high_arrow", ARROW_X, ARROW_UP_Y, hl_up)
    w_num("temp_high", HL_ANCHOR, HI_Y, "1832", hl11, 3, -3, 2, True, 28)
    w_image("high_degree", HL_DEG_X, HI_TOP - 1, hl_deg)
    w_image("low_arrow", ARROW_X, ARROW_DN_Y, hl_dn)
    w_num("temp_low", HL_ANCHOR, LO_Y, "2032", hl11, 3, -3, 2, True, 17)
    w_image("low_degree", HL_DEG_X, LO_TOP - 1, hl_deg)
    # row 2: humidity %, air quality, UV
    w_num("humidity", HUM_DX - 2, S_Y, "2831", small11, 3, -3, 0, True, 65)
    w_list("humidity_pct", HUM_DX + PCT_DX - 2, B2 - 15, "2831", hum_pct, 65)
    w_num("aqi", AQI_DX - 2, S_Y, "5031", small11, 3, -3, 0, True, 42)
    w_num("uv_index", UV_DX - 2, S_Y, "7831", small11, 2, -3, 0, True, 3)
    # alarm block: label baked, HH:MM in the stock 7-segment temperature digits (LCD black)
    w_num("alarm_hour", AH_X, AD_Y, "0851", t11, 2, -6, 0, False, 7)
    w_image("alarm_colon", AC_X, AD_Y, al_colon)
    w_num("alarm_minute", AM_X, AD_Y, "1051", t11, 2, -6, 0, False, 30)
    w_image("hub", 72, 100, hub)
    # bezel strip: icon | 20-seg bar | digits + '%' (left-aligned after bar, equal gaps, centred)
    w_list("batt_icon_s", BI_X, BI_Y, "0841", sbatt, 80)
    w_list("batt_bar", BAR_X, BAR_Y, "0841", bbars, 80)
    w_num("battery_pct", BATT_NUM_R, BD_Y, "0841", bdig, 3, BD_SP, 2, True, 80)   # right-aligned
    w_list("battery_pct_sign", PCT_X, BD_Y, "0841", bpct_list, 80)
    # sockets: 22 px, 8 px gaps from x=16; the charging socket keeps ~9 px from the date tab's slant
    SOCK_Y, SOCK_X = 247, (16, 46, 76, 106)
    w_list("bt_status", SOCK_X[0], SOCK_Y, "2041", [(0, bt_off), (1, bt_on)], 1)
    w_list("sleep_mode", SOCK_X[1], SOCK_Y, "1841", [(0, slp_off), (1, slp_on)], 0)
    w_list("screen_lock", SOCK_X[2], SOCK_Y, "3041", [(0, lock_off), (1, lock_on)], 0)
    w_list("charging", SOCK_X[3], SOCK_Y, "1041", [(0, chg_off), (1, chg_on)], 0)


    # ---------------- tap zones: computed from the bounds of the element each one opens (see README)
    OLD_TAPS = [   # previous build's zones
        ("stopwatch", 9, 36, 163, 164), ("alarm", 17, 308, 119, 65), ("steps", 179, 40, 75, 84),
        ("hrm", 179, 124, 75, 78), ("sleep", 254, 124, 71, 81), ("distance", 254, 40, 71, 84),
        ("weather", 136, 292, 190, 81), ("calendar", 138, 245, 187, 47),
        ("flashlight", 16, 200, 306, 45),
        ("timer", 20, 373, 297, 86)]
    TAPS = tap_zones()
    for a, x, y, w, h in TAPS:
        w_tap(a, x, y, w, h)
    rects = [(t[1], t[2], t[1] + t[3], t[2] + t[4]) for t in TAPS]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = rects[i], rects[j]
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                raise SystemExit(f"tap overlap: {TAPS[i]} vs {TAPS[j]}")
    tt = ["| Action | previous x,y,w,h | new x,y,w,h | new covers |", "|---|---|---|---|"]
    COVERS = {"stopwatch": "analog dial (r 77)", "alarm": "ALARM label + bell + HH:MM in the LCD (meets weather)",
              "steps": f"upper-left STATS cell ({STAT_TAP_W}x{STAT_TAP_H})",
              "hrm": f"lower-left STATS cell ({STAT_TAP_W}x{STAT_TAP_H})",
              "distance": f"upper-right STATS cell ({STAT_TAP_W}x{STAT_TAP_H}); Workouts 0100f300",
              "sleep": f"lower-right STATS cell ({STAT_TAP_W}x{STAT_TAP_H})",
              "weather": "temperature + high/low + icon + humidity/AQI/UV (aligned with calendar column)",
              "calendar": "weekday/date tab (aligned with weather column)",
              "flashlight": f"strip battery bar + %; left=stopwatch left, right=calendar right, y {STRIP_TAP_Y0}..{STRIP_TAP_Y1}",
              "timer": "digital clock SEC+HH:MM (spans alarm-left .. weather-right)"}
    oldd = {t[0]: t[1:] for t in OLD_TAPS}
    for a_, x, y, w, h in TAPS:
        old = '%d,%d,%d,%d' % oldd[a_] if a_ in oldd else 'new'
        tt.append(f"| {a_} | {old} | {x},{y},{w},{h} | {COVERS[a_]} |")
    (PREV / "tap_zones.md").write_text("\n".join(tt) + "\n", encoding="utf-8")
    print("\n".join(tt))

    # ---------------- per style: preview + fprj (UTF-8 bytes, utf-16 header like EasyFace's own files)
    style_subs = {}
    for key, stem, title, tint in STYLES:
        sub = {}
        if tint is not None:
            for f in (hand_hour, hand_min, hand_sec):
                sub[f] = put(colorize(Image.open(IMG / f), tint), f.replace(".png", f"_{key}.png"))
            # centre hub/cap stays stock in every style
            vlit = mcol(tint, BEZEL_TEXT[0])        # lit status icons: Original's 225
            slit = mcol(tint, STRIP_TEXT[0])        # strip battery % digits: stock header grey 153
            sub[bt_on] = put(bt_badge(True, vlit), f"bt_1_{key}.png")
            sub[slp_on] = put(status_socket("moon", True, vlit), f"slp_1_{key}.png")
            sub[chg_on] = put(status_socket("charge", True, vlit), f"chg_1_{key}.png")
            sub[lock_on] = put(status_socket("lock", True, vlit), f"lock_1_{key}.png")
            for f, a_ in zip(bdig, bd_alpha):
                sub[f] = put(batt_digit(a_, slit), f.replace(".png", f"_{key}.png"))
            for (_, src_f), (_, dst_f) in zip(bpct_list, make_batt_pct_list(slit, f"_{key}")):
                sub[src_f] = dst_f
            ulit = mcol(tint, STRIP_BAR_UNLIT[0])
            for (k_, w_), (_, f) in zip(bfill_keys, sbatt):
                sub[f] = put(batt_icon_small(w_, slit, slit), f.replace(".png", f"_{key}.png"))
            for i, (_, f) in enumerate(bbars):
                sub[f] = put(batt_bar_frame(i, BAR_W, unlit=ulit, lit=slit),
                             f.replace(".png", f"_{key}.png"))
            sub[sleep_cover_0] = put(make_sleep_cover(Image.open(IMG / bgs[key])),
                                     f"sleep_cover_0_{key}.png")
        style_subs[key] = sub
    for key, stem, title, tint in STYLES:
        SUB.clear()
        SUB.update(style_subs[key])
        prev = render(bgs[key])
        prev.save(PREV / f"preview_{key}.png")
        pname = f"preview_{key}_RGB.png"
        prev.convert("RGB").save(PROJ / pname)
        prev.convert("RGB").save(IMG / pname)
        (PROJ / f"{stem}.fprj").write_bytes(fprj_xml(bgs[key], title, pname).encode("utf-8"))

    # tap overlay on the Phosphor Green preview, 2x for legibility: each zone filled + outlined in its
    # own colour, labelled with the action it opens and its x,y,w,h (face pixels)
    TAP_DESC = {"alarm": "Alarm", "steps": "Steps", "distance": "Workouts", "sleep": "Sleep",
                "hrm": "Heart rate", "weather": "Weather", "calendar": "Calendar", "flashlight": "Flashlight",
                "stopwatch": "Stopwatch", "timer": "Timer"}

    def _font(sz):
        for f in ("arialbd.ttf", "DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
            try:
                return ImageFont.truetype(f, sz)
            except OSError:
                pass
        return ImageFont.load_default()
    cols = [(255, 60, 60), (255, 200, 0), (0, 170, 255), (255, 120, 0), (230, 60, 230),
            (0, 230, 230), (255, 255, 255), (255, 90, 160), (150, 120, 255), (255, 170, 90)]

    def tap_map(K, f1, f2, coords, pad):
        ov = Image.open(PREV / "preview_green.png").convert("RGBA")
        if K != 1:
            ov = ov.resize((336 * K, 480 * K), Image.LANCZOS)
        lay = Image.new("RGBA", ov.size)
        ld = ImageDraw.Draw(lay)
        for i, (a, x, y, w, h) in enumerate(TAPS):
            c = cols[i % len(cols)]
            ld.rectangle((x * K, y * K, (x + w) * K - 1, (y + h) * K - 1), fill=c + (60,), outline=c + (255,),
                         width=max(2, K + 1))
        for i, (a, x, y, w, h) in enumerate(TAPS):
            c = cols[i % len(cols)]
            t1 = TAP_DESC.get(a, a)
            b1 = ld.textbbox((0, 0), t1, font=f1)
            bw, bh = b1[2] + 2 * pad, b1[3] + 2 * pad
            if coords:
                t2 = "%d,%d %dx%d" % (x, y, w, h)
                b2 = ld.textbbox((0, 0), t2, font=f2)
                bw, bh = max(b1[2], b2[2]) + 2 * pad, b1[3] + b2[3] + 2 * pad + 2
            px0, py0 = x * K + 3 * K, y * K + 3 * K
            ld.rounded_rectangle((px0, py0, px0 + bw, py0 + bh), 2 * K, fill=(0, 0, 0, 215), outline=c + (255,),
                                 width=max(1, K))
            ld.text((px0 + pad, py0 + pad - 2), t1, font=f1, fill=c + (255,))
            if coords:
                ld.text((px0 + pad, py0 + pad + 1 + b1[3]), t2, font=f2, fill=(230, 230, 230, 255))
        ov.alpha_composite(lay)
        return ov

    tap_map(2, _font(14), _font(10), True, 5).save(PREV / "taps_overlay.png")
    write_aod_project()
    # comparison sheet: stock, every style, shortcut map (no AOD)
    stock_prev = Image.open(STOCK / IMAGES["Image1"]).convert("RGBA")
    panels = [("STOCK (Xiaomi preview)", stock_prev)]
    panels += [(t, Image.open(PREV / f"preview_{k}.png")) for k, _, t, _ in STYLES]
    panels.append(("Shortcuts (tap zones, on Phosphor Green)", tap_map(1, _font(11), None, False, 3)))
    NC = 5
    NR = (len(panels) + NC - 1) // NC
    sheet = Image.new("RGBA", (346 * NC - 10, 500 * NR), (255, 255, 255, 255))
    dr = ImageDraw.Draw(sheet)
    for i, (lab, im) in enumerate(panels):
        cx, cy = (i % NC) * 346, (i // NC) * 500
        sheet.alpha_composite(im.convert("RGBA"), (cx, cy + 20))
        dr.text((cx + 4, cy + 4), lab, fill=(0, 0, 0, 255))
    sheet.save(PREV / "compare_sheet.png")
    brightness_table(stock_bg)
    print("project generated:", PROJ, "images:", len(list(IMG.iterdir())),
          "image bytes:", sum(p.stat().st_size for p in IMG.iterdir()),
          "AOD:", AOD / "AOD.fprj")

    if "--verify-only" in sys.argv:
        verify_compiled((OUT / FACE_FILE).read_bytes())
        return
    if not compile_:
        return
    compile_face()


def _aod_recolor(src: Path, col, name: str, dest: Path | None = None,
                 *, tight_pad: int | None = None) -> str:
    """Keep alpha of an LCD glyph; paint opaque pixels with `col` (dim AOD ink on black).

    If tight_pad is set, crop horizontally to the ink bbox plus that many pixels each side
    (keeps full height). Used for colon/hyphen so layout widths match visible ink.
    """
    dest = dest or AOD_IMG
    im = Image.open(src).convert("RGBA")
    a = im.split()[3]
    if tight_pad is not None:
        bb = a.getbbox()
        if bb:
            left = max(0, bb[0] - tight_pad)
            right = min(im.size[0], bb[2] + tight_pad)
            im = im.crop((left, 0, right, im.size[1]))
            a = im.split()[3]
    out = Image.new("RGBA", im.size, tuple(col[:3]) + (0,))
    out.putalpha(a)
    out.save(dest / name)
    return name


def _aod_fprj_body(hx, hy, DW, DH, HSP, MSP, colon_x, colon_y, CW, CH, mx,
                   dx0, dy, DDW, DDH, DSP, sep_x, SW, month_x,
                   wx, wy, WW, WH, week_pairs,
                   bg, preview, big11, colon, date11, sep, xsi):
    bl_big = "|".join(big11)
    bl_date = "|".join(date11)
    bl_week = "|".join(f"({k}):{f}" for k, f in week_pairs)
    return [
        '<?xml version="1.0" encoding="utf-16"?>',
        f'<FaceProject DeviceType="{DEVICE_TYPE}" Name="{DISPLAY_NAME}" Id="{FACE_ID}">',
        f'<Screen Title="AOD" Bitmap="{preview}">',
        f'<Widget p3:type="FaceWidgetImage" Shape="30" Name="bg_RGB" X="0" Y="0" Width="336" Height="480" '
        f'Alpha="255" Visible_Src="0" Bitmap="{bg}" {xsi} />',
        f'<Widget p3:type="FaceWidgetDigitalNum" Shape="32" Name="hours" X="{hx}" Y="{hy}" Width="{DW}" Height="{DH}" '
        f'Alpha="255" Visible_Src="0" Digits="2" Alignment="0" Spacing="{HSP}" Blanking="0" Value_Src="0811" '
        f'BitmapList="{bl_big}" {xsi} />',
        f'<Widget p3:type="FaceWidgetImage" Shape="30" Name="time_colon" X="{colon_x}" Y="{colon_y}" '
        f'Width="{CW}" Height="{CH}" Alpha="255" Visible_Src="0" Bitmap="{colon}" {xsi} />',
        f'<Widget p3:type="FaceWidgetDigitalNum" Shape="32" Name="minutes" X="{mx}" Y="{hy}" Width="{DW}" Height="{DH}" '
        f'Alpha="255" Visible_Src="0" Digits="2" Alignment="0" Spacing="{MSP}" Blanking="0" Value_Src="1011" '
        f'BitmapList="{bl_big}" {xsi} />',
        f'<Widget p3:type="FaceWidgetDigitalNum" Shape="32" Name="day" X="{dx0}" Y="{dy}" Width="{DDW}" Height="{DDH}" '
        f'Alpha="255" Visible_Src="0" Digits="2" Alignment="0" Spacing="{DSP}" Blanking="0" Value_Src="1812" '
        f'BitmapList="{bl_date}" {xsi} />',
        f'<Widget p3:type="FaceWidgetImage" Shape="30" Name="date_sep" X="{sep_x}" Y="{dy}" Width="{SW}" Height="{DDH}" '
        f'Alpha="255" Visible_Src="0" Bitmap="{sep}" {xsi} />',
        f'<Widget p3:type="FaceWidgetDigitalNum" Shape="32" Name="month" X="{month_x}" Y="{dy}" Width="{DDW}" Height="{DDH}" '
        f'Alpha="255" Visible_Src="0" Digits="2" Alignment="0" Spacing="{DSP}" Blanking="0" Value_Src="1012" '
        f'BitmapList="{bl_date}" {xsi} />',
        f'<Widget p3:type="FaceWidgetImageList" Shape="31" Name="weekday" X="{wx}" Y="{wy}" Width="{WW}" Height="{WH}" '
        f'Alpha="255" Visible_Src="0" Index_Src="2012" DefaultIndex="0" BitmapList="{bl_week}" {xsi} />',
        '</Screen>',
        '</FaceProject>',
        '',
    ]


def write_aod_project():
    """Build project/AOD/{AOD.fprj,images}/ — dim always-on face packed by Compiler with the main face.

    Layout: large HH:MM centred, DD-MM date below, short weekday (SUN..SAT) centred under the date.
    EasyFace/Compiler only accepts a single AOD.fprj here (extra A.fprj..G.fprj makes it error
    "AOD face file is not found"). Colour-matched AOD per scheme needs the official GMF editor.
    No seconds, no taps, no analog hands.
    """
    if AOD.exists():
        shutil.rmtree(AOD)
    AOD_IMG.mkdir(parents=True)

    bg_name = "aod_bg_RGB.png"
    Image.new("RGB", (336, 480), (0, 0, 0)).save(AOD_IMG / bg_name)

    DW, DH, HSP, MSP = 48, 78, 6, 4
    DDW, DDH, DSP = 20, 28, -6
    hy, dy = AOD_HH_Y, AOD_DATE_Y
    # Stock colon/hyphen sit in wide transparent cells; trim to ink so group widths match what you see,
    # then centre both rows with equal side margins and equal gaps around the separator.
    AOD_TIGHT_PAD = 4
    AOD_COLON_GAP = 6
    AOD_DATE_GAP = 6

    colon_src = Image.open(IMG / "time_colon.png").convert("RGBA")
    cbb = colon_src.split()[3].getbbox()
    CW = (cbb[2] - cbb[0]) + 2 * AOD_TIGHT_PAD
    CH = colon_src.size[1]
    hh_w = 2 * DW + HSP
    mm_w = 2 * DW + MSP
    time_w = hh_w + AOD_COLON_GAP + CW + AOD_COLON_GAP + mm_w
    hx = (336 - time_w) // 2
    colon_x = hx + hh_w + AOD_COLON_GAP
    mx = colon_x + CW + AOD_COLON_GAP
    colon_y = hy + (DH - CH) // 2

    # Nudge so the *visible ink* of the sample HH:MM is centred (narrow "1" etc. leave empty
    # pad inside the digit cells; cell-box centring alone looks shifted).
    hh = f"{AOD_SAMPLE['hour']:02d}"
    mm = f"{AOD_SAMPLE['minute']:02d}"
    def _ink(path):
        return Image.open(path).convert("RGBA").split()[3].getbbox()
    h0 = _ink(IMG / f"num_big_{int(hh[0]):02d}.png")
    m1 = _ink(IMG / f"num_big_{int(mm[1]):02d}.png")
    ink_l = hx + h0[0]
    ink_r = mx + DW + MSP + m1[2]  # Pillow bbox right is exclusive
    shift = int(round(168 - (ink_l + ink_r) / 2))
    hx += shift
    colon_x += shift
    mx += shift

    sep_src = Image.open(IMG / "date_sep.png").convert("RGBA")
    sbb = sep_src.split()[3].getbbox()
    SW = (sbb[2] - sbb[0]) + 2 * AOD_TIGHT_PAD
    day_w = 2 * DDW + DSP
    date_w = day_w + AOD_DATE_GAP + SW + AOD_DATE_GAP + day_w
    dx0 = (336 - date_w) // 2
    sep_x = dx0 + day_w + AOD_DATE_GAP
    month_x = sep_x + SW + AOD_DATE_GAP

    # Same optical nudge for the sample date row
    dd = f"{AOD_SAMPLE['day']:02d}"
    mo = f"{AOD_SAMPLE['month']:02d}"
    d0 = _ink(IMG / f"num_d_{int(dd[0]):02d}.png")
    o1 = _ink(IMG / f"num_d_{int(mo[1]):02d}.png")
    d_ink_l = dx0 + d0[0]
    d_ink_r = month_x + DDW + DSP + o1[2]
    d_shift = int(round(168 - (d_ink_l + d_ink_r) / 2))
    dx0 += d_shift
    sep_x += d_shift
    month_x += d_shift

    WW, WH = Image.open(IMG / "week_00.png").size
    wy = dy + DDH + AOD_WEEK_GAP
    wx = (336 - WW) // 2
    wk = AOD_SAMPLE["weekday"]
    wbb = _ink(IMG / f"week_{wk:02d}.png")
    w_shift = int(round(168 - (wx + wbb[0] + wx + wbb[2]) / 2))
    wx += w_shift

    xsi = 'xmlns:p3="http://www.w3.org/2001/XMLSchema-instance"'

    # Single packed AOD (neutral dim grey). EasyFace allows one AOD.fprj only.
    clock_col, date_col = AOD_GREY, AOD_DATE_GREY
    big = [_aod_recolor(IMG / f"num_big_{i:02d}.png", clock_col, f"aod_big_{i:02d}.png")
           for i in range(10)]
    big11 = big + [big[0]]
    colon = _aod_recolor(IMG / "time_colon.png", clock_col, "aod_colon.png", tight_pad=AOD_TIGHT_PAD)
    date = [_aod_recolor(IMG / f"num_d_{i:02d}.png", date_col, f"aod_date_{i:02d}.png")
            for i in range(10)]
    date11 = date + [date[0]]
    sep = _aod_recolor(IMG / "date_sep.png", date_col, "aod_date_sep.png", tight_pad=AOD_TIGHT_PAD)
    week = [_aod_recolor(IMG / f"week_{i:02d}.png", date_col, f"aod_week_{i:02d}.png")
            for i in range(7)]
    week_pairs = list(enumerate(week))

    prev = Image.new("RGBA", (336, 480), (0, 0, 0, 255))
    for i, ch in enumerate(hh):
        prev.alpha_composite(Image.open(AOD_IMG / big[int(ch)]), (hx + i * (DW + HSP), hy))
    prev.alpha_composite(Image.open(AOD_IMG / colon), (colon_x, colon_y))
    for i, ch in enumerate(mm):
        prev.alpha_composite(Image.open(AOD_IMG / big[int(ch)]), (mx + i * (DW + MSP), hy))
    for i, ch in enumerate(dd):
        prev.alpha_composite(Image.open(AOD_IMG / date[int(ch)]), (dx0 + i * (DDW + DSP), dy))
    prev.alpha_composite(Image.open(AOD_IMG / sep), (sep_x, dy))
    for i, ch in enumerate(mo):
        prev.alpha_composite(Image.open(AOD_IMG / date[int(ch)]), (month_x + i * (DDW + DSP), dy))
    prev.alpha_composite(Image.open(AOD_IMG / week[wk]), (wx, wy))
    prev.save(PREV / "preview_aod.png")
    prev.convert("RGB").save(AOD_IMG / "preview_aod_RGB.png")
    body = _aod_fprj_body(
        hx, hy, DW, DH, HSP, MSP, colon_x, colon_y, CW, CH, mx,
        dx0, dy, DDW, DDH, DSP, sep_x, SW, month_x,
        wx, wy, WW, WH, week_pairs,
        bg_name, "preview_aod_RGB.png", big11, colon, date11, sep, xsi)
    (AOD / "AOD.fprj").write_bytes("\r\n".join(body).encode("utf-8"))

    root_aod = ROOT / "AOD"
    if root_aod.exists() or root_aod.is_symlink():
        if root_aod.is_dir() and not root_aod.is_symlink():
            shutil.rmtree(root_aod)
        else:
            root_aod.unlink()
    try:
        root_aod.symlink_to(AOD, target_is_directory=True)
    except OSError:
        shutil.copytree(AOD, root_aod)
    print(f"AOD project: {AOD / 'AOD.fprj'} (single; EasyFace limit)  "
          f"centred time_w={time_w} date_w={date_w} weekday@{wx},{wy}")


def compile_face():
    """Run Compiler.exe, stamp id/name, patch tap records, verify. Refuses if any adb device is attached
    (Compiler.exe would otherwise push to an emulator and reboot it)."""
    OUT.mkdir(exist_ok=True)
    (PROJ / "output").mkdir(exist_ok=True)   # compiler writes <name>.info next to the fprj
    face = OUT / FACE_FILE
    if face.exists():
        face.unlink()
    adb = COMPILER.parent / "adb.exe"
    if adb.exists():
        devs = subprocess.run([str(adb), "devices"], capture_output=True, text=True, timeout=60).stdout
        attached = [l for l in devs.splitlines()[1:] if l.strip()]
        if attached:
            raise SystemExit("adb device(s) attached - disconnect them before compiling: %s" % attached)
    info = PROJ / "output" / (Path(FACE_FILE).stem + ".info")
    if info.exists():
        info.unlink()
    logf = open(OUT / "compiler.log", "wb")
    proc = subprocess.Popen([str(COMPILER), "-b", str(PROJ / "active.fprj"), "output", FACE_FILE, FACE_ID],
                            cwd=str(ROOT), stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    t0 = time.time()
    while proc.poll() is None and time.time() - t0 < 240:
        if face.exists() and info.exists():
            time.sleep(2)
            break
        time.sleep(0.5)
    if proc.poll() is None:
        proc.kill()
        print("compiler stopped after packing (skipping its adb emulator upload)")
    proc.wait()
    logf.close()
    if adb.exists():
        subprocess.run([str(adb), "kill-server"], capture_output=True, timeout=60)
    out = (OUT / "compiler.log").read_bytes()
    print(out.decode("utf-8", "replace").encode("ascii", "backslashreplace").decode("ascii"))
    print("compiler rc", proc.returncode)
    if not face.exists():
        raise SystemExit("NO FACE OUTPUT")
    raw = bytearray(face.read_bytes())
    rid = bytes(raw[ID_OFF:ID_OFF + FIELD_LEN]).split(b"\0", 1)[0]
    rname = bytes(raw[NAME_OFF:NAME_OFF + FIELD_LEN]).split(b"\0", 1)[0]
    print("header as compiled: id=%r name=%r" % (rid, rname))
    if rid != FACE_ID.encode() or rname != DISPLAY_NAME.encode():
        raw[ID_OFF:ID_OFF + FIELD_LEN] = FACE_ID.encode().ljust(FIELD_LEN, b"\0")
        raw[NAME_OFF:NAME_OFF + FIELD_LEN] = DISPLAY_NAME.encode().ljust(FIELD_LEN, b"\0")
        print("header patched")
    print("tap records patched (official H,W order + stock jump codes):", patch_taps(raw))
    face.write_bytes(raw)
    for info in (PROJ / "output").glob("*.info"):
        shutil.copy2(info, OUT / info.name)
    DIST.mkdir(exist_ok=True)
    shutil.copy2(face, DIST / FACE_FILE)
    info_out = OUT / (Path(FACE_FILE).stem + ".info")
    if info_out.exists():
        shutil.copy2(info_out, DIST / info_out.name)
    print("FACE", face, face.stat().st_size, "bytes", "sha256", hashlib.sha256(face.read_bytes()).hexdigest())
    print("published", DIST / FACE_FILE)
    verify_compiled(face.read_bytes())


def face_style_indices(d):
    """Valid style-block indices in a compiled .face (stops before garbage past the table)."""
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


def is_aod_style(d, fi):
    """AOD styles have no tap zones and far fewer images than a full Complex LCD+ style."""
    base = 168 + fi * 88
    n_tap = struct.unpack_from("<I", d, base + 8 + 9 * 8)[0]
    n_img = struct.unpack_from("<I", d, base + 8 + 2 * 8)[0]
    return n_tap == 0 and n_img < 40


def verify_compiled(d):
    """Re-render every style from the compiled .face, compare with the pre-compile preview, overwrite
    previews/preview_<style>.png with the compiled render, and print the digit alignment table.

    Compiler inserts the AOD face as its own style block (here: index 1, between active and A..G).
    Those blocks are skipped when matching the 8 normal style previews and written to preview_aod.png.
    """
    samples = {}
    for w in W:
        if w["t"] == "num":
            samples[w["Value_Src"]] = w["sample"]
        elif w["t"] == "list":
            samples.setdefault(w["Index_Src"], w["sample"])
    names = {"0821": "steps", "0822": "BPM", "0823": "kcal", "0824": "stand", "0828": "sleep h", "1812": "day", "1012": "month",
             "0841": "battery %", "0811": "HH", "1011": "MM", "1811": "seconds", "2031": "temperature",
             "0851": "alarm HH", "1051": "alarm MM", "1821": "distance", "5031": "AQI", "1832": "today high", "2032": "today low",
             "7831": "UV", "2831": "humidity"}
    all_fi = face_style_indices(d)
    aod_fi = [fi for fi in all_fi if is_aod_style(d, fi)]
    main_fi = [fi for fi in all_fi if fi not in aod_fi]
    print(f"compiled styles: {len(all_fi)} total, {len(main_fi)} main, {len(aod_fi)} AOD {aod_fi}")

    aod_samples = {"0811": AOD_SAMPLE["hour"], "1011": AOD_SAMPLE["minute"],
                   "1812": AOD_SAMPLE["day"], "1012": AOD_SAMPLE["month"],
                   "2012": AOD_SAMPLE["weekday"]}
    for i, fi in enumerate(aod_fi):
        im, _ = render_compiled(d, fi, aod_samples)
        # Map AOD style order to STYLES keys (Compiler: AOD.fprj then A..G if multi-AOD packed)
        key = STYLES[i][0] if i < len(STYLES) else f"aod{i}"
        out = PREV / ("preview_aod.png" if key == "orig" else f"preview_aod_{key}.png")
        im.save(out)
        ref_path = AOD_IMG / ("preview_aod_RGB.png" if key == "orig" else f"preview_aod_{key}_RGB.png")
        if ref_path.exists():
            ref = Image.open(ref_path).convert("RGB")
            diff = ImageChops.difference(im.convert("RGB"), ref).getbbox()
            print(f"compiled AOD style #{fi} ({key}) vs preview: "
                  f"{'IDENTICAL' if diff is None else 'DIFFERS in ' + str(diff)}")
        else:
            print(f"compiled AOD style #{fi} saved to {out.name}")

    used = set()
    facts = []
    for key, stem, title, tint in STYLES:
        pref = PREV / f"preview_{key}.png"
        ref = Image.open(pref).convert("RGBA")
        # Recover if a previous broken verify overwrote a style preview with the AOD frame
        if sum(ref.convert("L").resize((1, 1)).getpixel((0, 0)) for _ in [0]) < 20:
            alt = PROJ / f"preview_{key}_RGB.png"
            if alt.exists():
                ref = Image.open(alt).convert("RGBA")
        best = None
        for fi in main_fi:
            if fi in used:
                continue
            im, fcts = render_compiled(d, fi, samples)
            diff = ImageChops.difference(im.convert("RGB"), ref.convert("RGB")).getbbox()
            if best is None or diff is None:
                best = (fi, im, fcts, diff)
            if diff is None:
                break
        if best is None:
            raise SystemExit(f"no main style left to match preview_{key}")
        fi, im, facts, diff = best
        used.add(fi)
        print(f"compiled style #{fi} vs preview_{key}: {'IDENTICAL' if diff is None else 'DIFFERS in ' + str(diff)}")
        im.save(PREV / f"preview_{key}.png")
    print("tap records (compiled): face id  jump code  image  W x H (stored H,W)")
    for fi, rid, o in tap_records(d):
        if fi in aod_fi:
            continue
        h, w = struct.unpack_from("<HH", d, o + 52)
        iid = struct.unpack_from("<I", d, o + 48)[0]
        imgs, _ = _face_tables(d, fi)(2)
        io = imgs[iid][0]
        iw, ih = struct.unpack_from("<HH", d, io + 4)
        print(f"  #{fi} {d[o:o + 2].rstrip(bytes(1)).decode():>2s}  {d[o + 40:o + 44].hex()}  img {iw}x{ih}  zone {w}x{h}")
    print("digit groups (compiled, last matched style): name  x(anchor) y  frame WxH  spacing  glyph rows top-base")
    for f in facts:
        print(f"  {names.get(f['src'], f['src']):12s} {f['x']:4d} {f['y']:4d}  {f['w']}x{f['h']}  {f['sp']:3d}  {f['top']}-{f['base']}")


if __name__ == "__main__":
    main()

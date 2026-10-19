#!/usr/bin/env python3
"""One-off asset generator: builds bezel header labels from stock glyph pixels (no fonts needed).
Writes tools/assets/label_stats.png, label_alarm.png, label_{music,cards,compass}.png and the
cap-12 strip versions label_*_s.png. build.py only reads the PNGs.

Glyph sources (all from the stock background stock/resources/23fd149.png):
  S, T, M  stock "STEPS/BPM" header (rows 9-23, grey 153)            -> alpha = L/153
  R, L     stock "WORLD" label (rows 213-227, grey 204; same cap height 14.5 px and ~2.6 px stems as the
           headers)                                                   -> alpha = L/204
  A        the stock 'A' of the "KCAL" box label (dark ink 54 on panel 139, cap 13 px, rows 144-156).
           The box labels have an embossed inner shade (~111-134) under the crossbar, so alpha is taken
           against 110 instead of the panel level: alpha = (110-L)/(110-54). It is scaled by 14.5/13
           (16x supersampled, bicubic) to the header cap height (top at row 9.5, baseline row 23 like the
           header caps) and emboldened by 1/8 px per side (stems 2.45 -> ~2.7 px, header S/T stems 2.63),
           then reduced with a box filter. The STAND 'A' is the same glyph; the KCAL one is used because
           its right edge is clean (the STAND 'A' touches the N's anti-aliasing).
Labels are saved in stock header grey (153) with alpha = coverage."""
from pathlib import Path
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "tools" / "assets"
out.mkdir(exist_ok=True)
Lg = Image.open(ROOT / "stock" / "resources" / "23fd149.png").convert("L")


def hdr(x0, x1):                       # header glyph, rows 8..25
    return Lg.crop((x0, 8, x1 + 1, 26)).point(lambda v: 0 if v <= 2 else min(255, round(v * 255 / 153)))


def world(x0, x1):                     # WORLD glyph, rows 212..229 (same cap/baseline offset as rows 8..25)
    return Lg.crop((x0, 212, x1 + 1, 230)).point(lambda v: 0 if v <= 2 else min(255, round(v * 255 / 204)))


def box_glyph(x0, x1, ref=110, grow=1, SS=16):
    """Any stock box-label glyph (KCAL/STAND row, dark ink 54 on panel 139, cap 13 px rows 144-156)
    scaled to the header cap (14.5 px), same recipe as the A below."""
    return kcal_a(x0, x1, ref, grow, SS)


def kcal_a(x0=220, x1=232, ref=110, grow=1, SS=16):
    src = Lg.crop((x0, 144, x1 + 1, 157)).point(lambda v: max(0, min(255, round((ref - v) * 255 / (ref - 54)))))
    s = 14.5 / 13
    big = src.resize((round(src.width * s * SS), round(14.5 * SS)), Image.BICUBIC)
    if grow:
        big = big.filter(ImageFilter.MaxFilter(2 * grow + 1))
    w = int(src.width * s + 0.999) + 1
    can = Image.new("L", (w * SS, 18 * SS))
    can.paste(big, (0, round(1.5 * SS)))          # cap top at row 9.5 of the face (index 1.5)
    o = can.resize((w, 18), Image.BOX)
    bb = o.getbbox()
    return o.crop((bb[0], 0, bb[2], 18))


def compose(parts):
    lab = Image.new("L", (sum(p.width + g for p, g in parts), 18))
    x = 0
    for p, g in parts:
        lab.paste(Image.composite(p, lab.crop((x, 0, x + p.width, 18)), p.point(lambda v: 255 if v else 0)), (x, 0))
        x += p.width + g
    bb = lab.getbbox()
    return lab.crop((bb[0], 0, bb[2], 18))


def save(lab, name, tight=False):
    if tight:
        lab = lab.crop(lab.getbbox())
    im = Image.new("RGBA", lab.size, (153, 153, 153, 0))
    im.putalpha(lab)
    im.save(out / name)
    print(name, im.size)


S, T, M = hdr(191, 201), hdr(204, 215), hdr(297, 311)
R, L = world(140, 151), world(155, 164)
A = kcal_a()
# gaps: S-T 2 px as in the stock header; T/A kerned -1 (diagonals give the optical gap); stock WORLD
# spacing (1-3 px) for A-L-A-R-M
save(compose([(S, 2), (T, -1), (A, -1), (T, 2), (S, 0)]), "label_stats.png")
save(compose([(A, 1), (L, 1), (A, 1), (R, 2), (M, 0)]), "label_alarm.png", tight=True)

# Strip shortcut labels: MUSIC / CARDS / COMPASS in the header font (cap 14.5 px), then scaled to
# stock BATTERY label size (cap 12) as label_*_s.png for the bezel strip.
#   M, S        stock "STEPS/BPM" header (M of BPM, S of STEPS)
#   I           stock "TIME" header (x 81-84)
#   R, D, O     stock "WORLD" label (R 141-151, D 167-179, O 122-137)
#   A, C        KCAL box-label A / C, scaled to the header cap like the STATS A
#   U           built from the WORLD O: its bowl below the middle row, the middle row's two side strokes
#               continued straight up to the cap top, and 3 centre columns dropped (O 16 px -> U 13 px,
#               about the D's width, as in the stock geometric sans)
I_ = hdr(81, 84)
D_, O_ = world(167, 179), world(122, 137)
C_ = box_glyph(208, 218)
P_ = hdr(232, 242)


def u_glyph():
    o = O_
    mid = 8                                            # middle row of the cap (rows 1.5 .. 15 of the crop)
    u = o.copy()
    for y in range(0, mid):
        for x in range(o.width):
            u.putpixel((x, y), o.getpixel((x, mid)) if y >= 1 else 0)
    # cap top row: copy the O's own top-row coverage onto the stems (anti-aliased stem ends)
    for x in range(o.width):
        u.putpixel((x, 1), round(o.getpixel((x, mid)) * o.getpixel((7, 1)) / 255) if o.getpixel((x, mid)) else 0)
    c = o.width // 2
    return drop_cols(u, c - 1, c + 2)


def drop_cols(im, a, b):
    """drop columns a..b-1"""
    out = Image.new("L", (im.width - (b - a), 18))
    out.paste(im.crop((0, 0, a, 18)), (0, 0))
    out.paste(im.crop((b, 0, im.width, 18)), (a, 0))
    return out


U_ = u_glyph()
save(compose([(M, 2), (U_, 2), (S, 2), (I_, 2), (C_, 0)]), "label_music.png", tight=True)
save(compose([(C_, 1), (A, 1), (R, 2), (D_, 2), (S, 0)]), "label_cards.png", tight=True)
save(compose([(C_, 2), (O_, 2), (M, 2), (P_, 2), (A, 1), (S, 2), (S, 0)]), "label_compass.png", tight=True)


# 1.0 (three shortcuts): the same words at the stock BATTERY label size (cap 12 px instead of 14.5, the
# stock secondary bezel label of this face), so MUSIC, CARDS and COMPASS fit next to the battery.
# Scaled from the header-size alpha (16x bicubic up, box down), baseline kept on the bottom row.
def scaled(name_in, name_out, cap=12.0, src_cap=14.5):
    a = Image.open(out / name_in).split()[3]
    s = cap / src_cap
    SS = 16
    big = a.resize((a.width * SS, a.height * SS), Image.BICUBIC)
    w, h = int(a.width * s + 0.999) + 1, int(a.height * s + 0.999) + 1
    can = Image.new("L", (w * SS, h * SS))
    bw, bh = round(a.width * s * SS), round(a.height * s * SS)
    can.paste(big.resize((bw, bh), Image.BICUBIC), (0, h * SS - bh))
    save(can.resize((w, h), Image.BOX), name_out, tight=True)


for n in ("music", "cards", "compass"):
    scaled(f"label_{n}.png", f"label_{n}_s.png")

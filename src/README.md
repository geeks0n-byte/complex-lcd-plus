# Complex LCD+ (clean EasyFace rebuild)

A clean EasyFace (Gen2) rebuild of the stock Xiaomi **Complex LCD** face for DeviceType `567`
(the compiler reports it as MiBand10Pro / P67, 336x480). It uses the stock `manifest.xml` as the blueprint and the untouched stock images.
FaceId `29100101`, display name **Complex LCD+**.

## Build (one command)

```
cd C:\Users\Giga\Documents\Repos\complex-lcd-plus\src
python tools\build.py              # regenerate everything, then compile
python tools\build.py --no-compile # only regenerate the project and previews
python tools\build.py --verify-only # re-render the existing output\ComplexLCDPlus.face and check it
```

Requirements: Python 3 with Pillow, and EasyFace at `C:\Users\Giga\Documents\Projects\miband\tools\easyface\Compiler.exe`, used as-is. The build needs no fonts: the only text asset, `tools\assets\label_alarm.png`, is pre-rendered by `tools\make_assets.py` (URW Gothic Demi, run once on Linux).

`tools\build.py` does all of this from scratch:
1. Reads `stock\resources\manifest.xml` and the stock PNGs. This is the untouched stock unpack, and `resource.bin` matches its `hashCode`.
2. Regenerates `project\images\` (flat ASCII names), the recolored backgrounds, the battery-fill frames and the "%" frames.
3. Writes `project\active.fprj` and `A.fprj` ... `G.fprj` (8 styles) as UTF-8 bytes with EasyFace's own `encoding="utf-16"` header line, the same as files EasyFace saves itself.
4. Renders previews into `previews\` (centred numbers use floor rounding, which reproduces the stock preview pixel-for-pixel). These include `compare_sheet.png` (stock first, then every colour style, then the tap map — **no AOD panels**), `preview_aod.png`, and `taps_overlay.png` (Phosphor Green at 2x with labelled zones).
5. Writes `project/AOD/{AOD.fprj,images}/` (single AOD pack — EasyFace rejects multiple AOD styles) and runs `Compiler.exe -b project\active.fprj output ComplexLCDPlus.face 29100101`.
6. Stamps the face id and name into the header (offset 40 = id, offset 104 = name). Compiler.exe takes the id from a fixed template and the name from the active style's `Screen Title`.
7. **Verifies the compiled face:** `render_compiled()` parses the `.face` and re-renders every main style plus AOD; each must match the pre-compile preview pixel-for-pixel (`IDENTICAL`). Also publishes a shallow copy to `..\ComplexLCDPlus.face` (repo root).

**adb safety.** After packing, Compiler.exe always tries to push the face to the Xiaomi emulator over adb, using `shell rm …watchface_list.json` and `shell reboot`. To prevent that, `build.py` refuses to compile while any adb device is attached. It also stops the compiler as soon as the `.face` and `.info` exist, then runs `adb kill-server`. The `.info` file is boilerplate the compiler always writes; it holds no real data.

## Layout (current build 1.0)

Order left→right / top→bottom as on the band. Authoritative positions live in `tools/build.py`; `previews/tap_zones.md` is regenerated every build.

| Area | What | Source | Notes |
|---|---|---|---|
| Weather row (LCD) | **main temp** (stock 28×38 + degree) then **↑ high / ↓ low** (14×26 grey digits + degree rings + arrows) then stock 46×46 **weather icon** at (265,293) | `2031`, `1832`, `2032`, `3031` | Feels-like `1831` unsupported on band. HL secondary grey (50 % glyph alpha, full LCD shadow). Arrows centred on solid digit height. **Equal 8 px ink gaps** temp↔HL and HL↔icon (`G_ROW1`). |
| Humidity / AQI / UV (LCD) | drop + `NN%`; leaf + value; sun + value; small box digits, baseline 362 | `2831`, `5031`, `7831` | Order **humidity → AQI → UV**. Filled 16 px icons baked into the shared background (`icon_drop` / `icon_leaf` / `icon_sun` + `lcd_shadow`). Slots sized for worst case (100% / 3-digit AQI / UV 11), `ROW2_GAP` 10, right-aligned to x 322. |
| Next alarm (LCD) | "ALARM" + bell, then HH:MM in stock 7-segment temperature digits | `0851` / `1051` | Label + bell centred over HH:MM ink; same baseline as row 2 (362). |
| Battery strip (bezel) | compact **icon \| 20-segment bar \| digits + `%`**, all on `0841`, scheme-aware (lit ~153 / unlit ~70) | `0841` | Icon 27×14; bar height 14; `%` digits bar-height with 2 px solid digit gap and 3 px before `%`. Equal **16 px** air icon↔bar↔%. Group centred on the **full screen** using common 2-digit `NN%` ink → **22/22** side margins (sample 80%). MUSIC / CARDS / COMPASS labels **removed**. |
| Status sockets | BT, sleep mode, lock, charging; 22 px, 8 px gaps, x 16/46/76/106, y 247 | `2041`, `1841`, `3041`, `1041` | Off = dim grey; on = white / scheme colour. |
| Stat boxes | Upper: STEP \| KM (decimal). Lower: BPM \| SLEEP (decimal + zero-cover) | `0821`, `1821`, `0822`, `0828` | KM/BPM cells swapped vs stock. Sleep no-data: ImageList cover shows single `0` (**band OK**). No sleep H:MM (no sources). Equal **75×80** tap cells; workouts/sleep right edge = calendar right. |
| AOD | Dim grey HH:MM + DD-MM + short weekday (SUN..SAT) on black | time / date / weekday | Single `project/AOD/AOD.fprj` only (EasyFace). Preview `preview_aod.png`; not on the compare sheet. Per-scheme AOD needs GMF. |
| Removed vs earlier 1.0 drafts | MUSIC/CARDS/COMPASS strip; battery-%-inside-icon; calories bezel; enlarged weather | | Flashlight tap is the whole strip. |

### Tap zones (current: 10 zones)

Same geometry rules as before (ink bounds + pad, min 44, overlap split, neighbour gap close). Every zone has its own full-size transparent bitmap; `patch_taps()` fixes Height/Width order and stock 0x1x codes. **KM cell → Workouts** `0100f300` (packed as `breath` placeholder, then patched; **band OK**). **Flashlight** spans the battery strip (left = stopwatch left, right = calendar right). No music/cards/compass taps. Authoritative table: `previews/tap_zones.md`.

| Action | x,y,w,h | Covers |
|---|---|---|
| stopwatch | 9,36,163,164 | analog dial (r 77) |
| alarm | 17,308,121,63 | ALARM + bell + HH:MM |
| steps | 179,40,75,80 | upper-left STATS cell |
| hrm | 179,120,75,80 | lower-left STATS (BPM) |
| sleep | 254,120,71,80 | lower-right STATS (SLEEP) |
| distance | 254,40,71,80 | upper-right STATS (KM) → Workouts `0100f300` |
| weather | 138,292,187,79 | temp + HL + icon + humidity/AQI/UV |
| calendar | 138,245,187,47 | weekday/date tab |
| flashlight | 9,200,316,45 | battery icon + bar + % |
| timer | 17,371,308,88 | SEC + HH:MM |

A music/workout test variant is kept at `output/tests/ComplexLCDPlus_music-workout-test.face` (SHA256 E986D453…; **never modify**). Editable widgets are not supported by EasyFace Gen2 (need GMF).

## Fixes compared with the broken Mi-Create unpack

Every value comes straight from the stock manifest.

| Element | Stock definition | Rebuild |
|---|---|---|
| Date row | flex row at (234,257), gap −6: month, separator, day | **DD-MM**: day at x=234, separator at 262, month at 276; leading zeros on, spacing −6 |
| Battery % | flex row at (64,308), gap −3: number (left, spacing −5) then "%" | number left-packed at x=64, no leading zeros. The "%" is an ImageList on 0841 that moves with the digit count (x=81/96/111), so it reproduces the flex row exactly |
| Battery icon and fill | icon at (25,311); LineProgressBar 28x12 at (28,316) | icon restored. The fill is an ImageList of 29 clipped frames, one per pixel of fill width |
| Segment bar | 11 images, keys 0,1,11…91 | identical |
| Steps, calories, HR, sleep score | centred at x=219/290, spacing −3, no leading zeros (sleep score: 3 digits) | Alignment=center. X is derived the way Compiler.exe derives the anchor (anchor = X + w·digits/2) |
| HH:MM:SS | (89,377) spacing 6, (213,377) spacing 4, (24,406) spacing 1, leading zeros | identical |
| Hands | pivots (6,39) / (6,38) / (6,38); dial centre (90,117); hour hand 720° per 24 h | The compiler puts the pivot at the widget centre, so the AnalogClock box is (0,0) 180x234 → (90,117). Hand origins in the binary match stock: (84,78) and (84,79) |
| Temperature | °C, 28x38 digits right-aligned at x=256, y=298; degree sign at (253,302) | **bigger**: the HH:MM digits downscaled to 30x49 (62.5 %), right-aligned at x=230, y=311, spacing 2, no leading zeros. Minus = 11th frame, made from the middle segment ('8' minus '0'). A crisp redrawn 14 px degree ring at (232,313) |
| Weather icon | 46x46 at (265,293) | the 18 stock icons upscaled to 64x64 (LANCZOS) at (249,303). The widest icon's right edge sits on x=313 (= minutes right edge), centred on the temperature digits. Keys unchanged |
| Weekday | stock position and keys | identical |
| AM/PM | ImageList on miscIsPM at (243,351) | removed (see Background clean-up) |

## Added elements (option 3b) — historical notes

Early experiment positions (alarm/distance on the WORLD TIME strip, etc.). **Current** placement is in the Layout table above; alarm lives in the LCD, distance is the KM stat cell, strip is icon|bar|%.

| Element | Where | Data source | Notes |
|---|---|---|---|
| Next alarm | old WORLD TIME row, left: "ALARM" label 70x15 at (20,215), HH at (97,208), colon (128,208), MM at (132,208); the whole row (label, 7 px gap, HH:MM, x 20-161) is centred under the dial centre x=90 | AlarmHour `0851`, AlarmMinute `1051` (two 2-digit numbers, leading zeros) | label in the header font (`tools/assets/label_alarm.png`, see STATS row): A from the KCAL box label, L and R from the stock WORLD label, M from STEPS/BPM. Its cap height (14.5 px) equals the digit height, so label and digits share both the baseline (row 229) and the top row (215). Same 7 px label-digit gap as the stock WORLD TIME row. Colours: Original label grey 153, digits 225; other styles the scheme hue at the same luminance (digits as grey 225, label as grey 153, same colour as TIME/STATS). The 567 data list has no "alarm enabled" source, so "no alarm set" can't be shown as `--:--`. What the band reports then (00:00 or the last alarm) is untested |
| Distance today | old WORLD TIME row, right, centred under the stat boxes (outer x 181-322, centre x=252): walker icon 14x20 at (206,210), value at (228,208), dot 2x2 at (244,228), "KM" 28x15 at (269,215) | `1821` ("Current step (kilometer)", native healthStepKiloMeter): one DigitalNum, 2 digits, leading zero, Spacing 6, alarm digit set, plus a fixed dot in the 6 px gap -> "4.2" | walker and KM (`tools/assets/icon_walk.png`, `label_km.png`; K = M's stock left stem + the arms of the KCAL box-label K, M from STEPS/BPM) in the label colour, value and dot in the alarm digit colour; same baseline (229) and top (215) as the alarm. The value is always 2 digits (leading zero), so the group has a fixed width (walker 206 to KM 297) and stays centred for every value up to 9.9; no tap. **Assumes 1821 counts in 0.1 km; unverified, and probably wrong:** the sleep research (see SLEEP) suggests distance, like sleep, may be a decimal value (km) shown through the DigitalNum decimal mode (Digits=20). Compare the band with Mi Fitness: if a 4.2 km day shows 0.4 (or 0.0), switch distance to the same decimal mode as sleep. |
| Bluetooth | old BATTERY spot, 24x24 at (16,246), same left column as the alarm row (first of three sockets: Bluetooth x=16, screen lock x=56, charging x=96, y=246, 16 px gaps) | BT connection status `2041` (0 = off, 1 = on), 2-frame ImageList | socket in the stock LED-dot look with a Bluetooth rune. Connected: rune lit (white on Original, scheme colour on the others, `bt_1_<style>.png`). Disconnected: dim grey rune, no slash (same dim look as the other off sockets) |
| Charging | old BATTERY spot, 24x24 at (96,246) (swapped with the lock on request) | charging status `1041`, 2-frame ImageList (0 dim, 1 lit) | same socket style with a lightning bolt. Charging: bolt lit (white on Original, scheme colour on the others, `chg_1_<style>.png`). Not charging: dim grey bolt, no slash. The ImageList uses the largest key <= value, so a value of 2 (if the band reports "full" that way) would also show lit. Untested |
| Screen lock | old BATTERY spot, 24x24 at (56,246) | screen lock `3041`, 2-frame ImageList (0 dim, 1 lit) | same socket style with a padlock (`lock_1_<style>.png`). Lit = locked, dim grey = unlocked. On the band the face is normally seen unlocked, so the lit state may rarely or never appear (e.g. only on the lock/AOD transition). Untested |
| TIME header | stock position, bbox 67,9-116,23 (copy box 64,6-120,27) | static, baked into each style background | Original: the stock pixels copied back 1:1 (grey 153). Other styles: the stock glyph anti-aliasing as alpha, in the ALARM-label colour (scheme hue at the luminance of grey 153) |
| STATS header | replaces STEPS/BPM; `tools/assets/label_stats.png` (62x18) at (220,8), ink 220-281, centre x=250.5 (STEPS/BPM 191-311: 251), rows 9-24 | static, baked into each style background | S and T are the stock STEPS/BPM header pixels. The A is the stock A of the KCAL box label (dark ink on the panel, cap 13 px; the STAND A is the same glyph but touches the N's anti-aliasing), scaled 14.5/13 to the header cap height and emboldened 1/8 px per side to the header stem width (~2.6 px). Built by `tools/make_assets.py` (no fonts). Same colours as TIME. The top-centre dot (160-175) is ~44 px from both labels |

The alarm row is centred under the dial (x=90) and the distance group under the stat boxes (x=252), so each sits under the block it belongs to.

## Alignment (checked on the compiled face)

| Group | Y | Frame | Solid glyph rows | Note |
|---|---|---|---|---|
| steps / BPM | 82 / 82 | 14x26 | 88-103 / 88-103 | equal |
| kcal / sleep score | 172 / 172 | 14x26 | 178-193 / 178-193 | equal |
| day / month (+ weekday) | 257 / 257 | 20x28 | 262-280 | equal. **Fixed:** stock digits 1, 4 and 7 stop one row short (bottom row 22 instead of 23), so a day like "16" sat 1 px higher than "08". Those three frames are extended by one row (`fix_date_baseline`). This also applies to battery % (same digit set) |
| battery % / "%" / icon | 308 | 20x28 | 313-331 | equal |
| temperature | 311 | 30x49 | 313-357 | tops aligned with the battery % row (313) |
| HH / MM | 377 / 377 | 48x78 | 379-451 | equal |
| seconds | 406 | 26x42 | 410-444 | stock position. The base sits 7 px above HH:MM (451) by stock design; `SEC_SHIFT = 7` in build.py would align it (moves the SEC label too), but it was reverted to stock at user request |
| alarm HH / MM / label | 208 | 14x26 | 214-229 | label baseline 229 |

The 7-segment 1 and 4 (HH:MM, seconds, temperature) are a little shorter than the other digits by design of the stock font; that is unchanged.

## SLEEP stat (replaces STAND, lower-right stat box)

- **Source:** `0828` "Sleep Duration" (EasyFace DeviceInfo.db, DeviceType 567; `1028` is Sleep Score, `1828` target progress, `2028` quality).
- **Unit (assumed: hours with one decimal, e.g. 7.5 = 7 h 30 min; medium confidence):** a Mi Band 8 data-ID list on bandbbs (thread 7564) calls 0828 "睡眠-小时" (sleep, hours); official Xiaomi faces for Band 8/9 show sleep with a decimal point and a "trailing zero" option; EasyFace issue #26 ("show sleep time as a decimal, 7.5 hours") was answered with Mi-Create issue #9: a DigitalNum with `Digits="20"` and 12 bitmaps (index 10 = minus, index 11 = decimal point) makes the firmware draw the value with its decimals, and the reporter confirmed it worked. No separate sleep-hours/sleep-minutes ids exist for 567, and Compiler.exe writes Digits raw (no expressions), so H:MM is not possible; "7.5" is the best format available.
- **Widget:** `sleep_hours`, DigitalNum, Digits=20 (decimal mode), centred (Alignment 1) on the stock STAND anchor x=290, y=172, small LCD digits, spacing -3, no leading zeros. Bitmap 11 is `num_s_dot.png`: a dot drawn like a stock stroke (2x2 solid core with 75-alpha edges, like the digit 1's "+##+" stem) on the digit baseline, cell cols 5-8 of the same 14x26 cell. Digits have ink in cell cols 2-11 and the pitch is 11 px, so the dot splits the 12 free px into 4 | 4 | 4: equal gaps on both sides (the first 3 px dot sat 5 | 4). The gaps are wider than the 1 px between two digits because every bitmap of a DigitalNum shares one cell size (stock arrays too), so the dot takes a full digit pitch; bitmap 10 (minus) is a copy of 0 and never needed.
- **Band:** no-data without cover showed literal `00`. An ImageList cover on `0828` (key 0 = panel + single `0`, key ≥1 = transparent) is **band-confirmed**. After a real night, confirm decimals still show (e.g. `7.5`).
- **Label:** `sleep_label()` erases STAND and builds SLEEP from stock box-label glyphs; part of the shared background.
- **Tap:** opens the sleep app (`btn_[sleep]`).

## Tap zones (history / mechanics)

The **current** zone table is in the Layout section above and in `previews/tap_zones.md`. Older tables below are historical.

Computed by `tap_zones()` from widget ink bounds + pad, min size, overlap split, neighbour gap close; post-normalize locks STATS to equal cells and flashlight/workouts/sleep edges to stopwatch/calendar. Build fails on overlap.

"Wiki" = EasyFace Actions page id. "Compiler table" = RedmiWatch4ActionPacker ids (sleep, cards, compass, breath, …). Unknown ids pack as empty taps.

### Tap records: post-compile patch (taps did not work on the band)

Build 01795A11… (8x8 tap bitmap, records as Compiler.exe writes them) had no working taps on a Mi Band 10 Pro. `stock\resource.bin` is Xiaomi's own compiled build of this face for the same device, and its 5 action records (steps, calories, stand, heart rate, weather) differ from what Compiler.exe v4.23 writes (RedmiWatch4ActionPacker, used for every 567 face):

| | Compiler.exe v4.23 | Xiaomi build (stock) |
|---|---|---|
| tap bitmap | whatever the widget uses (we had one shared 8x8 transparent image) | own transparent bitmap of exactly the zone size (70x60, 140x32) |
| size words at record +52/+54 | Width, Height | Height, Width (steps 70x60 -> `3c00 4600`, weather 140x32 -> `2000 8c00`) |
| steps / calories / stand code (+40) | `01 00 33 01` | `01 00 33 11` |
| heart-rate code | `01 00 53 00` | `01 00 53 10` |
| weather code | `05 00 13 02` | `01 00 13 12` |

The build now (1) gives every tap zone its own fully transparent `btn_<action>.png` of exactly the zone size, and (2) after compiling, `patch_taps()` swaps the size words to Height, Width in every record and replaces the steps/calories/standing/hrm/weather codes with the stock ones. The app jumps (alarm `01008302`, calendar `01005304`, flashlight `0100a301`, stopwatch `01000302`, timer `01006302`) are not in the stock face and keep the compiler's codes. The `.face` has no checksum (DefaultPacker writes none), so patching in place is safe. `--verify-only` prints every record (code, bitmap size, zone).

Band test (Mi Band 10 Pro): with this patch all 10 taps work. A tap-test face (since deleted) showed the Height, Width size order is what matters: only the unpatched record failed; an 8x8 tap bitmap and the compiler's own codes also worked once the size order was fixed. The patch is kept as is.

**Tap bitmaps (v2.5):** v2.1-v2.4 used 8x8 bitmaps (zone size only in the widget Width/Height). On the band (v2.4) only the zones with Xiaomi-style codes (0x10 flag in byte 3, written by `patch_taps()`: sleep `0100c310`, and the stock-coded steps / calories `01003311`, hrm `01005310`, weather `01001312`) worked; every app jump with the compiler's code (alarm `01008302`, calendar `01005304`, flashlight `0100a301`, stopwatch `01000302`, timer `01006302`) stopped working. The earlier 8x8 test had only covered the Xiaomi-coded zones. These app jumps had worked with zone-sized bitmaps, so v2.5 goes back to zone-sized bitmaps for every zone. Adding the 0x10 flag to the app codes is not done: no stock record shows such a code for these apps, so it is unverified. Cost: ~0.48 MB per style (3.84 MB for 8 styles).

## LCD drop shadow (v2.2 audit)

Every stock LCD and stat-box glyph carries a soft drop shadow: the digit sets (main clock, seconds, temperature, date, box digits, degree, dash, date separator, weekday) in their images, and SEC, STEP/BPM, KCAL/STAND (so also the copied KM / SLEEP letters) baked into the background. Measured: black, offset (0, +2), Gaussian sigma ~0.85, opacity ~0.144. `lcd_shadow()` applies that to ALARM label, humidity/AQI/UV icons, humidity `%`, alarm colon/bell, decimal dots, and HL arrows/rings (glyph at 50 % alpha, shadow full strength). On the black bezel, strip battery icon/bar/digits/`%` use shadow-free light copies (`deshadow()` / drawn bezel art) so they stay crisp.

## Background clean-up (all styles)

`clean_background()` in build.py, applied before recoloring:
- Header labels **TIME**, **STEPS/BPM** and **BATTERY** erased from the shared base background. **TIME** and a new **STATS** label are then put back per style by `header_labels()` (see below). They sit on pure black bezel (every border pixel around them is 0,0,0), so they are filled with black. The small decorative LED dot left of the old BATTERY label (16 px dark-grey disc, fill #1E1E1E, rim #343434, bbox 9,250-24,265; no manifest widget or data behind it) is moved to the top centre, bbox 160,14-175,29 (centre ≈167.5,21.5). That is where the band OS draws its red unread-notification dot (estimated from on-device footage of a 336x480 Band 8 Pro; not confirmed for the 10 Pro), so the grey dot reads as an unlit LED that the OS lights up. The old spot is filled with black (it was pure black around the dot). The BATTERY erase box is 40,248-125,268 so the faint shadow row at y=266 is gone too; the three status sockets now sit there.
- The whole **WORLD TIME** strip (◄, "WORLD TIME", ►, rows 213-231) is filled with black. Rows 207-236 were already pure black apart from these graphics.
- The **↰ arrow** next to AM/PM is removed. It is a static, decorative glyph baked into the stock background, with no data binding in manifest.xml. The panel there is refilled column by column from clean panel rows above and below it, because the panel shading only varies horizontally.
- **AM/PM** (stock ImageList on `miscIsPM`) is removed completely. EasyFace/DeviceInfo for 567 documents only `0813 AM/PM` and no 12h/24h-mode source, so a "show only in 12h mode" variant cannot be built in a documented way.

## Styles (EasyFace multi-style: every `*.fprj` in `project\` becomes one style)

| File | Title | Recolor |
|---|---|---|
| active.fprj | Original LCD | stock look: grey LCD panel, white dial markings |
| A.fprj | Phosphor Green | panels tinted (112,170,100); dial markings, hands and unlit battery segments green; centre hub stays stock |
| B.fprj | Amber | panels tinted (206,146,62); same elements amber |
| C.fprj | Ice Blue | hue/saturation of (112,164,200) |
| D.fprj | Signal Red | hue/saturation of (204,100,96) |
| E.fprj | Teal | hue/saturation of (96,180,166) |
| F.fprj | Violet | hue/saturation of (152,122,204) |
| G.fprj | Rose Pink | hue/saturation of (206,120,168) |

All 8 styles are active again since v2.4 (on request; v2.1-v2.3 shipped 5 / 3 / 3 of them for size). Tried and dropped: Lemon Yellow (at the stock panel luminance a yellow hue turns olive/khaki, muddy next to Amber/Green) and White (identical to Original LCD once brightness is matched). The compiler has no style limit (face count is one header byte; every `*.fprj` in `project\` becomes a style); no firmware limit for 567 is documented, so the style count is a size decision (see Known limits).

The stock face is a neutral grey LCD. Original LCD is the untouched stock look. Recolored styles:
- **Brightness matching (all colour styles):** every recoloured pixel gets the relative luminance (sRGB, Rec.709) of the stock grey pixel it replaces, in the scheme hue (`match_lum()`): LCD panel and unlit battery segments at the tint's saturation, dial numerals/ticks/rings, hands, headers, alarm row, walker/KM and lit status icons at max(saturation, 0.65). Value is solved first; where even full value is too dark (white hands, bright ticks/digits in red/violet/pink/ice) the saturation is lowered, not below 0.30, so those elements turn pastel and the few targets still out of reach are flagged with `*` in `previews\brightness_table.md` (per element: stock grey, stock L*, every style before -> after). The dark dial plate (L 40-48) stays dark, and the central disc, crosshair and hub (r < 27) keep their stock pixels.
- **Stroke weight:** `light_copy()` now writes straight alpha (solid colour + the glyph alpha). It used to paste through the alpha as a mask, which also multiplied the RGB by alpha, so soft edge pixels were dimmed twice and the coloured labels and all alarm/distance digits (Original too) looked thinner than the stock glyphs.
- **Per-style image copies** (each `.fprj` points to its own files): backgrounds `bg_<style>_RGB.png` (TIME/STATS), lit status sockets, hands, strip battery icon/bar/`%` digits (scheme hue at header grey 153 / unlit 70). LCD digits and weather icons stay shared black/colour art.
- AOD is one shared pack for all styles (EasyFace limit).

## Known limits

- **Size (current build 1.0): 17,786,416 bytes (~17.0 MiB), 8 styles + AOD, zone-sized tap bitmaps, 20-seg scheme-aware strip bar.** Earlier milestones: ~15.5 MiB before AOD/strip rework; v2.4 (15,182,484, 8x8 taps) installed on the band. If install ever fails on size, drop styles in `STYLES` (~2 MB each). Compiler.exe has no image compressor for 567 (`PassCompressor` / raw 32-bit ARGB); each style packs its own copies. Zone-sized tap bitmaps are required for app jumps (8x8 broke them).
- **No °F auto-switch.** Stock hides °C/°F by unit (`hideWhenUnitMismatch`), but only °C is built.
- **No compression** (see above) and **no editable slots or components**.
- **Feels-like removed in v2.6.** `1831 CurrentTemperatureFeel` read 0 on the Mi Band 10 Pro (v2.4/v2.5). It only exists in EasyFace's generic data-source table (`DeviceInfo.db` / `dev567.xml`, same for every model); Xiaomi's editor config for this face (`stock\editor.config.json`) offers only current temperature, weather icon and today's min/max; Mi-Create's per-device lists have no 1831 for Band 9 / Band 10; and the weather message Mi Fitness sends to the band (Gadgetbridge's reverse-engineered `xiaomi.proto`, `WeatherCurrent`) has temperature, humidity, wind, UV, AQI, warnings and pressure, but no feels-like field. Its spot now shows today's high / low (`1832` / `2032`, both in Xiaomi's own source list). Chance of rain (`6031`) showed 0 % on the band and is not in that message either (a bandbbs Band 9 face author reports the rain source as unavailable); it was replaced by AQI (`5031`).
- **One preview only.** Compiler.exe packs only the active style's preview, and the style titles are not stored in the `.face`. Only the face name "Complex LCD+" ends up in the header.
- Taps: steps/hrm/weather/sleep and Workouts (`0100f300`) confirmed on the band; app jumps need zone-sized bitmaps. Codes for `calendar`, `flashlight`, `stopwatch`, `timer`, `alarm` are the compiler's. Alarm time / BT / charging / lock sources are on DeviceInfo for 567; charging/lock lit states lightly tested. AOD and post-sleep decimal still want a band glance.
- Number widgets are byte-identical to the stock records except bytes 6-7 (stock `parameter`=1000). EasyFace writes 0 there for this device, as it does for every Gen2 face it builds.
- Opening the project in the EasyFace GUI needs elevation (UAC), so it was not checked from automation.

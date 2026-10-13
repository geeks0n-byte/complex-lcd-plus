# Complex LCD+

Custom watch face for **Xiaomi Smart Band 10 Pro** (336×480), version **1.0**.

A cleaned-up, rebuilt take on Xiaomi’s stock **Complex LCD** layout: eight color styles, denser LCD weather/stats row, compact battery strip, and working tap shortcuts.

![Compare sheet](compare_sheet.png)

## Download

Grab **`ComplexLCDPlus.face`** from this repo, or from the [latest Release](https://github.com/geeks0n-byte/complex-lcd-plus/releases/latest).

Source, EasyFace project, and build script are in [`src/`](src/).

## Install (minstall)

Install with [minstall](https://github.com/HyperionD/minstall) — direct Bluetooth install for Band 10 Pro, no official app needed.

1. Install the minstall APK from its [Releases](https://github.com/HyperionD/minstall/releases) (Android; there's also a Linux desktop build).
2. Get your band's authkey: in Mi Fitness, open **Profile > About**, tap the app icon at the top repeatedly, confirm the log export, then use minstall's **Auto-detect** (or copy the `encryptKey` value from `Download/wearablelog/*.zip`).
3. Close Mi Fitness, then in minstall enter the band MAC + authkey and tap **Connect & authenticate**.
4. Pick `ComplexLCDPlus.face` and tap install; wait for the band to confirm.

Face id: `29100101` (installs separately from stock Complex LCD).
## What’s included

| | |
|---|---|
| Device | Mi Band 10 Pro / P67, DeviceType 567 |
| Styles | 8 color schemes + AOD |
| Preview | Stock-style sheet first, then every style, tap map last |

### Highlights vs stock Complex LCD

- Bigger, clearer weather / high–low / icon row with humidity, AQI, and UV
- Next-alarm line in the LCD area
- Compact battery icon + segmented bar + %
- Stats: steps, distance (km), heart rate, sleep (decimal hours)
- Tap zones for stopwatch, alarm, steps, HR, sleep, workouts (via distance cell), weather, calendar, flashlight, timer

## Disclaimer

Unofficial fan face. **Not affiliated with Xiaomi.** Layout and many assets are derived from the stock Complex LCD face; redistribute and use at your own risk. Xiaomi names and trademarks belong to their owners.

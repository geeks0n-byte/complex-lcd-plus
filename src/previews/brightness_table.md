# Per-element brightness: CIE L* (perceived lightness from relative luminance)

Stock grey = level measured from the stock pixels (alarm digits / lit icons: the Original's 225, labels: stock header grey 153). Before = previous build (vivid tint scaled by fixed factors); after = this build (scheme hue/saturation at the stock element's luminance). Original LCD = stock values in every row. '*' = target brighter than the scheme hue can reach at saturation >= 0.3; shown value is the closest.

| Element | stock grey | stock L* | Phosphor Green (before -> after) | Amber (before -> after) | Ice Blue (before -> after) | Signal Red (before -> after) | Teal (before -> after) | Violet (before -> after) | Rose Pink (before -> after) |
|---|---|---|---|---|---|---|---|---|---|
| LCD panel | 139 | 57.9 | 64.3 -> 57.9 | 65.0 -> 57.9 | 65.1 -> 57.9 | new -> 57.9 | new -> 57.9 | new -> 57.9 | new -> 57.9 |
| Dial numerals | 170 | 69.6 | 62.3 -> 69.6 | 54.4 -> 69.6 | 50.1 -> 69.6 | new -> 69.6 | new -> 69.6 | new -> 69.6 | new -> 69.6 |
| Dial ticks | 217 | 86.7 | 77.8 -> 86.7 | 68.2 -> 86.7 | 62.9 -> 86.7 | new -> 80.6* | new -> 86.7 | new -> 77.6* | new -> 81.0* |
| Dial rings / rim | 57 | 24.0 | 20.8 -> 24.0 | 17.7 -> 24.0 | 15.9 -> 24.0 | new -> 24.0 | new -> 24.0 | new -> 24.0 | new -> 24.0 |
| Hands (h/m/s) | 255 | 100.0 | 89.9 -> 94.3* | 78.9 -> 90.5* | 72.9 -> 86.9* | new -> 80.6* | new -> 94.9* | new -> 77.6* | new -> 81.0* |
| Headers TIME / STATS | 153 | 63.2 | 51.5 -> 63.2 | 44.8 -> 63.2 | 41.2 -> 63.2 | new -> 63.2 | new -> 63.2 | new -> 63.2 | new -> 63.2 |
| ALARM label, walker, KM | 153 | 63.2 | 51.5 -> 63.2 | 44.8 -> 63.2 | 41.2 -> 63.2 | new -> 63.2 | new -> 63.2 | new -> 63.2 | new -> 63.2 |
| Alarm / distance digits | 225 | 89.5 | 73.6 -> 89.5 | 64.4 -> 89.5 | 59.4 -> 86.9* | new -> 80.6* | new -> 89.5 | new -> 77.6* | new -> 81.0* |
| Status icons lit | 225 | 89.5 | 89.9 -> 89.5 | 78.9 -> 89.5 | 72.9 -> 86.9* | new -> 80.6* | new -> 89.5 | new -> 77.6* | new -> 81.0* |
| Strip battery % + lit segments | 153 | 63.2 | 51.5 -> 63.2 | 44.8 -> 63.2 | 41.2 -> 63.2 | new -> 63.2 | new -> 63.2 | new -> 63.2 | new -> 63.2 |
| Strip battery unlit segments | 70 | 29.7 | 23.6 -> 29.7 | 20.1 -> 29.7 | 18.2 -> 29.7 | new -> 29.7 | new -> 29.7 | new -> 29.7 | new -> 29.7 |

Unchanged in every style (identical pixels, so identical luminance): LCD digits (black), weather icons (stock colour art), status icons off (grey 78 rune), centre hub.

Largest |L* difference| before (Green/Amber/Ice): 30.1; after (rows without '*'): 0.00.

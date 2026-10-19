# Complex LCD+ build

Author: gix0n

From the `src` directory:

```
python tools/build.py
```

Set `FACE_COMPILER_HOME` to the directory that contains `Compiler.exe`, or set `FACE_COMPILER` to the executable.

`tools/build.py` reads `stock/resources/` from disk (local unpack, not published), regenerates `project/` and `previews/`, and writes `output/ComplexLCDPlus.face`.

Python 3 with Pillow is required.

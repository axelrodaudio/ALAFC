# Windows Binaries - How They're Built

The `.exe` files in a release are built with **PyInstaller**, directly from
the Python source in this repo - nothing extra is added. They are built
**without numba/llvmlite** (excluded on purpose): the codec's numba path is
purely an optional speed-up, self-checked against the pure-numpy reference
at runtime, so removing it from the frozen build keeps things simple and
guarantees identical output to the reference implementation - just without
the 30-60x JIT speed-up. If you need that speed, run from source with
`pip install numba`.

Build it yourself (Windows, from the repo root):

    py -m pip install pyinstaller
    py -m PyInstaller --onefile --console --exclude-module numba --exclude-module llvmlite alafc.py

See `ALAFC_Build_EXE.bat` for the exact flags used for all five tools
(`alafc`, `alafc_convert`, `alafc_player`, `alafc_gui_player`,
`alafc_lossless_tester`).

**Antivirus false positives:** PyInstaller one-file executables are
frequently flagged by Windows Defender / other AVs - this is a common,
well-documented false positive for small, unsigned PyInstaller tools, not
specific to ALAFC. If in doubt, don't trust the binary - build it yourself
from source with the command above, or read alafc.py directly; it's plain
Python with no obfuscation.

ffmpeg is not bundled (used only by alafc_convert for non-WAV input) - keep
it on PATH separately if you need FLAC/MP3/etc. conversion.

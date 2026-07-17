# ALAFC — Axelrod Lossless Audio Format Codec

A lossless audio codec that compresses tighter than FLAC, with built-in
integrity verification, damage recovery, and per-segment adaptive stereo.

Created by **Axelrod**, 2026. MIT License.

## Measured results (vs FLAC compression level 8)

| Content              | FLAC -8     | ALAFC       | Difference |
|----------------------|-------------|-------------|------------|
| 16-bit / 44.1 kHz    | 2,766,634 B | 2,522,775 B | **-8.8 %** |
| 24-bit / 192 kHz     | 5,969,756 B | 5,320,494 B | **-10.9 %**|
| 32-bit / 384 kHz     | n/a*        | 36.0 % of raw PCM | — |

*ffmpeg's FLAC encoder does not support 32-bit; ALAFC does.
Measured on synthetic test tracks. A community benchmark on a real
24-bit/96kHz file (Hydrogenaudio forum, July 2026) put ALAFC within 0.34
percentage points of TAK's best preset (p4m), and ahead of FLAC -8,
Monkey's Audio -c5000, and WavPack -h - real music tends to land closer to
that than the synthetic numbers above. Every decode is verified against an
embedded MD5 of the original PCM.

## Features

- Bit-exact lossless: MD5 of the source PCM embedded in every file,
  checked on every decode
- 16 / 24 / 32-bit integer PCM, mono/stereo, any sample rate
  (tested to 384 kHz, format allows more)
- **Per-segment adaptive stereo** (v5): each ~6s segment picks whichever
  of 4 FLAC-style modes costs least - L/R, mid/side, L/side, or side/R -
  instead of one whole-file average. L/side and side/R (new in v5) win
  when the two channels have noticeably different loudness; v4's simpler
  L/R-vs-mid/side choice is still there as a subset
- **Self-healing files**: audio is stored in ~6 s segments, each with a
  sync marker and CRC32. Corruption mutes only the damaged segment and
  reports its exact position; everything else stays bit-exact
- Compression: per-block LPC (order <= 32) + a cascade of four sign-sign
  NLMS adaptive filters + partition-adaptive Rice coding
- Optional `numba` acceleration (30-60x), always self-checked against
  the reference implementation at runtime

## Tools

- **NeoAmp** — bit-perfect ASIO/WASAPI music player with a Winamp-style
  UI, live spectrum display, and full codec info readout
- **TrueScope** — lossless-authenticity checker + player: shows the real
  measured frequency response of a track and flags a lossy brick-wall
  cutoff if one is found (same technique as Spek/TAK/Adobe Audition's
  frequency analysis), with a pin-to-compare view for two files at once
- **alafc_lossless_tester** — command-line version of the same
  authenticity check, saves a spectrogram PNG per file
- **alafc_convert** — any format (via ffmpeg) to `.alafc` and back,
  batch mode with `--folder`
- **alafc_player** — command-line bit-perfect ASIO/WASAPI player
- **alafc_player_android.html** — single-file browser player, no
  installation, JavaScript decoder verified bit-identical to the
  reference (16-bit files)

Windows standalone `.exe` builds of every tool (no Python required) can be
built with `ALAFC_Build_EXE.bat`, or downloaded from the Releases page.

## Quick start (Windows, from source)

    pip install numpy sounddevice numba scipy matplotlib
    python alafc_convert.py song.flac          # -> song.alafc
    python neoamp.py song.alafc                # play it
    python truescope.py                        # authenticity + spectrum

## Quick start (Windows, standalone .exe)

Download the binaries from the Releases page (or build them yourself with
`ALAFC_Build_EXE.bat`), then just double-click / drag files onto them -
no Python needed. See `PACKAGING_NOTES.md` for how they're built and an
antivirus false-positive note.

## Quick start (Android / anywhere)

Open `alafc_player_android.html` in a browser, pick a 16-bit `.alafc`
file, play.

## Files

    alafc.py                   codec core + CLI (encode/decode/verify/info)
    alafc_gui_logic.py         shared info/spectrum helpers for NeoAmp & TrueScope
    alafc_convert.py           any format -> .alafc and back (batch mode: --folder)
    alafc_player.py            command-line bit-perfect player (ASIO / WASAPI exclusive)
    neoamp.py                  GUI music player (Winamp-style, live spectrum)
    truescope.py                lossless authenticity scope + player
    truescope_loader.py        shared single-decode loader for TrueScope
    alafc_lossless_tester.py   command-line authenticity checker
    alafc_player_android.html  browser player, no installation
    ALAFC_Build_EXE.bat        builds standalone .exe for every tool (PyInstaller)
    ALAFC_Converter.bat        drag & drop converter (needs Python)
    ALAFC_Converter_EXE.bat    drag & drop converter (standalone .exe, no Python)
    ALAFC_LosslessTester.bat   drag & drop authenticity checker
    ALAFC_SPEC.md              format specification
    PACKAGING_NOTES.md         how the .exe binaries are built
    CHANGELOG.md
    demos/                     sample files, incl. a deliberately damaged one

See `ALAFC_SPEC.md` for the bitstream format.

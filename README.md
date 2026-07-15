# ALAFC — Axelrod Lossless Audio Format Codec

A lossless audio codec that compresses tighter than FLAC, with built-in
integrity verification and damage recovery.

Created by **Axelrod**, 2026. MIT License.

## Measured results (vs FLAC compression level 8)

| Content              | FLAC -8     | ALAFC 1.0   | Difference |
|----------------------|-------------|-------------|------------|
| 16-bit / 44.1 kHz    | 2,766,634 B | 2,522,767 B | **-8.8 %** |
| 24-bit / 192 kHz     | 5,969,756 B | 5,320,490 B | **-10.9 %**|
| 32-bit / 384 kHz     | n/a*        | 36.0 % of raw PCM | — |

*ffmpeg's FLAC encoder does not support 32-bit; ALAFC does.
Measured on synthetic test tracks; results on real recordings vary
(typically 3-10 % smaller than FLAC -8). Every decode is verified
against an embedded MD5 of the original PCM.

## Features

- Bit-exact lossless: MD5 of the source PCM embedded in every file,
  checked on every decode
- 16 / 24 / 32-bit integer PCM, mono/stereo, any sample rate
  (tested to 384 kHz, format allows more)
- **Self-healing files**: audio is stored in ~6 s segments, each with a
  sync marker and CRC32. Corruption mutes only the damaged segment and
  reports its exact position; everything else stays bit-exact
- Compression: per-block LPC (order <= 32) + a cascade of four sign-sign
  NLMS adaptive filters + partition-adaptive Rice coding, with exact
  L/R vs mid/side selection
- Windows: converter (WAV/FLAC/MP3 via ffmpeg) and a bit-perfect
  ASIO / WASAPI-exclusive player
- Android / any OS: single-file browser player (JavaScript decoder,
  verified bit-identical to the reference), 16-bit files
- Optional `numba` acceleration (30-60x), always self-checked against
  the reference implementation at runtime

## Quick start (Windows)

    pip install numpy sounddevice numba
    python alafc_convert.py song.flac          # -> song.alafc
    python alafc_player.py --list              # find your ASIO device
    python alafc_player.py song.alafc --device N

## Quick start (Android / anywhere)

Open `alafc_player_android.html` in a browser, pick an `.alafc` file, play.

## Files

    alafc.py                   codec core + CLI (encode/decode/verify/info)
    alafc_convert.py           any format -> .alafc and back (batch mode: --folder)
    alafc_player.py            Windows bit-perfect player (ASIO / WASAPI exclusive)
    alafc_player_android.html  browser player, no installation
    ALAFC_SPEC.md              format specification
    demos/                     sample files, incl. a deliberately damaged one

See `ALAFC_SPEC.md` for the bitstream format.

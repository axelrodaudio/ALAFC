# Changelog

## 1.3.0 (2026-07-21)

Same v5 bitstream as 1.2.0 — files are fully interchangeable in both
directions (verified: old 1.2.0 code decodes new files and vice versa,
MD5 OK). The engine is rebuilt for speed and memory:

- FFT autocorrelation replaces the O(n^2) lag scan in LPC analysis
- LPC order search pruned by Levinson error; Rice k searched in a
  window around the mean instead of scanning all 33 values
- Segments are encoded/decoded on a thread pool (numba kernels now
  run with nogil=True)
- Encoder streams the WAV in ~6 s segments: constant RAM regardless
  of file length (1.2.0 held the whole file in memory as int64)
- Decoder fills tight int16/int32 buffers; decode-to-WAV streams
  segment by segment
- New speed profiles: --fast (pure LPC+Rice), --normal (light NLMS),
  --max (full cascade, default — same compression as 1.2.0,
  byte-identical output size on the demo corpus)

Measured on the reference machine (numba, 8 threads):
44.1 kHz / 16-bit: encode 21.6x realtime at max (1.2.0: 10.8x),
26.4x at fast; decode 516x realtime for fast files, 47x for max.
192 kHz / 24-bit: encode 6.3x at max (1.2.0: 5.5x), 9.1x at fast;
decode 67.8x for fast files. Fast profile costs ~2-3 pp of ratio
vs max. New: test_alafc_130.py (correctness suite) and
bench_alafc_130.py (1.2.0 vs 1.3.0 benchmark + memory probe).



## 1.2.0 - 2026-07-17

- Codec format v5: per-segment stereo mode extended from 2 choices to
  FLAC's 4-way scheme (L/R, mid/side, L/side, side/R), picked by exact
  cost estimate per ~6s segment. L/side and side/R skip mid/side's
  rounding entirely (one channel kept raw) and tend to win when the two
  channels have noticeably different loudness - a case plain mid/side
  doesn't handle as well. Measured +0.6% over the v4 2-way scheme on an
  asymmetric-loudness test track, on top of v4's own gains; no change on
  content where L/R or mid/side already do the job. v1-v4 files decode
  unchanged - v4's mode values 0 and 1 keep the exact same meaning in v5.
- Prompted directly by a follow-up Hydrogenaudio comment (Porcus)
  explaining how the FLAC reference encoder makes this exact choice.

## 1.1.0 - 2026-07-17

- Codec format v4: per-segment adaptive stereo mode. v1-v3 chose L/R vs
  mid/side once for the whole file; v4 picks whichever costs less
  independently for each ~6s segment, so a track whose stereo width
  changes over time (e.g. a mono intro vs a hard-panned chorus) is no
  longer stuck with one whole-file average. On a track built to alternate
  those two cases, this beats the best fixed whole-file choice by ~3.9%;
  on already-uniform content the difference is a few bytes of table
  overhead. v1/v2/v3 files still decode unchanged.
- Prompted by community feedback (Hydrogenaudio) pointing out ALAFC only
  did a single whole-file mid/side decision, unlike more granular
  per-block approaches in other codecs.
- Fixed a stray real name in a source file comment that should have said
  "Axelrod" (privacy fix, no functional change).

## 1.0.0 - 2026-07-15

Initial public release.

- Lossless codec: per-block LPC + 4-stage sign-sign NLMS cascade
  + partition-adaptive Rice coding, exact L/R vs mid/side selection
- -8.8% vs FLAC -8 at 16-bit/44.1kHz, -10.9% at 24-bit/192kHz
  (synthetic test material; real recordings vary)
- 16/24/32-bit integer PCM, sample rates tested to 384 kHz
- MD5 of source PCM embedded in every file, verified on every decode
- Self-healing files: ~6s segments with sync marker + CRC32;
  corruption mutes only the damaged segment and reports its position
- Windows: bit-perfect ASIO / WASAPI-exclusive player, converter
  (WAV/FLAC/MP3 via ffmpeg), drag & drop .bat wrapper
- Android / any OS: single-file browser player with a JavaScript
  decoder verified bit-identical to the reference implementation
- Optional numba acceleration (30-60x), runtime self-checked

# ALAFC Format Specification (v4, codec 1.1.0)

All multi-byte header fields are big-endian. The bitstream is MSB-first.
All arithmetic is integer-exact; ">> s" means arithmetic shift right
(floor division by 2^s), including for negative values.

## File layout

    [header] [stereo mode table, stereo files only, v4+] [channel 0 segments...] [channel 1 segments...]

Channels are stored sequentially: for stereo, the full stream of
channel 0, then channel 1.

## Header

    magic       4 B   "ALAF"
    version     1 B   4 (decoders must also accept 1, 2, 3)
    stereo_flag 1 B   0 = L/R, 1 = mid/side (whole file, v1-v3 meaning),
                      2 = per-segment table follows (v4, stereo files)
    channels    1 B   1 or 2
    bits        1 B   16, 24 or 32
    samplerate  4 B
    frames      8 B   samples per channel
    block       2 B   block size in samples (4096..32768, rate-dependent)
    partition   2 B   Rice partition size (128)
    n_stages    1 B   NLMS cascade depth
    per stage:  2 B order, 1 B shift        (1.0 default: 512/13, 128/12, 32/10, 8/8)
    md5         16 B  MD5 of the raw source PCM bytes (v2+)
    seg_blocks  2 B   blocks per segment (64) (v3+)
    n_segs      2 B   segment count (v4, stereo only, i.e. stereo_flag==2)
    seg_modes   1 B * n_segs   0=L/R, 1=mid/side for that segment (v4, stereo only)

## Stereo decorrelation

mid = (L+R)>>1, side = L-R. Inverse: L = mid + ((side + (side&1)) >> 1),
R = L - side.

- v1-v3: one choice for the whole file, by exact LPC-residual cost probe
  on a middle slice.
- v4: one choice per segment (see Segments below), by the same exact cost
  probe applied to that segment's samples only. This lets the encoder
  track content whose stereo width changes over time - e.g. a mono intro
  versus a wide, hard-panned chorus - rather than committing to a single
  whole-file average. Segment i's channel-0 stream holds mid[i] if
  seg_modes[i]==1 else L[i]; channel-1 holds side[i] or R[i] the same way.
  A decoder combines them back to L/R per segment using seg_modes[i].

## Segments (v3+)

Each channel is split into segments of `block * seg_blocks` samples.
Predictor and filter state is RESET at every segment start, so each
segment decodes independently. Per segment, byte-aligned:

    sync     2 B   0xA1 0xAF
    length   4 B   payload bytes
    crc32    4 B   CRC32 (zlib polynomial) of the payload
    payload  ...

A decoder that finds a bad CRC or lost sync outputs silence for that
segment, reports its time position, and resynchronises at the next
sync marker (the length field allows direct skipping, which also
enables seeking). The seg_modes table itself is not individually
CRC-protected (it is small and read once, ahead of the segment stream).

## Segment payload

For each block in the segment:

    order   1 B     LPC order (1..32)
    shift   1 B     coefficient shift (0..15)
    coefs   2 B * order   signed 15-bit quantized LPC coefficients
    rice    residual bitstream for the block (below)

## Pipeline (encoder view)

1. Stereo decorrelation - see above (whole-file choice in v1-v3,
   per-segment in v4).
2. LPC per block: pred[n] = (sum(c_j * x[n-1-j]) + 2^(shift-1)) >> shift,
   history continuous within a segment, zeros at segment start.
   r1 = x - pred.
3. NLMS cascade over r1 (per segment, state starts at zero). Per stage
   (order M, shift s), per sample: p = (w . h + 2^(s-1)) >> s where h is
   the last M stage inputs; out = in - p; then w += sign(out) * sign(h)
   elementwise. Stages applied in header order.
4. Rice coding of the final residual, zigzag mapped
   (u = 2v if v>=0 else -2v-1). Per partition of `partition` samples:
   6-bit parameter k, then per value: quotient u>>k in unary
   (capped at 40 ones = escape, then the full u in 40 raw bits),
   a 0 terminator, and k low bits.
   v1/v2 files use a 5-bit k and 32-bit escape.

## Versions

    v1  no MD5
    v2  + embedded MD5
    v3  + segments (sync/length/CRC32), 32-bit PCM, 6-bit k, 40-bit escape
    v4  + per-segment adaptive stereo mode (was: one whole-file choice)

MIT License, (c) 2026 Axelrod.

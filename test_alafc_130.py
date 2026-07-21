#!/usr/bin/env python3
# ALAFC 1.3.0 test suite. Run next to alafc.py:  python test_alafc_130.py
# Passes on the numpy reference path and (much faster) with numba installed.
import os, sys, hashlib, filecmp
import numpy as np
import alafc

TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_t130')
os.makedirs(TMP, exist_ok=True)
alafc.SEG_BLOCKS = 2          # short segments so tests exercise multi-segment
                              # paths quickly (segb is stored in the header,
                              # so files stay self-describing)

FAILED = []

def check(name, cond, extra=''):
    tag = 'PASS' if cond else 'FAIL'
    print(f'[{tag}] {name}{(" - " + extra) if extra else ""}')
    if not cond:
        FAILED.append(name)

def rng(seed):
    return np.random.default_rng(seed)

def music(n, ch, bits, seed=1):
    r = rng(seed)
    t = np.arange(n)
    amp = (1 << (bits - 1)) * 0.55
    base = (np.sin(t * 0.031) + 0.6 * np.sin(t * 0.187 + 1.0)
            + 0.25 * np.sin(t * 0.771))
    out = np.empty(n * ch, dtype=np.int64)
    for c in range(ch):
        sig = base * (1.0 if c == 0 else 0.83) + 0.02 * r.standard_normal(n)
        out[c::ch] = np.clip(sig * amp / 1.9, -(1 << (bits - 1)),
                             (1 << (bits - 1)) - 1).astype(np.int64)
    return out

def noise_fs(n, ch, bits, seed=2):
    r = rng(seed)
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return r.integers(lo, hi + 1, size=n * ch).astype(np.int64)

def silence(n, ch, bits):
    return np.zeros(n * ch, dtype=np.int64)

def roundtrip(name, pcm, ch, sr, bits, profile, threads=None):
    wav_in = os.path.join(TMP, f'{name}.wav')
    afc = os.path.join(TMP, f'{name}_{profile}.alafc')
    wav_out = os.path.join(TMP, f'{name}_{profile}_out.wav')
    raw_src = alafc.wav_write(wav_in, pcm, ch, sr, bits)
    alafc.encode(wav_in, afc, verbose=False, profile=profile, threads=threads)
    # in-memory decode
    out, ch2, sr2, bits2, ok, dmg = alafc.decode_to_memory(afc, verbose=False)
    m_ok = (ok is True and not dmg and ch2 == ch and sr2 == sr and bits2 == bits
            and np.array_equal(out.astype(np.int64), pcm))
    # streaming decode to WAV
    alafc.decode(afc, wav_out, verbose=False)
    raw_out = open(wav_out, 'rb').read()
    raw_in = open(wav_in, 'rb').read()
    check(f'{name} [{profile}] roundtrip', m_ok and raw_out == raw_in)
    return afc

def main():
    print(f'engine: {alafc._engine_note}')

    # 1. music, stereo 16-bit, several segments, all profiles
    p = music(20000, 2, 16)
    for prof in ('fast', 'normal', 'max'):
        roundtrip('music16s', p, 2, 8000, 16, prof)

    # 2. music, mono 24-bit, odd length
    p = music(12345, 1, 24, seed=3)
    for prof in ('fast', 'max'):
        roundtrip('music24m', p, 1, 8000, 24, prof)

    # 3. full-scale 32-bit noise (Rice escape-path stress)
    p = noise_fs(9000, 2, 32)
    for prof in ('fast', 'max'):
        roundtrip('noise32s', p, 2, 8000, 32, prof)

    # 4. silence
    p = silence(10000, 2, 16)
    for prof in ('fast', 'max'):
        roundtrip('silence16s', p, 2, 8000, 16, prof)

    # 5. tiny file (shorter than one block)
    p = music(100, 1, 16, seed=4)
    for prof in ('fast', 'normal', 'max'):
        roundtrip('tiny16m', p, 1, 8000, 16, prof)

    # 6. determinism: threads=1 and threads=4 must give identical bytes
    p = music(24000, 2, 16, seed=5)
    wav_in = os.path.join(TMP, 'det.wav')
    alafc.wav_write(wav_in, p, 2, 8000, 16)
    for prof in ('fast', 'max'):
        f1 = os.path.join(TMP, f'det_{prof}_t1.alafc')
        f4 = os.path.join(TMP, f'det_{prof}_t4.alafc')
        alafc.encode(wav_in, f1, verbose=False, profile=prof, threads=1)
        alafc.encode(wav_in, f4, verbose=False, profile=prof, threads=4)
        check(f'determinism [{prof}] t1==t4', filecmp.cmp(f1, f4, shallow=False))

    # 7. damage recovery: corrupt one mid-file segment, rest must survive
    p = music(30000, 1, 16, seed=6)          # 4 segments of 8192 (last 5424)
    wav_in = os.path.join(TMP, 'dmg.wav')
    afc = os.path.join(TMP, 'dmg.alafc')
    alafc.wav_write(wav_in, p, 1, 8000, 16)
    alafc.encode(wav_in, afc, verbose=False, profile='fast')
    data = bytearray(open(afc, 'rb').read())
    sync = bytes([alafc.SYNC0, alafc.SYNC1])
    i1 = data.find(sync)                     # segment 0
    i2 = data.find(sync, i1 + 2)             # segment 1
    data[i2 + 30] ^= 0xFF                    # flip a byte inside seg 1 payload
    open(afc, 'wb').write(data)
    out, ch2, sr2, bits2, ok, dmg = alafc.decode_to_memory(afc, verbose=False)
    seg = 8192
    intact = (np.array_equal(out[:seg].astype(np.int64), p[:seg]) and
              np.array_equal(out[2*seg:].astype(np.int64), p[2*seg:]))
    muted = not np.any(out[seg:2*seg])
    check('damage recovery', bool(dmg) and ok is None and intact and muted,
          f'damaged={dmg}')

    # 8. info() smoke test on a 1.3.0-written file
    try:
        alafc.info(os.path.join(TMP, 'music16s_fast.alafc'))
        check('info()', True)
    except Exception as e:
        check('info()', False, str(e))

    # 9. wav helpers: 24-bit negative-value packing roundtrip
    p = np.array([-1, 0, 1, -8388608, 8388607, -12345], dtype=np.int64)
    raw = alafc.pcm_to_bytes(p, 24)
    back = alafc._raw_to_i64(raw, 3)
    check('24-bit pack/unpack', np.array_equal(back, p))

    print()
    if FAILED:
        print(f'{len(FAILED)} FAILED: {FAILED}')
        sys.exit(1)
    print('ALL TESTS PASSED')

if __name__ == '__main__':
    main()

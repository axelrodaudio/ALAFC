#!/usr/bin/env python3
# ALAFC v5 bitstream - Axelrod Lossless Audio Format Codec
# Version 1.3.0 - Copyright (c) 2026 Axelrod. MIT License (see LICENSE.txt)
#
# 1.3.0: same v5 bitstream as 1.2.0 (old files decode, new files play in old
#        tools), but the engine is rebuilt for speed and memory:
#        - FFT autocorrelation (was O(n^2) np.correlate - the main cost at
#          high sample rates)
#        - LPC order search pruned by Levinson error estimate (exact cost is
#          only computed for the best few candidates)
#        - Rice parameter search windowed around the mean instead of scanning
#          all 33 k values
#        - segments are encoded/decoded on a thread pool (numba kernels run
#          with nogil=True, so threads scale across cores)
#        - encode streams the WAV in segment-sized chunks: constant RAM even
#          for hours-long 24/192 files (was: whole file as int64 in memory)
#        - decode writes straight into a tight int16/int32 buffer, and
#          decode-to-WAV streams segment by segment (constant RAM)
#        - speed profiles: --fast (pure LPC, FLAC-class speed),
#          --normal (light NLMS), --max (full 4-stage cascade, default,
#          same compression as 1.2.0)
# v5 (1.2.0): per-segment stereo extended to FLAC's 4-way choice
#        (L/R, mid/side, L/side, side/R).
# v4: per-segment adaptive stereo (L/R vs mid/side per ~6s segment).
# v3: 16/24/32-bit PCM, any sample rate, self-healing segments
#        (sync marker + length + CRC32, damage mutes only that segment).
# v2: embedded MD5 of raw PCM, verified on decode.
#
# Pipeline: PCM -> stereo decorrelation -> per-block LPC -> sign-sign NLMS
# cascade -> adaptive Rice coding. Integer-exact.
# Optional numba = 30-60x faster; every fast path self-checks vs the reference.

import sys, os, wave, time, hashlib, zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

try:
    from numba import njit
    HAVE_NUMBA = True
except Exception:
    HAVE_NUMBA = False
    def njit(*a, **k):
        def wrap(f): return f
        return wrap

MAGIC = b'ALAF'
VER = 5
PART = 128
PREC = 15
MAXORD = 32
ORDERS = [1, 2, 4, 8, 12, 16, 20, 24, 28, 32]
ESC_Q = 40
ESC_RAW = 40            # v3 escape payload bits (v1/v2 used 32)
SEG_BLOCKS = 64         # blocks per self-contained segment (~6 s)
SYNC0, SYNC1 = 0xA1, 0xAF
LMS_STAGES = [(512, 13), (128, 12), (32, 10), (8, 8)]

# 1.3.0 speed profiles: NLMS cascade per profile + how many LPC order
# candidates get an exact cost evaluation. All three write plain v5 files.
PROFILES = {
    'fast':   [],                                        # pure LPC+Rice
    'normal': [(128, 12), (32, 10)],                     # light cascade
    'max':    [(512, 13), (128, 12), (32, 10), (8, 8)],  # full (= 1.2.0)
}
PROFILE_CAND = {'fast': 2, 'normal': 3, 'max': 4}
PROBE_CAND = 2

_engine_note = 'numba' if HAVE_NUMBA else 'numpy (pip install numba => 30-60x faster)'


def pick_block(sr):
    if sr <= 48000: return 4096
    if sr <= 96000: return 8192
    if sr <= 192000: return 16384
    return 32768


def _nthreads(threads):
    if threads and threads > 0: return threads
    if not HAVE_NUMBA:
        return 1   # numpy path holds the GIL - threads would only add overhead
    return max(1, min(8, os.cpu_count() or 1))

# ------------------------------------------------------------------ bit I/O

class BitWriter:
    def __init__(self):
        self.buf = bytearray(); self.acc = 0; self.n = 0
    def write(self, val, nbits):
        self.acc = (self.acc << nbits) | (int(val) & ((1 << nbits) - 1))
        self.n += nbits
        while self.n >= 8:
            self.n -= 8
            self.buf.append((self.acc >> self.n) & 0xFF)
            self.acc &= (1 << self.n) - 1
    def write_bytes(self, b):
        assert self.n == 0, 'write_bytes needs byte alignment'
        self.buf.extend(b)
    def mark(self):
        assert self.n == 0, 'mark needs byte alignment'
        return len(self.buf)
    def align(self):
        if self.n: self.write(0, 8 - self.n)
    def bytes(self):
        self.align(); return bytes(self.buf)


class BitReader:
    def __init__(self, data, pos=0):
        self.d = data; self.pos = pos; self.acc = 0; self.n = 0
    def read(self, nbits):
        while self.n < nbits:
            self.acc = (self.acc << 8) | self.d[self.pos]
            self.pos += 1; self.n += 8
        self.n -= nbits
        v = (self.acc >> self.n) & ((1 << nbits) - 1)
        self.acc &= (1 << self.n) - 1
        return v
    def read_unary_cap(self, cap):
        q = 0
        while q < cap:
            if self.read(1) == 0: return q
            q += 1
        return cap
    def align(self):
        self.n = 0; self.acc = 0
    def state(self):
        return (self.pos, self.n, self.acc)
    def restore(self, st):
        self.pos, self.n, self.acc = st

# ------------------------------------------------------------------ WAV I/O

def _raw_to_i64(raw, sw):
    if sw == 2:
        return np.frombuffer(raw, dtype='<i2').astype(np.int64)
    if sw == 4:
        return np.frombuffer(raw, dtype='<i4').astype(np.int64)
    b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int64)
    v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
    return (v ^ 0x800000) - 0x800000


class _WavIn:
    """Streaming WAV reader - reads frames in chunks, never the whole file."""
    def __init__(self, path):
        self.w = wave.open(path, 'rb')
        self.ch = self.w.getnchannels()
        self.sw = self.w.getsampwidth()
        self.sr = self.w.getframerate()
        self.nf = self.w.getnframes()
        if self.ch not in (1, 2): raise ValueError('only mono/stereo supported')
        if self.sw not in (2, 3, 4): raise ValueError('only 16/24/32-bit integer PCM supported')
        self.bits = self.sw * 8
    def read(self, frames):
        raw = self.w.readframes(frames)
        return raw, _raw_to_i64(raw, self.sw)
    def rewind(self):
        self.w.rewind()
    def close(self):
        self.w.close()


def wav_read(path):
    win = _WavIn(path)
    raw, pcm = win.read(win.nf)
    ch, sr, nf, bits = win.ch, win.sr, win.nf, win.bits
    win.close()
    return pcm, ch, sr, nf, bits, raw


def pcm_to_bytes(pcm, bits):
    if bits == 16:
        return pcm.astype('<i2').tobytes()
    if bits == 32:
        return pcm.astype('<i4').tobytes()
    v = (pcm.astype(np.int64) & 0xFFFFFF).astype(np.uint32)
    b = np.empty((len(pcm), 3), dtype=np.uint8)
    b[:, 0] = v & 0xFF; b[:, 1] = (v >> 8) & 0xFF; b[:, 2] = (v >> 16) & 0xFF
    return b.tobytes()


def wav_write(path, pcm, ch, sr, bits):
    raw = pcm_to_bytes(pcm, bits)
    with wave.open(path, 'wb') as w:
        w.setnchannels(ch); w.setsampwidth(bits // 8); w.setframerate(sr)
        w.writeframes(raw)
    return raw

# ------------------------------------------------------------------ LPC

def levinson(ac, maxorder):
    err = ac[0]
    lpc = np.zeros(maxorder)
    out = {}
    for i in range(maxorder):
        if err <= 0: break
        r = -ac[i + 1]
        r -= np.dot(lpc[:i], ac[i:0:-1])
        r /= err
        lpc[i] = r
        lpc[:i] = lpc[:i] + r * lpc[:i][::-1]
        err *= (1 - r * r)
        out[i + 1] = (-lpc[:i + 1].copy(), err)
    return out


def quantize_lpc(coefs, prec=PREC):
    cmax = np.abs(coefs).max()
    if cmax <= 0: return None
    shift = prec - 1 - (int(np.floor(np.log2(cmax))) + 1)
    shift = max(0, min(15, shift))
    qmax = (1 << (prec - 1)) - 1; qmin = -(1 << (prec - 1))
    q = np.empty(len(coefs), dtype=np.int64); e = 0.0
    for i, c in enumerate(coefs):
        v = c * (1 << shift) + e
        qi = int(round(v)); qi = max(qmin, min(qmax, qi))
        e = v - qi
        q[i] = qi
    return q, shift


def lpc_residual(xpad, s, bn, qcoef, shift):
    o = len(qcoef)
    seg = xpad[s + MAXORD - o : s + MAXORD + bn]
    W = sliding_window_view(seg, o)[:bn]
    acc = W @ qcoef[::-1]
    half = 1 << (shift - 1) if shift > 0 else 0
    pred = (acc + half) >> shift
    return xpad[s + MAXORD : s + MAXORD + bn] - pred


def _autocorr_fft(w, lags):
    """Autocorrelation lags 0..lags via FFT - identical values to
    np.correlate(w, w, 'full')[n-1 : n+lags] up to float rounding,
    but O(n log n) instead of O(n^2)."""
    n = len(w)
    m = 1
    while m < 2 * n:
        m <<= 1
    F = np.fft.rfft(w, m)
    return np.fft.irfft(F * np.conj(F), m)[:lags + 1]


def rice_cost_est(res):
    """Exact best Rice cost, searching only a window around the mean-derived
    k (the optimum is always within +-1 of it in practice)."""
    u = np.where(res >= 0, 2 * res, -2 * res - 1).astype(np.uint64)
    n = len(u)
    if n == 0: return 0
    mean = int(u.sum()) // n
    k0 = mean.bit_length() - 1 if mean > 0 else 0
    best = None
    for k in range(max(0, k0 - 1), min(32, k0 + 2) + 1):
        c = (k + 1) * n + int((u >> np.uint64(k)).sum())
        if best is None or c < best: best = c
    return best


def analyze_block(xpad, s, bn, candidates=4):
    blk = xpad[s + MAXORD : s + MAXORD + bn].astype(np.float64)
    if bn < 64:
        q = np.array([1], dtype=np.int64)
        return q, 0, lpc_residual(xpad, s, bn, q, 0)
    w = blk * np.hanning(bn)
    ac = _autocorr_fft(w, MAXORD)
    ac[0] += 1e-9 * ac[0] + 1e-10
    models = levinson(ac, MAXORD)
    # rank orders by Levinson prediction error, exact-evaluate the best few
    ranked = []
    for o in ORDERS:
        if o not in models: continue
        err = models[o][1]
        est = bn * 0.5 * np.log2(max(err, 1e-12)) + o * 16
        ranked.append((est, o))
    ranked.sort()
    best = None
    for _, o in ranked[:max(1, candidates)]:
        qz = quantize_lpc(models[o][0])
        if qz is None: continue
        q, sh = qz
        r = lpc_residual(xpad, s, bn, q, sh)
        cost = rice_cost_est(r) + 16 + o * 16
        if best is None or cost < best[0]:
            best = (cost, q, sh, r)
    if best is None:
        q = np.array([1], dtype=np.int64)
        return q, 0, lpc_residual(xpad, s, bn, q, 0)
    return best[1], best[2], best[3]

# ------------------------------------------------------------------ NLMS cascade

def _lms_forward_np(inp, order, shift):
    n = len(inp)
    xp = np.zeros(n + order, dtype=np.int64); xp[order:] = inp
    sp = np.sign(xp).astype(np.int8)
    out = np.empty(n, dtype=np.int64)
    w = np.zeros(order, dtype=np.int64)
    half = 1 << (shift - 1)
    for i in range(n):
        p = (int(w @ xp[i : i + order]) + half) >> shift
        o = int(xp[i + order]) - p
        out[i] = o
        if o > 0: np.add(w, sp[i : i + order], out=w)
        elif o < 0: np.subtract(w, sp[i : i + order], out=w)
    return out


@njit(cache=True, nogil=True)
def _lms_forward_nb(inp, order, shift):
    n = inp.shape[0]
    out = np.empty(n, np.int64)
    buf = np.zeros(2 * order, np.int64)
    sgn = np.zeros(2 * order, np.int64)
    w = np.zeros(order, np.int64)
    half = np.int64(1) << np.int64(shift - 1)
    for i in range(n):
        wp = i % order
        acc = np.int64(0)
        for j in range(order):
            acc += w[j] * buf[wp + j]
        p = (acc + half) >> shift
        x = inp[i]
        o = x - p
        out[i] = o
        if o > 0:
            for j in range(order): w[j] += sgn[wp + j]
        elif o < 0:
            for j in range(order): w[j] -= sgn[wp + j]
        s = np.int64(1) if x > 0 else (np.int64(-1) if x < 0 else np.int64(0))
        buf[wp] = x; buf[wp + order] = x
        sgn[wp] = s; sgn[wp + order] = s
    return out


_lms_nb_ok = None

def lms_forward(inp, order, shift):
    global _lms_nb_ok
    if HAVE_NUMBA and _lms_nb_ok is not False:
        try:
            out = _lms_forward_nb(inp, order, shift)
            if _lms_nb_ok is None:
                K = min(4096, len(inp))
                _lms_nb_ok = bool(np.array_equal(out[:K], _lms_forward_np(inp[:K], order, shift)))
            if _lms_nb_ok:
                return out
        except Exception:
            _lms_nb_ok = False
    return _lms_forward_np(inp, order, shift)

# ------------------------------------------------------------------ Rice coding
# 1.3.0: both encoders search k only in a +-window around the mean-derived
# value (identical windows in py and numba, so the self-check still matches
# bit for bit). The chosen k is stored per partition, so any k the encoder
# picks is a valid v5 stream.

def _rice_encode_py(bw, res, esc_raw):
    u = np.where(res >= 0, 2 * res, -2 * res - 1).astype(np.uint64)
    n = len(u)
    for ps in range(0, n, PART):
        pu = u[ps : ps + PART]; pn = len(pu)
        usum = int(pu.sum())
        mean = usum // pn
        k0 = mean.bit_length() - 1 if mean > 0 else 0
        klo = max(0, k0 - 1); khi = min(32, k0 + 2)
        bestk, bestc = klo, None
        for k in range(klo, khi + 1):
            c = (k + 1) * pn + int((pu >> np.uint64(k)).sum())
            if bestc is None or c < bestc: bestc, bestk = c, k
        bw.write(bestk, 6)
        k = bestk; mask = (1 << k) - 1
        for uv in pu.tolist():
            q = uv >> k
            if q < ESC_Q:
                bw.write((((1 << q) - 1) << 1) << k | (uv & mask), q + 1 + k)
            else:
                bw.write((1 << ESC_Q) - 1, ESC_Q)
                bw.write(uv, esc_raw)


@njit(cache=True, nogil=True)
def _rice_encode_nb(res, part, esc, esc_raw, acc0, n0):
    n = res.shape[0]
    out = np.empty(16 * n + 64, np.uint8)
    cnt = 0
    acc = acc0; nb = n0
    ps = 0
    while ps < n:
        pn = min(part, n - ps)
        usum = np.int64(0)
        for j in range(pn):
            v = res[ps + j]
            usum += 2 * v if v >= 0 else -2 * v - 1
        mean = usum // pn
        k0 = 0
        m = mean
        while m > 1:
            m >>= 1
            k0 += 1
        if mean <= 0:
            k0 = 0
        klo = k0 - 1
        if klo < 0: klo = 0
        khi = k0 + 2
        if khi > 32: khi = 32
        bestk = klo; bestc = np.int64(1) << 62
        for k in range(klo, khi + 1):
            c = np.int64((k + 1) * pn)
            for j in range(pn):
                v = res[ps + j]
                u = 2 * v if v >= 0 else -2 * v - 1
                c += u >> k
            if c < bestc: bestc = c; bestk = k
        acc = (acc << 6) | bestk
        nb += 6
        while nb >= 8:
            nb -= 8
            out[cnt] = (acc >> nb) & 0xFF; cnt += 1
            acc &= (np.int64(1) << nb) - 1
        k = bestk
        mask = (np.int64(1) << k) - 1
        for j in range(pn):
            v = res[ps + j]
            u = 2 * v if v >= 0 else -2 * v - 1
            q = u >> k
            if q < esc:
                # emit unary+terminator, then k payload bits (each chunk <= 41 bits)
                acc = (acc << (q + 1)) | ((((np.int64(1) << q) - 1) << 1))
                nb += q + 1
                while nb >= 8:
                    nb -= 8
                    out[cnt] = (acc >> nb) & 0xFF; cnt += 1
                    acc &= (np.int64(1) << nb) - 1
                if k > 0:
                    acc = (acc << k) | (u & mask)
                    nb += k
                    while nb >= 8:
                        nb -= 8
                        out[cnt] = (acc >> nb) & 0xFF; cnt += 1
                        acc &= (np.int64(1) << nb) - 1
            else:
                acc = (acc << esc) | ((np.int64(1) << esc) - 1)
                nb += esc
                while nb >= 8:
                    nb -= 8
                    out[cnt] = (acc >> nb) & 0xFF; cnt += 1
                    acc &= (np.int64(1) << nb) - 1
                acc = (acc << esc_raw) | (u & ((np.int64(1) << esc_raw) - 1))
                nb += esc_raw
                while nb >= 8:
                    nb -= 8
                    out[cnt] = (acc >> nb) & 0xFF; cnt += 1
                    acc &= (np.int64(1) << nb) - 1
        ps += pn
    return out[:cnt], acc, nb


_rice_enc_nb_ok = None

def rice_encode_block(bw, res, esc_raw=ESC_RAW):
    global _rice_enc_nb_ok
    if HAVE_NUMBA and _rice_enc_nb_ok is not False:
        try:
            if _rice_enc_nb_ok is None:
                bw2 = BitWriter(); bw2.acc = bw.acc; bw2.n = bw.n
                _rice_encode_py(bw2, res, esc_raw)
                by, acc, nb = _rice_encode_nb(res, PART, ESC_Q, esc_raw, bw.acc, bw.n)
                _rice_enc_nb_ok = bool(bytes(by) == bytes(bw2.buf) and acc == bw2.acc and nb == bw2.n)
                if _rice_enc_nb_ok:
                    bw.buf.extend(by.tobytes()); bw.acc = int(acc); bw.n = int(nb)
                    return
            else:
                by, acc, nb = _rice_encode_nb(res, PART, ESC_Q, esc_raw, bw.acc, bw.n)
                bw.buf.extend(by.tobytes()); bw.acc = int(acc); bw.n = int(nb)
                return
        except Exception:
            _rice_enc_nb_ok = False
    _rice_encode_py(bw, res, esc_raw)


def _rice_decode_py(br, n, kbits, esc_raw):
    out = np.empty(n, dtype=np.int64)
    i = 0
    while i < n:
        k = br.read(kbits)
        pn = min(PART, n - i)
        for j in range(pn):
            q = br.read_unary_cap(ESC_Q)
            if q < ESC_Q:
                u = (q << k) | (br.read(k) if k else 0)
            else:
                u = br.read(esc_raw)
            out[i + j] = (u >> 1) ^ -(u & 1)
        i += pn
    return out


@njit(cache=True, nogil=True)
def _rice_decode_nb(data, pos0, n0, acc0, n, part, esc, kbits, esc_raw):
    out = np.empty(n, np.int64)
    pos = pos0; nb = n0; acc = acc0
    i = 0
    while i < n:
        while nb < kbits:
            acc = (acc << 8) | data[pos]; pos += 1; nb += 8
        nb -= kbits
        k = (acc >> nb) & ((np.int64(1) << kbits) - 1)
        acc &= (np.int64(1) << nb) - 1
        pn = min(part, n - i)
        for j in range(pn):
            q = 0
            while q < esc:
                while nb < 1:
                    acc = (acc << 8) | data[pos]; pos += 1; nb += 8
                nb -= 1
                bit = (acc >> nb) & 1
                acc &= (np.int64(1) << nb) - 1
                if bit == 0: break
                q += 1
            if q < esc:
                if k > 0:
                    while nb < k:
                        acc = (acc << 8) | data[pos]; pos += 1; nb += 8
                    nb -= k
                    r = (acc >> nb) & ((np.int64(1) << k) - 1)
                    acc &= (np.int64(1) << nb) - 1
                else:
                    r = np.int64(0)
                u = (np.int64(q) << k) | r
            else:
                while nb < esc_raw:
                    acc = (acc << 8) | data[pos]; pos += 1; nb += 8
                nb -= esc_raw
                u = (acc >> nb) & ((np.int64(1) << esc_raw) - 1)
                acc &= (np.int64(1) << nb) - 1
            out[i + j] = (u >> 1) ^ -(u & 1)
        i += pn
    return out, pos, nb, acc


_rice_dec_nb_ok = None

def rice_decode_block(br, n, kbits=6, esc_raw=ESC_RAW):
    global _rice_dec_nb_ok
    if HAVE_NUMBA and _rice_dec_nb_ok is not False:
        try:
            if _rice_dec_nb_ok is None:
                st = br.state()
                ref = _rice_decode_py(br, n, kbits, esc_raw)
                st_after = br.state()
                out, pos, nb, acc = _rice_decode_nb(
                    np.frombuffer(br.d, dtype=np.uint8), st[0], st[1], st[2],
                    n, PART, ESC_Q, kbits, esc_raw)
                _rice_dec_nb_ok = bool(np.array_equal(out, ref) and
                                       (int(pos), int(nb), int(acc)) == st_after)
                return ref
            out, pos, nb, acc = _rice_decode_nb(
                np.frombuffer(br.d, dtype=np.uint8), br.pos, br.n, br.acc,
                n, PART, ESC_Q, kbits, esc_raw)
            br.pos = int(pos); br.n = int(nb); br.acc = int(acc)
            return out
        except Exception:
            _rice_dec_nb_ok = False
    return _rice_decode_py(br, n, kbits, esc_raw)

# ------------------------------------------------------------------ stereo

def to_ms(L, R):
    return (L + R) >> 1, L - R


def from_ms(mid, side):
    L = mid + ((side + (side & 1)) >> 1)
    return L, L - side


def est_ch_cost(x):
    d = np.diff(np.diff(x))
    return len(x) * np.log2(float(np.mean(np.abs(d))) + 1.0)

# ------------------------------------------------------------------ reconstruction

def _reconstruct_np(r2, block, params, stages, limit=None):
    n = len(r2) if limit is None else min(limit, len(r2))
    S = len(stages)
    bufs = [np.zeros(n + o, dtype=np.int64) for o, _ in stages]
    sgns = [np.zeros(n + o, dtype=np.int8) for o, _ in stages]
    ws = [np.zeros(o, dtype=np.int64) for o, _ in stages]
    halves = [1 << (sh - 1) if sh > 0 else 0 for _, sh in stages]
    xpad = np.zeros(n + MAXORD, dtype=np.int64)
    bi = -1; qrev = None; sh = 0; lo = 0; lhalf = 0
    for i in range(n):
        if i % block == 0:
            bi += 1
            qrev, sh = params[bi]
            lo = len(qrev); lhalf = 1 << (sh - 1) if sh > 0 else 0
        v = int(r2[i])
        for sidx in range(S - 1, -1, -1):
            o, ssh = stages[sidx]
            buf = bufs[sidx]
            p = (int(ws[sidx] @ buf[i : i + o]) + halves[sidx]) >> ssh
            if v > 0: np.add(ws[sidx], sgns[sidx][i : i + o], out=ws[sidx])
            elif v < 0: np.subtract(ws[sidx], sgns[sidx][i : i + o], out=ws[sidx])
            v = v + p
            buf[i + o] = v
            sgns[sidx][i + o] = 1 if v > 0 else (-1 if v < 0 else 0)
        pred = (int(qrev @ xpad[i + MAXORD - lo : i + MAXORD]) + lhalf) >> sh
        xpad[i + MAXORD] = v + pred
    return xpad[MAXORD : MAXORD + n]


@njit(cache=True, nogil=True)
def _reconstruct_nb(r2, block, orders, shifts, coefs, st_ord, st_shift, maxord):
    n = r2.shape[0]
    S = st_ord.shape[0]
    off = np.zeros(S + 1, np.int64)
    woff = np.zeros(S + 1, np.int64)
    for s in range(S):
        off[s + 1] = off[s] + 2 * st_ord[s]
        woff[s + 1] = woff[s] + st_ord[s]
    buf = np.zeros(off[S], np.int64)
    sgn = np.zeros(off[S], np.int64)
    W = np.zeros(woff[S], np.int64)
    xbuf = np.zeros(2 * maxord, np.int64)
    x = np.empty(n, np.int64)
    bi = -1
    o = np.int64(0); sh = np.int64(0); half = np.int64(0)
    for i in range(n):
        if i % block == 0:
            bi += 1
            o = orders[bi]; sh = shifts[bi]
            half = (np.int64(1) << (sh - 1)) if sh > 0 else np.int64(0)
        v = r2[i]
        for s in range(S - 1, -1, -1):
            so = st_ord[s]; ss = st_shift[s]
            wp = i % so
            b0 = off[s]; w0 = woff[s]
            acc = np.int64(0)
            for j in range(so):
                acc += W[w0 + j] * buf[b0 + wp + j]
            hs = (np.int64(1) << (ss - 1)) if ss > 0 else np.int64(0)
            p = (acc + hs) >> ss
            if v > 0:
                for j in range(so): W[w0 + j] += sgn[b0 + wp + j]
            elif v < 0:
                for j in range(so): W[w0 + j] -= sgn[b0 + wp + j]
            v = v + p
            sv = np.int64(1) if v > 0 else (np.int64(-1) if v < 0 else np.int64(0))
            buf[b0 + wp] = v; buf[b0 + wp + so] = v
            sgn[b0 + wp] = sv; sgn[b0 + wp + so] = sv
        wp32 = i % maxord
        acc = np.int64(0)
        for j in range(o):
            acc += coefs[bi, j] * xbuf[wp32 + maxord - o + j]
        pred = (acc + half) >> sh
        xi = v + pred
        x[i] = xi
        xbuf[wp32] = xi; xbuf[wp32 + maxord] = xi
    return x


_rec_nb_ok = None

def reconstruct(r2, block, params, stages):
    global _rec_nb_ok
    if HAVE_NUMBA and _rec_nb_ok is not False:
        try:
            nbk = len(params)
            orders = np.array([len(q) for q, _ in params], dtype=np.int64)
            shifts = np.array([sh for _, sh in params], dtype=np.int64)
            coefs = np.zeros((nbk, MAXORD), dtype=np.int64)
            for i, (q, _) in enumerate(params):
                coefs[i, :len(q)] = q
            st_o = np.array([o for o, _ in stages], dtype=np.int64)
            st_s = np.array([s for _, s in stages], dtype=np.int64)
            x = _reconstruct_nb(r2, block, orders, shifts, coefs, st_o, st_s, MAXORD)
            if _rec_nb_ok is None:
                K = min(2 * block, len(r2))
                ref = _reconstruct_np(r2, block, params, stages, limit=K)
                _rec_nb_ok = bool(np.array_equal(x[:K], ref))
            if _rec_nb_ok:
                return x
        except Exception:
            _rec_nb_ok = False
    return _reconstruct_np(r2, block, params, stages)

# ------------------------------------------------------------------ warmup

_warmed = False

def _warmup():
    """Run every fast/reference pair once, serially, on tiny data: settles
    the numba self-check flags and compiles the kernels before the thread
    pool starts, so worker threads never race on the check globals."""
    global _warmed
    if _warmed: return
    rng = np.random.default_rng(7)
    x = rng.integers(-2000, 2000, size=1024).astype(np.int64)
    r = lms_forward(x, 8, 8)
    bw = BitWriter()
    rice_encode_block(bw, r)
    br = BitReader(bw.bytes())
    rice_decode_block(br, len(r))
    params = [(np.array([1], dtype=np.int64), 0)] * ((len(x) + 255) // 256)
    reconstruct(x, 256, params, [(8, 8)])
    _warmed = True

# ------------------------------------------------------------------ encode helpers

def _stereo_probe(x, blk):
    """LPC-residual cost estimate for one candidate channel of one segment.
    1.3.0: pruned order search (2 exact candidates) - same decisions in
    practice, an order of magnitude cheaper."""
    n = len(x)
    xpad = np.zeros(n + MAXORD, dtype=np.int64); xpad[MAXORD:] = x
    bits = 0
    for s in range(0, n, blk):
        bn = min(blk, n - s)
        q, sh, r = analyze_block(xpad, s, bn, candidates=PROBE_CAND)
        bits += rice_cost_est(r)
    return bits


def _encode_segment_payload(xseg, blk, stages=None, candidates=4):
    """Self-contained segment: fresh LPC history + fresh LMS state."""
    if stages is None: stages = LMS_STAGES
    n = len(xseg)
    xpad = np.zeros(n + MAXORD, dtype=np.int64); xpad[MAXORD:] = xseg
    r1 = np.empty(n, dtype=np.int64)
    params = []
    for s in range(0, n, blk):
        bn = min(blk, n - s)
        q, sh, r = analyze_block(xpad, s, bn, candidates=candidates)
        params.append((q, sh))
        r1[s : s + bn] = r
    stagein = r1
    for (o, sh) in stages:
        stagein = lms_forward(stagein, o, sh)
    r2 = stagein
    pw = BitWriter()
    bi = 0
    for s in range(0, n, blk):
        bn = min(blk, n - s)
        q, sh = params[bi]; bi += 1
        pw.write(len(q), 8); pw.write(sh, 8)
        for c in q.tolist():
            pw.write(c & 0xFFFF, 16)
        rice_encode_block(pw, r2[s : s + bn])
    return pw.bytes(), float(np.mean(np.abs(r1))) if n else 0.0, \
           float(np.mean(np.abs(r2))) if n else 0.0


def _build_header(ch, bits, sr, nf, blk, stages, nseg):
    bw = BitWriter()
    for b in MAGIC: bw.write(b, 8)
    bw.write(VER, 8)
    bw.write(2 if ch == 2 else 0, 8)   # stereo-mode field: 2 = per-segment table (v4+)
    bw.write(ch, 8); bw.write(bits, 8)
    bw.write(sr, 32); bw.write(nf, 64)
    bw.write(blk, 16); bw.write(PART, 16)
    bw.write(len(stages), 8)
    for o, s in stages:
        bw.write(o, 16); bw.write(s, 8)
    md5_off = bw.mark()
    for _ in range(16): bw.write(0, 8)  # md5 placeholder, patched at the end
    bw.write(SEG_BLOCKS, 16)
    modes_off = None
    if ch == 2:
        bw.write(nseg, 16)
        modes_off = bw.mark()
        for _ in range(nseg): bw.write(0, 8)  # mode table placeholder
    return bw.bytes(), md5_off, modes_off


def _pipeline(pool, worker, jobs, depth):
    """Submit jobs to the pool with a bounded look-ahead, yield results in
    order. Keeps memory constant while still filling every core."""
    q = deque()
    for job in jobs:
        q.append(pool.submit(worker, *job))
        if len(q) >= depth:
            yield q.popleft().result()
    while q:
        yield q.popleft().result()


SLOT0_SRC = ('L', 'M', 'L', 'S')
SLOT1_SRC = ('R', 'S', 'S', 'R')


def encode(wav_in, alafc_out, verbose=True, self_verify=True,
           profile='max', threads=None):
    """Encode WAV -> .alafc (bitstream v5, readable by 1.1.0+ tools).

    profile: 'fast' (pure LPC+Rice, FLAC-class speed),
             'normal' (light NLMS cascade),
             'max' (full cascade - same compression as 1.2.0, default).
    Streaming: the WAV is read in ~6 s segments (twice for stereo - once per
    channel slot, since the v5 layout stores all of slot 0 before slot 1),
    so RAM stays constant no matter how long the file is."""
    t0 = time.time()
    if profile not in PROFILES:
        raise ValueError(f'unknown profile {profile!r} (fast/normal/max)')
    stages = PROFILES[profile]
    cand = PROFILE_CAND[profile]
    win = _WavIn(wav_in)
    ch, sr, nf, bits = win.ch, win.sr, win.nf, win.bits
    blk = pick_block(sr)
    seg_len = blk * SEG_BLOCKS
    nseg = (nf + seg_len - 1) // seg_len if nf else 0
    header, md5_off, modes_off = _build_header(ch, bits, sr, nf, blk, stages, nseg)
    nthreads = _nthreads(threads)
    _warmup()
    md5 = hashlib.md5()
    seg_modes = []
    stats = {0: [0.0, 0.0, 0], 1: [0.0, 0.0, 0]}

    def seg_jobs(slot):
        win.rewind()
        for i in range(nseg):
            want = min(seg_len, nf - i * seg_len)
            raw, x = win.read(want)
            if slot == 0:
                md5.update(raw)
            if ch == 2:
                L = x[0::2]; R = x[1::2]
                if slot == 0:
                    M, S = to_ms(L, R)
                    cL = _stereo_probe(L, blk); cR = _stereo_probe(R, blk)
                    cM = _stereo_probe(M, blk); cS = _stereo_probe(S, blk)
                    costs = (cL + cR, cM + cS, cL + cS, cS + cR)  # LR MS LS SR
                    mode = min(range(4), key=lambda k: costs[k])
                    seg_modes.append(mode)
                    src = {'L': L, 'M': M, 'S': S}[SLOT0_SRC[mode]]
                else:
                    mode = seg_modes[i]
                    src = to_ms(L, R)[1] if SLOT1_SRC[mode] == 'S' else R
                yield (src.copy(),)
            else:
                yield (x,)

    def worker(xseg):
        return _encode_segment_payload(xseg, blk, stages, cand)

    with open(alafc_out, 'wb') as f, \
         ThreadPoolExecutor(max_workers=nthreads) as ex:
        f.write(header)
        for slot in ([0, 1] if ch == 2 else [0]):
            st = stats[slot]
            for payload, a1, a2 in _pipeline(ex, worker, seg_jobs(slot),
                                             nthreads + 2):
                f.write(bytes([SYNC0, SYNC1]))
                f.write(len(payload).to_bytes(4, 'big'))
                f.write((zlib.crc32(payload) & 0xFFFFFFFF).to_bytes(4, 'big'))
                f.write(payload)
                st[0] += a1; st[1] += a2; st[2] += 1
            if verbose and st[2]:
                print(f'  ch{slot}: mean|res| {st[0]/st[2]:.1f} -> '
                      f'{st[1]/st[2]:.1f} ({st[2]} segments)')
        if verbose and ch == 2 and seg_modes:
            mc = [seg_modes.count(k) for k in range(4)]
            print(f'  stereo: LR={mc[0]} MS={mc[1]} LS={mc[2]} SR={mc[3]} '
                  f'(of {len(seg_modes)} segments)')
        f.seek(md5_off); f.write(md5.digest())
        if ch == 2 and seg_modes:
            f.seek(modes_off); f.write(bytes(seg_modes))
    win.close()
    if self_verify:
        _, _, _, _, ok, dmg = decode_to_memory(alafc_out, verbose=False,
                                               threads=threads)
        if ok is not True or dmg:
            raise RuntimeError('encode self-verify FAILED - do not use this file')
    if verbose:
        src = os.path.getsize(wav_in)
        out = os.path.getsize(alafc_out)
        print(f'{src} B -> {out} B ({100*out/src:.1f}%) in {time.time()-t0:.1f}s'
              f'{" [verified lossless]" if self_verify else ""} '
              f'profile: {profile} threads: {nthreads} engine: {_engine_note}')
    return os.path.getsize(alafc_out)

# ------------------------------------------------------------------ decode

def _read_header(br):
    if bytes(br.read(8) for _ in range(4)) != MAGIC:
        raise ValueError('not an ALAFC file')
    ver = br.read(8)
    if ver not in (1, 2, 3, 4, 5): raise ValueError(f'unsupported ALAFC version {ver}')
    stereo_flag = br.read(8)
    use_ms = stereo_flag == 1
    ch = br.read(8); bits = br.read(8)
    sr = br.read(32); nf = br.read(64)
    block = br.read(16); part = br.read(16)
    nst = br.read(8)
    stages = [(br.read(16), br.read(8)) for _ in range(nst)]
    md5 = bytes(br.read(8) for _ in range(16)) if ver >= 2 else None
    segb = br.read(16) if ver >= 3 else 0
    seg_modes = None
    if ver >= 4 and stereo_flag == 2:
        nseg = br.read(16)
        seg_modes = [br.read(8) for _ in range(nseg)]
    return ver, use_ms, ch, bits, sr, nf, block, part, stages, md5, segb, seg_modes


def _parse_v12_channel(br, n, block):
    params = []; r2 = np.empty(n, dtype=np.int64)
    for s in range(0, n, block):
        bn = min(block, n - s)
        o = br.read(8); sh = br.read(8)
        q = np.empty(o, dtype=np.int64)
        for i in range(o):
            v = br.read(16)
            q[i] = v - 65536 if v >= 32768 else v
        r2[s : s + bn] = rice_decode_block(br, bn, kbits=5, esc_raw=32)
        params.append((q[::-1].copy(), sh))
    br.align()
    return params, r2


def _decode_segment_payload(payload, nsamp, blk, stages):
    br = BitReader(payload)
    params = []; r2 = np.empty(nsamp, dtype=np.int64)
    for s in range(0, nsamp, blk):
        bn = min(blk, nsamp - s)
        o = br.read(8); sh = br.read(8)
        q = np.empty(o, dtype=np.int64)
        for i in range(o):
            v = br.read(16)
            q[i] = v - 65536 if v >= 32768 else v
        r2[s : s + bn] = rice_decode_block(br, bn)
        params.append((q[::-1].copy(), sh))
    return reconstruct(r2, blk, params, stages)


def _find_sync(data, start):
    i = data.find(bytes([SYNC0, SYNC1]), start)
    return i if i >= 0 else None


def _scan_channel_segments(data, pos, nf, seg_len):
    """Walk one channel's segment chain: returns (list of payload|None, pos).
    None means the segment is missing/corrupt (bad sync, length or CRC)."""
    payloads = []
    for s0 in range(0, nf, seg_len):
        payload = None
        if pos + 10 <= len(data) and data[pos] == SYNC0 and data[pos + 1] == SYNC1:
            plen = int.from_bytes(data[pos+2:pos+6], 'big')
            crc = int.from_bytes(data[pos+6:pos+10], 'big')
            pstart = pos + 10
            if pstart + plen <= len(data):
                cand = data[pstart : pstart + plen]
                if (zlib.crc32(cand) & 0xFFFFFFFF) == crc:
                    payload = cand
                pos = pstart + plen
            else:
                pos = len(data)
        else:
            nxt = _find_sync(data, pos + 1)
            pos = nxt if nxt is not None else len(data)
        payloads.append(payload)
    return payloads, pos


class _MemSink:
    """Collects decoded interleaved int64 chunks into a tight int16/int32
    array (2-4x less RAM than the old int64 intermediate)."""
    def __init__(self, nf, ch, bits, md5ref):
        self.out = np.empty(nf * ch, dtype=np.int16 if bits == 16 else np.int32)
        self.bits = bits; self.md5ref = md5ref
        self.md5 = hashlib.md5() if md5ref is not None else None
        self.pos = 0
    def write(self, inter64):
        n = len(inter64)
        self.out[self.pos : self.pos + n] = inter64
        self.pos += n
        if self.md5 is not None:
            self.md5.update(pcm_to_bytes(inter64, self.bits))
    def finish(self, damaged):
        if self.md5 is None or damaged:
            return None
        return self.md5.digest() == self.md5ref


class _WavSink:
    """Streams decoded segments straight into a WAV file - constant RAM."""
    def __init__(self, path, ch, sr, bits, md5ref):
        self.w = wave.open(path, 'wb')
        self.w.setnchannels(ch); self.w.setsampwidth(bits // 8)
        self.w.setframerate(sr)
        self.bits = bits; self.md5ref = md5ref
        self.md5 = hashlib.md5() if md5ref is not None else None
    def write(self, inter64):
        raw = pcm_to_bytes(inter64, self.bits)
        self.w.writeframes(raw)
        if self.md5 is not None:
            self.md5.update(raw)
    def finish(self, damaged):
        self.w.close()
        if self.md5 is None or damaged:
            return None
        return self.md5.digest() == self.md5ref


def _recombine(a, b, mode):
    if mode == 0:               # LR: a=L, b=R
        return a, b
    if mode == 2:               # LS: a=L, b=side -> R = L - side
        return a, a - b
    if mode == 3:               # SR: a=side, b=R -> L = R + side
        return b + a, b
    return from_ms(a, b)        # MS


def _decode_core(data, verbose, threads, sink_factory):
    """Parse + reconstruct into a sink. Returns (sink_result_ok, ch, sr,
    bits, damaged, sink)."""
    br = BitReader(data)
    ver, use_ms, ch, bits, sr, nf, block, part, stages, md5, segb, seg_modes = _read_header(br)
    global PART; PART = part
    _warmup()
    damaged = []
    sink = sink_factory(nf, ch, bits, sr, md5)
    if ver < 3:
        chans = []
        for ci in range(ch):
            params, r2 = _parse_v12_channel(br, nf, block)
            if verbose: print(f'  ch{ci}: reconstructing...')
            chans.append(reconstruct(r2, block, params, stages))
        if ch == 2:
            L, R = from_ms(chans[0], chans[1]) if use_ms else (chans[0], chans[1])
            inter = np.empty(nf * 2, dtype=np.int64)
            inter[0::2] = L; inter[1::2] = R
        else:
            inter = chans[0]
        sink.write(inter)
        ok = sink.finish(damaged)
        return ok, ch, sr, bits, damaged, sink

    seg_len = block * segb
    nthreads = _nthreads(threads)
    pos = br.pos                       # header is byte-aligned in v3+
    chan_payloads = []
    for ci in range(ch):
        payloads, pos = _scan_channel_segments(data, pos, nf, seg_len)
        chan_payloads.append(payloads)
    nseg = len(chan_payloads[0]) if ch else 0
    seg_sizes = [min(seg_len, nf - i * seg_len) for i in range(nseg)]

    def dec_one(payload, nsamp):
        if payload is None:
            return None
        try:
            return _decode_segment_payload(payload, nsamp, block, stages)
        except Exception:
            return None

    if ch == 2:
        def jobs():
            for i in range(nseg):
                yield (chan_payloads[0][i], chan_payloads[1][i], seg_sizes[i], i)

        def worker(p0, p1, nsamp, i):
            a = dec_one(p0, nsamp)
            b = dec_one(p1, nsamp)
            d0 = a is None; d1 = b is None
            if a is None: a = np.zeros(nsamp, dtype=np.int64)
            if b is None: b = np.zeros(nsamp, dtype=np.int64)
            if seg_modes is not None:
                mode = seg_modes[i]
            else:
                mode = 1 if use_ms else 0
            L, R = _recombine(a, b, mode)
            inter = np.empty(nsamp * 2, dtype=np.int64)
            inter[0::2] = L; inter[1::2] = R
            return inter, d0, d1, i

        with ThreadPoolExecutor(max_workers=nthreads) as ex:
            for inter, d0, d1, i in _pipeline(ex, worker, jobs(), nthreads + 2):
                s0 = i * seg_len
                if d0: damaged.append((0, s0 / sr))
                if d1: damaged.append((1, s0 / sr))
                sink.write(inter)
    else:
        def jobs():
            for i in range(nseg):
                yield (chan_payloads[0][i], seg_sizes[i], i)

        def worker(p0, nsamp, i):
            a = dec_one(p0, nsamp)
            d0 = a is None
            if a is None: a = np.zeros(nsamp, dtype=np.int64)
            return a, d0, i

        with ThreadPoolExecutor(max_workers=nthreads) as ex:
            for a, d0, i in _pipeline(ex, worker, jobs(), nthreads + 2):
                if d0: damaged.append((0, i * seg_len / sr))
                sink.write(a)
    if verbose:
        for ci in range(ch):
            print(f'  ch{ci}: {"OK" if not damaged else "recovered"}')
    ok = sink.finish(damaged)
    return ok, ch, sr, bits, damaged, sink


def decode_to_memory(path, verbose=True, threads=None):
    """Returns (pcm int16/int32, channels, samplerate, bits, md5_ok, damaged_list)."""
    global _rec_nb_ok, _rice_dec_nb_ok
    data = open(path, 'rb').read()
    factory = lambda nf, ch, bits, sr, md5: _MemSink(nf, ch, bits, md5)
    ok, ch, sr, bits, damaged, sink = _decode_core(data, verbose, threads, factory)
    if ok is False and HAVE_NUMBA and (_rec_nb_ok or _rice_dec_nb_ok):
        _rec_nb_ok = False; _rice_dec_nb_ok = False
        if verbose: print('  ! fast engine mismatch, retrying with reference engine')
        ok, ch, sr, bits, damaged, sink = _decode_core(data, verbose, threads, factory)
    return sink.out, ch, sr, bits, ok, damaged


def decode(alafc_in, wav_out, verbose=True, threads=None):
    global _rec_nb_ok, _rice_dec_nb_ok
    t0 = time.time()
    data = open(alafc_in, 'rb').read()
    factory = lambda nf, ch, bits, sr, md5: _WavSink(wav_out, ch, sr, bits, md5)
    ok, ch, sr, bits, damaged, _ = _decode_core(data, verbose, threads, factory)
    if ok is False and HAVE_NUMBA and (_rec_nb_ok or _rice_dec_nb_ok):
        _rec_nb_ok = False; _rice_dec_nb_ok = False
        if verbose: print('  ! fast engine mismatch, retrying with reference engine')
        ok, ch, sr, bits, damaged, _ = _decode_core(data, verbose, threads, factory)
    if verbose:
        if damaged:
            ts = ', '.join(f'{t:.1f}s' for _, t in damaged[:8])
            msg = f'RECOVERED: {len(damaged)} damaged segment(s) muted (near {ts})'
        elif ok is True:
            msg = 'verified lossless (MD5 OK)'
        elif ok is None:
            msg = 'v1 file - no embedded checksum'
        else:
            msg = 'WARNING: MD5 mismatch!'
        print(f'decoded in {time.time()-t0:.1f}s - {msg} engine: {_engine_note}')
    if ok is False and not damaged:
        raise RuntimeError('MD5 mismatch - decoded audio does not match original')


def info(path):
    data = open(path, 'rb').read(65536)  # generous enough for header + mode table
    br = BitReader(data)
    ver, use_ms, ch, bits, sr, nf, block, part, stages, md5, segb, seg_modes = _read_header(br)
    size = os.path.getsize(path)
    dur = nf / sr
    rawsz = nf * ch * (bits // 8)
    extra = f' / segments of {block*segb} samples (CRC32)' if ver >= 3 else ''
    if seg_modes is not None:
        names = ['LR', 'MS', 'LS', 'SR']
        counts = [seg_modes.count(k) for k in range(4)]
        parts = ' '.join(f'{names[k]}={counts[k]}' for k in range(4) if counts[k])
        stereo_txt = f'per-segment ({parts})'
    else:
        stereo_txt = 'mid-side' if use_ms else 'L-R'
    lms_txt = '+'.join(str(o) for o, _ in stages) if stages else 'off (fast profile)'
    print(f'ALAFC v{ver}: {sr} Hz / {bits}-bit / {ch}ch / {dur:.1f}s / '
          f'{stereo_txt} / NLMS {lms_txt} / {size} B '
          f'({100*size/rawsz:.1f}% of raw PCM){extra}')


def pcm_md5(path):
    with wave.open(path, 'rb') as w:
        return hashlib.md5(w.readframes(w.getnframes())).hexdigest()


def _cli():
    args = [a for a in sys.argv[1:]]
    flags = {a for a in args if a.startswith('--')}
    args = [a for a in args if not a.startswith('--')]
    profile = 'max'
    if '--fast' in flags: profile = 'fast'
    if '--normal' in flags: profile = 'normal'
    if '--max' in flags: profile = 'max'
    threads = None
    for fl in list(flags):
        if fl.startswith('--threads='):
            threads = int(fl.split('=', 1)[1])
    self_verify = '--no-verify' not in flags
    if not args:
        print('usage: alafc.py encode in.wav out.alafc [--fast|--normal|--max] '
              '[--threads=N] [--no-verify]\n'
              '       alafc.py decode in.alafc out.wav [--threads=N]\n'
              '       alafc.py verify a.wav b.wav | info file.alafc')
        sys.exit(1)
    cmd = args[0]
    if cmd == 'encode':
        encode(args[1], args[2], profile=profile, threads=threads,
               self_verify=self_verify)
    elif cmd == 'decode':
        decode(args[1], args[2], threads=threads)
    elif cmd == 'info':
        info(args[1])
    elif cmd == 'verify':
        a, b = pcm_md5(args[1]), pcm_md5(args[2])
        print('MD5 A:', a); print('MD5 B:', b)
        print('LOSSLESS OK' if a == b else 'MISMATCH!')
        sys.exit(0 if a == b else 1)
    else:
        print('unknown command', cmd); sys.exit(1)


if __name__ == '__main__':
    _cli()

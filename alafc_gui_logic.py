"""Pure-logic helpers for the ALAFC GUI player - info panel + level meter.
No tkinter here on purpose, so this half can be fully unit-tested anywhere.
"""
import os
import numpy as np
import alafc


def track_info(path, frames, sr, ch, bits, status):
    """Build the rich 'codec readout' shown in the LCD panel."""
    ext = os.path.splitext(path)[1].lower()
    nf = len(frames)
    dur = nf / sr if sr else 0
    disk_size = os.path.getsize(path)

    info = dict(
        filename=os.path.basename(path),
        title=os.path.splitext(os.path.basename(path))[0],
        sr=sr, bits=bits, ch=ch,
        chan_txt='MONO' if ch == 1 else 'STEREO',
        duration=dur,
        duration_txt=_fmt_time(dur),
        disk_size=disk_size,
        status=status,
    )

    if ext == '.alafc':
        data = open(path, 'rb').read(65536)
        br = alafc.BitReader(data)
        try:
            ver, use_ms, fch, fbits, fsr, fnf, block, part, stages, md5, segb, seg_modes = \
                alafc._read_header(br)
            raw_size = fnf * fch * (fbits // 8)
            pct = 100 * disk_size / raw_size if raw_size else 0
            kbps = (disk_size * 8) / dur / 1000 if dur else 0
            if seg_modes is not None:
                names = ['LR', 'MS', 'LS', 'SR']
                counts = [seg_modes.count(k) for k in range(4)]
                parts = '/'.join(f'{names[k]}{counts[k]}' for k in range(4) if counts[k])
                stereo_txt = f'ADAPTIVE ({parts})'
            else:
                stereo_txt = 'MID/SIDE' if use_ms else 'L / R'
            info.update(
                format='ALAFC v%d' % ver,
                stereo_mode=stereo_txt if fch == 2 else '-',
                pct_of_raw=pct,
                kbps=kbps,
                lpc_order=alafc.MAXORD,
                cascade=' / '.join(str(o) for o, _ in stages),
                segmented=ver >= 3,
            )
        except Exception:
            info.update(format='ALAFC', stereo_mode='-', pct_of_raw=0, kbps=0,
                        lpc_order=alafc.MAXORD, cascade='-', segmented=False)
    else:
        raw_size = nf * ch * (bits // 8)
        kbps = (disk_size * 8) / dur / 1000 if dur else 0
        info.update(format='WAV (PCM)', stereo_mode='-', pct_of_raw=100.0,
                    kbps=kbps, lpc_order=None, cascade='-', segmented=False)
    return info


def _fmt_time(t):
    t = max(0, int(t))
    return f'{t // 60}:{t % 60:02d}'


def format_readout_lines(info):
    """Two short LCD-style lines, Winamp-'192kbps 48kHz stereo'-esque."""
    line1 = f"{info['format']}  {info['sr']} Hz  {info['bits']}-bit  {info['chan_txt']}"
    if info.get('stereo_mode', '-') != '-':
        line1 += f"  {info['stereo_mode']}"
    if info['format'].startswith('ALAFC'):
        line2 = (f"{info['kbps']:.0f} kbps avg  {info['pct_of_raw']:.0f}% of raw  "
                 f"LPC<={info['lpc_order']}  cascade {info['cascade']}")
    else:
        line2 = f"{info['kbps']:.0f} kbps  uncompressed PCM"
    return line1, line2


def status_readout(info):
    """(text, level) where level in {'ok','warn','err','na'} for colour."""
    s = info['status']
    if s == 'MD5_OK':
        return 'VERIFIED LOSSLESS (MD5 OK)', 'ok'
    if s == 'MD5_FAIL':
        return 'MD5 MISMATCH - FILE DAMAGED', 'err'
    if s == 'NO_MD5':
        return 'NO CHECKSUM (v1 FILE)', 'warn'
    if s.startswith('RECOVERED'):
        return s, 'warn'
    return 'WAV - UNCOMPRESSED', 'na'


# ------------------------------------------------------------- level meter
N_BANDS = 12
_BAND_EDGES_HZ = np.geomspace(60, 16000, N_BANDS + 1)


def meter_levels(frames, ch, pos, sr, bits=16, window=1024):
    """Return N_BANDS values 0..1 from a short chunk around `pos`, log-spaced
    (bass..treble) - a real (not decorative) mini-spectrum, Winamp-EQ style."""
    n = len(frames)
    if n == 0:
        return np.zeros(N_BANDS)
    s = max(0, min(pos, n - 1))
    e = min(n, s + window)
    chunk = frames[s:e]
    if len(chunk) == 0:
        return np.zeros(N_BANDS)
    mono = chunk.mean(axis=1).astype(np.float64) if ch > 1 else chunk[:, 0].astype(np.float64)
    if len(mono) < window:
        mono = np.pad(mono, (0, window - len(mono)))
    mono *= np.hanning(len(mono))
    mag = np.abs(np.fft.rfft(mono))
    freqs = np.fft.rfftfreq(len(mono), d=1.0 / sr)
    levels = np.zeros(N_BANDS)
    full_scale = 2 ** (bits - 1)
    peak_ref = full_scale * window / 2  # rough full-scale reference for a window this size
    for i in range(N_BANDS):
        lo, hi = _BAND_EDGES_HZ[i], _BAND_EDGES_HZ[i + 1]
        sel = (freqs >= lo) & (freqs < hi)
        if sel.any():
            levels[i] = mag[sel].max()
    db = 20 * np.log10(levels / peak_ref + 1e-9)
    norm = np.clip((db + 60) / 60, 0.0, 1.0)  # -60dB..0dB -> 0..1
    return norm

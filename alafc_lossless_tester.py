#!/usr/bin/env python3
# ALAFC Lossless Authenticity Tester - Copyright (c) 2026 Axelrod. MIT License.
#
# Answers a different question than "is this file lossless" (a WAV/FLAC/ALAFC
# container is ALWAYS bit-exact by construction). It answers: "was the AUDIO
# inside it already lossy-compressed before being put in this container?"
# That's the classic "fake lossless" / transcode check people mean when they
# ask this. Method: look for an artificial brick-wall frequency cutoff, the
# fingerprint every lossy codec leaves (MP3/AAC/etc throw away everything
# above a bitrate-dependent frequency). Same technique as Spek/TAU.
#
#   py alafc_lossless_tester.py song.flac
#   py alafc_lossless_tester.py *.flac              (drag & drop several)
#
# Output: a spectrogram PNG per file (open it, look for a hard ceiling) plus
# a plain-language verdict in the console.
import sys, os, subprocess, shutil, tempfile, math
import numpy as np
from scipy.signal import stft
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import alafc  # local codec module, for reading .alafc files

AUDIO_EXTS = {'.flac', '.mp3', '.m4a', '.aac', '.ogg', '.opus', '.wma', '.aiff', '.ape', '.wv'}

# Typical brick-wall signatures left by lossy encoders (Hz -> likely source)
SIGNATURES = [
    (11025, '~64-96 kbps MP3 (very low bitrate)'),
    (16000, '~128 kbps MP3'),
    (17500, '~160-192 kbps MP3'),
    (19000, '~224-256 kbps MP3 or ~128-160 kbps AAC'),
    (20500, '~320 kbps MP3 or high-bitrate AAC/Opus'),
]


def need_ffmpeg():
    if shutil.which('ffmpeg') is None:
        sys.exit('Need ffmpeg for this format:  winget install Gyan.FFmpeg')


def load_mono(path):
    """Return (samples float32 [-1,1], sr) using one channel, any input format."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.alafc':
        pcm, ch, sr, bits, ok, damaged = alafc.decode_to_memory(path, verbose=False)
        if ok is False:
            print(f'  ! MD5 mismatch on {os.path.basename(path)} - file may be damaged')
        frames = pcm.reshape(-1, ch).astype(np.float64)
        mono = frames.mean(axis=1)
        scale = 32768.0 if bits == 16 else (2 ** 31)
        return (mono / scale).astype(np.float32), sr
    elif ext == '.wav':
        pcm64, ch, sr, nf, bits, _ = alafc.wav_read(path)
        frames = pcm64.reshape(-1, ch).astype(np.float64)
        mono = frames.mean(axis=1)
        scale = 32768.0 if bits == 16 else (2 ** (bits - 1))
        return (mono / scale).astype(np.float32), sr
    elif ext in AUDIO_EXTS:
        need_ffmpeg()
        tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False); tmp.close()
        r = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', path,
                            '-ac', '1', '-acodec', 'pcm_s16le', tmp.name])
        if r.returncode != 0:
            os.unlink(tmp.name)
            sys.exit(f'ffmpeg could not decode {path}')
        pcm64, ch, sr, nf, bits, _ = alafc.wav_read(tmp.name)
        os.unlink(tmp.name)
        return (pcm64.astype(np.float32) / 32768.0), sr
    else:
        sys.exit(f'Unsupported: {ext}')


def spectrogram(samples, sr, nfft=4096):
    hop = nfft // 4
    # trim leading/trailing near-silence so quiet intros don't skew the noise floor
    absf = np.abs(samples)
    thresh = max(absf.max() * 0.02, 1e-6)
    idx = np.where(absf > thresh)[0]
    if len(idx) > sr:  # keep at least ~1s
        samples = samples[idx[0]:idx[-1] + 1]
    f, t, Z = stft(samples, fs=sr, nperseg=nfft, noverlap=nfft - hop, boundary=None)
    mag_db = 20 * np.log10(np.abs(Z) + 1e-12)
    return f, t, mag_db


SILENCE_BELOW_PEAK_DB = 65.0  # this far below the loudest content = "true silence"

def analyze_cutoff(f, mag_db):
    """Per-bin loud-frame energy (95th percentile over time). A real lossy-codec
    cutoff means energy falls to near-total silence and STAYS silent all the way
    to Nyquist - unlike a local dip (e.g. a quiet gap between instrument
    harmonics), which recovers again higher up. We scan for the highest
    frequency bin still meaningfully above the noise floor; everything past it
    must remain silent, which is guaranteed by construction here."""
    from scipy.ndimage import median_filter
    profile_raw = np.percentile(mag_db, 95, axis=1)
    profile = median_filter(profile_raw, size=5)  # kill single-bin FFT-leakage spikes
    peak = profile.max()
    nyquist = f[-1]
    bin_hz = f[1] - f[0]

    alive = profile > (peak - SILENCE_BELOW_PEAK_DB)
    alive_idx = np.where(alive)[0]
    if len(alive_idx) == 0 or alive_idx[-1] >= len(profile) - 2:
        cutoff_hz, drop_db, steepness, transition_hz = nyquist, 0.0, 0.0, bin_hz
    else:
        top_idx = alive_idx[-1]
        cutoff_hz = f[top_idx]
        win = max(1, int(round(500 / bin_hz)))
        after_idx = min(top_idx + win, len(profile) - 1)
        drop_db = profile[top_idx] - profile[after_idx]
        transition_hz = max(f[after_idx] - f[top_idx], bin_hz)
        steepness = drop_db / (transition_hz / 1000.0)

    return dict(cutoff_hz=cutoff_hz, nyquist=nyquist, drop_db=drop_db,
                transition_hz=transition_hz, steepness=steepness,
                profile=profile_raw, freqs=f, peak=peak)


def classify(info):
    cutoff = info['cutoff_hz']; nyq = info['nyquist']
    steep = info['steepness']; drop = info['drop_db']
    gap_khz = (nyq - cutoff) / 1000.0

    if gap_khz < 0.6:
        return ('GENUINE', f'Съдържанието стига практически до Nyquist '
                f'({cutoff/1000:.1f} от {nyq/1000:.1f} kHz), без трайна тишина отгоре - '
                f'типично за истински lossless.')
    guess = min(SIGNATURES, key=lambda s: abs(s[0] - cutoff))[1]
    conf = 'силна' if (steep >= 20 and drop >= 18) else 'умерена'
    return ('FAKE', f'Трайна тишина над {cutoff/1000:.1f} kHz (спад {drop:.0f} dB, '
            f'{steep:.0f} dB/kHz) чак до Nyquist - {conf} сигнатура на lossy '
            f'компресия. Вероятен източник: {guess}.')


def render(path_png, f, t, mag_db, info, title):
    fig, ax = plt.subplots(figsize=(11, 5))
    vmax = info['peak']; vmin = vmax - 90
    im = ax.pcolormesh(t, f / 1000, mag_db, shading='auto', cmap='inferno',
                       vmin=vmin, vmax=vmax)
    if info['cutoff_hz'] > 0:
        ax.axhline(info['cutoff_hz'] / 1000, color='cyan', linewidth=1, linestyle='--',
                   label=f"detected cutoff: {info['cutoff_hz']/1000:.1f} kHz")
        ax.legend(loc='upper right', facecolor='#222', labelcolor='white')
    ax.set_ylim(0, info['nyquist'] / 1000)
    ax.set_xlabel('Time (s)'); ax.set_ylabel('Frequency (kHz)')
    ax.set_title(title, fontsize=10)
    fig.colorbar(im, ax=ax, label='dB')
    fig.tight_layout()
    fig.savefig(path_png, dpi=130)
    plt.close(fig)


def analyze_file(path, outdir=None):
    print(f'\n=== {os.path.basename(path)} ===')
    samples, sr = load_mono(path)
    f, t, mag_db = spectrogram(samples, sr)
    info = analyze_cutoff(f, mag_db)
    verdict, msg = classify(info)
    tag = {'GENUINE': 'ВЕРОЯТНО ИСТИНСКИ LOSSLESS',
           'FAKE': 'ВЕРОЯТНО FAKE (транскод от lossy)',
           'UNCLEAR': 'НЕЯСНО - виж спектрограмата'}[verdict]
    print(f'  {tag}')
    print(f'  {msg}')
    outdir = outdir or os.path.dirname(os.path.abspath(path))
    png = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0] + '_spectrogram.png')
    render(png, f, t, mag_db, info, f'{os.path.basename(path)}  -  {tag}')
    print(f'  Спектрограма: {os.path.basename(png)}')
    return verdict, info, png


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('usage: alafc_lossless_tester.py file1.flac [file2.wav ...]')
        sys.exit(1)
    for p in sys.argv[1:]:
        try:
            analyze_file(p)
        except Exception as ex:
            print(f'  ГРЕШКА: {ex}')
    print('\nГотово. Отвори .png файловете за да видиш спектрограмите с очи.')

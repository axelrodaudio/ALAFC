"""TrueScope loader - one decode pass feeds BOTH playback and analysis, so the
graph you see is guaranteed to be the exact audio being played (no separate
re-decode that could drift). No tkinter here, so it's fully unit-testable.
"""
import os, shutil, subprocess, tempfile
import numpy as np
import alafc

AUDIO_EXTS = {'.flac', '.mp3', '.m4a', '.aac', '.ogg', '.opus', '.wma', '.aiff', '.ape', '.wv'}


def load_for_scope(path):
    """Decode once. Returns:
      play_frames  - (n,ch) int, ready for the audio device (24-bit pre-shifted)
      mono_norm    - float64 mono mix, normalized to [-1,1], for spectral analysis
      sr, ch, bits - format
      status       - 'MD5_OK' / 'NO_MD5' / 'MD5_FAIL' / 'RECOVERED: ...' / 'WAV' / 'DECODED (FLAC)' etc
      disk_size    - bytes on disk
    """
    ext = os.path.splitext(path)[1].lower()
    tmp_path = None
    try:
        if ext == '.alafc':
            pcm, ch, sr, bits, ok, damaged = alafc.decode_to_memory(path, verbose=False)
            if damaged:
                spots = ', '.join(f'{t:.0f}s' for _, t in damaged[:5])
                status = f'RECOVERED: {len(damaged)} seg near {spots}'
            elif ok is True:
                status = 'MD5_OK'
            elif ok is None:
                status = 'NO_MD5'
            else:
                status = 'MD5_FAIL'
            if ok is False and not damaged:
                raise RuntimeError('MD5 mismatch - файлът е повреден')
        elif ext == '.wav':
            pcm64, ch, sr, nf, bits, _ = alafc.wav_read(path)
            pcm = pcm64.astype(np.int16) if bits == 16 else pcm64.astype(np.int32)
            status = 'WAV'
        elif ext in AUDIO_EXTS:
            if shutil.which('ffmpeg') is None:
                raise RuntimeError('Нужен е ffmpeg:  winget install Gyan.FFmpeg')
            tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
            tmp.close(); tmp_path = tmp.name
            r = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', path,
                               '-acodec', 'pcm_s16le', tmp_path])
            if r.returncode != 0:
                raise RuntimeError('ffmpeg не можа да декодира файла')
            pcm64, ch, sr, nf, bits, _ = alafc.wav_read(tmp_path)
            pcm = pcm64.astype(np.int16)
            status = f'DECODED ({ext[1:].upper()})'
        else:
            raise RuntimeError('Поддържани: .alafc .wav .flac .mp3 .m4a и др.')

        frames = pcm.reshape(-1, ch)
        mono = frames.mean(axis=1).astype(np.float64)
        full_scale = float(2 ** (bits - 1))
        mono_norm = mono / full_scale

        play_frames = frames.copy()
        if bits == 24:
            play_frames = (play_frames.astype(np.int32) << 8)

        return play_frames, mono_norm, sr, ch, bits, status, os.path.getsize(path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

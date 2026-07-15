#!/usr/bin/env python3
# ALAFC Player - bit-perfect playback through ASIO / WASAPI
# Copyright (c) 2026 Axelrod. MIT License.
#
#   python alafc_player.py --list                       # show output devices
#   python alafc_player.py song.alafc                   # default device
#   python alafc_player.py song.alafc --device 14       # by index (ASIO device)
#   python alafc_player.py song.alafc --device "Topping"  # by name match (prefers ASIO)
#   python alafc_player.py song.alafc --exclusive --device 9   # WASAPI exclusive mode
#
# Also plays plain .wav files with the same path (handy for A/B tests).
import os, sys, argparse, time

# IMPORTANT: must be set BEFORE importing sounddevice, otherwise the
# non-ASIO PortAudio DLL is loaded (sounddevice >= 0.5.1 ships both).
os.environ.setdefault('SD_ENABLE_ASIO', '1')

import numpy as np
try:
    import sounddevice as sd
except ImportError:
    sys.exit('sounddevice is missing. Install it with:  pip install sounddevice')
import alafc


def list_devices():
    apis = sd.query_hostapis()
    have_asio = False
    print('Output devices:')
    for i, d in enumerate(sd.query_devices()):
        if d['max_output_channels'] < 1:
            continue
        api = apis[d['hostapi']]['name']
        mark = ''
        if 'ASIO' in api.upper():
            mark = '  <-- ASIO'
            have_asio = True
        print(f"  [{i:>3}] {d['name']}  ({api}, {d['max_output_channels']}ch, "
              f"{int(d['default_samplerate'])} Hz){mark}")
    if not have_asio:
        print('\nNo ASIO devices found. Either the DAC/interface ASIO driver is not '
              'installed, or this sounddevice version has no ASIO DLL '
              '(pip install --upgrade sounddevice). WASAPI exclusive (--exclusive) '
              'is a bit-perfect alternative.')


def pick_device(spec):
    if spec is None:
        return None
    try:
        return int(spec)
    except ValueError:
        pass
    apis = sd.query_hostapis()
    devs = sd.query_devices()
    cands = [i for i, d in enumerate(devs)
             if d['max_output_channels'] > 0 and spec.lower() in d['name'].lower()]
    if not cands:
        sys.exit(f'No output device matches "{spec}". Use --list to see devices.')
    for i in cands:  # prefer the ASIO entry of that device
        if 'ASIO' in apis[devs[i]['hostapi']]['name'].upper():
            return i
    return cands[0]


def load(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.alafc':
        print('Decoding', os.path.basename(path), '...')
        t0 = time.time()
        pcm, ch, sr, bits, ok, damaged = alafc.decode_to_memory(path, verbose=True)
        if damaged:
            spots = ', '.join(f'{t:.1f}s' for _, t in damaged[:8])
            status = f'RECOVERED - {len(damaged)} damaged segment(s) muted (near {spots})'
        elif ok is True:
            status = 'verified lossless (MD5 OK)'
        elif ok is None:
            status = 'older file - no embedded checksum'
        else:
            status = 'MD5 MISMATCH - file damaged!'
            print(f'Decoded in {time.time()-t0:.1f}s - {status}')
            sys.exit(1)
        print(f'Decoded in {time.time()-t0:.1f}s - {status}')
    elif ext == '.wav':
        pcm64, ch, sr, nf, bits, _ = alafc.wav_read(path)
        pcm = pcm64.astype(np.int16) if bits == 16 else pcm64.astype(np.int32)
    else:
        sys.exit('Supported: .alafc and .wav')
    return pcm.reshape(-1, ch), ch, sr, bits


def main():
    ap = argparse.ArgumentParser(description='ALAFC bit-perfect player (ASIO/WASAPI)')
    ap.add_argument('file', nargs='?', help='.alafc or .wav file')
    ap.add_argument('--list', action='store_true', help='list output devices')
    ap.add_argument('--device', help='device index or name substring (prefers ASIO)')
    ap.add_argument('--exclusive', action='store_true',
                    help='WASAPI exclusive mode (bit-perfect without ASIO)')
    args = ap.parse_args()

    if args.list or not args.file:
        list_devices()
        return

    frames, ch, sr, bits = load(args.file)
    if bits == 24:
        # 24-bit samples left-justified in 32-bit words - the standard
        # bit-perfect representation ASIO/WASAPI drivers expect.
        frames = (frames.astype(np.int32) << 8)

    dev = pick_device(args.device)
    extra = sd.WasapiSettings(exclusive=True) if args.exclusive else None
    dur = len(frames) / sr
    dname = sd.query_devices(dev)['name'] if dev is not None else 'default output'
    mode = 'WASAPI exclusive' if args.exclusive else ('ASIO' if dev is not None and
           'ASIO' in sd.query_hostapis(sd.query_devices(dev)['hostapi'])['name'].upper()
           else 'shared')
    print(f'> {os.path.basename(args.file)}  |  {sr} Hz / {bits}-bit / {ch}ch / '
          f'{int(dur)//60}:{int(dur)%60:02d}  |  {dname}  |  {mode}, bit-perfect, no DSP')

    try:
        sd.play(frames, sr, device=dev, extra_settings=extra)
        sd.wait()
        print('Done.')
    except KeyboardInterrupt:
        sd.stop()
        print('\nStopped.')
    except sd.PortAudioError as e:
        print('\nPortAudio error:', e)
        print('Tips: the sample rate must be supported by the device '
              '(set it in the ASIO/driver control panel), and no other app '
              'should hold the device in exclusive mode.')
        sys.exit(1)


if __name__ == '__main__':
    main()

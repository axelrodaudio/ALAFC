#!/usr/bin/env python3
# ALAFC Converter
# Copyright (c) 2026 Axelrod. MIT License.
#
#   python alafc_convert.py song.wav                # -> song.alafc
#   python alafc_convert.py song.flac               # -> song.alafc (via ffmpeg)
#   python alafc_convert.py song.alafc              # -> song.wav
#   python alafc_convert.py song.flac out.alafc     # explicit output name
#   python alafc_convert.py --folder "D:\\Music\\Album"   # all wav+flac in folder
#
# Non-WAV inputs need ffmpeg in PATH:  winget install Gyan.FFmpeg
import sys, os, json, argparse, subprocess, shutil, tempfile
import alafc

AUDIO_EXTS = {'.flac', '.mp3', '.m4a', '.aac', '.ogg', '.opus', '.wma', '.aiff', '.ape', '.wv'}


def need_ffmpeg():
    if shutil.which('ffmpeg') is None:
        sys.exit('This input format needs ffmpeg. Install it with:\n'
                 '  winget install Gyan.FFmpeg\nthen open a new terminal.')


def probe_bits(path):
    """Decide target bit depth (16 or 24) for a non-WAV source."""
    if shutil.which('ffprobe') is None:
        return 16
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
             'stream=bits_per_raw_sample,bits_per_sample,sample_fmt,channels',
             '-of', 'json', path],
            capture_output=True, text=True, timeout=30)
        st = json.loads(r.stdout)['streams'][0]
        if int(st.get('channels', 2)) > 2:
            sys.exit('Only mono/stereo sources are supported.')
        braw = int(st.get('bits_per_raw_sample') or st.get('bits_per_sample') or 0)
        if braw >= 17:
            return 24
        if braw > 0:
            return 16
        fmt = st.get('sample_fmt', '')
        return 24 if fmt.startswith('s32') else 16
    except Exception:
        return 16


def to_alafc(src, out):
    ext = os.path.splitext(src)[1].lower()
    src_size = os.path.getsize(src)
    if ext == '.wav':
        wav = src
        tmp = None
    else:
        need_ffmpeg()
        bits = probe_bits(src)
        codec = 'pcm_s24le' if bits == 24 else 'pcm_s16le'
        tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        tmp.close()
        wav = tmp.name
        print(f'  ffmpeg: {os.path.basename(src)} -> {bits}-bit WAV')
        r = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', src,
                            '-vn', '-acodec', codec, wav])
        if r.returncode != 0:
            sys.exit('ffmpeg failed to decode the input.')
    try:
        alafc.encode(wav, out, verbose=True)
    finally:
        if tmp is not None:
            os.unlink(wav)
    print(f'  {os.path.basename(src)} ({src_size} B) -> '
          f'{os.path.basename(out)} ({os.path.getsize(out)} B, '
          f'{100*os.path.getsize(out)/src_size:.1f}% of source)')


def from_alafc(src, out):
    alafc.decode(src, out)
    print(f'  {os.path.basename(src)} -> {os.path.basename(out)}')


def convert_one(src, out=None):
    ext = os.path.splitext(src)[1].lower()
    if ext == '.alafc':
        from_alafc(src, out or os.path.splitext(src)[0] + '.wav')
    elif ext == '.wav' or ext in AUDIO_EXTS:
        to_alafc(src, out or os.path.splitext(src)[0] + '.alafc')
    else:
        sys.exit(f'Unsupported extension: {ext}')


def main():
    ap = argparse.ArgumentParser(description='ALAFC converter')
    ap.add_argument('input', nargs='?', help='input file')
    ap.add_argument('output', nargs='?', help='output file (optional)')
    ap.add_argument('--folder', help='convert every .wav/.flac in a folder to .alafc')
    args = ap.parse_args()

    if args.folder:
        files = sorted(f for f in os.listdir(args.folder)
                       if os.path.splitext(f)[1].lower() in ('.wav', '.flac'))
        if not files:
            sys.exit('No .wav/.flac files found in that folder.')
        total_in = total_out = 0
        for i, f in enumerate(files, 1):
            src = os.path.join(args.folder, f)
            out = os.path.splitext(src)[0] + '.alafc'
            print(f'[{i}/{len(files)}] {f}')
            to_alafc(src, out)
            total_in += os.path.getsize(src); total_out += os.path.getsize(out)
        print(f'\nTotal: {total_in} B -> {total_out} B '
              f'({100*total_out/total_in:.1f}% of source files)')
        return
    if not args.input:
        ap.print_help(); sys.exit(1)
    convert_one(args.input, args.output)


if __name__ == '__main__':
    main()

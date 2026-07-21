#!/usr/bin/env python3
# ALAFC 1.2.0 vs 1.3.0 benchmark.
#
# Before replacing alafc.py, keep the old one as alafc_120.py:
#   copy C:\ALAFC\alafc.py C:\ALAFC\alafc_120.py
# Then put the new alafc.py + this script in C:\ALAFC and run:
#   python bench_alafc_130.py song.wav
#   python bench_alafc_130.py demos\demo_music.alafc      (decodes it first)
#
# Peak-RAM probe (run each in a FRESH process, then compare the two numbers):
#   python bench_alafc_130.py mem120 song.wav
#   python bench_alafc_130.py mem130 song.wav [fast|normal|max]
import os, sys, time, tempfile

def _need(mod):
    try:
        return __import__(mod)
    except ImportError:
        print(f'ERROR: {mod}.py not found next to this script.')
        if mod == 'alafc_120':
            print('Run:  copy alafc.py alafc_120.py   BEFORE replacing alafc.py')
        sys.exit(1)

def peak_mb():
    try:
        import ctypes
        from ctypes import wintypes as wt
        class PMC(ctypes.Structure):
            _fields_ = [('cb', wt.DWORD), ('PageFaultCount', wt.DWORD),
                        ('PeakWorkingSetSize', ctypes.c_size_t),
                        ('WorkingSetSize', ctypes.c_size_t),
                        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                        ('PagefileUsage', ctypes.c_size_t),
                        ('PeakPagefileUsage', ctypes.c_size_t)]
        pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
        ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.PeakWorkingSetSize / 2**20
    except Exception:
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:
            return None

def as_wav(path, alafc_new):
    if not path.lower().endswith('.alafc'):
        return path
    tmp = os.path.join(tempfile.gettempdir(), 'alafc_bench_src.wav')
    print(f'decoding {os.path.basename(path)} -> temp WAV...')
    alafc_new.decode(path, tmp, verbose=False)
    return tmp

def main():
    args = sys.argv[1:]
    if args and args[0] in ('mem120', 'mem130'):
        which = args[0]; src = args[1]
        prof = args[2] if len(args) > 2 else 'max'
        mod = _need('alafc_120') if which == 'mem120' else _need('alafc')
        src = as_wav(src, _need('alafc'))
        out = os.path.join(tempfile.gettempdir(), 'alafc_bench_mem.alafc')
        t0 = time.perf_counter()
        if which == 'mem120':
            mod.encode(src, out, verbose=False, self_verify=False)
        else:
            mod.encode(src, out, verbose=False, self_verify=False,
                       profile=prof)
        dt = time.perf_counter() - t0
        pk = peak_mb()
        label = '1.2.0' if which == 'mem120' else f'1.3.0 {prof}'
        pk_txt = f'{pk:.0f} MB' if pk is not None else 'n/a'
        print(f'{label}: encode {dt:.1f}s, peak RAM {pk_txt}')
        return
    if not args:
        print(__doc__ or 'usage: bench_alafc_130.py song.wav'); sys.exit(1)

    old = _need('alafc_120')
    new = _need('alafc')
    src = as_wav(args[0], new)
    import wave
    with wave.open(src, 'rb') as w:
        secs = w.getnframes() / w.getframerate()
        raw = os.path.getsize(src)
        desc = (f'{w.getframerate()} Hz / {w.getsampwidth()*8}-bit / '
                f'{w.getnchannels()}ch / {secs:.0f}s')
    print(f'source: {os.path.basename(src)}  ({desc})')
    print(f'engine: {new._engine_note}\n')
    tmp = tempfile.gettempdir()

    rows = []
    f120 = os.path.join(tmp, 'b_120.alafc')
    t0 = time.perf_counter(); old.encode(src, f120, verbose=False, self_verify=False)
    te = time.perf_counter() - t0
    rows.append(('1.2.0 (old)', te, os.path.getsize(f120)))
    files = {'1.2.0': f120}
    for prof in ('fast', 'normal', 'max'):
        fp = os.path.join(tmp, f'b_130_{prof}.alafc')
        t0 = time.perf_counter()
        new.encode(src, fp, verbose=False, self_verify=False, profile=prof)
        te = time.perf_counter() - t0
        rows.append((f'1.3.0 {prof}', te, os.path.getsize(fp)))
        files[prof] = fp

    print(f'{"ENCODE":14s} {"time":>8s} {"x realtime":>11s} {"size":>12s} {"% of WAV":>9s}')
    for name, te, sz in rows:
        print(f'{name:14s} {te:8.1f}s {secs/te:10.1f}x {sz:12,d} {100*sz/raw:8.1f}%')

    print(f'\n{"DECODE":34s} {"time":>8s} {"x realtime":>11s}')
    t0 = time.perf_counter(); r = old.decode_to_memory(f120, verbose=False)
    td = time.perf_counter() - t0
    ok_old = r[4]
    print(f'{"1.2.0 code, 1.2.0 file":34s} {td:8.1f}s {secs/td:10.1f}x')
    for label, fp in (('1.3.0 code, 1.2.0 file', f120),
                      ('1.3.0 code, 1.3.0 max file', files['max']),
                      ('1.3.0 code, 1.3.0 fast file', files['fast'])):
        t0 = time.perf_counter(); r = new.decode_to_memory(fp, verbose=False)
        td = time.perf_counter() - t0
        tag = '' if r[4] is True else '  [MD5 CHECK FAILED!]'
        print(f'{label:34s} {td:8.1f}s {secs/td:10.1f}x{tag}')

    print('\nCROSS-VERSION COMPATIBILITY')
    r = old.decode_to_memory(files['fast'], verbose=False)
    print(f'  old 1.2.0 code reads new fast file: '
          f'{"MD5 OK" if r[4] is True else "FAILED"}')
    r = old.decode_to_memory(files['max'], verbose=False)
    print(f'  old 1.2.0 code reads new max  file: '
          f'{"MD5 OK" if r[4] is True else "FAILED"}')
    r = new.decode_to_memory(f120, verbose=False)
    print(f'  new 1.3.0 code reads old 1.2.0 file: '
          f'{"MD5 OK" if r[4] is True else "FAILED"}')
    print('\n(encode timed with self-verify off in both versions; '
          'default encoding still verifies)')

if __name__ == '__main__':
    main()

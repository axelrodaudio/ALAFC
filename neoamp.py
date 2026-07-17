#!/usr/bin/env python3
# NeoAmp - a bit-perfect lossless player powered by the ALAFC codec.
# Copyright (c) 2026 Axelrod. MIT License.
# Play / Pause / Seek / Playlist. Bit-perfect: ASIO or WASAPI exclusive, no DSP.
# Winamp-style LCD skin. The "EQ-like" bars are a REAL live spectrum reader
# (FFT of the decoded audio) - display only, nothing is applied to the sound,
# so bit-perfect output is never touched.
#   Start:  py neoamp.py   (or double-click NeoAmp.exe once built)
import os, sys, threading, queue, time, wave
os.environ.setdefault('SD_ENABLE_ASIO', '1')   # before importing sounddevice!
import numpy as np
try:
    import sounddevice as sd
except ImportError:
    sys.exit('sounddevice is missing. Install it with:  pip install sounddevice')
import alafc
import alafc_gui_logic as L


class Engine:
    """Audio engine: decoded track in memory, movable play position."""
    def __init__(self):
        self.stream = None
        self.frames = None          # (n, ch) int16/int32 - playback data (24-bit shifted)
        self.sr = 0; self.ch = 0; self.bits = 16
        self.pos = 0
        self.playing = False
        self.ended = False
        self.lock = threading.Lock()

    def _cb(self, out, nframes, t, status):
        with self.lock:
            if not self.playing or self.frames is None:
                out[:] = 0
                return
            n = len(self.frames)
            p = self.pos
            end = min(p + nframes, n)
            take = end - p
            out[:take] = self.frames[p:end]
            if take < nframes:
                out[take:] = 0
            self.pos = end
            if end >= n:
                self.playing = False
                self.ended = True

    def load(self, frames, sr, ch, bits):
        with self.lock:
            self.frames = frames; self.sr = sr; self.ch = ch; self.bits = bits
            self.pos = 0; self.playing = False; self.ended = False

    def open_stream(self, device, exclusive):
        self.close_stream()
        dtype = 'int16' if self.bits == 16 else 'int32'
        extra = sd.WasapiSettings(exclusive=True) if exclusive else None
        self.stream = sd.OutputStream(samplerate=self.sr, channels=self.ch,
                                      dtype=dtype, device=device,
                                      callback=self._cb, extra_settings=extra)
        self.stream.start()

    def close_stream(self):
        if self.stream is not None:
            try:
                self.stream.stop(); self.stream.close()
            except Exception:
                pass
            self.stream = None

    def play(self):
        with self.lock:
            if self.frames is not None:
                if self.pos >= len(self.frames):
                    self.pos = 0
                self.ended = False
                self.playing = True

    def pause(self):
        with self.lock:
            self.playing = False

    def stop(self):
        with self.lock:
            self.playing = False; self.pos = 0; self.ended = False

    def seek_seconds(self, sec):
        with self.lock:
            if self.frames is None: return
            self.pos = max(0, min(int(sec * self.sr), len(self.frames)))
            self.ended = False

    def position_seconds(self):
        with self.lock:
            return (self.pos / self.sr) if self.sr else 0.0

    def duration_seconds(self):
        with self.lock:
            return (len(self.frames) / self.sr) if self.frames is not None and self.sr else 0.0


def to_playable(pcm, ch, bits):
    frames = pcm.reshape(-1, ch)
    if bits == 24:
        frames = (frames.astype(np.int32) << 8)   # 24-in-32, bit-perfect
    return frames


def load_track(path):
    """Decode .alafc or read .wav.
    Returns (play_frames, meter_frames, sr, ch, bits, status_code).
    play_frames: what goes to the audio device (24-bit pre-shifted for ASIO/WASAPI).
    meter_frames: true-scale PCM, used only for the on-screen spectrum/info - never
    touches playback, so the bit-perfect audio path is unaffected either way."""
    ext = os.path.splitext(path)[1].lower()
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
    else:
        raise RuntimeError('Поддържани: .alafc и .wav')
    meter_frames = pcm.reshape(-1, ch)
    play_frames = to_playable(pcm, ch, bits)
    return play_frames, meter_frames, sr, ch, bits, status


def peek_duration(path):
    """Fast header-only peek, for playlist duration labels - no full decode."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == '.alafc':
            data = open(path, 'rb').read(65536)
            br = alafc.BitReader(data)
            ver, use_ms, ch, bits, sr, nf, block, part, stages, md5, segb, seg_modes = \
                alafc._read_header(br)
            return nf / sr if sr else None
        if ext == '.wav':
            with wave.open(path, 'rb') as w:
                return w.getnframes() / w.getframerate()
    except Exception:
        pass
    return None


def output_devices():
    apis = sd.query_hostapis()
    devs = []
    for i, d in enumerate(sd.query_devices()):
        if d['max_output_channels'] > 0:
            api = apis[d['hostapi']]['name']
            devs.append((i, d['name'], api, 'ASIO' in api.upper()))
    return devs


def fmt_time(t):
    t = max(0, int(t))
    return f'{t // 60}:{t % 60:02d}'


# ------------------------------------------------------------------ colours
BG        = '#0d0d0d'
LCD_BG    = '#050a05'
PANEL_BG  = '#0e130e'
GREEN     = '#39ff14'
GREEN_DIM = '#1e8f0d'
GREEN_MID = '#2bd40f'
AMBER     = '#ffb340'
RED       = '#ff5f57'
BLUE_SEL  = '#1656c9'
MONO      = ('Consolas', 10)
MONO_SM   = ('Consolas', 9)
MONO_BIG  = ('Consolas', 24, 'bold')
MONO_TTL  = ('Consolas', 12, 'bold')


def main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    eng = Engine()
    jobs = queue.Queue()
    playlist = []                    # list of file paths
    current = {'idx': -1, 'loading': False, 'meter_frames': None, 'bits': 16}
    dragging = {'on': False}
    marquee = {'x': 0.0}
    smooth = {'v': np.zeros(L.N_BANDS)}

    root = tk.Tk()
    root.title('NeoAmp')
    root.configure(bg=BG)
    root.geometry('640x700')
    root.minsize(560, 640)

    style = ttk.Style(root)
    try:
        style.theme_use('clam')
    except Exception:
        pass
    style.configure('Dark.TCombobox', fieldbackground='#141414', background='#141414',
                    foreground=GREEN, arrowcolor=GREEN, bordercolor=GREEN_DIM,
                    lightcolor='#141414', darkcolor='#141414')
    style.map('Dark.TCombobox', fieldbackground=[('readonly', '#141414')],
              selectbackground=[('readonly', '#141414')],
              selectforeground=[('readonly', GREEN)])

    # ================================================================ header
    header = tk.Frame(root, bg=BG)
    header.pack(fill='x', padx=10, pady=(10, 4))
    tk.Label(header, text='N E O A M P', bg=BG, fg=GREEN,
             font=('Consolas', 14, 'bold')).pack(side='left')
    tk.Label(header, text=' · ALAFC engine', bg=BG, fg=GREEN_DIM,
             font=MONO_SM).pack(side='left', pady=(4, 0))

    devs = output_devices()
    names = [f"[{i}] {n} ({a})" + ('  *ASIO' if asio else '') for i, n, a, asio in devs]
    dev_var = tk.StringVar()
    combo = ttk.Combobox(header, textvariable=dev_var, values=names, state='readonly',
                         style='Dark.TCombobox', width=26, font=MONO_SM)
    combo.pack(side='right')
    def_idx = 0
    found = False
    for j, (i, n, a, asio) in enumerate(devs):
        if asio and 'fiio' in n.lower():
            def_idx = j; found = True; break
    if not found:
        for j, (i, n, a, asio) in enumerate(devs):
            if asio:
                def_idx = j; break
    if names:
        combo.current(def_idx)
    excl_var = tk.BooleanVar(value=False)
    tk.Checkbutton(header, text='WASAPI excl.', variable=excl_var, bg=BG, fg=GREEN_DIM,
                   selectcolor='#141414', activebackground=BG, font=MONO_SM,
                   activeforeground=GREEN).pack(side='right', padx=8)

    def chosen_device():
        j = combo.current()
        if j < 0:
            return None, False
        i, n, a, asio = devs[j]
        return i, (not asio) and excl_var.get()

    # ================================================================ LCD panel
    lcd = tk.Frame(root, bg=LCD_BG, highlightthickness=1, highlightbackground=GREEN_DIM)
    lcd.pack(fill='x', padx=10, pady=4)

    top_row = tk.Frame(lcd, bg=LCD_BG)
    top_row.pack(fill='x', padx=10, pady=(10, 2))
    time_var = tk.StringVar(value='0:00')
    tk.Label(top_row, textvariable=time_var, bg=LCD_BG, fg=GREEN, font=MONO_BIG,
             width=6, anchor='w').pack(side='left')

    title_wrap = tk.Frame(top_row, bg=LCD_BG)
    title_wrap.pack(side='left', fill='both', expand=True, padx=(10, 0))
    marquee_canvas = tk.Canvas(title_wrap, bg=LCD_BG, height=24, highlightthickness=0)
    marquee_canvas.pack(fill='x', pady=(4, 0))
    marquee_item = marquee_canvas.create_text(4, 12, text='— NO TRACK LOADED —', anchor='w',
                                              fill=GREEN, font=MONO_TTL)

    line1_var = tk.StringVar(value='')
    line2_var = tk.StringVar(value='')
    tk.Label(lcd, textvariable=line1_var, bg=LCD_BG, fg=GREEN_MID, font=MONO_SM,
             anchor='w').pack(fill='x', padx=10, pady=(4, 0))
    tk.Label(lcd, textvariable=line2_var, bg=LCD_BG, fg=GREEN_DIM, font=MONO_SM,
             anchor='w').pack(fill='x', padx=10)

    status_var = tk.StringVar(value='')
    status_label = tk.Label(lcd, textvariable=status_var, bg=LCD_BG, fg=GREEN,
                            font=('Consolas', 9, 'bold'), anchor='w')
    status_label.pack(fill='x', padx=10, pady=(2, 8))

    # ---- live spectrum (real FFT of the decoded audio - display only) ----
    BARS_H = 44
    bars_canvas = tk.Canvas(lcd, height=BARS_H, bg=LCD_BG, highlightthickness=0)
    bars_canvas.pack(fill='x', padx=10, pady=(0, 10))
    bar_geo = {'rects': []}

    def layout_bars(event=None):
        bars_canvas.delete('bar')
        bar_geo['rects'] = []
        w = bars_canvas.winfo_width() or 600
        n = L.N_BANDS
        gap = 3
        bw = max(2.0, (w - gap * (n - 1)) / n)
        for i in range(n):
            x0 = i * (bw + gap)
            rid = bars_canvas.create_rectangle(x0, BARS_H, x0 + bw, BARS_H,
                                               fill=GREEN_DIM, outline='', tags='bar')
            bar_geo['rects'].append((rid, x0, bw))
    bars_canvas.bind('<Configure>', layout_bars)

    def draw_bars(levels):
        for (rid, x0, bw), lv in zip(bar_geo['rects'], levels):
            y0 = BARS_H - lv * BARS_H
            color = GREEN if lv < 0.75 else (AMBER if lv < 0.92 else RED)
            bars_canvas.coords(rid, x0, y0, x0 + bw, BARS_H)
            bars_canvas.itemconfig(rid, fill=color)

    # ================================================================ transport
    ctl = tk.Frame(root, bg=BG)
    ctl.pack(pady=8)

    def mkbtn(txt, cmd, w=4):
        return tk.Button(ctl, text=txt, command=cmd, font=('Consolas', 13, 'bold'),
                         width=w, bg='#141414', fg=GREEN, activebackground='#1f1f1f',
                         activeforeground=GREEN, relief='flat', bd=0,
                         highlightthickness=1, highlightbackground=GREEN_DIM,
                         cursor='hand2')

    # ================================================================ seek bar
    seekrow = tk.Frame(root, bg=BG)
    seekrow.pack(fill='x', padx=16, pady=(0, 4))
    scale = ttk.Scale(seekrow, from_=0, to=100, orient='horizontal')
    scale.pack(fill='x')

    def on_release(e):
        dragging['on'] = False
        if eng.duration_seconds() > 0:
            eng.seek_seconds(float(scale.get()))
    scale.bind('<ButtonPress-1>', lambda e: dragging.update(on=True))
    scale.bind('<ButtonRelease-1>', on_release)

    # ================================================================ playlist
    pl_wrap = tk.Frame(root, bg=BG)
    pl_wrap.pack(fill='both', expand=True, padx=10, pady=(4, 10))
    tk.Label(pl_wrap, text='PLAYLIST', bg=BG, fg=GREEN_DIM,
             font=('Consolas', 9, 'bold')).pack(anchor='w')
    pl_frame = tk.Frame(pl_wrap, bg=PANEL_BG, highlightthickness=1,
                        highlightbackground=GREEN_DIM)
    pl_frame.pack(fill='both', expand=True, pady=(2, 0))
    lb = tk.Listbox(pl_frame, bg=PANEL_BG, fg=GREEN_MID, selectbackground=BLUE_SEL,
                    selectforeground='#ffffff', activestyle='none', bd=0,
                    highlightthickness=0, font=MONO)
    lb.pack(side='left', fill='both', expand=True, padx=6, pady=6)
    sb = tk.Scrollbar(pl_frame, command=lb.yview)
    sb.pack(side='right', fill='y')
    lb.config(yscrollcommand=sb.set)

    btnrow = tk.Frame(pl_wrap, bg=BG)
    btnrow.pack(fill='x', pady=(4, 0))

    def add_files():
        paths = filedialog.askopenfilenames(
            title='Добави музика',
            filetypes=[('ALAFC / WAV', '*.alafc *.wav'), ('Всички', '*.*')])
        for p in paths:
            playlist.append(p)
            idx = len(playlist)
            dur = peek_duration(p)
            label = f'{idx:>2}. {os.path.basename(p)}'
            if dur:
                label += f'   [{fmt_time(dur)}]'
            lb.insert('end', label)

    def clear_list():
        eng.pause()
        playlist.clear()
        lb.delete(0, 'end')
        current.update(idx=-1, meter_frames=None)
        time_var.set('0:00')
        marquee_canvas.itemconfig(marquee_item, text='— NO TRACK LOADED —')
        line1_var.set(''); line2_var.set(''); status_var.set('')
        draw_bars(np.zeros(L.N_BANDS))

    tk.Button(btnrow, text='+ Добави файлове', command=add_files,
             bg='#141414', fg=GREEN, activebackground='#1f1f1f',
             activeforeground=GREEN, relief='flat', bd=0,
             highlightthickness=1, highlightbackground=GREEN_DIM,
             font=MONO_SM, cursor='hand2').pack(side='left')
    tk.Button(btnrow, text='Изчисти', command=clear_list,
             bg='#141414', fg=GREEN_DIM, activebackground='#1f1f1f',
             activeforeground=GREEN, relief='flat', bd=0,
             highlightthickness=1, highlightbackground=GREEN_DIM,
             font=MONO_SM, cursor='hand2').pack(side='left', padx=6)
    tk.Label(btnrow, text='bit-perfect · силата се контролира от копчето на DAC-а',
             bg=BG, fg=GREEN_DIM, font=('Consolas', 8)).pack(side='right')

    # ================================================================ playback control
    current_info = [None]   # boxed, set by tick() on load

    def apply_status_colour():
        text, level = L.status_readout(current_info[0]) if current_info[0] else ('', 'na')
        status_var.set(text)
        status_label.config(fg={'ok': GREEN, 'warn': AMBER, 'err': RED, 'na': GREEN_DIM}[level])

    def start_track(idx):
        if not (0 <= idx < len(playlist)) or current['loading']:
            return
        current['idx'] = idx
        current['loading'] = True
        lb.selection_clear(0, 'end')
        for i in range(lb.size()):
            lb.itemconfig(i, bg=PANEL_BG, fg=GREEN_MID)
        lb.itemconfig(idx, bg=BLUE_SEL, fg='#ffffff')
        lb.see(idx)
        path = playlist[idx]
        marquee_canvas.itemconfig(marquee_item, text=os.path.basename(path) + '  —  зареждане...')
        line1_var.set(''); line2_var.set('')
        status_var.set('Декодиране...'); status_label.config(fg=GREEN_DIM)

        def work():
            try:
                play_f, meter_f, sr, ch, bits, status = load_track(path)
                jobs.put(('loaded', path, play_f, meter_f, sr, ch, bits, status))
            except Exception as ex:
                jobs.put(('error', str(ex)))
        threading.Thread(target=work, daemon=True).start()

    def play_pause():
        if eng.frames is None:
            sel = lb.curselection()
            start_track(sel[0] if sel else 0)
            return
        if eng.playing:
            eng.pause()
        else:
            eng.play()

    def next_track(auto=False):
        if not playlist:
            return
        i = current['idx'] + 1
        if i < len(playlist):
            start_track(i)
        elif not auto:
            start_track(0)

    def prev_track():
        if not playlist:
            return
        if eng.position_seconds() > 3:
            eng.seek_seconds(0)
            return
        start_track(max(0, current['idx'] - 1))

    mkbtn('|◄◄', prev_track).pack(side='left', padx=3)
    playbtn = mkbtn('▶', play_pause)
    playbtn.pack(side='left', padx=3)
    mkbtn('■', lambda: eng.stop()).pack(side='left', padx=3)
    mkbtn('►►|', lambda: next_track(False)).pack(side='left', padx=3)

    lb.bind('<Double-Button-1>',
           lambda e: start_track(lb.curselection()[0]) if lb.curselection() else None)

    def on_close():
        eng.close_stream()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', on_close)

    # ================================================================ tick loop
    def tick():
        try:
            while True:
                job = jobs.get_nowait()
                if job[0] == 'loaded':
                    _, path, play_f, meter_f, sr, ch, bits, status = job
                    try:
                        eng.load(play_f, sr, ch, bits)
                        dev, excl = chosen_device()
                        eng.open_stream(dev, excl)
                        eng.play()
                        current['meter_frames'] = meter_f
                        current['bits'] = bits
                        info = L.track_info(path, meter_f, sr, ch, bits, status)
                        current_info[0] = info
                        l1, l2 = L.format_readout_lines(info)
                        line1_var.set(l1); line2_var.set(l2)
                        apply_status_colour()
                        marquee_canvas.itemconfig(marquee_item,
                                                  text=info['title'].upper())
                        marquee['x'] = 0.0
                        scale.configure(to=max(1.0, eng.duration_seconds()))
                    except Exception as ex:
                        status_var.set('Грешка при отваряне на устройството')
                        status_label.config(fg=RED)
                        messagebox.showerror('Аудио устройство',
                            f'{ex}\n\nСъвет: провери дали друга програма не държи '
                            'DAC-а, или пробвай друго устройство от списъка.')
                    current['loading'] = False
                elif job[0] == 'error':
                    status_var.set('Грешка: ' + job[1])
                    status_label.config(fg=RED)
                    current['loading'] = False
        except queue.Empty:
            pass

        dur = eng.duration_seconds()
        if dur > 0 and not dragging['on']:
            scale.set(eng.position_seconds())
        time_var.set(fmt_time(eng.position_seconds()))
        playbtn.config(text='||' if eng.playing else '▶')

        # marquee scroll
        bbox = marquee_canvas.bbox(marquee_item)
        if bbox:
            tw = bbox[2] - bbox[0]
            cw = marquee_canvas.winfo_width() or 1
            if tw > cw:
                marquee['x'] -= 1.4
                if marquee['x'] < -(tw + 50):
                    marquee['x'] = 0.0
                marquee_canvas.coords(marquee_item, 4 + marquee['x'], 12)
            else:
                marquee_canvas.coords(marquee_item, 4, 12)

        # live spectrum
        if eng.playing and current['meter_frames'] is not None:
            lv = L.meter_levels(current['meter_frames'], eng.ch, eng.pos, eng.sr,
                                bits=current['bits'])
        else:
            lv = np.zeros(L.N_BANDS)
        smooth['v'] = smooth['v'] * 0.6 + lv * 0.4
        draw_bars(smooth['v'])

        if eng.ended:
            eng.ended = False
            next_track(auto=True)
        root.after(50, tick)

    for p in sys.argv[1:]:
        if os.path.exists(p):
            playlist.append(p)
            dur = peek_duration(p)
            label = f'{len(playlist):>2}. {os.path.basename(p)}'
            if dur:
                label += f'   [{fmt_time(dur)}]'
            lb.insert('end', label)
    if playlist:
        start_track(0)

    root.after(100, layout_bars)
    root.after(120, tick)
    root.mainloop()


if __name__ == '__main__':
    main()

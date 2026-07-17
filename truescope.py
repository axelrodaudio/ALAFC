#!/usr/bin/env python3
# TrueScope - lossless authenticity scope + player, powered by ALAFC.
# Copyright (c) 2026 Axelrod. MIT License.
#
# Shows the REAL measured frequency response of a track (not a decorative
# visualizer) and marks a detected lossy brick-wall cutoff if one is found -
# the same technique as Adobe Audition's Frequency Analysis panel, Spek, or
# TAU. The analysis functions are the ones already validated against real
# MP3 (64/128/192/320 kbps), AAC, Opus and genuine full-spectrum material
# earlier in this project (see alafc_lossless_tester.py) - this app reuses
# them as-is rather than re-implementing detection logic, so results here
# match that tool exactly.
#
# Honesty notes (please read):
#  - A steep, sustained cutoff well below Nyquist is a reliable sign of a
#    lossy source (works very well for MP3). A soft/gradual rolloff can be
#    either a well-encoded lossy file (some AAC/Opus) OR a natural
#    recording - the graph is shown so you can judge that yourself; the
#    verdict text is a strong hint, not a certificate.
#  - "Probable source" bitrate guesses are nearest-signature heuristics
#    based on the measured cutoff frequency, not a certain answer.
#
#   Start:  py truescope.py   (or double-click TrueScope.exe once built)
import os, sys, threading, queue
os.environ.setdefault('SD_ENABLE_ASIO', '1')   # before importing sounddevice!
import numpy as np
try:
    import sounddevice as sd
except ImportError:
    sys.exit('sounddevice is missing. Install it with:  pip install sounddevice')

from truescope_loader import load_for_scope, AUDIO_EXTS
from alafc_lossless_tester import spectrogram, analyze_cutoff, classify, SIGNATURES


class Engine:
    """Audio engine: decoded track in memory, movable play position."""
    def __init__(self):
        self.stream = None
        self.frames = None
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


VERDICT_TAG = {'GENUINE': 'GENUINE', 'FAKE': 'FAKE', 'UNCLEAR': 'UNCLEAR'}
VERDICT_COLOR = {'GENUINE': '#39ff14', 'FAKE': '#ff5f57', 'UNCLEAR': '#ffb340'}

BG = '#15181b'
PANEL_BG = '#0e1114'
FIG_BG = '#101316'
AXES_BG = '#0a0c0e'
FG = '#d6dbe0'
DIM = '#7a828a'
ACCENT = '#39ff14'
CUTOFF_C = '#ff5f57'
PIN_C = '#6a7078'
BLUE_SEL = '#1656c9'
MONO = ('Consolas', 10)
MONO_SM = ('Consolas', 9)


def main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

    eng = Engine()
    jobs = queue.Queue()
    entries = []              # list of dicts: {path, verdict, cutoff, analyzing}
    current = {'idx': -1, 'loading': False}
    dragging = {'on': False}
    pinned = {'data': None, 'label': None, 'bits': None}   # (freqs, profile_db, label) or None

    root = tk.Tk()
    root.title('TrueScope')
    root.configure(bg=BG)
    root.geometry('700x760')
    root.minsize(600, 680)

    style = ttk.Style(root)
    try:
        style.theme_use('clam')
    except Exception:
        pass
    style.configure('Dark.TCombobox', fieldbackground='#1c2024', background='#1c2024',
                    foreground=FG, arrowcolor=FG, bordercolor='#33383e')
    style.map('Dark.TCombobox', fieldbackground=[('readonly', '#1c2024')])

    # ================================================================ header
    header = tk.Frame(root, bg=BG)
    header.pack(fill='x', padx=10, pady=(10, 4))
    tk.Label(header, text='TrueScope', bg=BG, fg=FG,
             font=('Consolas', 14, 'bold')).pack(side='left')
    tk.Label(header, text='  authenticity scope · ALAFC engine', bg=BG, fg=DIM,
             font=MONO_SM).pack(side='left', pady=(3, 0))

    devs = output_devices()
    names = [f"[{i}] {n} ({a})" + ('  *ASIO' if asio else '') for i, n, a, asio in devs]
    dev_var = tk.StringVar()
    combo = ttk.Combobox(header, textvariable=dev_var, values=names, state='readonly',
                         style='Dark.TCombobox', width=24, font=MONO_SM)
    combo.pack(side='right')
    def_idx = 0; found = False
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
    tk.Checkbutton(header, text='WASAPI excl.', variable=excl_var, bg=BG, fg=DIM,
                   selectcolor='#1c2024', activebackground=BG, font=MONO_SM,
                   activeforeground=FG).pack(side='right', padx=8)

    def chosen_device():
        j = combo.current()
        if j < 0: return None, False
        i, n, a, asio = devs[j]
        return i, (not asio) and excl_var.get()

    # ================================================================ frequency graph
    graph_wrap = tk.Frame(root, bg=PANEL_BG, highlightthickness=1, highlightbackground='#2a2f34')
    graph_wrap.pack(fill='both', expand=True, padx=10, pady=4)
    tk.Label(graph_wrap, text='FREQUENCY ANALYSIS', bg=PANEL_BG, fg=DIM,
             font=('Consolas', 8, 'bold')).pack(anchor='w', padx=8, pady=(6, 0))

    fig = Figure(figsize=(6, 3.1), dpi=100, facecolor=FIG_BG)
    ax = fig.add_subplot(111)
    ax.set_facecolor(AXES_BG)
    ax.tick_params(colors=DIM, labelsize=8)
    for sp in ax.spines.values():
        sp.set_color('#2a2f34')
    ax.set_xlabel('Hz (log)', color=DIM, fontsize=8)
    ax.set_ylabel('dBFS', color=DIM, fontsize=8)
    ax.grid(True, color='#22262a', linewidth=0.6, which='both')

    # Real audiophile range, log-spaced (how frequency response is normally
    # read: 20Hz-20kHz spans 3 decades, linear spacing wastes most of the
    # screen on the boring top octave). Extends past 20kHz automatically for
    # hi-res files whose Nyquist goes higher.
    from matplotlib.ticker import FuncFormatter, NullFormatter
    AUD_LO, AUD_HI = 20, 20000
    ax.set_xscale('log')
    ax.set_xlim(AUD_LO, AUD_HI)

    def hz_fmt(x, pos):
        return f'{x/1000:g}k' if x >= 1000 else f'{x:g}'
    ax.xaxis.set_major_formatter(FuncFormatter(hz_fmt))
    ax.xaxis.set_minor_formatter(NullFormatter())

    def tick_set_for(xmax):
        base = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
        extra = [30000, 40000, 50000, 60000, 80000, 100000, 150000, 192000]
        ticks = [t for t in base if t <= xmax]
        ticks += [t for t in extra if 20000 < t <= xmax]
        ax.set_xticks(ticks)

    tick_set_for(AUD_HI)

    def db_floor_for_bits(bits):
        """Theoretical PCM quantization noise floor: 6.02*N + 1.76 dB below 0dBFS."""
        return -(6.02 * bits + 1.76)

    line_pin, = ax.plot([], [], color=PIN_C, linewidth=1.0, linestyle='--', label='pinned')
    line_main, = ax.plot([], [], color=ACCENT, linewidth=1.1, label='current')
    cutoff_vline = ax.axvline(1, color=CUTOFF_C, linewidth=1.0, linestyle=':')
    cutoff_vline.set_visible(False)
    ax.set_ylim(db_floor_for_bits(16) - 3, 3)   # sane default before any file loads
    fig.tight_layout(pad=1.2)

    canvas = FigureCanvasTkAgg(fig, master=graph_wrap)
    canvas.get_tk_widget().configure(bg=PANEL_BG, highlightthickness=0)
    canvas.get_tk_widget().pack(fill='both', expand=True, padx=6, pady=4)

    def redraw_graph(freqs=None, profile=None, cutoff_hz=None, nyquist=None, bits=None):
        if freqs is not None:
            # log axis can't show 0 Hz - the DC bin - drop it
            mask = freqs > 0
            line_main.set_data(freqs[mask], profile[mask])
        else:
            line_main.set_data([], [])
        pin_bits = pinned.get('bits')
        if pinned['data'] is not None:
            pf, pp = pinned['data']
            pmask = pf > 0
            line_pin.set_data(pf[pmask], pp[pmask])
        else:
            line_pin.set_data([], [])

        xmax = max(AUD_HI, nyquist) if nyquist else AUD_HI
        ax.set_xlim(AUD_LO, xmax)
        tick_set_for(xmax)

        bit_candidates = [b for b in (bits, pin_bits) if b]
        floor = min(db_floor_for_bits(b) for b in bit_candidates) if bit_candidates else db_floor_for_bits(16)
        ax.set_ylim(floor - 3, 3)   # 0 dBFS at top - the true full range for this bit depth

        if cutoff_hz and nyquist and cutoff_hz < nyquist - 200:
            cutoff_vline.set_xdata([max(cutoff_hz, AUD_LO), max(cutoff_hz, AUD_LO)])
            cutoff_vline.set_visible(True)
        else:
            cutoff_vline.set_visible(False)
        canvas.draw_idle()

    # ---- readout under the graph ----
    readout = tk.Frame(graph_wrap, bg=PANEL_BG)
    readout.pack(fill='x', padx=8, pady=(0, 6))
    cutoff_var = tk.StringVar(value='—')
    tk.Label(readout, textvariable=cutoff_var, bg=PANEL_BG, fg=FG,
             font=MONO_SM, anchor='w').pack(fill='x')
    verdict_var = tk.StringVar(value='Избери файл от списъка')
    verdict_label = tk.Label(readout, textvariable=verdict_var, bg=PANEL_BG, fg=DIM,
                             font=('Consolas', 10, 'bold'), anchor='w')
    verdict_label.pack(fill='x', pady=(2, 0))

    # ================================================================ transport
    ctl = tk.Frame(root, bg=BG)
    ctl.pack(pady=6)

    def mkbtn(txt, cmd, w=4):
        return tk.Button(ctl, text=txt, command=cmd, font=('Consolas', 12, 'bold'),
                         width=w, bg='#1c2024', fg=FG, activebackground='#262b30',
                         activeforeground=FG, relief='flat', bd=0,
                         highlightthickness=1, highlightbackground='#33383e',
                         cursor='hand2')

    seekrow = tk.Frame(root, bg=BG)
    seekrow.pack(fill='x', padx=16, pady=(0, 2))
    time_var = tk.StringVar(value='0:00 / 0:00')
    scale = ttk.Scale(seekrow, from_=0, to=100, orient='horizontal')
    scale.pack(fill='x')
    tk.Label(seekrow, textvariable=time_var, bg=BG, fg=DIM, font=MONO_SM).pack(anchor='e')

    def on_release(e):
        dragging['on'] = False
        if eng.duration_seconds() > 0:
            eng.seek_seconds(float(scale.get()))
    scale.bind('<ButtonPress-1>', lambda e: dragging.update(on=True))
    scale.bind('<ButtonRelease-1>', on_release)

    # ================================================================ file list
    pl_wrap = tk.Frame(root, bg=BG)
    pl_wrap.pack(fill='both', expand=True, padx=10, pady=(6, 10))
    row = tk.Frame(pl_wrap, bg=BG)
    row.pack(fill='x')
    tk.Label(row, text='FILES', bg=BG, fg=DIM, font=('Consolas', 9, 'bold')).pack(side='left')

    pl_frame = tk.Frame(pl_wrap, bg=PANEL_BG, highlightthickness=1, highlightbackground='#2a2f34')
    pl_frame.pack(fill='both', expand=True, pady=(2, 0))
    lb = tk.Listbox(pl_frame, bg=PANEL_BG, fg=FG, selectbackground=BLUE_SEL,
                    selectforeground='#ffffff', activestyle='none', bd=0,
                    highlightthickness=0, font=MONO)
    lb.pack(side='left', fill='both', expand=True, padx=6, pady=6)
    sb = tk.Scrollbar(pl_frame, command=lb.yview)
    sb.pack(side='right', fill='y')
    lb.config(yscrollcommand=sb.set)

    def entry_label(e):
        base = f"{e['idx']:>2}. {os.path.basename(e['path'])}"
        if e['verdict'] is None:
            return base + ('   [анализ...]' if e['analyzing'] else '   [не е анализиран]')
        tag = VERDICT_TAG[e['verdict']]
        c = f"  ~{e['cutoff']/1000:.1f}kHz" if e['cutoff'] else ''
        return f"{base}   [{tag}{c}]"

    def refresh_row(i):
        lb.delete(i)
        lb.insert(i, entry_label(entries[i]))
        col = {'GENUINE': ACCENT, 'FAKE': CUTOFF_C, 'UNCLEAR': '#ffb340', None: DIM}
        lb.itemconfig(i, fg=col[entries[i]['verdict']])
        if i == current['idx']:
            lb.itemconfig(i, bg=BLUE_SEL, fg='#ffffff')

    btnrow = tk.Frame(pl_wrap, bg=BG)
    btnrow.pack(fill='x', pady=(4, 0))

    def add_files():
        exts = ' '.join('*' + e for e in AUDIO_EXTS) + ' *.wav *.alafc'
        paths = filedialog.askopenfilenames(
            title='Добави файлове',
            filetypes=[('Audio', exts), ('Всички', '*.*')])
        for p in paths:
            idx = len(entries) + 1
            entries.append({'idx': idx, 'path': p, 'verdict': None, 'cutoff': None,
                            'analyzing': False, 'info': None, 'bits': None})
            lb.insert('end', entry_label(entries[-1]))

    def clear_list():
        eng.pause()
        entries.clear(); lb.delete(0, 'end')
        current.update(idx=-1)
        redraw_graph()
        cutoff_var.set('—'); verdict_var.set('Избери файл от списъка')
        verdict_label.config(fg=DIM)
        time_var.set('0:00 / 0:00')

    def pin_current():
        i = current['idx']
        if i < 0 or entries[i]['info'] is None:
            return
        info = entries[i]['info']
        pinned['data'] = (info['freqs'], info['profile'])
        pinned['label'] = os.path.basename(entries[i]['path'])
        pinned['bits'] = entries[i]['bits']
        redraw_graph(info['freqs'], info['profile'], info['cutoff_hz'], info['nyquist'],
                    bits=entries[i]['bits'])

    def unpin():
        pinned['data'] = None
        pinned['bits'] = None
        i = current['idx']
        if 0 <= i < len(entries) and entries[i]['info']:
            info = entries[i]['info']
            redraw_graph(info['freqs'], info['profile'], info['cutoff_hz'], info['nyquist'],
                        bits=entries[i]['bits'])
        else:
            redraw_graph()

    tk.Button(btnrow, text='+ Добави', command=add_files, bg='#1c2024', fg=FG,
             activebackground='#262b30', activeforeground=FG, relief='flat', bd=0,
             highlightthickness=1, highlightbackground='#33383e', font=MONO_SM,
             cursor='hand2').pack(side='left')
    tk.Button(btnrow, text='Изчисти', command=clear_list, bg='#1c2024', fg=DIM,
             activebackground='#262b30', activeforeground=FG, relief='flat', bd=0,
             highlightthickness=1, highlightbackground='#33383e', font=MONO_SM,
             cursor='hand2').pack(side='left', padx=6)
    tk.Button(btnrow, text='📌 Pin', command=pin_current, bg='#1c2024', fg=FG,
             activebackground='#262b30', activeforeground=FG, relief='flat', bd=0,
             highlightthickness=1, highlightbackground='#33383e', font=MONO_SM,
             cursor='hand2').pack(side='left', padx=(0, 6))
    tk.Button(btnrow, text='Unpin', command=unpin, bg='#1c2024', fg=DIM,
             activebackground='#262b30', activeforeground=FG, relief='flat', bd=0,
             highlightthickness=1, highlightbackground='#33383e', font=MONO_SM,
             cursor='hand2').pack(side='left')

    # ================================================================ analysis + playback
    def select_track(idx):
        if not (0 <= idx < len(entries)) or current['loading']:
            return
        prev = current['idx']
        current['idx'] = idx
        current['loading'] = True
        if 0 <= prev < len(entries):
            refresh_row(prev)
        entries[idx]['analyzing'] = True
        refresh_row(idx)
        lb.see(idx)
        path = entries[idx]['path']
        verdict_var.set('Анализ...'); verdict_label.config(fg=DIM)
        cutoff_var.set('—')

        def work():
            try:
                play_f, mono, sr, ch, bits, status, size = load_for_scope(path)
                f, t, mag_db = spectrogram(mono.astype(np.float32), sr)
                info = analyze_cutoff(f, mag_db)
                verdict, msg = classify(info)
                jobs.put(('loaded', idx, play_f, sr, ch, bits, status, info, verdict, msg))
            except Exception as ex:
                jobs.put(('error', idx, str(ex)))
        threading.Thread(target=work, daemon=True).start()

    def play_pause():
        if eng.frames is None:
            sel = lb.curselection()
            select_track(sel[0] if sel else 0)
            return
        if eng.playing:
            eng.pause()
        else:
            eng.play()

    def step_track(delta):
        if not entries:
            return
        i = max(0, min(len(entries) - 1, current['idx'] + delta))
        select_track(i)

    mkbtn('|◄◄', lambda: step_track(-1)).pack(side='left', padx=3)
    playbtn = mkbtn('▶', play_pause)
    playbtn.pack(side='left', padx=3)
    mkbtn('■', lambda: eng.stop()).pack(side='left', padx=3)
    mkbtn('►►|', lambda: step_track(1)).pack(side='left', padx=3)

    lb.bind('<Double-Button-1>',
           lambda e: select_track(lb.curselection()[0]) if lb.curselection() else None)

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
                    _, idx, play_f, sr, ch, bits, status, info, verdict, msg = job
                    e = entries[idx]
                    e['analyzing'] = False
                    e['verdict'] = verdict
                    e['cutoff'] = info['cutoff_hz']
                    e['info'] = info
                    e['bits'] = bits
                    refresh_row(idx)
                    if idx == current['idx']:
                        try:
                            eng.load(play_f, sr, ch, bits)
                            dev, excl = chosen_device()
                            eng.open_stream(dev, excl)
                            eng.play()
                            scale.configure(to=max(1.0, eng.duration_seconds()))
                        except Exception as ex:
                            messagebox.showerror('Аудио устройство',
                                f'{ex}\n\nПроверката все пак приключи - само пускането не '
                                'проработи. Пробвай друго устройство от списъка.')
                        redraw_graph(info['freqs'], info['profile'], info['cutoff_hz'],
                                    info['nyquist'], bits=bits)
                        floor = -(6.02 * bits + 1.76)
                        cutoff_var.set(
                            f"Cutoff: {info['cutoff_hz']/1000:.1f} kHz   "
                            f"(Δ {info['drop_db']:.0f} dB / {info['steepness']:.0f} dB per kHz)   "
                            f"Nyquist: {info['nyquist']/1000:.1f} kHz   "
                            f"{bits}-bit floor: {floor:.0f} dBFS")
                        verdict_var.set(msg)
                        verdict_label.config(fg=VERDICT_COLOR[verdict])
                    current['loading'] = False
                elif job[0] == 'error':
                    _, idx, err = job
                    entries[idx]['analyzing'] = False
                    refresh_row(idx)
                    if idx == current['idx']:
                        verdict_var.set('Грешка: ' + err)
                        verdict_label.config(fg=CUTOFF_C)
                    current['loading'] = False
        except queue.Empty:
            pass

        dur = eng.duration_seconds()
        if dur > 0 and not dragging['on']:
            scale.set(eng.position_seconds())
        time_var.set(f'{fmt_time(eng.position_seconds())} / {fmt_time(dur)}')
        playbtn.config(text='||' if eng.playing else '▶')

        if eng.ended:
            eng.ended = False
            step_track(1)
        root.after(150, tick)

    for p in sys.argv[1:]:
        if os.path.exists(p):
            idx = len(entries) + 1
            entries.append({'idx': idx, 'path': p, 'verdict': None, 'cutoff': None,
                            'analyzing': False, 'info': None, 'bits': None})
            lb.insert('end', entry_label(entries[-1]))
    if entries:
        select_track(0)

    root.after(120, tick)
    root.mainloop()


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Content AI - @huyit32
Pipeline: Script -> Names -> Character Refs -> Scene Prompts
"""

import sys, subprocess, ctypes, os

# Server mode: frozen exe re-launched as API server subprocess
if '--server' in sys.argv:
    # --noconsole sets stdout/stderr to None; uvicorn logging calls .isatty() on them
    if sys.stdout is None: sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None: sys.stderr = open(os.devnull, 'w')
    from api_server import app as _fastapi_app
    import uvicorn
    uvicorn.run(_fastapi_app, host='0.0.0.0', port=int(os.environ.get('PORT', 6969)))
    sys.exit(0)

if not getattr(sys, 'frozen', False):
    for _pkg, _mod in [('requests','requests'), ('customtkinter','customtkinter')]:
        try:
            __import__(_mod)
        except ImportError:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', _pkg])

import re, json, time, uuid, threading, webbrowser, itertools
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import requests
import customtkinter as ctk

# ─── ChatGPT API server pool ─────────────────────────────────────────────────
_API_URL  = 'http://127.0.0.1:6969'   # primary (kept for compat)
_conv_id  = ''                         # conversation ID (auto-captured)

def _discover_servers(base_port: int = 6969, count: int = 8) -> list[str]:
    """Probe ports base_port..base_port+count-1, return URLs that respond /health."""
    found = []
    for port in range(base_port, base_port + count):
        try:
            r = requests.get(f'http://127.0.0.1:{port}/health', timeout=1)
            if r.ok:
                found.append(f'http://127.0.0.1:{port}')
        except Exception:
            pass
    return found or [f'http://127.0.0.1:{base_port}']

_api_pool:    list[str]       = []   # populated on first use
_api_cycle:   itertools.cycle = None
_api_lock     = threading.Lock()
_server_procs: list           = []   # launched subprocesses

_SERVER_SCRIPT = Path(__file__).parent / 'api_server.py'


def _find_cookie_files() -> list:
    """Read chatgpt_cookie.txt or cookie.txt next to the exe/script.
    Each non-empty line = one account's cookie string."""
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    for fname in ('chatgpt_cookie.txt', 'cookie.txt'):
        p = base / fname
        if p.exists():
            lines = [l.strip() for l in p.read_text('utf-8').splitlines() if l.strip()]
            if lines:
                return lines
    return [None]  # no cookie file → manual login


def _kill_port(port: int):
    try:
        out = subprocess.check_output(['netstat', '-ano'], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if f':{port} ' in line and 'LISTENING' in line:
                pid = int(line.split()[-1])
                subprocess.run(['taskkill', '/PID', str(pid), '/F'],
                               capture_output=True)
                break
    except Exception:
        pass


def _launch_servers(accounts: list) -> list[int]:
    """Launch one api_server per account. accounts = list of cookie strings (or None)."""
    global _server_procs, _api_pool, _api_cycle
    ports = []
    for i, cookie_str in enumerate(accounts):
        port = 6969 + i
        _kill_port(port)
        env = os.environ.copy()
        env['PORT'] = str(port)
        if cookie_str is not None:
            env['COOKIE_STRING'] = cookie_str
        # Frozen: re-launch this exe with --server flag; else call api_server.py directly
        cmd = [sys.executable, '--server'] if getattr(sys, 'frozen', False) else [sys.executable, str(_SERVER_SCRIPT)]
        proc = subprocess.Popen(cmd, env=env)
        _server_procs.append(proc)
        ports.append(port)
    _api_pool  = [f'http://127.0.0.1:{p}' for p in ports]
    _api_cycle = itertools.cycle(_api_pool)
    return ports


def _cleanup_servers():
    for proc in _server_procs:
        try:
            proc.terminate()
        except Exception:
            pass


# ─── Font ────────────────────────────────────────────────────────────────────

def _load_font():
    base = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).parent
    font_dir = base / 'fonts'
    font_dir.mkdir(exist_ok=True)
    needed = {
        'OpenSans-Regular.ttf': 'https://github.com/googlefonts/opensans/raw/main/fonts/ttf/OpenSans-Regular.ttf',
        'OpenSans-Bold.ttf':    'https://github.com/googlefonts/opensans/raw/main/fonts/ttf/OpenSans-Bold.ttf',
    }
    for fname, url in needed.items():
        fp = font_dir / fname
        if not fp.exists():
            try:
                r = requests.get(url, timeout=15)
                if r.ok and len(r.content) > 1000:
                    fp.write_bytes(r.content)
            except Exception:
                pass
        if fp.exists():
            try:
                ctypes.windll.gdi32.AddFontResourceW(str(fp))
            except Exception:
                pass
    try:
        ctypes.windll.user32.SendMessageW(0xFFFF, 0x001D, 0, 0)
    except Exception:
        pass

threading.Thread(target=_load_font, daemon=True).start()
F = 'Open Sans'

# ─── ChatGPT via local API server ─────────────────────────────────────────────

def _next_server() -> str:
    global _api_pool, _api_cycle
    with _api_lock:
        if not _api_pool:
            _api_pool  = _discover_servers()
            _api_cycle = itertools.cycle(_api_pool)
            print(f'[pool] {len(_api_pool)} server(s): {_api_pool}', flush=True)
        return next(_api_cycle)

def ask(message: str, on_delta=None) -> str:
    global _conv_id
    url = _next_server()
    try:
        resp = requests.post(
            f'{url}/conversation',
            json={'message': message},
            timeout=180,
        )
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f'Không kết nối được API server tại {url}.\nChạy: python ChatGPT/api_server.py')
    if resp.status_code != 200:
        raise RuntimeError(f'API server lỗi {resp.status_code}: {resp.text[:200]}')
    data = resp.json()
    text = data.get('result', '').strip()
    if not text:
        raise RuntimeError('API server trả về rỗng.')
    cid = data.get('conv_id') or ''
    if cid:
        _conv_id = cid
    if on_delta:
        on_delta(text)
    return text


# ─── Helpers ──────────────────────────────────────────────────────────────────

def parse_scenes(text: str) -> list[tuple[int, str]]:
    scenes = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Match: "123. text", "123 text", "123_ text", "[123] text", "[123]. text"
        m = re.match(r'^\[?(\d+)\]?[_\.\s]\s*(.+)', line)
        if m:
            scenes.append((int(m.group(1)), m.group(2).strip()))
    return scenes


def _parse_scene_prompts(response: str) -> dict[int, str]:
    """Parse AI response into {scene_num: prompt_text}, handles multi-line and format quirks."""
    result: dict[int, str] = {}
    current_num: int | None = None
    current_parts: list[str] = []

    for line in response.splitlines():
        line = line.strip()
        if not line:
            if current_num is not None and current_parts:
                result[current_num] = ' '.join(current_parts)
                current_num = None
                current_parts = []
            continue
        # Match [N], [N], [N]: [N]. etc.
        m = re.match(r'^\[?(\d+)\]?[:\.\s]\s*(.*)', line)
        if m:
            if current_num is not None and current_parts:
                result[current_num] = ' '.join(current_parts)
            current_num = int(m.group(1))
            body = m.group(2).strip()
            current_parts = [body] if body else []
        elif current_num is not None:
            current_parts.append(line)

    if current_num is not None and current_parts:
        result[current_num] = ' '.join(current_parts)

    return result


STYLES: dict[str, str] = {
    'MAPPA Anime':
        'anime style, MAPPA studio quality, cinematic lighting, detailed key animation, dramatic angles',
    'Solo Leveling Manhwa':
        'Korean manhwa, Solo Leveling aesthetic, dark fantasy, dynamic action, vibrant power aura, webtoon coloring',
    'Demon Slayer (ufotable)':
        'anime, ufotable studio, Demon Slayer aesthetic, detailed backgrounds, vivid breath FX, warm golden light',
    'Jujutsu Kaisen':
        'anime, MAPPA studio, JJK aesthetic, urban setting, cursed energy aura, high-contrast cinematic',
    'Vinland Saga':
        'anime, WIT Studio, Vinland Saga, medieval Norse, realistic proportions, cold muted palette',
    'Omniscient Reader':
        'Korean manhwa, Omniscient Reader, post-apocalyptic, dimensional effects, webtoon style',
    'Berserk':
        'dark fantasy manga, Berserk aesthetic, grim medieval horror, heavy shadow contrast, brutal atmosphere',
    'Manhwa Action':
        'Korean manhwa, dynamic fight poses, bold outlines, vibrant flats, impact speed lines',
}

CN_LANGS  = ['Tiếng Việt', 'English']
CN_STYLES = ['anime', 'manhwa', 'realistic', 'cinematic',
             'thumbnail YouTube', 'novel illustration',
             'MAPPA Anime', 'ufotable', 'Solo Leveling']
CN_MODELS = ['Flux', 'SDXL', 'Pony', 'Midjourney', 'Niji', 'ChatGPT image']


# ─── App ─────────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('Content AI  —  @huyit32')
        self.geometry('1340x860')
        self.minsize(1000, 640)
        ctk.set_appearance_mode('dark')
        ctk.set_default_color_theme('blue')

        # State
        self._files:      list[tuple[Path, str]] = []
        self._scenes:     list[tuple[int, str]]  = []
        self._names:      list[str]              = []   # extracted names (clean, no ★)
        self._main_chars: set[str]               = set()  # subset of _names that are main
        self._char_refs:  dict[str, str]         = {}   # name -> ref prompt
        self._stop        = threading.Event()

        self._build()
        self._launch_and_wait()

    # ── Startup overlay ───────────────────────────────────────────────────────

    def _launch_and_wait(self):
        cookie_files = _find_cookie_files()
        ports        = _launch_servers(cookie_files)

        # Full-screen loading overlay — sits on top of the built UI
        ov = ctk.CTkFrame(self, fg_color='#111111', corner_radius=0)
        ov.place(x=0, y=0, relwidth=1, relheight=1)
        ov.lift()

        ctk.CTkLabel(ov, text='Content AI',
                     font=ctk.CTkFont(family=F, size=36, weight='bold')).pack(pady=(100, 6))
        ctk.CTkLabel(ov, text='@huyit32',
                     font=ctk.CTkFont(family=F, size=12), text_color='gray45').pack(pady=(0, 40))

        acc_labels: dict[int, ctk.CTkLabel] = {}
        for i, port in enumerate(ports):
            lbl = ctk.CTkLabel(
                ov,
                text=f'  Account {i+1}  —  ⏳ Đang mở Chrome...',
                font=ctk.CTkFont(family=F, size=13),
                text_color='gray55', anchor='w')
            lbl.pack(fill='x', padx=120, pady=4)
            acc_labels[port] = lbl

        dot = [0]

        def _poll():
            all_ok = True
            for port in ports:
                lbl = acc_labels[port]
                i   = ports.index(port)
                try:
                    r  = requests.get(f'http://127.0.0.1:{port}/health', timeout=0.8)
                    ok = r.ok
                except Exception:
                    ok = False

                if ok:
                    lbl.configure(
                        text=f'  Account {i+1}  —  ✓ Sẵn sàng',
                        text_color='#4caf50')
                else:
                    all_ok = False
                    dots   = '.' * (dot[0] % 4)
                    lbl.configure(
                        text=f'  Account {i+1}  —  ⏳ Đang khởi động{dots}',
                        text_color='gray55')

            dot[0] += 1
            if all_ok:
                self._refresh_conv_id()
                self.after(400, ov.destroy)
            else:
                self.after(1000, _poll)

        self.after(800, _poll)

    # ── Layout ───────────────────────────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=280, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky='nsew')
        sidebar.grid_propagate(False)

        main = ctk.CTkFrame(self, fg_color='transparent')
        main.grid(row=0, column=1, padx=10, pady=10, sticky='nsew')
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        self._sidebar(sidebar)
        self._main(main)

    def _sidebar(self, p):
        p.grid_columnconfigure(0, weight=1)

        # title
        ctk.CTkLabel(p, text='Content AI',
                     font=ctk.CTkFont(family=F, size=20, weight='bold')).grid(
            row=0, column=0, padx=16, pady=(20, 2), sticky='ew')
        ctk.CTkLabel(p, text='@huyit32  •  ChatGPT',
                     font=ctk.CTkFont(family=F, size=11), text_color='gray55').grid(
            row=1, column=0, padx=16, pady=(0, 12), sticky='ew')

        self._hr(p, 2)

        # Files
        ctk.CTkLabel(p, text='SCRIPT FILES',
                     font=ctk.CTkFont(family=F, size=10, weight='bold'),
                     text_color='gray50').grid(row=3, column=0, padx=16, pady=(10, 3), sticky='w')

        fr = ctk.CTkFrame(p, fg_color='transparent')
        fr.grid(row=4, column=0, padx=16, pady=2, sticky='ew')
        fr.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(fr, text='Add Files', height=30,
                      font=ctk.CTkFont(family=F, size=12),
                      command=self._add_files).grid(row=0, column=0, sticky='ew', padx=(0,4))
        ctk.CTkButton(fr, text='X', width=32, height=30,
                      font=ctk.CTkFont(family=F, size=12),
                      fg_color='#3a3a3a', hover_color='#555',
                      command=self._clear_files).grid(row=0, column=1)

        self._file_box = ctk.CTkTextbox(p, height=100, state='disabled',
                                         font=ctk.CTkFont(family=F, size=11))
        self._file_box.grid(row=5, column=0, padx=16, pady=(3, 10), sticky='ew')

        self._scene_lbl = ctk.CTkLabel(p, text='0 scenes loaded',
                                        font=ctk.CTkFont(family=F, size=11),
                                        text_color='gray55')
        self._scene_lbl.grid(row=6, column=0, padx=16, pady=(0, 8), sticky='w')

        self._hr(p, 7)

        # Style
        ctk.CTkLabel(p, text='STYLE',
                     font=ctk.CTkFont(family=F, size=10, weight='bold'),
                     text_color='gray50').grid(row=8, column=0, padx=16, pady=(10, 3), sticky='w')

        self._style_var = ctk.StringVar(value='MAPPA Anime')
        ctk.CTkOptionMenu(p, values=list(STYLES.keys()),
                          variable=self._style_var, height=32,
                          font=ctk.CTkFont(family=F, size=12)).grid(
            row=9, column=0, padx=16, pady=2, sticky='ew')

        ctk.CTkLabel(p, text='Extra notes:',
                     font=ctk.CTkFont(family=F, size=11), text_color='gray60').grid(
            row=10, column=0, padx=16, pady=(8, 2), sticky='w')
        self._extra = ctk.CTkEntry(p, placeholder_text='e.g. rain, night, cinematic…',
                                    height=30, font=ctk.CTkFont(family=F, size=12))
        self._extra.grid(row=11, column=0, padx=16, pady=(0, 8), sticky='ew')

        # Batch size
        bfr = ctk.CTkFrame(p, fg_color='transparent')
        bfr.grid(row=12, column=0, padx=16, pady=4, sticky='ew')
        bfr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bfr, text='Parallel workers:',
                     font=ctk.CTkFont(family=F, size=11)).grid(row=0, column=0, sticky='w')
        self._batch = ctk.CTkEntry(bfr, width=52, height=28,
                                    font=ctk.CTkFont(family=F, size=12))
        self._batch.insert(0, '5')
        self._batch.grid(row=0, column=1)

        self._hr(p, 13)

        # Pipeline steps
        ctk.CTkLabel(p, text='PIPELINE',
                     font=ctk.CTkFont(family=F, size=10, weight='bold'),
                     text_color='gray50').grid(row=14, column=0, padx=16, pady=(10, 4), sticky='w')

        steps = [
            ('Step 1  —  Extract Names',     '#1a4f82', '#0e3357', self._run_step1),
            ('Step 2  —  Character Refs',    '#1e6b27', '#134519', self._run_step2),
            ('Step 3  —  Scene Prompts',     '#5a1880', '#3a0f54', self._run_step3),
            ('Run All Steps',                '#7a5000', '#4f3400', self._run_all),
        ]
        for i, (label, fg, hv, cmd) in enumerate(steps):
            ctk.CTkButton(p, text=label, height=36,
                          font=ctk.CTkFont(family=F, size=12, weight='bold'),
                          fg_color=fg, hover_color=hv,
                          command=cmd, corner_radius=6).grid(
                row=15+i, column=0, padx=16, pady=3, sticky='ew')

        self._stop_btn = ctk.CTkButton(
            p, text='Stop', height=28,
            font=ctk.CTkFont(family=F, size=12),
            fg_color='#7a1515', hover_color='#4f0d0d',
            state='disabled', command=self._do_stop, corner_radius=6)
        self._stop_btn.grid(row=19, column=0, padx=16, pady=(4, 4), sticky='ew')

        self._hr(p, 20)

        ctk.CTkLabel(p, text='DICH TRUYEN CN',
                     font=ctk.CTkFont(family=F, size=10, weight='bold'),
                     text_color='gray50').grid(row=21, column=0, padx=16, pady=(8, 3), sticky='w')

        ctk.CTkButton(p, text='Dich CN -> Prompt', height=36,
                      font=ctk.CTkFont(family=F, size=12, weight='bold'),
                      fg_color='#1a4f6a', hover_color='#0e3347',
                      command=self._open_cn_tool, corner_radius=6).grid(
            row=22, column=0, padx=16, pady=(0, 8), sticky='ew')

        self._hr(p, 23)

        # API status + Settings button
        self._api_lbl = ctk.CTkLabel(
            p, text=self._api_status(),
            font=ctk.CTkFont(family=F, size=10), text_color='gray50')
        self._api_lbl.grid(row=24, column=0, padx=16, pady=(8, 4), sticky='w')

        ctk.CTkButton(p, text='Settings', height=28,
                      font=ctk.CTkFont(family=F, size=11),
                      fg_color='#2a2a2a', hover_color='#3a3a3a',
                      command=self._open_settings, corner_radius=6).grid(
            row=25, column=0, padx=16, pady=(0, 8), sticky='ew')

        self._hr(p, 26)

        self._conv_lbl = ctk.CTkLabel(
            p, text='Chat: (chưa có)',
            font=ctk.CTkFont(family=F, size=10), text_color='gray50',
            anchor='w', wraplength=220)
        self._conv_lbl.grid(row=27, column=0, padx=16, pady=(6, 3), sticky='ew')

        cfr = ctk.CTkFrame(p, fg_color='transparent')
        cfr.grid(row=28, column=0, padx=16, pady=(0, 16), sticky='ew')
        cfr.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(cfr, text='Mở Chat', height=26,
                      font=ctk.CTkFont(family=F, size=11),
                      fg_color='#1a3a2a', hover_color='#0e2419',
                      command=self._open_conv_browser, corner_radius=6).grid(
            row=0, column=0, sticky='ew', padx=(0, 4))
        ctk.CTkButton(cfr, text='Copy', width=46, height=26,
                      font=ctk.CTkFont(family=F, size=11),
                      fg_color='#2a2a2a', hover_color='#3a3a3a',
                      command=self._copy_conv_id, corner_radius=6).grid(
            row=0, column=1)

    def _main(self, p):
        # Status bar
        sf = ctk.CTkFrame(p, corner_radius=8)
        sf.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        sf.grid_columnconfigure(0, weight=1)
        self._status = ctk.CTkLabel(sf, text='Load script files, then run the pipeline steps.',
                                     anchor='w', font=ctk.CTkFont(family=F, size=12))
        self._status.grid(row=0, column=0, padx=14, pady=(8, 3), sticky='ew')
        self._prog = ctk.CTkProgressBar(sf, height=6, corner_radius=3)
        self._prog.grid(row=1, column=0, padx=14, pady=(0, 8), sticky='ew')
        self._prog.set(0)

        # Tabs (3 steps)
        self._tabs = ctk.CTkTabview(p, corner_radius=10)
        self._tabs.grid(row=1, column=0, sticky='nsew')

        t1 = self._tabs.add('Step 1  —  Names')
        t2 = self._tabs.add('Step 2  —  Character Refs')
        t3 = self._tabs.add('Step 3  —  Scene Prompts')

        for t in (t1, t2, t3):
            t.grid_rowconfigure(0, weight=1)
            t.grid_columnconfigure(0, weight=1)

        # Tab 1: editable name list
        t1_inner = ctk.CTkFrame(t1, fg_color='transparent')
        t1_inner.grid(row=0, column=0, sticky='nsew')
        t1_inner.grid_rowconfigure(1, weight=1)
        t1_inner.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(t1_inner,
                     text='★ = main character (detailed ref)  |  no ★ = supporting  |  Edit before Step 2.',
                     font=ctk.CTkFont(family=F, size=11), text_color='gray60',
                     anchor='w').grid(row=0, column=0, padx=6, pady=(6, 3), sticky='ew')
        self._names_box = ctk.CTkTextbox(t1_inner, font=ctk.CTkFont(family=F, size=13),
                                          wrap='none')
        self._names_box.grid(row=1, column=0, sticky='nsew', padx=4, pady=4)

        # Tab 2: character refs
        t2_inner = ctk.CTkFrame(t2, fg_color='transparent')
        t2_inner.grid(row=0, column=0, sticky='nsew')
        t2_inner.grid_rowconfigure(1, weight=1)
        t2_inner.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(t2_inner,
                     text='Visual reference prompts per character — used in Step 3 for consistency.',
                     font=ctk.CTkFont(family=F, size=11), text_color='gray60',
                     anchor='w').grid(row=0, column=0, padx=6, pady=(6, 3), sticky='ew')
        self._refs_box = ctk.CTkTextbox(t2_inner, font=ctk.CTkFont(family=F, size=12),
                                         wrap='word')
        self._refs_box.grid(row=1, column=0, sticky='nsew', padx=4, pady=4)

        # Tab 3: scene prompts
        t3_inner = ctk.CTkFrame(t3, fg_color='transparent')
        t3_inner.grid(row=0, column=0, sticky='nsew')
        t3_inner.grid_rowconfigure(1, weight=1)
        t3_inner.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(t3_inner,
                     text='Image prompts — numbered to match script scenes.',
                     font=ctk.CTkFont(family=F, size=11), text_color='gray60',
                     anchor='w').grid(row=0, column=0, padx=6, pady=(6, 3), sticky='ew')
        self._prompts_box = ctk.CTkTextbox(t3_inner, font=ctk.CTkFont(family=F, size=12),
                                            wrap='word')
        self._prompts_box.grid(row=1, column=0, sticky='nsew', padx=4, pady=4)

        # Toolbar
        tb = ctk.CTkFrame(p, fg_color='transparent')
        tb.grid(row=2, column=0, sticky='ew', pady=(6, 0))
        for label, cmd in [
            ('Copy Names',    lambda: self._copy(self._names_box)),
            ('Copy Refs',     lambda: self._copy(self._refs_box)),
            ('Copy Prompts',  lambda: self._copy(self._prompts_box)),
            ('Save Refs',     lambda: self._save(self._refs_box, 'char_refs')),
            ('Save Prompts',  lambda: self._save(self._prompts_box, 'scene_prompts')),
            ('Clear All',     self._clear_all),
        ]:
            ctk.CTkButton(tb, text=label, height=28, width=110,
                          font=ctk.CTkFont(family=F, size=11),
                          command=cmd).pack(side='left', padx=3)

    # ── Util ─────────────────────────────────────────────────────────────────

    def _refresh_conv_id(self):
        def _do():
            cid = _conv_id
            if cid:
                short = cid[:8] + '...' + cid[-4:]
                self._conv_lbl.configure(text=f'Chat: {short}')
            else:
                self._conv_lbl.configure(text='Chat: (chưa có)')
        self.after(0, _do)

    def _open_conv_browser(self):
        if _conv_id:
            webbrowser.open(f'https://chatgpt.com/c/{_conv_id}')
        else:
            messagebox.showinfo('Chưa có', 'Chưa có conversation nào. Chạy pipeline trước.')

    def _copy_conv_id(self):
        if _conv_id:
            self.clipboard_clear()
            self.clipboard_append(f'https://chatgpt.com/c/{_conv_id}')
            messagebox.showinfo('Copied', f'Đã copy link conversation!')
        else:
            messagebox.showinfo('Chưa có', 'Chưa có conversation nào.')

    def _api_status(self) -> str:
        try:
            r = requests.get(f'{_API_URL}/health', timeout=2)
            return '● ChatGPT server — online' if r.ok else '⚠ ChatGPT server — lỗi'
        except Exception:
            return '⚠ ChatGPT server — offline'

    def _open_settings(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title('Settings')
        dlg.geometry('460x200')
        dlg.resizable(False, False)
        dlg.grab_set()

        ctk.CTkLabel(dlg, text='ChatGPT API Server',
                     font=ctk.CTkFont(family=F, size=14, weight='bold')).pack(pady=(18, 4))
        ctk.CTkLabel(dlg,
                     text=f'Chạy server: python ChatGPT/api_server.py\nĐịa chỉ mặc định: {_API_URL}',
                     font=ctk.CTkFont(family=F, size=11), text_color='gray60',
                     justify='center').pack(pady=(0, 12))

        self._api_lbl.configure(text=self._api_status())

        ctk.CTkButton(dlg, text='Kiểm tra kết nối', height=34,
                      font=ctk.CTkFont(family=F, size=12),
                      command=lambda: (
                          self._api_lbl.configure(text=self._api_status()),
                          messagebox.showinfo('Status', self._api_status())
                      )).pack(padx=16, fill='x')
        ctk.CTkButton(dlg, text='Đóng', height=34, width=80,
                      font=ctk.CTkFont(family=F, size=12),
                      fg_color='#3a3a3a', hover_color='#555',
                      command=dlg.destroy).pack(pady=(8, 16), padx=16, fill='x')

    def _open_cn_tool(self):
        win = ctk.CTkToplevel(self)
        win.title('Dịch CN → Image Prompt')
        win.geometry('1100x740')
        win.resizable(True, True)

        # ── Top controls ──────────────────────────────────────────────────────
        ctrl = ctk.CTkFrame(win, fg_color='transparent')
        ctrl.pack(fill='x', padx=14, pady=(12, 4))

        ctk.CTkLabel(ctrl, text='Ngôn ngữ:',
                     font=ctk.CTkFont(family=F, size=12)).pack(side='left', padx=(0, 4))
        lang_var = ctk.StringVar(value=CN_LANGS[0])
        ctk.CTkOptionMenu(ctrl, values=CN_LANGS, variable=lang_var, width=120, height=30,
                          font=ctk.CTkFont(family=F, size=12)).pack(side='left', padx=(0, 12))

        ctk.CTkLabel(ctrl, text='Style:',
                     font=ctk.CTkFont(family=F, size=12)).pack(side='left', padx=(0, 4))
        style_var = ctk.StringVar(value=CN_STYLES[0])
        ctk.CTkOptionMenu(ctrl, values=CN_STYLES, variable=style_var, width=160, height=30,
                          font=ctk.CTkFont(family=F, size=12)).pack(side='left', padx=(0, 12))

        ctk.CTkLabel(ctrl, text='Model:',
                     font=ctk.CTkFont(family=F, size=12)).pack(side='left', padx=(0, 4))
        model_var = ctk.StringVar(value=CN_MODELS[0])
        ctk.CTkOptionMenu(ctrl, values=CN_MODELS, variable=model_var, width=140, height=30,
                          font=ctk.CTkFont(family=F, size=12)).pack(side='left', padx=(0, 16))

        status_lbl = ctk.CTkLabel(ctrl, text='', font=ctk.CTkFont(family=F, size=11),
                                   text_color='gray60')
        status_lbl.pack(side='left', expand=True, fill='x')

        process_btn = ctk.CTkButton(ctrl, text='Dịch & Gen Prompt', height=30, width=160,
                                     font=ctk.CTkFont(family=F, size=12, weight='bold'),
                                     fg_color='#1a4f6a', hover_color='#0e3347')
        process_btn.pack(side='right', padx=(8, 0))

        # ── Input ─────────────────────────────────────────────────────────────
        ctk.CTkLabel(win, text='Nhập văn bản tiếng Trung:',
                     font=ctk.CTkFont(family=F, size=11), text_color='gray60',
                     anchor='w').pack(fill='x', padx=14, pady=(4, 2))
        inp_box = ctk.CTkTextbox(win, height=130, font=ctk.CTkFont(family='Courier', size=12),
                                  wrap='word')
        inp_box.pack(fill='x', padx=14, pady=(0, 6))

        # ── Table ─────────────────────────────────────────────────────────────
        ctk.CTkLabel(win, text='Output:', font=ctk.CTkFont(family=F, size=11),
                     text_color='gray60', anchor='w').pack(fill='x', padx=14, pady=(0, 2))

        tbl_frame = ctk.CTkFrame(win)
        tbl_frame.pack(fill='both', expand=True, padx=14, pady=(0, 4))

        # Style Treeview for dark mode
        tv_style = ttk.Style()
        tv_style.theme_use('clam')
        tv_style.configure('CN.Treeview',
                            background='#1e1e1e', foreground='#e0e0e0',
                            fieldbackground='#1e1e1e', rowheight=60,
                            font=('Courier', 10))
        tv_style.configure('CN.Treeview.Heading',
                            background='#2a2a2a', foreground='#cccccc',
                            font=(F, 11, 'bold'), relief='flat')
        tv_style.map('CN.Treeview', background=[('selected', '#1a4f6a')])

        tv = ttk.Treeview(tbl_frame, style='CN.Treeview',
                          columns=('orig', 'trans', 'prompt'), show='headings')
        tv.heading('orig',   text='Gốc (中文)')
        tv.heading('trans',  text='Dịch')
        tv.heading('prompt', text='Image Prompt')
        tv.column('orig',   width=220, minwidth=120)
        tv.column('trans',  width=240, minwidth=120)
        tv.column('prompt', width=560, minwidth=200)

        sb_y = ttk.Scrollbar(tbl_frame, orient='vertical',   command=tv.yview)
        sb_x = ttk.Scrollbar(tbl_frame, orient='horizontal', command=tv.xview)
        tv.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        sb_y.pack(side='right', fill='y')
        sb_x.pack(side='bottom', fill='x')
        tv.pack(fill='both', expand=True)

        # ── Bottom toolbar ─────────────────────────────────────────────────────
        bot = ctk.CTkFrame(win, fg_color='transparent')
        bot.pack(fill='x', padx=14, pady=(4, 12))

        def _copy_tsv():
            rows = ['\t'.join(['Gốc', 'Dịch', 'Prompt'])]
            for iid in tv.get_children():
                rows.append('\t'.join(tv.item(iid, 'values')))
            win.clipboard_clear(); win.clipboard_append('\n'.join(rows))
            messagebox.showinfo('Copied', f'{len(rows)-1} dòng đã copy (TSV).')

        def _save_tsv():
            rows = ['\t'.join(['Gốc', 'Dịch', 'Prompt'])]
            for iid in tv.get_children():
                rows.append('\t'.join(tv.item(iid, 'values')))
            if len(rows) <= 1:
                messagebox.showwarning('Empty', 'Chưa có dữ liệu.'); return
            p = filedialog.asksaveasfilename(defaultextension='.tsv',
                                             filetypes=[('TSV', '*.tsv'), ('All', '*.*')])
            if p:
                Path(p).write_text('\n'.join(rows), encoding='utf-8')
                messagebox.showinfo('Saved', f'Đã lưu {p}')

        def _clear_table():
            for iid in tv.get_children(): tv.delete(iid)
            status_lbl.configure(text='')

        for lbl, cmd in [('Copy TSV', _copy_tsv), ('Lưu TSV', _save_tsv), ('Xoá', _clear_table)]:
            ctk.CTkButton(bot, text=lbl, height=28, width=100,
                          font=ctk.CTkFont(family=F, size=11),
                          command=cmd).pack(side='left', padx=3)

        # ── Processing ────────────────────────────────────────────────────────
        def _do_process():
            cn_text = inp_box.get('1.0', 'end').strip()
            if not cn_text:
                messagebox.showwarning('Trống', 'Nhập văn bản tiếng Trung trước.'); return
            _clear_table()
            status_lbl.configure(text='Đang xử lý...')
            process_btn.configure(state='disabled')

            def _worker():
                lang  = lang_var.get()
                style = style_var.get()
                model = model_var.get()
                prompt = (
                    f'You are a professional translator and image prompt writer.\n\n'
                    f'Process the Chinese text below:\n'
                    f'1. Split into individual sentences (1 sentence per row, max 2 if inseparable)\n'
                    f'2. Translate each naturally to {lang}\n'
                    f'3. Write an image generation prompt in {style} style, optimized for {model}\n'
                    f'   — Keep character appearance/outfit consistent across all rows\n'
                    f'   — Include: subject, setting, action, mood, lighting, camera angle\n\n'
                    f'Output STRICTLY as tab-separated values, NO header, NO markdown:\n'
                    f'CHINESE_SENTENCE<TAB>TRANSLATION<TAB>IMAGE_PROMPT\n\n'
                    f'Rules: one row per sentence, prompt in English, no extra text.\n\n'
                    f'Chinese text:\n{cn_text}'
                )
                try:
                    result = ask(prompt)
                    rows = []
                    for line in result.splitlines():
                        line = line.strip()
                        if not line or line.count('\t') < 2:
                            # Try pipe separator fallback
                            parts = [p.strip() for p in line.split('|')]
                            if len(parts) >= 3:
                                rows.append(tuple(parts[:3]))
                            continue
                        parts = line.split('\t', 2)
                        if len(parts) == 3:
                            rows.append(tuple(parts))
                    def _update():
                        for row in rows:
                            tv.insert('', 'end', values=row)
                        n = len(rows)
                        status_lbl.configure(text=f'Xong — {n} dòng.')
                        process_btn.configure(state='normal')
                    win.after(0, _update)
                except Exception as e:
                    win.after(0, lambda err=str(e): (
                        status_lbl.configure(text=f'Lỗi: {err}'),
                        process_btn.configure(state='normal'),
                        messagebox.showerror('Lỗi', err),
                    ))

            threading.Thread(target=_worker, daemon=True).start()

        process_btn.configure(command=_do_process)

    @staticmethod
    def _hr(parent, row):
        ctk.CTkFrame(parent, height=1, fg_color='gray25').grid(
            row=row, column=0, padx=16, pady=2, sticky='ew')

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title='Select script files', filetypes=[('Text', '*.txt'), ('All', '*.*')])
        existing = {p for p, _ in self._files}
        for fp in paths:
            p = Path(fp)
            if p not in existing:
                self._files.append((p, p.read_text(encoding='utf-8', errors='ignore')))
        self._refresh_files()

    def _clear_files(self):
        self._files.clear()
        self._scenes.clear()
        self._refresh_files()

    def _refresh_files(self):
        self._file_box.configure(state='normal')
        self._file_box.delete('1.0', 'end')
        for p, _ in self._files:
            self._file_box.insert('end', f'{p.name}\n')
        self._file_box.configure(state='disabled')
        # parse scenes
        all_text = '\n'.join(c for _, c in self._files)
        self._scenes = parse_scenes(all_text)
        self._scene_lbl.configure(text=f'{len(self._scenes)} scenes loaded')

    def _all_text(self) -> str:
        return '\n'.join(c for _, c in self._files)

    def _style(self) -> str:
        base = STYLES.get(self._style_var.get(), '')
        extra = self._extra.get().strip()
        return f'{base}, {extra}' if extra else base

    def _batch_n(self) -> int:
        try:
            return max(1, int(self._batch.get()))
        except Exception:
            return 8

    def _st(self, msg: str):
        self.after(0, lambda: self._status.configure(text=msg))

    def _pg(self, v: float):
        self.after(0, lambda: self._prog.set(max(0.0, min(1.0, v))))

    def _append(self, box, text: str):
        def _do():
            box.insert('end', text)
            box.see('end')
        self.after(0, _do)

    def _make_delta_cb(self, box):
        """Return an on_delta callback that streams text into box in real time."""
        def cb(delta: str):
            self.after(0, lambda d=delta: (box.insert('end', d), box.see('end')))
        return cb

    def _busy(self, on: bool):
        self.after(0, lambda: self._stop_btn.configure(
            state='normal' if on else 'disabled'))

    def _copy(self, box):
        t = box.get('1.0', 'end').strip()
        self.clipboard_clear()
        self.clipboard_append(t)
        messagebox.showinfo('Copied', 'Copied to clipboard!')

    def _save(self, box, name: str):
        t = box.get('1.0', 'end').strip()
        if not t:
            messagebox.showwarning('Empty', 'Nothing to save.')
            return
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        p = filedialog.asksaveasfilename(
            defaultextension='.txt', initialfile=f'{name}_{ts}.txt',
            filetypes=[('Text', '*.txt')])
        if p:
            Path(p).write_text(t, encoding='utf-8')
            messagebox.showinfo('Saved', f'Saved:\n{p}')

    def _clear_all(self):
        for box in (self._names_box, self._refs_box, self._prompts_box):
            box.delete('1.0', 'end')
        self._char_refs.clear()
        self._main_chars.clear()
        self._prog.set(0)
        self._st('Cleared.')

    def _do_stop(self):
        self._stop.set()
        self._st('Stopping after current request...')

    def _guard(self) -> bool:
        if not self._files:
            messagebox.showwarning('No Files', 'Add script files first.')
            return False
        if not self._scenes:
            messagebox.showwarning('No Scenes', 'No numbered lines found in files.')
            return False
        self._stop.clear()
        return True

    # ── Step buttons ─────────────────────────────────────────────────────────

    def _run_step1(self):
        if not self._guard(): return
        self._names_box.delete('1.0', 'end')
        threading.Thread(target=self._t_step1, daemon=True).start()

    def _parse_names_box(self):
        """Parse names_box lines → update self._names and self._main_chars."""
        raw = self._names_box.get('1.0', 'end').strip()
        self._names = []
        self._main_chars = set()
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith('★'):
                name = line[1:].strip()
                if name:
                    self._names.append(name)
                    self._main_chars.add(name)
            else:
                self._names.append(line)

    def _run_step2(self):
        if not self._guard(): return
        if not self._names_box.get('1.0', 'end').strip():
            messagebox.showwarning('No Names', 'Run Step 1 first, or type names manually.')
            return
        self._parse_names_box()
        self._refs_box.delete('1.0', 'end')
        self._char_refs.clear()
        threading.Thread(target=self._t_step2, daemon=True).start()

    def _run_step3(self):
        if not self._guard(): return
        self._prompts_box.delete('1.0', 'end')
        threading.Thread(target=self._t_step3, daemon=True).start()

    def _run_all(self):
        if not self._guard(): return
        self._names_box.delete('1.0', 'end')
        self._refs_box.delete('1.0', 'end')
        self._prompts_box.delete('1.0', 'end')
        self._char_refs.clear()
        self._main_chars.clear()
        threading.Thread(target=self._t_all, daemon=True).start()

    # ── Threads ───────────────────────────────────────────────────────────────

    def _t_step1(self):
        self._busy(True)
        self._st('Step 1 — Extracting character names...')
        self._pg(0.1)
        try:
            lines = self._extract_names()  # returns display lines with ★ prefix
            self._main_chars = {l[1:].strip() for l in lines if l.startswith('★')}
            self._names = [l[1:].strip() if l.startswith('★') else l for l in lines]
            display = '\n'.join(lines)
            self.after(0, lambda: (
                self._names_box.delete('1.0', 'end'),
                self._names_box.insert('1.0', display)
            ))
            main_n = len(self._main_chars)
            total_n = len(self._names)
            self._st(f'Step 1 done — {main_n} main + {total_n - main_n} supporting. Edit, then Step 2.')
            self._pg(1.0)
        except Exception as e:
            self._st(f'Error: {e}')
            self._append(self._names_box, f'Error: {e}\n')
            self.after(0, lambda err=str(e): messagebox.showerror('Step 1 Error', err))
        finally:
            self._busy(False)
            self._refresh_conv_id()

    def _t_step2(self):
        self._busy(True)
        total = len(self._names)
        self._st(f'Step 2 — Generating references for {total} characters...')
        self._pg(0.0)
        try:
            for i, name in enumerate(self._names):
                if self._stop.is_set(): break
                is_main = name in self._main_chars
                tag = '★ ' if is_main else ''
                self._st(f'Step 2 — {tag}"{name}" ({i+1}/{total})...')
                self._pg(i / total)
                try:
                    label = f'★ {name} [MAIN]' if is_main else name
                    self._append(self._refs_box, f'=== {label} ===\n')
                    ref = self._gen_char_ref(name, is_main=is_main)
                    self._char_refs[name] = ref
                    self._append(self._refs_box, '\n\n')
                except Exception as e:
                    self._append(self._refs_box, f'Error: {e}\n\n')
                    self.after(0, lambda err=str(e): messagebox.showerror('Step 2 Error', err))
                time.sleep(0.1)
            self._st(f'Step 2 done — {len(self._char_refs)} refs ready. Now run Step 3.')
            self._pg(1.0)
        finally:
            self._busy(False)
            self._refresh_conv_id()

    def _t_step3(self):
        self._busy(True)
        scenes = self._scenes
        try:
            self._run_scene_prompts(scenes, p0=0.0, p1=1.0)
            self._st(f'Step 3 done — {len(scenes)} scene prompts generated.')
        except Exception as e:
            self._st(f'Error: {e}')
            self.after(0, lambda err=str(e): messagebox.showerror('Step 3 Error', err))
        finally:
            self._busy(False)
            self._refresh_conv_id()

    def _t_all(self):
        self._busy(True)

        # Step 1
        self._st('Step 1/3 — Extracting names...')
        self._pg(0.02)
        try:
            lines = self._extract_names()
            self._main_chars = {l[1:].strip() for l in lines if l.startswith('★')}
            self._names = [l[1:].strip() if l.startswith('★') else l for l in lines]
            display = '\n'.join(lines)
            self.after(0, lambda: (
                self._names_box.delete('1.0', 'end'),
                self._names_box.insert('1.0', display)
            ))
            main_n = len(self._main_chars)
            self._st(f'Step 1 done — {main_n} main + {len(self._names)-main_n} supporting.')
        except Exception as e:
            self._append(self._names_box, f'Error: {e}\n')
            self._names = []

        if self._stop.is_set():
            self._st('Stopped.')
            self._busy(False)
            return

        # Step 2
        total = len(self._names)
        self._st(f'Step 2/3 — Generating {total} character refs...')
        for i, name in enumerate(self._names):
            if self._stop.is_set(): break
            is_main = name in self._main_chars
            tag = '★ ' if is_main else ''
            self._st(f'Step 2/3 — {tag}"{name}" ({i+1}/{total})...')
            self._pg(0.1 + 0.35 * (i / max(total, 1)))
            try:
                label = f'★ {name} [MAIN]' if is_main else name
                self._append(self._refs_box, f'=== {label} ===\n')
                ref = self._gen_char_ref(name, is_main=is_main)
                self._char_refs[name] = ref
                self._append(self._refs_box, '\n\n')
            except Exception as e:
                self._append(self._refs_box, f'Error: {e}\n\n')
            time.sleep(0.5)

        if self._stop.is_set():
            self._st('Stopped.')
            self._busy(False)
            return

        # Step 3
        self._st('Step 3/3 — Generating scene prompts...')
        self._run_scene_prompts(self._scenes, p0=0.45, p1=1.0)
        self._st(f'All done — {len(self._scenes)} scenes processed.')
        self._busy(False)
        self._refresh_conv_id()

    # ── AI calls ──────────────────────────────────────────────────────────────

    def _extract_names(self) -> list[str]:
        """Returns display lines: '★ Name' for main chars, 'Name' for supporting."""
        text = self._all_text()
        if len(text) > 5_000:
            text = text[:5_000] + '\n...[truncated]'
        prompt = (
            'Read the story text below. Extract ALL named characters and identify '
            'which are MAIN characters (appear frequently, drive the plot) vs SUPPORTING.\n\n'
            'Output format — one name per line:\n'
            '★ Name   (for main characters)\n'
            'Name     (for supporting characters)\n\n'
            'Rules: no numbering, no descriptions, no extra text — ONLY the lines above.\n\n'
            f'Story:\n{text}\n\n'
            'Characters:'
        )
        result = ask(prompt)
        lines = []
        for line in result.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith('★'):
                name = line[1:].strip().lstrip('•-–—*0123456789. ').strip()
                if name and 1 < len(name) < 50:
                    lines.append(f'★ {name}')
            else:
                name = line.lstrip('•-–—*0123456789. ').strip()
                if name and 1 < len(name) < 50:
                    lines.append(name)
        return lines

    def _gen_char_ref(self, name: str, is_main: bool = False) -> str:
        text = self._all_text()
        if len(text) > 4_000:
            text = text[:4_000] + '\n...[truncated]'
        style = self._style()
        if is_main:
            detail = (
                f'Describe in detail: gender, age range, hair (color/style), eyes, '
                f'skin tone, clothing/outfit, weapons or items, body build, '
                f'distinctive features, overall impression.\n'
                f'Output: comma-separated prompt, 2-3 lines, no intro text, no name.'
            )
        else:
            detail = (
                f'Describe briefly: gender, age, hair, eyes, clothing, key features.\n'
                f'Output: one comma-separated prompt, 1 line, no intro text, no name.'
            )
        prompt = (
            f'Based on the story text below, create a visual reference prompt '
            f'for image generation of the character "{name}".\n\n'
            f'Art style: {style}\n\n'
            f'{detail}\n\n'
            f'Story:\n{text}\n\n'
            f'Prompt for {name}:'
        )
        result = ask(prompt)
        self._append(self._refs_box, result)
        return result

    # 10 scenes per request — 20 caused truncation on free providers
    SCENES_PER_REQ = 10

    def _gen_scene_batch(self, chunk: list[tuple[int, str]], style: str,
                          char_block: str) -> dict[int, str]:
        scene_lines = '\n'.join(f'SCENE {n}: {d}' for n, d in chunk)
        nums = [n for n, _ in chunk]
        count = len(nums)
        num_str = ', '.join(str(n) for n in nums)
        prompt = (
            f'Art style: {style}\n\n'
            f'{char_block}'
            f'Write exactly {count} image-generation prompts for scenes: {num_str}.\n'
            f'You MUST output ALL {count} scenes. Do NOT skip any scene number.\n'
            f'Output ONLY lines in this exact format — nothing else:\n'
            f'[N] prompt text here\n\n'
            f'Rules: N = scene number, one line per scene, no intro, no commentary. '
            f'Each prompt: characters, setting, action, mood, lighting.\n\n'
            f'Scenes:\n{scene_lines}\n\nPrompts:'
        )
        resp = ask(prompt)
        parsed = _parse_scene_prompts(resp)
        return parsed

    def _run_scene_prompts(self, scenes: list[tuple[int, str]],
                            p0: float, p1: float):
        style = self._style()
        total = len(scenes)

        char_block = ''
        if self._char_refs:
            lines = [f'- {n}: {r.splitlines()[0]}'
                     for n, r in self._char_refs.items()]
            char_block = 'Character references:\n' + '\n'.join(lines) + '\n\n'

        batches = [scenes[i:i + self.SCENES_PER_REQ]
                   for i in range(0, total, self.SCENES_PER_REQ)]
        n_batches = len(batches)
        # workers = number of live servers; each server handles 1 request at a time
        workers = max(1, len(_api_pool) if _api_pool else 1)

        done_scenes = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_batch = {
                pool.submit(self._gen_scene_batch, batch, style, char_block): batch
                for batch in batches
            }

            for fut in as_completed(future_to_batch):
                if self._stop.is_set():
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                batch = future_to_batch[fut]
                try:
                    batch_results = fut.result()
                    for n, _ in batch:
                        if n not in batch_results:
                            batch_results[n] = '[missing]'
                except Exception as e:
                    batch_results = {n: f'[error: {e}]' for n, _ in batch}

                out = ''.join(
                    f'[{n}] {batch_results[n]}\n'
                    for n, _ in sorted(batch, key=lambda x: x[0])
                )
                if out:
                    self._append(self._prompts_box, out)

                done_scenes += len(batch)
                self._st(f'Step 3 — {done_scenes}/{total} scenes done...')
                self._pg(p0 + (p1 - p0) * (done_scenes / total))
                # Brief pause between batches to avoid triggering ChatGPT rate limiting
                if done_scenes < total and not self._stop.is_set():
                    time.sleep(1)

        self._pg(p1)


# ─── Entry ───────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = App()
    app.protocol('WM_DELETE_WINDOW', lambda: (_cleanup_servers(), app.destroy()))
    app.mainloop()

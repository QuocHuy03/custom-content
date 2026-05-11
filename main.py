#!/usr/bin/env python3
"""
Anime Prompt Generator
- Output 1: Image prompts (numbered) theo từng scene
- Output 2: Thông tin nhân vật từ tất cả file
- AI: ChatGPT free (không cần API key)
"""

import sys, subprocess

for _pkg in ['requests', 'customtkinter']:
    try:
        __import__(_pkg)
    except ImportError:
        print(f'Installing {_pkg}...')
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', _pkg])

import os, re, json, time, uuid, random, base64, hashlib, threading
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox

import requests
import customtkinter as ctk

# ─── Open Sans font (auto-download nếu chưa có) ──────────────────────────────

def _load_open_sans():
    import ctypes
    font_dir = Path(__file__).parent / 'fonts'
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
            ctypes.windll.gdi32.AddFontResourceW(str(fp))
    try:
        ctypes.windll.user32.SendMessageW(0xFFFF, 0x001D, 0, 0)
    except Exception:
        pass

_load_open_sans()
FONT = 'Open Sans'

# ─── ChatGPT Free API (port từ chatgpt-free.js) ───────────────────────────────

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36')
_csrf_cache: str | None = None


def _rnd_ip() -> str:
    return '.'.join(str(random.randint(0, 255)) for _ in range(4))


def _headers(accept: str, did: str) -> dict:
    ip = _rnd_ip()
    return {
        'accept': accept, 'Content-Type': 'application/json',
        'cache-control': 'no-cache', 'Referer': 'https://chatgpt.com/',
        'oai-device-id': did, 'oai-language': 'en', 'User-Agent': UA,
        'pragma': 'no-cache', 'priority': 'u=1, i',
        'sec-ch-ua': '"Not A(Brand";v="8","Chromium";v="132","Google Chrome";v="132"',
        'sec-ch-ua-mobile': '?0', 'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-site': 'same-origin', 'sec-fetch-mode': 'cors',
        'X-Forwarded-For': ip, 'X-Real-IP': ip,
    }


def _fake_sentinel() -> str:
    cfg = [
        random.randint(3000, 6001),
        datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT+0100 (Central European Time)'),
        4294705152, 0, UA, 'de', 'de', 401,
        'mediaSession', 'location', 'scrollX',
        f'{random.uniform(1000, 5000):.4f}', str(uuid.uuid4()), '', 12,
        int(time.time() * 1000),
    ]
    return 'gAAAAAC' + base64.b64encode(json.dumps(cfg).encode()).decode()


def _solve_pow(seed: str, difficulty: str) -> str:
    cfg = [
        random.choice([8, 12, 16, 24]) + random.choice([3000, 4000, 6000]),
        datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT+0100 (Central European Time)'),
        4294705152, 0, UA,
    ]
    diff_len = len(difficulty) // 2
    for i in range(100_000):
        cfg[3] = i
        b64 = base64.b64encode(json.dumps(cfg).encode()).decode()
        h = hashlib.new('sha3_512', (seed + b64).encode()).hexdigest()
        if h[:diff_len] <= difficulty:
            return 'gAAAAAB' + b64
    return 'gAAAAABwQ8Lk5FbGpA2NcR9' + base64.b64encode(f'"{seed}"'.encode()).decode()


def _get_csrf(did: str) -> str:
    global _csrf_cache
    if _csrf_cache:
        return _csrf_cache
    r = requests.get('https://chatgpt.com/api/auth/csrf',
                     headers=_headers('application/json', did), timeout=20)
    tok = r.json().get('csrfToken')
    if not tok:
        raise RuntimeError('Cannot obtain CSRF token')
    _csrf_cache = tok
    return tok


def _get_sentinel(did: str, csrf: str) -> tuple[dict, str]:
    cookie = f'__Host-next-auth.csrf-token={csrf}; oai-did={did}; oai-nav-state=1;'
    r = requests.post(
        'https://chatgpt.com/backend-anon/sentinel/chat-requirements',
        headers={**_headers('application/json', did), 'Cookie': cookie},
        json={'p': _fake_sentinel()}, timeout=20,
    )
    d = r.json()
    oai_sc = ''
    raw_cookies = r.headers.get('set-cookie', '')
    if 'oai-sc=' in raw_cookies:
        for part in raw_cookies.split(';'):
            if 'oai-sc=' in part:
                oai_sc = part.split('oai-sc=')[-1].strip()
                break
    return d, oai_sc


def _new_session() -> dict:
    global _csrf_cache
    did = str(uuid.uuid4())
    csrf = _get_csrf(did)
    d, oai_sc = _get_sentinel(did, csrf)
    if not d.get('token') or not d.get('proofofwork'):
        _csrf_cache = None
        csrf = _get_csrf(did)
        d, oai_sc = _get_sentinel(did, csrf)
    if not d.get('token'):
        raise RuntimeError('Cannot obtain sentinel token')
    pow_ = d['proofofwork']
    return {
        'did': did, 'csrf': csrf, 'oai_sc': oai_sc,
        'token': d['token'],
        'proof': _solve_pow(pow_['seed'], pow_['difficulty']),
    }


def _extract_msg_text(msg: dict) -> tuple[str, bool]:
    """Return (text, is_done) from an assistant message dict."""
    if not isinstance(msg, dict):
        return '', False
    if not (isinstance(msg.get('author'), dict) and
            msg['author'].get('role') == 'assistant'):
        return '', False
    content = msg.get('content') or {}
    text = ''
    if isinstance(content, dict):
        parts = content.get('parts', [])
        if parts and isinstance(parts[0], str):
            text = parts[0]
    done = msg.get('status') == 'finished_successfully'
    return text, done


def _parse_sse(resp) -> str:
    text, done, buf = '', False, ''
    for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
        if not chunk:
            continue
        buf += chunk
        lines = buf.split('\n')
        buf = lines[-1]
        for line in lines[:-1]:
            if not line.startswith('data:'):
                continue
            raw = line[5:].strip()
            if not raw or raw == '[DONE]':
                continue
            try:
                j = json.loads(raw)
            except Exception:
                continue

            # Skip non-dict (e.g. the bare "v1" string token)
            if not isinstance(j, dict):
                continue

            err = j.get('error')
            if err:
                msg_text = err.get('message', str(err)) if isinstance(err, dict) else str(err)
                raise RuntimeError(msg_text)

            p, o, v = j.get('p'), j.get('o'), j.get('v')

            # Format A: top-level {message: {...}}
            if isinstance(j.get('message'), dict):
                t, fin = _extract_msg_text(j['message'])
                if t:
                    text = t
                if fin:
                    done = True

            # Format B: single patch op on message fields
            if isinstance(p, str) and isinstance(o, str):
                if p == '/message/content/parts/0':
                    if o in ('append', 'add') and isinstance(v, str):
                        text += v
                    elif o == 'replace' and isinstance(v, str):
                        text = v
                if p == '/message/status' and o == 'replace' and v == 'finished_successfully':
                    done = True

            # Format C: {o:"add", p:"", v:{message:{...}}} — root add op
            if o in ('add', 'replace') and isinstance(v, dict):
                inner = v.get('message')
                if inner:
                    t, fin = _extract_msg_text(inner)
                    if t:
                        text = t
                    if fin:
                        done = True

            # Format D: {v: {message: {...}}} — v1 wrapper (p/o absent)
            if p is None and o is None and isinstance(v, dict):
                inner = v.get('message')
                if inner:
                    t, fin = _extract_msg_text(inner)
                    if t:
                        text = t
                    if fin:
                        done = True

            # Format E: {o:"patch", v:[...ops...]}
            ops_list: list = []
            if o == 'patch' and isinstance(v, list):
                ops_list = v
            elif p is None and o is None and isinstance(v, list):
                ops_list = v
            for op in ops_list:
                if not isinstance(op, dict):
                    continue
                op_p, op_o, op_v = op.get('p'), op.get('o'), op.get('v')
                if op_p == '/message/content/parts/0':
                    if op_o in ('append', 'add') and isinstance(op_v, str):
                        text += op_v
                    elif op_o == 'replace' and isinstance(op_v, str):
                        text = op_v
                if op_p == '/message/status' and op_o == 'replace' and op_v == 'finished_successfully':
                    done = True

            if j.get('type') == 'message_stream_complete':
                done = True
        if done:
            break
    return text


def chatgpt_ask(message: str) -> str:
    sess = _new_session()
    cookie = (f'__Host-next-auth.csrf-token={sess["csrf"]}; '
              f'oai-did={sess["did"]}; oai-nav-state=1; oai-sc={sess["oai_sc"]};')
    r = requests.post(
        'https://chatgpt.com/backend-anon/conversation',
        headers={
            **_headers('text/event-stream', sess['did']),
            'Cookie': cookie,
            'openai-sentinel-chat-requirements-token': sess['token'],
            'openai-sentinel-proof-token': sess['proof'],
        },
        json={
            'action': 'next',
            'messages': [{
                'id': str(uuid.uuid4()),
                'author': {'role': 'user'},
                'create_time': int(time.time() * 1000),
                'content': {'content_type': 'text', 'parts': [message]},
                'metadata': {'serialization_metadata': {'custom_symbol_offsets': []},
                             'dictation': False},
            }],
            'paragen_cot_summary_display_override': 'allow',
            'parent_message_id': 'client-created-root',
            'model': 'auto',
            'timezone_offset_min': -420,
            'timezone': 'Asia/Saigon',
            'suggestions': [],
            'history_and_training_disabled': True,
            'conversation_mode': {'kind': 'primary_assistant'},
            'system_hints': [],
            'supports_buffering': True,
            'supported_encodings': ['v1'],
            'client_contextual_info': {
                'is_dark_mode': True, 'time_since_loaded': 7,
                'page_height': 911, 'page_width': 1080, 'pixel_ratio': 1,
                'screen_height': 1080, 'screen_width': 1920,
                'app_name': 'chatgpt.com',
            },
        },
        stream=True, timeout=120,
    )
    if not r.ok:
        raise RuntimeError(f'HTTP {r.status_code}: {r.text[:300]}')
    text = _parse_sse(r)
    if not text:
        raise RuntimeError('ChatGPT returned empty response')
    return text


# ─── Text Parsing ─────────────────────────────────────────────────────────────

def parse_scenes(text: str) -> list[tuple[int, str]]:
    """Return [(scene_num, content), ...] from numbered paragraphs."""
    scenes = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^(\d+)[_\.\s]\s*(.+)', line)
        if m:
            scenes.append((int(m.group(1)), m.group(2).strip()))
    return scenes


# ─── Style Presets ────────────────────────────────────────────────────────────

STYLES: dict[str, str] = {
    'MAPPA Anime':
        'anime style, MAPPA studio quality, cinematic lighting, detailed key animation, dramatic camera angles, high production value',
    'Solo Leveling Manhwa':
        'Korean manhwa art style, Solo Leveling aesthetic, dark fantasy, dynamic action, vibrant power aura effects, webtoon digital coloring',
    'Demon Slayer (ufotable)':
        'anime style, ufotable studio, Demon Slayer aesthetic, lush detailed backgrounds, vivid breath/water/fire FX, warm golden lighting',
    'Jujutsu Kaisen (MAPPA)':
        'anime style, MAPPA studio, Jujutsu Kaisen aesthetic, gritty urban setting, cursed energy purple aura, high-contrast cinematic lighting',
    'Vinland Saga (WIT)':
        'anime style, WIT Studio, Vinland Saga aesthetic, medieval Norse setting, realistic proportions, cold muted color palette',
    'Omniscient Reader':
        'Korean manhwa, Omniscient Reader Viewpoint aesthetic, post-apocalyptic dimensional rifts, vivid power effects, webtoon style',
    'Berserk Dark Fantasy':
        'dark fantasy manga, Berserk aesthetic, grim medieval horror, extreme shadow contrast, heavy crosshatching, brutal atmosphere',
    'Classic Ghibli':
        'Studio Ghibli anime film, soft watercolor backgrounds, warm natural lighting, expressive character faces, lush environments',
    'Manhwa Action':
        'Korean manhwa action webtoon, dynamic fight poses, bold outlines, vibrant color flats, impact speed lines, dramatic expression',
}


# ─── Application ─────────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('Content AI  —  @huyit32')
        self.geometry('1300x840')
        self.minsize(950, 620)
        ctk.set_appearance_mode('dark')
        ctk.set_default_color_theme('blue')

        self._files: list[tuple[Path, str]] = []
        self._stop = threading.Event()

        self._build()

    # ── UI construction ───────────────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=300, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky='nsew')
        sidebar.grid_propagate(False)

        main = ctk.CTkFrame(self, fg_color='transparent')
        main.grid(row=0, column=1, padx=10, pady=10, sticky='nsew')
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        self._build_sidebar(sidebar)
        self._build_main(main)

    def _build_sidebar(self, p):
        p.grid_columnconfigure(0, weight=1)

        # Header
        ctk.CTkLabel(p, text='Content AI',
                     font=ctk.CTkFont(family=FONT,size=18, weight='bold')).grid(
            row=0, column=0, padx=16, pady=(22, 6), sticky='ew')
        ctk.CTkLabel(p, text='@huyit32  •  ChatGPT Free',
                     font=ctk.CTkFont(family=FONT,size=11), text_color='gray55').grid(
            row=1, column=0, padx=16, pady=(0, 14), sticky='ew')

        self._divider(p, row=2)

        # Files section
        ctk.CTkLabel(p, text='INPUT FILES', font=ctk.CTkFont(family=FONT,size=11, weight='bold'),
                     text_color='gray55').grid(row=3, column=0, padx=16, pady=(10, 4), sticky='w')

        bf = ctk.CTkFrame(p, fg_color='transparent')
        bf.grid(row=4, column=0, padx=16, pady=2, sticky='ew')
        bf.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(bf, text='Add Files', height=32,
                      command=self._add_files).grid(row=0, column=0, sticky='ew', padx=(0, 5))
        ctk.CTkButton(bf, text='X', width=36, height=32,
                      fg_color='#3a3a3a', hover_color='#555',
                      command=self._clear_files).grid(row=0, column=1)

        self._file_box = ctk.CTkTextbox(p, height=115, state='disabled',
                                         font=ctk.CTkFont(family=FONT,size=11), corner_radius=6)
        self._file_box.grid(row=5, column=0, padx=16, pady=(4, 10), sticky='ew')

        self._divider(p, row=6)

        # Style section
        ctk.CTkLabel(p, text='STYLE', font=ctk.CTkFont(family=FONT,size=11, weight='bold'),
                     text_color='gray55').grid(row=7, column=0, padx=16, pady=(10, 4), sticky='w')

        self._style_var = ctk.StringVar(value='MAPPA Anime')
        ctk.CTkOptionMenu(p, values=list(STYLES.keys()),
                          variable=self._style_var, height=34,
                          font=ctk.CTkFont(family=FONT,size=12)).grid(
            row=8, column=0, padx=16, pady=2, sticky='ew')

        ctk.CTkLabel(p, text='Extra notes (optional):', font=ctk.CTkFont(family=FONT,size=11),
                     text_color='gray65').grid(row=9, column=0, padx=16, pady=(8, 2), sticky='w')
        self._extra = ctk.CTkEntry(p, placeholder_text='e.g. rain, night, close-up…', height=32)
        self._extra.grid(row=10, column=0, padx=16, pady=(0, 10), sticky='ew')

        self._divider(p, row=11)

        # Batch size
        bf2 = ctk.CTkFrame(p, fg_color='transparent')
        bf2.grid(row=12, column=0, padx=16, pady=8, sticky='ew')
        bf2.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bf2, text='Scenes per API call:',
                     font=ctk.CTkFont(family=FONT,size=11)).grid(row=0, column=0, sticky='w')
        self._batch = ctk.CTkEntry(bf2, width=58, height=28)
        self._batch.insert(0, '8')
        self._batch.grid(row=0, column=1)

        self._divider(p, row=13)

        # Action buttons
        actions = [
            ('Generate Image Prompts',  '#1a5fa8', '#0d3e72', self._gen_prompts),
            ('Extract Characters',      '#2e7d32', '#1b4d1e', self._extract_chars),
            ('Generate All (Both)',     '#6a1b9a', '#4a1370', self._gen_all),
        ]
        for i, (label, fg, hv, cmd) in enumerate(actions):
            ctk.CTkButton(p, text=label, height=40, fg_color=fg, hover_color=hv,
                          command=cmd, font=ctk.CTkFont(family=FONT,size=13, weight='bold'),
                          corner_radius=8).grid(
                row=14 + i, column=0, padx=16, pady=4, sticky='ew')

        self._stop_btn = ctk.CTkButton(
            p, text='Stop', height=32, fg_color='#8b2020', hover_color='#5e1515',
            state='disabled', command=self._do_stop, corner_radius=8)
        self._stop_btn.grid(row=17, column=0, padx=16, pady=(4, 20), sticky='ew')

    def _build_main(self, p):
        # Status bar
        sf = ctk.CTkFrame(p, corner_radius=8)
        sf.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        sf.grid_columnconfigure(0, weight=1)

        self._status = ctk.CTkLabel(sf, text='Ready — add files then click Generate.',
                                    anchor='w', font=ctk.CTkFont(family=FONT,size=12))
        self._status.grid(row=0, column=0, padx=14, pady=(8, 3), sticky='ew')
        self._prog = ctk.CTkProgressBar(sf, height=7, corner_radius=4)
        self._prog.grid(row=1, column=0, padx=14, pady=(0, 10), sticky='ew')
        self._prog.set(0)

        # Tabview
        self._tabs = ctk.CTkTabview(p, corner_radius=10)
        self._tabs.grid(row=1, column=0, sticky='nsew')

        tab_p = self._tabs.add('Image Prompts')
        tab_c = self._tabs.add('Characters')

        for tab in (tab_p, tab_c):
            tab.grid_rowconfigure(0, weight=1)
            tab.grid_columnconfigure(0, weight=1)

        self._prompts_box = ctk.CTkTextbox(tab_p, font=ctk.CTkFont(family=FONT,size=12), wrap='word')
        self._prompts_box.grid(row=0, column=0, sticky='nsew', padx=4, pady=4)

        self._chars_box = ctk.CTkTextbox(tab_c, font=ctk.CTkFont(family=FONT,size=12), wrap='word')
        self._chars_box.grid(row=0, column=0, sticky='nsew', padx=4, pady=4)

        # Toolbar
        tb = ctk.CTkFrame(p, fg_color='transparent')
        tb.grid(row=2, column=0, sticky='ew', pady=(6, 0))

        for label, cmd in [
            ('Copy Prompts',     lambda: self._copy(self._prompts_box)),
            ('Copy Characters',  lambda: self._copy(self._chars_box)),
            ('Save Prompts',     lambda: self._save(self._prompts_box, 'prompts')),
            ('Save Characters',  lambda: self._save(self._chars_box, 'characters')),
            ('Clear Output',     self._clear_output),
        ]:
            ctk.CTkButton(tb, text=label, height=30, width=135,
                          command=cmd, font=ctk.CTkFont(family=FONT,size=11)).pack(side='left', padx=3)

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _divider(parent, row):
        ctk.CTkFrame(parent, height=1, fg_color='gray30').grid(
            row=row, column=0, padx=16, pady=2, sticky='ew')

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title='Select story text files',
            filetypes=[('Text', '*.txt'), ('All', '*.*')])
        existing = {p for p, _ in self._files}
        for fp in paths:
            p = Path(fp)
            if p not in existing:
                self._files.append((p, p.read_text(encoding='utf-8', errors='ignore')))
        self._refresh_files()

    def _clear_files(self):
        self._files.clear()
        self._refresh_files()

    def _refresh_files(self):
        self._file_box.configure(state='normal')
        self._file_box.delete('1.0', 'end')
        for p, _ in self._files:
            self._file_box.insert('end', f'• {p.name}\n')
        self._file_box.configure(state='disabled')

    def _all_text(self) -> str:
        return '\n'.join(c for _, c in self._files)

    def _style_str(self) -> str:
        base = STYLES.get(self._style_var.get(), '')
        extra = self._extra.get().strip()
        return f'{base}, {extra}' if extra else base

    def _batch_n(self) -> int:
        try:
            return max(1, int(self._batch.get()))
        except Exception:
            return 8

    # Thread-safe UI updates
    def _ui_status(self, msg: str):
        self.after(0, lambda: self._status.configure(text=msg))

    def _ui_prog(self, v: float):
        self.after(0, lambda: self._prog.set(max(0.0, min(1.0, v))))

    def _ui_append(self, box, text: str):
        def _do():
            box.insert('end', text)
            box.see('end')
        self.after(0, _do)

    def _ui_set_busy(self, busy: bool):
        def _do():
            self._stop_btn.configure(state='normal' if busy else 'disabled')
        self.after(0, _do)

    def _copy(self, box):
        text = box.get('1.0', 'end').strip()
        self.clipboard_clear()
        self.clipboard_append(text)
        messagebox.showinfo('Copied', 'Copied to clipboard!')

    def _save(self, box, name: str):
        text = box.get('1.0', 'end').strip()
        if not text:
            messagebox.showwarning('Empty', 'Nothing to save.')
            return
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = filedialog.asksaveasfilename(
            defaultextension='.txt',
            initialfile=f'{name}_{ts}.txt',
            filetypes=[('Text', '*.txt')])
        if path:
            Path(path).write_text(text, encoding='utf-8')
            messagebox.showinfo('Saved', f'Saved to:\n{path}')

    def _clear_output(self):
        self._prompts_box.delete('1.0', 'end')
        self._chars_box.delete('1.0', 'end')
        self._prog.set(0)
        self._status.configure(text='Output cleared.')

    def _do_stop(self):
        self._stop.set()
        self._ui_status('Stopping after current request…')

    def _guard(self) -> bool:
        if not self._files:
            messagebox.showwarning('No Files', 'Please add story text files first.')
            return False
        self._stop.clear()
        return True

    # ── generation ────────────────────────────────────────────────────────

    def _gen_prompts(self):
        if not self._guard():
            return
        self._prompts_box.delete('1.0', 'end')
        threading.Thread(target=self._t_prompts, daemon=True).start()

    def _extract_chars(self):
        if not self._guard():
            return
        self._chars_box.delete('1.0', 'end')
        threading.Thread(target=self._t_chars, daemon=True).start()

    def _gen_all(self):
        if not self._guard():
            return
        self._prompts_box.delete('1.0', 'end')
        self._chars_box.delete('1.0', 'end')
        threading.Thread(target=self._t_all, daemon=True).start()

    # ── threads ───────────────────────────────────────────────────────────

    def _t_prompts(self):
        self._ui_set_busy(True)
        scenes = parse_scenes(self._all_text())
        if not scenes:
            self.after(0, lambda: messagebox.showwarning(
                'No scenes',
                'No numbered paragraphs found.\n\nExpected format:\n  1_Scene text\n  2_Scene text'))
            self._ui_set_busy(False)
            return
        self._run_prompts(scenes, p0=0.0, p1=1.0)
        self._ui_status(f'✓ Done! Generated prompts for {len(scenes)} scenes.')
        self._ui_set_busy(False)

    def _t_chars(self):
        self._ui_set_busy(True)
        self._ui_status('Extracting characters…')
        self._ui_prog(0.15)
        try:
            result = self._ask_chars()
            self._ui_append(self._chars_box, result)
            self._ui_status('✓ Characters extracted!')
        except Exception as e:
            self._ui_append(self._chars_box, f'❌ Error: {e}\n')
            self._ui_status(f'Error: {e}')
        self._ui_prog(1.0)
        self._ui_set_busy(False)

    def _t_all(self):
        self._ui_set_busy(True)

        # Step 1: characters
        self._ui_status('Step 1 / 2 — Extracting characters…')
        self._ui_prog(0.05)
        try:
            chars = self._ask_chars()
            self._ui_append(self._chars_box, chars)
        except Exception as e:
            self._ui_append(self._chars_box, f'❌ Error: {e}\n')

        if self._stop.is_set():
            self._ui_status('Stopped.')
            self._ui_set_busy(False)
            return

        # Step 2: prompts
        self._ui_status('Step 2 / 2 — Generating image prompts…')
        scenes = parse_scenes(self._all_text())
        if scenes:
            self._run_prompts(scenes, p0=0.5, p1=1.0)
        self._ui_status(f'✓ All done! {len(scenes)} scenes processed.')
        self._ui_set_busy(False)

    def _ask_chars(self) -> str:
        text = self._all_text()
        if len(text) > 12_000:
            text = text[:12_000] + '\n…[truncated for length]'

        prompt = (
            'You are a story analyst. Read the following story text and extract ALL named characters.\n\n'
            'For each character, provide a structured entry:\n'
            '**Name**: <character name>\n'
            '- Role: protagonist / antagonist / supporting / servant / etc.\n'
            '- Appearance: (describe if mentioned)\n'
            '- Personality: key traits\n'
            '- Story role: what they do or represent\n\n'
            f'Story text:\n{text}\n\n'
            'List every named character you can find:'
        )
        return chatgpt_ask(prompt)

    def _run_prompts(self, scenes: list[tuple[int, str]], p0: float, p1: float):
        style = self._style_str()
        batch = self._batch_n()
        total = len(scenes)

        for i in range(0, total, batch):
            if self._stop.is_set():
                break

            chunk = scenes[i:i + batch]
            first_n, last_n = chunk[0][0], chunk[-1][0]
            self._ui_status(
                f'Generating prompts — scenes {first_n}–{last_n} '
                f'({i + len(chunk)}/{total})…')
            self._ui_prog(p0 + (p1 - p0) * (i / total))

            scene_lines = '\n'.join(f'[{n}] {c}' for n, c in chunk)
            prompt = (
                f'You are an expert anime/illustration image-prompt writer.\n'
                f'Base art style: {style}\n\n'
                f'For each numbered scene below, write one detailed image-generation prompt in English.\n'
                f'Requirements:\n'
                f'- Keep the exact scene number in brackets, e.g. [1]\n'
                f'- 1–3 sentences: describe characters, setting, action, mood, lighting\n'
                f'- Weave the art style naturally into the description\n'
                f'- Output format strictly: [N] <prompt text>\n'
                f'- One scene per line, no extra commentary\n\n'
                f'Scenes:\n{scene_lines}\n\n'
                f'Prompts:'
            )

            try:
                resp = chatgpt_ask(prompt)
                parsed = re.findall(r'\[(\d+)\]\s*(.+?)(?=\n\[|\Z)', resp, re.DOTALL)
                if parsed:
                    out = ''.join(f'[{n}] {pt.strip()}\n\n' for n, pt in parsed)
                else:
                    out = resp.strip() + '\n\n'
                self._ui_append(self._prompts_box, out)
            except Exception as e:
                self._ui_append(self._prompts_box,
                                f'❌ Error (scenes {first_n}–{last_n}): {e}\n\n')

            time.sleep(1.5)

        self._ui_prog(p1)


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = App()
    app.mainloop()

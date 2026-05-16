#!/usr/bin/env python3
"""Content AI - @huyit32"""

import sys, subprocess, ctypes, os

# Server mode: frozen exe re-launched as API server subprocess
if '--server' in sys.argv:
    if sys.stdout is None: sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None: sys.stderr = open(os.devnull, 'w')
    from api_server import app as _fastapi_app
    import uvicorn
    uvicorn.run(_fastapi_app, host='0.0.0.0', port=int(os.environ.get('PORT', 6969)))
    sys.exit(0)

if not getattr(sys, 'frozen', False):
    for _pkg, _mod in [('requests', 'requests'), ('PyQt5', 'PyQt5')]:
        try:
            __import__(_mod)
        except ImportError:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', _pkg])

import re, time, threading, webbrowser, itertools, winreg
from datetime import datetime
from pathlib import Path

import requests
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QDialog,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QTextEdit, QProgressBar,
    QTabWidget, QFileDialog, QMessageBox, QSizePolicy,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QTextCursor

# ─── Auth ────────────────────────────────────────────────────────────────────

_AUTH_URL  = 'https://serverkey.taothaoai.com/api/veo_auto_ai/auth'
_KEY_FILE  = Path(sys.executable).parent / '.auth_key' if getattr(sys, 'frozen', False) \
             else Path(__file__).parent / '.auth_key'


def _get_device_id():
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography')
        val, _ = winreg.QueryValueEx(k, 'MachineGuid')
        winreg.CloseKey(k)
        return val
    except Exception:
        import uuid
        return str(uuid.getnode())


def _verify_key(key: str) -> bool:
    try:
        r = requests.post(
            _AUTH_URL,
            json={'key': key, 'device_id': _get_device_id()},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def _load_key() -> str | None:
    try:
        return _KEY_FILE.read_text('utf-8').strip() or None
    except Exception:
        return None


def _save_key(key: str):
    _KEY_FILE.write_text(key, encoding='utf-8')


# ─── ChatGPT API server pool ─────────────────────────────────────────────────

_API_URL       = 'http://127.0.0.1:6969'
_conv_id       = ''
_api_pool:     list[str]       = []
_api_cycle:    itertools.cycle = None
_api_lock      = threading.Lock()
_server_procs: list            = []
_SERVER_SCRIPT = Path(__file__).parent / 'api_server.py'


def _discover_servers(base_port=6969, count=8):
    found = []
    for port in range(base_port, base_port + count):
        try:
            r = requests.get(f'http://127.0.0.1:{port}/health', timeout=1)
            if r.ok:
                found.append(f'http://127.0.0.1:{port}')
        except Exception:
            pass
    return found or [f'http://127.0.0.1:{base_port}']


def _find_cookie_files():
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    # Try JSON format first
    p = base / 'cookies.json'
    if p.exists():
        try:
            import json as _json
            data = _json.loads(p.read_text('utf-8'))
            # New format: {"cookies": [...], "ua": "..."}
            if isinstance(data, dict):
                cookies = [c.strip() for c in data.get('cookies', []) if isinstance(c, str) and c.strip()]
            else:
                # Old format: ["cookie1", "cookie2"]
                cookies = [c.strip() for c in data if isinstance(c, str) and c.strip()]
            if cookies:
                return cookies
        except Exception:
            pass
    # Fallback: plain text
    for fname in ('cookie.txt', 'chatgpt_cookie.txt'):
        p = base / fname
        if p.exists():
            lines = [l.strip() for l in p.read_text('utf-8').splitlines() if l.strip()]
            if lines:
                return lines
    return [None]


def _load_ua():
    """Load saved User-Agent string from cookies.json, or return None."""
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    p = base / 'cookies.json'
    if p.exists():
        try:
            import json as _json
            data = _json.loads(p.read_text('utf-8'))
            if isinstance(data, dict):
                return (data.get('ua') or '').strip() or None
        except Exception:
            pass
    return None


def _kill_port(port):
    try:
        out = subprocess.check_output(['netstat', '-ano'], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if f':{port} ' in line and 'LISTENING' in line:
                pid = int(line.split()[-1])
                subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True)
                break
    except Exception:
        pass


def _launch_servers(accounts):
    global _server_procs, _api_pool, _api_cycle
    ua = _load_ua()
    ports = []
    for i, cookie_str in enumerate(accounts):
        port = 6969 + i
        _kill_port(port)
        env = os.environ.copy()
        env['PORT'] = str(port)
        if cookie_str is not None:
            env['COOKIE_STRING'] = cookie_str
        if ua:
            env['GPT_USER_AGENT'] = ua
        cmd = ([sys.executable, '--server'] if getattr(sys, 'frozen', False)
               else [sys.executable, str(_SERVER_SCRIPT)])
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


def _next_server():
    global _api_pool, _api_cycle
    with _api_lock:
        if not _api_pool:
            _api_pool  = _discover_servers()
            _api_cycle = itertools.cycle(_api_pool)
        return next(_api_cycle)


def ask(message):
    global _conv_id
    url = _next_server()
    try:
        resp = requests.post(f'{url}/conversation', json={'message': message}, timeout=180)
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f'Không kết nối được API server tại {url}.')
    if resp.status_code != 200:
        raise RuntimeError(f'API server lỗi {resp.status_code}: {resp.text[:200]}')
    data = resp.json()
    text = data.get('result', '').strip()
    if not text:
        raise RuntimeError('API server trả về rỗng.')
    cid = data.get('conv_id') or ''
    if cid:
        _conv_id = cid
    return text


# ─── Helpers ─────────────────────────────────────────────────────────────────

def parse_scenes(text):
    scenes = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^\[?(\d+)\]?[_\.\s]\s*(.+)', line)
        if m:
            scenes.append((int(m.group(1)), m.group(2).strip()))
    return scenes


def _parse_scene_prompts(response):
    result = {}
    current_num = None
    current_parts = []
    for line in response.splitlines():
        line = line.strip()
        if not line:
            if current_num is not None and current_parts:
                result[current_num] = ' '.join(current_parts)
                current_num = None
                current_parts = []
            continue
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


def _chunk_scenes(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _parse_char_refs_bulk(response):
    refs = {}
    current = None
    buf = []
    for line in response.splitlines():
        s = line.strip()
        m = re.match(r'^===\s*(.+?)\s*===\s*$', s)
        if m:
            if current is not None and buf:
                refs[current] = ' '.join(buf)
            raw = m.group(1).replace('[MAIN]', '').replace('★', '').strip()
            current = raw
            buf = []
        elif s and current is not None:
            buf.append(s)
    if current is not None and buf:
        refs[current] = ' '.join(buf)
    return refs


STYLES = {
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

# ─── Dark stylesheet ──────────────────────────────────────────────────────────

DARK_SS = """
* { font-family: 'Segoe UI', Arial, sans-serif; font-size: 13px; }
QMainWindow, QWidget { background: #1e1e1e; color: #dcdcdc; }
QFrame#sidebar { background: #242424; border-right: 1px solid #2e2e2e; }
QLabel { color: #dcdcdc; }
QTextEdit {
    background: #161616; color: #dcdcdc;
    border: 1px solid #2e2e2e; border-radius: 4px; padding: 4px;
}
QLineEdit {
    background: #161616; color: #dcdcdc;
    border: 1px solid #2e2e2e; border-radius: 4px; padding: 4px 8px;
}
QComboBox {
    background: #2a2a2a; color: #dcdcdc;
    border: 1px solid #383838; border-radius: 5px; padding: 4px 8px;
}
QComboBox QAbstractItemView {
    background: #2a2a2a; color: #dcdcdc;
    selection-background-color: #1a4f82;
}
QPushButton {
    background: #2a2a2a; color: #dcdcdc;
    border: 1px solid #383838; border-radius: 5px; padding: 5px 10px;
}
QPushButton:hover  { background: #333; border-color: #555; }
QPushButton:pressed { background: #222; }
QPushButton:disabled { background: #222; color: #444; border-color: #2a2a2a; }
QPushButton#btn_step1 { background: #1a4f82; border-color: #1a4f82; }
QPushButton#btn_step1:hover { background: #0e3357; }
QPushButton#btn_step2 { background: #1e6b27; border-color: #1e6b27; }
QPushButton#btn_step2:hover { background: #134519; }
QPushButton#btn_step3 { background: #5a1880; border-color: #5a1880; }
QPushButton#btn_step3:hover { background: #3a0f54; }
QPushButton#btn_all  { background: #7a5000; border-color: #7a5000; }
QPushButton#btn_all:hover  { background: #4f3400; }
QPushButton#btn_stop { background: #7a1515; border-color: #7a1515; }
QPushButton#btn_stop:hover { background: #4f0d0d; }
QPushButton#btn_settings { background: #2a2a2a; }
QProgressBar {
    background: #2a2a2a; border: none; border-radius: 3px;
    max-height: 6px; min-height: 6px;
}
QProgressBar::chunk { background: #1a6fcc; border-radius: 3px; }
QTabWidget::pane { border: 1px solid #2e2e2e; border-radius: 6px; top: -1px; }
QTabBar::tab {
    background: #242424; color: #888;
    padding: 8px 16px; border: 1px solid #2e2e2e;
    margin-right: 2px; border-radius: 4px 4px 0 0;
}
QTabBar::tab:selected { background: #1e1e1e; color: #dcdcdc; border-bottom: 2px solid #1a6fcc; }
QScrollBar:vertical   { background: #1e1e1e; width: 8px; border: none; }
QScrollBar::handle:vertical { background: #3a3a3a; border-radius: 4px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #1e1e1e; height: 8px; border: none; }
QScrollBar::handle:horizontal { background: #3a3a3a; border-radius: 4px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QFrame[frameShape="4"], QFrame[frameShape="5"] { color: #2e2e2e; }
QDialog { background: #1e1e1e; }
QMessageBox { background: #1e1e1e; }
"""

# ─── App ─────────────────────────────────────────────────────────────────────

class _Sigs(QObject):
    status    = pyqtSignal(str)
    progress  = pyqtSignal(float)
    append    = pyqtSignal(int, str)   # box_id (0/1/2), text
    names_set = pyqtSignal(str)
    busy      = pyqtSignal(bool)
    conv      = pyqtSignal()


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Content AI  —  @huyit32')
        self.resize(1340, 860)
        self.setMinimumSize(1000, 640)

        self._files:      list[tuple[Path, str]] = []
        self._scenes:     list[tuple[int, str]]  = []
        self._names:      list[str]              = []
        self._main_chars: set[str]               = set()
        self._char_refs:  dict[str, str]         = {}
        self._stop        = threading.Event()

        self._sigs = _Sigs()
        self._build()
        self._connect()
        _launch_servers(_find_cookie_files())

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        hl = QHBoxLayout(root)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        hl.addWidget(self._mk_sidebar())
        hl.addWidget(self._mk_main(), 1)

    def _mk_sidebar(self):
        sb = QFrame()
        sb.setObjectName('sidebar')
        sb.setFixedWidth(280)
        v = QVBoxLayout(sb)
        v.setContentsMargins(16, 20, 16, 16)
        v.setSpacing(3)

        lbl_title = QLabel('Content AI')
        lbl_title.setFont(QFont('Segoe UI', 17, QFont.Bold))
        lbl_title.setAlignment(Qt.AlignCenter)
        v.addWidget(lbl_title)

        lbl_sub = QLabel('@huyit32  •  ChatGPT')
        lbl_sub.setStyleSheet('color: #555; font-size: 11px;')
        lbl_sub.setAlignment(Qt.AlignCenter)
        v.addWidget(lbl_sub)
        v.addWidget(self._hr())

        v.addWidget(self._sec('SCRIPT FILES'))
        fr = QHBoxLayout()
        b_add = QPushButton('Add Files')
        b_add.clicked.connect(self._add_files)
        b_clr = QPushButton('X')
        b_clr.setFixedWidth(32)
        b_clr.clicked.connect(self._clear_files)
        fr.addWidget(b_add, 1)
        fr.addWidget(b_clr)
        v.addLayout(fr)

        self._file_box = QTextEdit()
        self._file_box.setReadOnly(True)
        self._file_box.setFixedHeight(80)
        self._file_box.setFont(QFont('Segoe UI', 10))
        v.addWidget(self._file_box)

        self._scene_lbl = QLabel('0 scenes loaded')
        self._scene_lbl.setStyleSheet('color: #555; font-size: 11px;')
        v.addWidget(self._scene_lbl)
        v.addWidget(self._hr())

        v.addWidget(self._sec('STYLE'))
        self._style_cb = QComboBox()
        self._style_cb.addItems(list(STYLES.keys()))
        v.addWidget(self._style_cb)

        v.addWidget(QLabel('Extra notes:'))
        self._extra = QLineEdit()
        self._extra.setPlaceholderText('e.g. rain, night, cinematic…')
        v.addWidget(self._extra)

        wr = QHBoxLayout()
        wr.addWidget(QLabel('Parallel workers:'), 1)
        self._batch = QLineEdit('5')
        self._batch.setFixedWidth(50)
        wr.addWidget(self._batch)
        v.addLayout(wr)
        v.addWidget(self._hr())

        v.addWidget(self._sec('PIPELINE'))
        for text, oid, slot in [
            ('Step 1  —  Extract Names',  'btn_step1', self._run_step1),
            ('Step 2  —  Character Refs', 'btn_step2', self._run_step2),
            ('Step 3  —  Scene Prompts',  'btn_step3', self._run_step3),
            ('Run All Steps',             'btn_all',   self._run_all),
        ]:
            b = QPushButton(text)
            b.setObjectName(oid)
            b.setFixedHeight(36)
            b.setFont(QFont('Segoe UI', 11, QFont.Bold))
            b.clicked.connect(slot)
            v.addWidget(b)

        self._stop_btn = QPushButton('Stop')
        self._stop_btn.setObjectName('btn_stop')
        self._stop_btn.setFixedHeight(28)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._do_stop)
        v.addWidget(self._stop_btn)
        v.addWidget(self._hr())

        self._api_lbl = QLabel(self._api_status())
        self._api_lbl.setStyleSheet('color: #555; font-size: 10px;')
        v.addWidget(self._api_lbl)

        b_settings = QPushButton('Settings')
        b_settings.setObjectName('btn_settings')
        b_settings.setFixedHeight(28)
        b_settings.clicked.connect(self._open_settings)
        v.addWidget(b_settings)
        v.addWidget(self._hr())

        self._conv_lbl = QLabel('Chat: (chưa có)')
        self._conv_lbl.setStyleSheet('color: #555; font-size: 10px;')
        self._conv_lbl.setWordWrap(True)
        v.addWidget(self._conv_lbl)

        cr = QHBoxLayout()
        b_open = QPushButton('Mở Chat')
        b_open.setFixedHeight(26)
        b_open.setStyleSheet('background: #1a3a2a;')
        b_open.clicked.connect(self._open_conv_browser)
        b_cid = QPushButton('Copy')
        b_cid.setFixedWidth(52)
        b_cid.setFixedHeight(26)
        b_cid.clicked.connect(self._copy_conv_id)
        cr.addWidget(b_open, 1)
        cr.addWidget(b_cid)
        v.addLayout(cr)

        v.addStretch()
        return sb

    def _mk_main(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)

        sf = QFrame()
        sf.setStyleSheet('background: #242424; border-radius: 8px;')
        sl = QVBoxLayout(sf)
        sl.setContentsMargins(14, 8, 14, 8)
        sl.setSpacing(4)
        self._status_lbl = QLabel('Load script files, then run the pipeline steps.')
        self._status_lbl.setFont(QFont('Segoe UI', 12))
        sl.addWidget(self._status_lbl)
        self._prog = QProgressBar()
        self._prog.setRange(0, 100)
        self._prog.setValue(0)
        self._prog.setTextVisible(False)
        sl.addWidget(self._prog)
        v.addWidget(sf)

        self._tabs = QTabWidget()
        self._tabs.setFont(QFont('Segoe UI', 11))
        v.addWidget(self._tabs, 1)

        for tab_title, attr in [
            ('Step 1  —  Names',          '_names_box'),
            ('Step 2  —  Character Refs',  '_refs_box'),
            ('Step 3  —  Scene Prompts',   '_prompts_box'),
        ]:
            tab = QWidget()
            tl = QVBoxLayout(tab)
            tl.setContentsMargins(4, 4, 4, 4)
            box = QTextEdit()
            box.setFont(QFont('Consolas', 11))
            tl.addWidget(box)
            setattr(self, attr, box)
            self._tabs.addTab(tab, tab_title)

        tb = QHBoxLayout()
        tb.setSpacing(4)
        for lbl, fn in [
            ('Copy Names',   lambda: self._copy(self._names_box)),
            ('Copy Refs',    lambda: self._copy(self._refs_box)),
            ('Copy Prompts', lambda: self._copy(self._prompts_box)),
            ('Save Refs',    lambda: self._save(self._refs_box,    'char_refs')),
            ('Save Prompts', lambda: self._save(self._prompts_box, 'scene_prompts')),
            ('Clear All',    self._clear_all),
        ]:
            b = QPushButton(lbl)
            b.setFixedHeight(28)
            b.setFont(QFont('Segoe UI', 10))
            b.clicked.connect(fn)
            tb.addWidget(b)
        tb.addStretch()
        v.addLayout(tb)
        return w

    # ── Signals ───────────────────────────────────────────────────────────────

    def _connect(self):
        s = self._sigs
        s.status.connect(self._status_lbl.setText)
        s.progress.connect(lambda v: self._prog.setValue(int(v * 100)))
        s.append.connect(self._do_append)
        s.names_set.connect(self._names_box.setPlainText)
        s.busy.connect(self._do_busy)
        s.conv.connect(self._refresh_conv_id)

    def _do_append(self, box_id, text):
        box = (self._names_box, self._refs_box, self._prompts_box)[box_id]
        box.moveCursor(QTextCursor.End)
        box.insertPlainText(text)
        box.moveCursor(QTextCursor.End)

    def _do_busy(self, on):
        self._stop_btn.setEnabled(on)

    # Thread-safe wrappers (called from bg threads)
    def _st(self, msg):   self._sigs.status.emit(msg)
    def _pg(self, v):     self._sigs.progress.emit(v)
    def _busy(self, on):  self._sigs.busy.emit(on)

    def _append(self, box, text):
        idx = [self._names_box, self._refs_box, self._prompts_box].index(box)
        self._sigs.append.emit(idx, text)

    def _make_delta_cb(self, box):
        def cb(delta):
            self._append(box, delta)
        return cb

    # ── Util ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _hr():
        f = QFrame()
        f.setFrameShape(QFrame.HLine)
        return f

    @staticmethod
    def _sec(text):
        l = QLabel(text)
        l.setStyleSheet('color: #555; font-size: 10px; font-weight: bold; margin-top: 8px;')
        return l

    def _refresh_conv_id(self):
        if _conv_id:
            short = _conv_id[:8] + '...' + _conv_id[-4:]
            self._conv_lbl.setText(f'Chat: {short}')
        else:
            self._conv_lbl.setText('Chat: (chưa có)')

    def _open_conv_browser(self):
        if _conv_id:
            webbrowser.open(f'https://chatgpt.com/c/{_conv_id}')
        else:
            QMessageBox.information(self, 'Chưa có', 'Chưa có conversation nào.')

    def _copy_conv_id(self):
        if _conv_id:
            QApplication.clipboard().setText(f'https://chatgpt.com/c/{_conv_id}')
            QMessageBox.information(self, 'Copied', 'Đã copy link conversation!')
        else:
            QMessageBox.information(self, 'Chưa có', 'Chưa có conversation nào.')

    def _api_status(self):
        try:
            r = requests.get(f'{_API_URL}/health', timeout=2)
            return '● ChatGPT server — online' if r.ok else '⚠ ChatGPT server — lỗi'
        except Exception:
            return '⚠ ChatGPT server — offline'

    def _copy(self, box):
        QApplication.clipboard().setText(box.toPlainText().strip())
        QMessageBox.information(self, 'Copied', 'Copied to clipboard!')

    def _save(self, box, name):
        t = box.toPlainText().strip()
        if not t:
            QMessageBox.warning(self, 'Empty', 'Nothing to save.')
            return
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        path, _ = QFileDialog.getSaveFileName(self, 'Save', f'{name}_{ts}.txt', 'Text (*.txt)')
        if path:
            Path(path).write_text(t, encoding='utf-8')

    def _clear_all(self):
        for b in (self._names_box, self._refs_box, self._prompts_box):
            b.clear()
        self._char_refs.clear()
        self._main_chars.clear()
        self._prog.setValue(0)
        self._status_lbl.setText('Cleared.')

    def _do_stop(self):
        self._stop.set()
        self._status_lbl.setText('Stopping after current request...')

    def _guard(self):
        if not self._files:
            QMessageBox.warning(self, 'No Files', 'Add script files first.')
            return False
        if not self._scenes:
            QMessageBox.warning(self, 'No Scenes', 'No numbered lines found in files.')
            return False
        self._stop.clear()
        return True

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, 'Select script files', '', 'Text (*.txt);;All (*.*)')
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
        self._file_box.setPlainText(''.join(p.name + '\n' for p, _ in self._files))
        self._scenes = parse_scenes('\n'.join(c for _, c in self._files))
        self._scene_lbl.setText(f'{len(self._scenes)} scenes loaded')

    def _all_text(self):
        return '\n'.join(c for _, c in self._files)

    def _style(self):
        base  = STYLES.get(self._style_cb.currentText(), '')
        extra = self._extra.text().strip()
        return f'{base}, {extra}' if extra else base

    def _batch_n(self):
        try:
            return max(1, int(self._batch.text()))
        except Exception:
            return 8

    # ── Settings dialog ───────────────────────────────────────────────────────

    def _open_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle('Settings — Cookies & User-Agent')
        dlg.resize(640, 560)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 16, 16, 14)
        v.setSpacing(6)

        lbl = QLabel('ChatGPT Cookies & User-Agent')
        lbl.setFont(QFont('Segoe UI', 13, QFont.Bold))
        v.addWidget(lbl)

        hint = QLabel(
            'Lấy từ DevTools (F12) → Network → chatgpt.com → bất kỳ request nào → Headers:\n'
            '• Cookie  →  dán vào ô Cookies bên dưới (mỗi tài khoản 1 hàng)\n'
            '• User-Agent  →  dán vào ô User-Agent'
        )
        hint.setStyleSheet('color: #666; font-size: 11px;')
        hint.setWordWrap(True)
        v.addWidget(hint)

        _base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
        _cj = _base / 'cookies.json'
        _cf = _base / 'cookie.txt'

        # Load existing data
        _existing_cookies: list[str] = []
        _existing_ua = ''
        if _cj.exists():
            try:
                import json as _json
                _data = _json.loads(_cj.read_text('utf-8'))
                if isinstance(_data, dict):
                    _existing_cookies = [c for c in _data.get('cookies', []) if isinstance(c, str)]
                    _existing_ua = (_data.get('ua') or '').strip()
                else:
                    _existing_cookies = [c for c in _data if isinstance(c, str)]
            except Exception:
                pass
        elif _cf.exists():
            _existing_cookies = [l for l in _cf.read_text('utf-8').splitlines() if l.strip()]

        v.addWidget(QLabel('Cookies (mỗi tài khoản 1 hàng):'))
        cookie_box = QTextEdit()
        cookie_box.setFont(QFont('Courier New', 9))
        cookie_box.setLineWrapMode(QTextEdit.NoWrap)
        cookie_box.setPlainText('\n'.join(_existing_cookies))
        v.addWidget(cookie_box, 1)

        v.addWidget(QLabel('User-Agent:'))
        ua_edit = QLineEdit()
        ua_edit.setPlaceholderText(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...'
        )
        ua_edit.setText(_existing_ua)
        v.addWidget(ua_edit)

        log_box = QTextEdit()
        log_box.setReadOnly(True)
        log_box.setFixedHeight(80)
        log_box.setFont(QFont('Segoe UI', 10))
        v.addWidget(log_box)

        # Log signal (thread-safe)
        class _LS(QObject):
            sig = pyqtSignal(str)
        ls = _LS()
        ls.sig.connect(lambda msg: (
            log_box.moveCursor(QTextCursor.End),
            log_box.insertPlainText(msg + '\n'),
            log_box.moveCursor(QTextCursor.End),
        ))

        def _validate():
            lines = [l.strip() for l in cookie_box.toPlainText().splitlines() if l.strip()]
            if not lines:
                ls.sig.emit('⚠ Chưa có cookie nào.')
                return
            log_box.clear()
            ls.sig.emit(f'Đang kiểm tra {len(lines)} cookie(s)...')
            _ua = ua_edit.text().strip() or (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
            )
            def _worker():
                for i, cookie in enumerate(lines):
                    try:
                        r = requests.get(
                            'https://chatgpt.com/api/auth/session',
                            headers={'Cookie': cookie, 'User-Agent': _ua, 'Accept': 'application/json'},
                            timeout=15,
                        )
                        if r.status_code == 200:
                            data = r.json()
                            if data.get('accessToken'):
                                email = data.get('user', {}).get('email', '?')
                                plan  = data.get('account', {}).get('planType', '?')
                                ls.sig.emit(f'✓ #{i+1} {email} ({plan})')
                            else:
                                ls.sig.emit(f'✗ #{i+1} Cookie hết hạn')
                        else:
                            ls.sig.emit(f'✗ #{i+1} HTTP {r.status_code}')
                    except Exception as e:
                        ls.sig.emit(f'✗ #{i+1} {e}')
            threading.Thread(target=_worker, daemon=True).start()

        def _save_restart():
            import json as _json
            lines = [l.strip() for l in cookie_box.toPlainText().splitlines() if l.strip()]
            if not lines:
                QMessageBox.warning(dlg, 'Trống', 'Nhập ít nhất 1 cookie.')
                return
            ua_val = ua_edit.text().strip()
            save_data: dict = {'cookies': lines}
            if ua_val:
                save_data['ua'] = ua_val
            _cj.write_text(_json.dumps(save_data, ensure_ascii=False, indent=2), encoding='utf-8')
            _cleanup_servers()
            _launch_servers(_find_cookie_files())
            ls.sig.emit(f'✓ Đã lưu {len(lines)} cookie(s) và restart servers.')

        br = QHBoxLayout()
        b_val = QPushButton('Validate')
        b_val.setFixedHeight(32)
        b_val.clicked.connect(_validate)
        b_sav = QPushButton('Lưu & Restart')
        b_sav.setFixedHeight(32)
        b_sav.setStyleSheet('background: #1a4f82;')
        b_sav.clicked.connect(_save_restart)
        b_cls = QPushButton('Đóng')
        b_cls.setFixedHeight(32)
        b_cls.setFixedWidth(70)
        b_cls.clicked.connect(dlg.accept)
        br.addWidget(b_val)
        br.addWidget(b_sav)
        br.addStretch()
        br.addWidget(b_cls)
        v.addLayout(br)
        dlg.exec_()

    # ── Step buttons ─────────────────────────────────────────────────────────

    def _run_step1(self):
        if not self._guard(): return
        self._names_box.clear()
        threading.Thread(target=self._t_step1, daemon=True).start()

    def _parse_names_box(self):
        self._names = []
        self._main_chars = set()
        for line in self._names_box.toPlainText().strip().splitlines():
            line = line.strip()
            if not line: continue
            if line.startswith('★'):
                name = line[1:].strip()
                if name:
                    self._names.append(name)
                    self._main_chars.add(name)
            else:
                self._names.append(line)

    def _run_step2(self):
        if not self._guard(): return
        if not self._names_box.toPlainText().strip():
            QMessageBox.warning(self, 'No Names', 'Run Step 1 first, or type names manually.')
            return
        self._parse_names_box()
        self._refs_box.clear()
        self._char_refs.clear()
        threading.Thread(target=self._t_step2, daemon=True).start()

    def _run_step3(self):
        if not self._guard(): return
        self._prompts_box.clear()
        threading.Thread(target=self._t_step3, daemon=True).start()

    def _run_all(self):
        if not self._guard(): return
        for b in (self._names_box, self._refs_box, self._prompts_box): b.clear()
        self._char_refs.clear()
        self._main_chars.clear()
        threading.Thread(target=self._t_all, daemon=True).start()

    # ── Threads ───────────────────────────────────────────────────────────────

    def _t_step1(self):
        self._busy(True)
        self._st('Step 1 — Extracting character names...')
        self._pg(0.1)
        try:
            lines = self._extract_names()
            self._main_chars = {l[1:].strip() for l in lines if l.startswith('★')}
            self._names = [l[1:].strip() if l.startswith('★') else l for l in lines]
            self._sigs.names_set.emit('\n'.join(lines))
            self._st(f'Step 1 done — {len(self._main_chars)} main + {len(self._names)-len(self._main_chars)} supporting.')
            self._pg(1.0)
        except Exception as e:
            self._st(f'Error: {e}')
            self._append(self._names_box, f'Error: {e}\n')
        finally:
            self._busy(False)
            self._sigs.conv.emit()

    def _t_step2(self):
        self._busy(True)
        total = len(self._names)
        self._st(f'Step 2 — Generating refs for {total} characters in 1 request...')
        self._pg(0.1)
        try:
            raw = self._gen_all_char_refs()
            refs = _parse_char_refs_bulk(raw)
            for name in self._names:
                if self._stop.is_set(): break
                is_main = name in self._main_chars
                label = f'★ {name} [MAIN]' if is_main else name
                self._append(self._refs_box, f'=== {label} ===\n')
                ref = refs.get(name, '[not found]')
                self._char_refs[name] = ref
                self._append(self._refs_box, ref + '\n\n')
            self._st(f'Step 2 done — {len(self._char_refs)} refs ready.')
            self._pg(1.0)
        except Exception as e:
            self._st(f'Error: {e}')
            self._append(self._refs_box, f'Error: {e}\n')
        finally:
            self._busy(False)
            self._sigs.conv.emit()

    def _t_step3(self):
        self._busy(True)
        try:
            self._run_scene_prompts(self._scenes, p0=0.0, p1=1.0)
            self._st(f'Step 3 done — {len(self._scenes)} scene prompts generated.')
        except Exception as e:
            self._st(f'Error: {e}')
        finally:
            self._busy(False)
            self._sigs.conv.emit()

    def _t_all(self):
        self._busy(True)
        self._st('Step 1/3 — Extracting names...')
        self._pg(0.02)
        try:
            lines = self._extract_names()
            self._main_chars = {l[1:].strip() for l in lines if l.startswith('★')}
            self._names = [l[1:].strip() if l.startswith('★') else l for l in lines]
            self._sigs.names_set.emit('\n'.join(lines))
            self._st(f'Step 1 done — {len(self._main_chars)} main + {len(self._names)-len(self._main_chars)} supporting.')
        except Exception as e:
            self._append(self._names_box, f'Error: {e}\n')
            self._names = []

        if self._stop.is_set():
            self._st('Stopped.'); self._busy(False); return

        total = len(self._names)
        self._st(f'Step 2/3 — Generating refs for {total} characters in 1 request...')
        self._pg(0.12)
        try:
            raw = self._gen_all_char_refs()
            refs = _parse_char_refs_bulk(raw)
            for name in self._names:
                if self._stop.is_set(): break
                is_main = name in self._main_chars
                label = f'★ {name} [MAIN]' if is_main else name
                self._append(self._refs_box, f'=== {label} ===\n')
                ref = refs.get(name, '[not found]')
                self._char_refs[name] = ref
                self._append(self._refs_box, ref + '\n\n')
            self._pg(0.45)
        except Exception as e:
            self._append(self._refs_box, f'Error: {e}\n\n')

        if self._stop.is_set():
            self._st('Stopped.'); self._busy(False); return

        self._st('Step 3/3 — Generating scene prompts...')
        self._run_scene_prompts(self._scenes, p0=0.45, p1=1.0)
        self._st(f'All done — {len(self._scenes)} scenes processed.')
        self._busy(False)
        self._sigs.conv.emit()

    # ── AI calls ─────────────────────────────────────────────────────────────

    def _extract_names(self):
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
            f'Story:\n{text}\n\nCharacters:'
        )
        result = ask(prompt)
        lines = []
        for line in result.splitlines():
            line = line.strip()
            if not line: continue
            if line.startswith('★'):
                name = line[1:].strip().lstrip('•-–—*0123456789. ').strip()
                if name and 1 < len(name) < 50:
                    lines.append(f'★ {name}')
            else:
                name = line.lstrip('•-–—*0123456789. ').strip()
                if name and 1 < len(name) < 50:
                    lines.append(name)
        return lines

    def _gen_all_char_refs(self):
        text = self._all_text()
        if len(text) > 4_000:
            text = text[:4_000] + '\n...[truncated]'
        style = self._style()
        char_list = '\n'.join(
            f'★ {n} [MAIN]' if n in self._main_chars else n
            for n in self._names
        )
        prompt = (
            f'Art style: {style}\n\n'
            f'Based on the story text, create visual reference prompts for image generation for each character.\n\n'
            f'★ MAIN characters: gender, age range, hair (color/style), eyes, skin tone, '
            f'clothing/outfit, weapons or items, body build, distinctive features — 2-3 comma-separated lines.\n'
            f'Supporting characters: gender, age, hair, eyes, clothing, key features — 1 comma-separated line.\n\n'
            f'Output ONLY in this exact format, no extra text:\n'
            f'=== CharacterName ===\n'
            f'description\n\n'
            f'Characters:\n{char_list}\n\n'
            f'Story:\n{text}\n\nRefs:'
        )
        return ask(prompt)

    def _gen_scene_batch(self, scenes, style, char_block):
        scene_lines = '\n'.join(f'SCENE {n}: {d}' for n, d in scenes)
        nums  = [n for n, _ in scenes]
        count = len(nums)
        prompt = (
            f'Art style: {style}\n\n{char_block}'
            f'Write exactly {count} image-generation prompts for scenes: {", ".join(str(n) for n in nums)}.\n'
            f'You MUST output ALL {count} scenes. Do NOT skip any scene number.\n'
            f'Output ONLY lines in this exact format — nothing else:\n'
            f'[N] prompt text here\n\n'
            f'Rules: N = scene number, one line per scene, no intro, no commentary. '
            f'Each prompt: characters, setting, action, mood, lighting.\n\n'
            f'Scenes:\n{scene_lines}\n\nPrompts:'
        )
        return _parse_scene_prompts(ask(prompt))

    def _gen_scene_batch_resilient(self, scenes, style, char_block):
        sizes = [100, 50, 25, 10, 5, 1]
        last_error = None

        for size in sizes:
            if len(scenes) <= size:
                try:
                    results = self._gen_scene_batch(scenes, style, char_block)
                    missing = [n for n, _ in scenes if n not in results]
                    if not missing:
                        return results
                    last_error = RuntimeError(
                        f'missing scenes in response: {", ".join(str(n) for n in missing[:10])}'
                    )
                except Exception as e:
                    last_error = e
                continue

            merged = {}
            ok = True
            for chunk in _chunk_scenes(scenes, size):
                try:
                    results = self._gen_scene_batch(chunk, style, char_block)
                    missing = [n for n, _ in chunk if n not in results]
                    if missing:
                        raise RuntimeError(
                            f'missing scenes in response: {", ".join(str(n) for n in missing[:10])}'
                        )
                    merged.update(results)
                except Exception as e:
                    last_error = e
                    ok = False
                    break
            if ok:
                return merged

        if last_error:
            raise last_error
        raise RuntimeError('failed to generate scene prompts')

    def _run_scene_prompts(self, scenes, p0, p1):
        style   = self._style()
        total   = len(scenes)
        char_block = ''
        if self._char_refs:
            lines = [f'- {n}: {r.splitlines()[0]}' for n, r in self._char_refs.items()]
            char_block = 'Character references:\n' + '\n'.join(lines) + '\n\n'

        self._st(f'Step 3 — Sending all {total} scenes in 1 request...')
        self._pg(p0 + (p1 - p0) * 0.05)

        try:
            results = self._gen_scene_batch_resilient(scenes, style, char_block)
            for n, _ in scenes:
                if n not in results:
                    results[n] = '[missing]'
        except Exception as e:
            results = {n: f'[error: {e}]' for n, _ in scenes}

        out = ''.join(f'[{n}] {results[n]}\n' for n, _ in sorted(scenes, key=lambda x: x[0]))
        if out:
            self._append(self._prompts_box, out)
        self._pg(p1)


# ─── Auth dialog ─────────────────────────────────────────────────────────────

class AuthDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Content AI — Kích hoạt')
        self.setFixedSize(420, 230)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 20)
        v.setSpacing(10)

        title = QLabel('Nhập License Key')
        title.setFont(QFont('Segoe UI', 14, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        v.addWidget(title)

        did = _get_device_id()
        did_lbl = QLabel(f'Device ID:  {did}')
        did_lbl.setStyleSheet('color: #555; font-size: 10px;')
        did_lbl.setAlignment(Qt.AlignCenter)
        did_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(did_lbl)

        self._key_input = QLineEdit()
        self._key_input.setPlaceholderText('XXXX-XXXX-XXXX-XXXX')
        self._key_input.setFixedHeight(36)
        self._key_input.setAlignment(Qt.AlignCenter)
        self._key_input.returnPressed.connect(self._activate)
        v.addWidget(self._key_input)

        self._status = QLabel('')
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet('font-size: 11px;')
        v.addWidget(self._status)

        btn = QPushButton('Kích hoạt')
        btn.setFixedHeight(36)
        btn.setStyleSheet('background: #1a4f82; font-size: 13px; font-weight: bold;')
        btn.clicked.connect(self._activate)
        v.addWidget(btn)

        saved = _load_key()
        if saved:
            self._key_input.setText(saved)

    def _activate(self):
        key = self._key_input.text().strip()
        if not key:
            self._status.setText('⚠ Nhập key trước.')
            self._status.setStyleSheet('color: #c87800; font-size: 11px;')
            return
        self._status.setText('Đang kiểm tra...')
        self._status.setStyleSheet('color: #888; font-size: 11px;')
        QApplication.processEvents()

        def _worker():
            ok = _verify_key(key)
            if ok:
                _save_key(key)
            return ok

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            ok = ex.submit(_worker).result()

        if ok:
            self._status.setText('✓ Kích hoạt thành công!')
            self._status.setStyleSheet('color: #4caf50; font-size: 11px;')
            QTimer.singleShot(600, self.accept)
        else:
            self._status.setText('✗ Key không hợp lệ hoặc đã hết hạn.')
            self._status.setStyleSheet('color: #e05252; font-size: 11px;')


# ─── Entry ───────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    qapp = QApplication(sys.argv)
    qapp.setStyleSheet(DARK_SS)

    saved_key = _load_key()
    if saved_key and _verify_key(saved_key):
        pass  # already activated
    else:
        dlg = AuthDialog()
        if dlg.exec_() != QDialog.Accepted:
            sys.exit(0)

    window = App()
    window.show()
    qapp.aboutToQuit.connect(_cleanup_servers)
    sys.exit(qapp.exec_())

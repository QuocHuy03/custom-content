"""
ChatGPT client via SeleniumBase.
Uses JS injection + DOM polling. Supports headless2 mode and conversation reuse.
"""
import json, re, subprocess, sys
from pathlib import Path
from time import time, sleep

try:
    from seleniumbase import Driver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
except ImportError:
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'seleniumbase'])
    from seleniumbase import Driver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

try:
    import requests as _requests
except ImportError:
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'requests'])
    import requests as _requests

_DEFAULT_COOKIE_TXT  = Path(__file__).parent / 'cookie.txt'
_DEFAULT_COOKIE_JSON = Path(__file__).parent / 'cookies.json'

CONV_PATHS = ('/backend-api/f/conversation', '/backend-api/conversation')
SEL_INPUT  = '#prompt-textarea'
SEL_SEND   = 'button[data-testid="send-button"]'
SEL_STOPS  = ['[data-testid="stop-button"]', 'button[aria-label="Stop generating"]']
SEL_ASST   = '[data-message-author-role="assistant"]'

# Chrome flags to reduce memory/CPU for simple chat usage
_CHROME_LITE_ARGS = " ".join([
    "--disable-gpu",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--mute-audio",
    "--no-first-run",
    "--disable-default-apps",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-client-side-phishing-detection",
    "--disable-component-update",
    "--js-flags=--max-old-space-size=512",
])

_SESSION_URL = 'https://chatgpt.com/api/auth/session'
_SESSION_REQUIRED = ('user', 'account', 'accessToken')


def check_cookie(cookie_string: str = None, cookie_file: str = None) -> dict:
    """Check ChatGPT cookie validity via /api/auth/session (no Chrome needed).

    Returns:
        {'valid': True,  'email': ..., 'plan': ..., 'expires': ..., 'delinquent': bool}
        {'valid': False, 'error': ...}
    """
    raw = cookie_string
    if not raw and cookie_file:
        try:
            raw = Path(cookie_file).read_text('utf-8').strip()
        except Exception as e:
            return {'valid': False, 'error': f'Cannot read cookie file: {e}'}

    if not raw:
        return {'valid': False, 'error': 'No cookie provided'}

    headers = {
        'Cookie': raw,
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept': 'application/json',
        'Referer': 'https://chatgpt.com/',
    }

    try:
        r = _requests.get(_SESSION_URL, headers=headers, timeout=15)
        if r.status_code != 200:
            return {'valid': False, 'error': f'HTTP {r.status_code}'}
        data = r.json()
    except Exception as e:
        return {'valid': False, 'error': str(e)}

    if not all(data.get(k) for k in _SESSION_REQUIRED):
        return {'valid': False, 'error': 'Incomplete session (cookie expired or invalid)'}

    user    = data['user']
    account = data['account']
    return {
        'valid':       True,
        'email':       user.get('email'),
        'plan':        account.get('planType'),
        'expires':     data.get('expires'),
        'delinquent':  account.get('isDelinquent', False),
    }


class ChatGPTSelenium:

    def __init__(self, conv_id: str = None, headless: bool = False,
                 cookie_file: str = None, cookie_string: str = None,
                 skip_cookie_check: bool = False):
        self.conv_id        = conv_id
        self._headless      = headless
        self._cookie_string = cookie_string  # raw "name=val; name=val" — takes priority
        self._cookie_txt    = Path(cookie_file) if cookie_file and cookie_file.endswith('.txt')  else _DEFAULT_COOKIE_TXT
        self._cookie_json   = Path(cookie_file) if cookie_file and cookie_file.endswith('.json') else _DEFAULT_COOKIE_JSON

        # Fast pre-check: validate cookie before launching Chrome
        if not skip_cookie_check:
            raw = cookie_string or (self._cookie_txt.read_text('utf-8').strip() if self._cookie_txt.exists() else None)
            if raw:
                print('[*] Checking cookie validity...', flush=True)
                info = check_cookie(cookie_string=raw)
                if not info['valid']:
                    raise ValueError(f'[!] Cookie invalid: {info["error"]}')
                status = 'DELINQUENT' if info.get('delinquent') else info.get('plan', '?')
                print(f'[+] Cookie OK — {info["email"]} ({status}), expires {info["expires"]}', flush=True)

        label = 'inline' if cookie_string else self._cookie_txt.name
        print(f'[*] Starting Chrome (SeleniumBase) cookie={label}...', flush=True)
        self.driver = Driver(browser='chrome', headless2=headless, uc=True,
                             chromium_arg=_CHROME_LITE_ARGS)
        self._start()

    # ── Init ─────────────────────────────────────────────────────────────────

    def _start(self):
        self.driver.get('https://chatgpt.com')
        self._inject_cookies()
        if self._wait_css(SEL_INPUT, 8):
            print('[+] ChatGPT ready! (cookie login)', flush=True)
            return
        print('[*] Waiting for manual login in Chrome...', flush=True)
        if not self._wait_css(SEL_INPUT, 300):
            raise RuntimeError('Timeout waiting for login')
        print('[+] ChatGPT ready!', flush=True)

    @staticmethod
    def _parse_cookie_raw(raw: str) -> list[dict]:
        """Parse a semicolon-separated cookie string into Selenium cookie dicts."""
        cookies = []
        for part in raw.split(';'):
            part = part.strip()
            if '=' not in part:
                continue
            name, _, value = part.partition('=')
            name = name.strip()
            c = {'name': name, 'value': value.strip(),
                 'domain': 'chatgpt.com', 'path': '/'}
            if name.startswith(('__Secure-', '__Host-', '__cf', '_cf', 'oai-')):
                c['secure'] = True
            cookies.append(c)
        return cookies

    def _parse_cookie_txt(self) -> list[dict]:
        # Inline string takes priority over file
        if self._cookie_string:
            return self._parse_cookie_raw(self._cookie_string)
        if not self._cookie_txt.exists():
            return []
        try:
            raw = self._cookie_txt.read_text('utf-8').strip()
            return self._parse_cookie_raw(raw)
        except Exception as e:
            print(f'[!] cookie.txt parse error: {e}', flush=True)
            return []

    def _parse_cookie_json(self) -> list[dict]:
        if not self._cookie_json.exists():
            return []
        try:
            data = json.loads(self._cookie_json.read_text('utf-8'))
            return [{'name': c['name'], 'value': c['value'],
                     'domain': c.get('domain', 'chatgpt.com'),
                     'path': c.get('path', '/')}
                    for c in (data if isinstance(data, list) else [])
                    if 'name' in c]
        except Exception as e:
            print(f'[!] cookies.json parse error: {e}', flush=True)
            return []

    def _inject_cookies(self):
        cookies = self._parse_cookie_txt() or self._parse_cookie_json()
        if not cookies:
            return
        ok = 0
        for c in cookies:
            try:
                self.driver.add_cookie(c)
                ok += 1
            except Exception:
                pass
        if ok:
            self.driver.refresh()
            sleep(1.5)
            print(f'[+] Loaded {ok} cookies', flush=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _wait_css(self, css: str, timeout=30) -> bool:
        try:
            WebDriverWait(self.driver, timeout).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, css)))
            return True
        except Exception:
            return False

    def _is_generating(self) -> bool:
        for sel in SEL_STOPS:
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if els and any(e.is_displayed() for e in els):
                    return True
            except Exception:
                pass
        # Fallback: textarea disabled means model is still writing
        try:
            el = self.driver.find_element(By.CSS_SELECTOR, SEL_INPUT)
            if el.get_attribute('disabled'):
                return True
        except Exception:
            pass
        return False

    def _inject_text(self, text: str):
        """JS inject — same method as chat.js reference."""
        self.driver.execute_script("""
            const el = document.querySelector('#prompt-textarea');
            if (!el) return;
            el.focus();
            const p = el.querySelector('p');
            if (p) p.textContent = arguments[0];
            else    el.textContent = arguments[0];
            el.dispatchEvent(new Event('input', { bubbles: true }));
        """, text)
        sleep(0.5)

    # ── Response capture ─────────────────────────────────────────────────────

    def _send_and_capture(self, text: str) -> str:
        before = len(self.driver.find_elements(By.CSS_SELECTOR, SEL_ASST))

        self._inject_text(text)

        btns = self.driver.find_elements(By.CSS_SELECTOR, SEL_SEND)
        if btns:
            btns[0].click()
        else:
            self.driver.find_element(By.CSS_SELECTOR, SEL_INPUT).send_keys(Keys.ENTER)

        print('[*] Waiting for response...', flush=True)

        # Phase 1: wait for a new assistant message element (max 25s)
        deadline = time() + 25
        while time() < deadline:
            if len(self.driver.find_elements(By.CSS_SELECTOR, SEL_ASST)) > before:
                print('[*] Response started', flush=True)
                break
            sleep(0.3)

        # Phase 2: single JS call per tick — returns {text, done} together
        _JS_POLL = """
        const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
        const text = msgs.length ? msgs[msgs.length - 1].innerText.trim() : '';
        const stop = document.querySelector('[data-testid="stop-button"]')
                  || document.querySelector('button[aria-label="Stop generating"]');
        const done = !stop || stop.offsetParent === null;
        return {text: text, done: done};
        """
        last_text = ''
        stable    = 0
        deadline  = time() + 180
        while time() < deadline:
            sleep(0.3)
            try:
                r = self.driver.execute_script(_JS_POLL)
                cur  = r.get('text', '')
                done = r.get('done', False)
            except Exception:
                cur, done = '', False

            if cur and cur == last_text:
                stable += 1
            else:
                stable    = 0
                last_text = cur

            if stable >= 2 and done:   # 0.6s stable + stop button gone
                break
            if stable >= 5:            # 1.5s safety exit
                break

        result = last_text or self._extract_dom()
        print(f'[+] Got {len(result)} chars', flush=True)
        return result

    def _extract_dom(self) -> str:
        msgs = self.driver.find_elements(By.CSS_SELECTOR, SEL_ASST)
        if not msgs:
            return ''
        return msgs[-1].text.strip()

    # ── Public API ────────────────────────────────────────────────────────────

    def ask(self, message: str, image=None) -> str:
        # Reopen driver if crashed
        try:
            self.driver.current_url
        except Exception:
            self.driver = Driver(browser='chrome', headless2=self._headless, uc=True,
                                 chromium_arg=_CHROME_LITE_ARGS)
            self._start()

        # Navigate to existing conversation or homepage
        target = f'https://chatgpt.com/c/{self.conv_id}' if self.conv_id else 'https://chatgpt.com'
        try:
            on_target = self.driver.current_url.startswith(target)
        except Exception:
            on_target = False
        if not on_target:
            self.driver.get(target)

        if not self._wait_css(SEL_INPUT, 20):
            raise RuntimeError('Cannot load ChatGPT chat page')

        result = self._send_and_capture(message)

        # Capture / update conversation ID from URL
        try:
            m = re.search(r'chatgpt\.com/c/([a-zA-Z0-9\-]+)', self.driver.current_url)
            if m:
                new_id = m.group(1)
                if new_id != self.conv_id:
                    self.conv_id = new_id
                    print(f'[+] Conversation ID: {self.conv_id}', flush=True)
        except Exception:
            pass

        if not result:
            raise RuntimeError('ChatGPT returned empty response')
        return result

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass

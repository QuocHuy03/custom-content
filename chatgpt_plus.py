import os, sys, asyncio, json
from pathlib import Path
from dotenv import load_dotenv
from gpt_plus import GPTPlus

_BASE = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
load_dotenv(_BASE / '.env', override=False)

_DEFAULT_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)


def _resolve_cookie(cookie_string=None, cookie_file=None):
    if cookie_string:
        return cookie_string.strip()
    if cookie_file:
        try:
            return Path(cookie_file).read_text('utf-8').strip()
        except Exception:
            return None
    # JSON format first (first cookie in the list)
    p = _BASE / 'cookies.json'
    if p.exists():
        try:
            data = json.loads(p.read_text('utf-8'))
            if isinstance(data, list) and data:
                first = next((c.strip() for c in data if isinstance(c, str) and c.strip()), None)
                if first:
                    return first
        except Exception:
            pass
    # Fallback: plain text files
    for fname in ('cookie.txt', 'chatgpt_cookie.txt'):
        p = _BASE / fname
        if p.exists():
            text = p.read_text('utf-8').strip()
            if text:
                # If multiple lines, take the first valid one
                first_line = next((l for l in text.splitlines() if l.strip()), None)
                return first_line or text
    return None


class ChatGPTPlus:
    def __init__(self, conv_id=None, cookie_string=None, cookie_file=None, ua_string=None, **_):
        self.conv_id = conv_id
        cookie = _resolve_cookie(cookie_string, cookie_file)
        if cookie:
            os.environ['GPT_COOKIES'] = cookie
        if ua_string:
            os.environ['GPT_USER_AGENT'] = ua_string
        elif not os.environ.get('GPT_USER_AGENT'):
            os.environ['GPT_USER_AGENT'] = _DEFAULT_UA
        self._gpt = GPTPlus()

    def ask(self, message: str) -> str:
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(self._gpt.chat(message))
        finally:
            loop.close()
        self.conv_id = result.get('conversation_id')
        return result['message']

    def close(self):
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self._gpt.close())
            loop.close()
        except Exception:
            pass

    async def aask(self, message: str) -> str:
        result = await self._gpt.chat(message)
        self.conv_id = result.get('conversation_id')
        return result['message']

    async def aask_stream(self, message: str):
        async for chunk in self._gpt.chat_stream(message):
            yield chunk
        self.conv_id = getattr(self._gpt, '_last_conv_id', None) or self.conv_id

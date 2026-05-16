from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from contextlib import asynccontextmanager
from uvicorn import run
import asyncio, os, json, logging

logger = logging.getLogger(__name__)

from chatgpt_plus import ChatGPTPlus

_PORT          = int(os.environ.get('PORT', 6969))
_COOKIE_STRING = os.environ.get('COOKIE_STRING') or None
_COOKIE_FILE   = os.environ.get('COOKIE_FILE')   or None
_UA_STRING     = os.environ.get('GPT_USER_AGENT') or None

_lock:   asyncio.Lock           = None
_client: Optional[ChatGPTPlus]  = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _lock, _client
    _lock   = asyncio.Lock()
    _client = ChatGPTPlus(cookie_string=_COOKIE_STRING, cookie_file=_COOKIE_FILE, ua_string=_UA_STRING)
    yield
    if _client:
        await asyncio.to_thread(_client.close)


app = FastAPI(lifespan=lifespan)


class ConversationRequest(BaseModel):
    message: str
    image:   Optional[str] = None


@app.post('/conversation')
async def create_conversation(request: ConversationRequest):
    if not request.message:
        raise HTTPException(status_code=400, detail='message is required')
    async with _lock:
        try:
            answer = await _client.aask(request.message)
            return {'status': 'success', 'result': answer, 'conv_id': _client.conv_id}
        except Exception as e:
            logger.exception('conversation error')
            raise HTTPException(status_code=500, detail=str(e))


@app.post('/conversation/stream')
async def stream_conversation(request: ConversationRequest):
    """SSE endpoint — yields data: {"text": "..."} chunks then data: [DONE]"""
    if not request.message:
        raise HTTPException(status_code=400, detail='message is required')

    async def generate():
        async with _lock:
            try:
                async for chunk in _client.aask_stream(request.message):
                    yield f"data: {json.dumps({'text': chunk})}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type='text/event-stream')


@app.get('/conv_id')
async def get_conv_id():
    return {'conv_id': _client.conv_id if _client else None}


@app.get('/health')
async def health():
    return {'status': 'ok', 'mode': 'plus', 'conv_id': _client.conv_id if _client else None}


@app.get('/check_cookie')
async def check_cookie_endpoint(cookie: str = Query(None, description='Raw cookie string')):
    """Validate a ChatGPT cookie via /api/auth/session."""
    import sys
    from pathlib import Path
    _RGPT = Path(r'C:\reverse-gpt')
    if str(_RGPT) not in sys.path:
        sys.path.insert(0, str(_RGPT))
    from gpt_plus import _env
    from curl_cffi import requests as cffi

    raw = cookie or _env('GPT_COOKIES')
    if not raw:
        raise HTTPException(status_code=400, detail='Provide ?cookie=... or set GPT_COOKIES')

    try:
        async with cffi.AsyncSession(impersonate='chrome131', timeout=15) as client:
            r = await client.get(
                'https://chatgpt.com/api/auth/session',
                headers={'Cookie': raw, 'User-Agent': _env('GPT_USER_AGENT'), 'Accept': 'application/json'},
            )
            if r.status_code != 200:
                raise HTTPException(status_code=401, detail=f'HTTP {r.status_code}')
            data = r.json()
            if not data.get('accessToken'):
                raise HTTPException(status_code=401, detail='Cookie expired or invalid')
            user    = data.get('user', {})
            account = data.get('account', {})
            return {
                'valid':      True,
                'email':      user.get('email'),
                'plan':       account.get('planType'),
                'expires':    data.get('expires'),
                'delinquent': account.get('isDelinquent', False),
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == '__main__':
    run(app, host='0.0.0.0', port=_PORT)

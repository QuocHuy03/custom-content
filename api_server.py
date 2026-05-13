from fastapi    import FastAPI, HTTPException, Query
from pydantic   import BaseModel
from typing     import Optional
from contextlib import asynccontextmanager
from uvicorn    import run
import asyncio, os

from chatgpt_selenium import ChatGPTSelenium, check_cookie

_lock:   asyncio.Lock              = None
_client: Optional[ChatGPTSelenium] = None

_PORT          = int(os.environ.get('PORT', 6969))
_CONV_ID       = os.environ.get('CONV_ID')       or None
_HEADLESS      = os.environ.get('HEADLESS', '').lower() in ('1', 'true', 'yes')
_COOKIE_FILE   = os.environ.get('COOKIE_FILE')   or None
_COOKIE_STRING = os.environ.get('COOKIE_STRING') or None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _lock, _client
    _lock = asyncio.Lock()
    _client = await asyncio.to_thread(
        ChatGPTSelenium,
        conv_id=_CONV_ID,
        headless=_HEADLESS,
        cookie_file=_COOKIE_FILE,
        cookie_string=_COOKIE_STRING,
    )
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
            answer: str = await asyncio.to_thread(
                _client.ask, request.message, request.image
            )
            return {'status': 'success', 'result': answer, 'conv_id': _client.conv_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Error: {str(e)}')


@app.get('/conv_id')
async def get_conv_id():
    return {'conv_id': _client.conv_id if _client else None}


@app.get('/health')
async def health():
    return {'status': 'ok', 'mode': 'selenium', 'conv_id': _client.conv_id if _client else None}


@app.get('/check_cookie')
async def check_cookie_endpoint(cookie: str = Query(None, description='Raw cookie string')):
    """Validate a ChatGPT cookie via /api/auth/session — no Chrome needed."""
    raw = cookie or os.environ.get('COOKIE_STRING') or None
    if not raw:
        raise HTTPException(status_code=400, detail='Provide ?cookie=... or set COOKIE_STRING env var')
    result = await asyncio.to_thread(check_cookie, raw)
    if not result['valid']:
        raise HTTPException(status_code=401, detail=result.get('error', 'Invalid cookie'))
    return result


if __name__ == '__main__':
    run(app, host='0.0.0.0', port=_PORT)

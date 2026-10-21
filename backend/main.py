"""
Pitchlight — FastAPI backend with ADK multi-agent architecture.

Session lifecycle:
  1. Browser connects via WebSocket /ws
  2. LiveCoachAgent (ADK LlmAgent) starts via runner.run_live()
     - Browser sends binary frames: 0x01=audio PCM, 0x02=webcam JPEG, 0x04=screen JPEG, 0x05=slide JPEG, 0x03=stop
     - Gemini calls flag_issue() tool → metric updates pushed to browser as JSON
     - Gemini coach audio → binary frame 0x10 to browser (no base64)
  3. Browser sends 0x03 → live session ends
  4. Post-session agents run in parallel (delivery/content/research) then synthesis
  5. Final scorecard + written report sent to browser, persisted to Firestore

Binary frame protocol (client→server):
  byte 0: 0x01 = audio PCM 16-bit 16kHz  |  0x02 = webcam JPEG
          0x04 = screen-share JPEG        |  0x05 = uploaded-slide JPEG
          0x03 = stop
  bytes 1..N: payload

Binary frame protocol (server→client):
  byte 0: 0x10 = coach audio PCM 24kHz
  bytes 1..N: payload
  (all other server→client messages are JSON text frames)
"""
import asyncio
import base64
import os
import ipaddress
import json
import hashlib
import logging
import math
import re
import sys
import time
import uuid
from urllib.parse import quote
from array import array
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google import genai
from google.genai import types

from backend.agents.live_coach import make_live_coach_agent
from backend.coach import (
    VALID_PERSONAS,
    normalize_coach_mode,
    normalize_delivery_context,
    normalize_primary_goal,
)
from backend.agents.post_session import (
    CONTENT_ANALYST,
    DELIVERY_ANALYST,
    PARALLEL_ANALYSTS,
    POST_SESSION_PIPELINE,
    RESEARCH_AGENT,
    SESSION_SUMMARY_AGENT,
    SYNTHESIS_AGENT,
    QA_REVIEW_AGENT,
    RECAP_AGENT,
    REWRITE_AGENT,
)
from backend.db import get_db
from backend.multimodal import extract_image_prompts, generate_session_assets
from backend.scorecard import MIN_SCORABLE_WORDS, build_scorecard
from backend.session_state import SessionState, extract_qa_pairs, looks_like_question, looks_like_tool_text
from backend.transcriber import LiveTranscriber, transcription_enabled
from backend import claude_client
from backend import meeting_brief
from backend import coach_extras
from backend import auth as accounts
from backend import library as doc_library
from backend import script_follow
from backend import prepare as prepare_mod
from backend import readiness as readiness_mod
from backend.userdata import get_userdata

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class _NormalLiveCloseFilter(logging.Filter):
    """
    Suppress noisy ADK "unexpected error" logs for normal WS close codes.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage().lower()
        if "an unexpected error occurred in live flow:" not in msg:
            return True
        return not any(token in msg for token in ("1000", "1001", "connectionclosedok"))


# Drop false-positive normal-close errors from ADK internals while preserving
# actual exception logs.
logging.getLogger("google_adk.google.adk.flows.llm_flows.base_llm_flow").addFilter(
    _NormalLiveCloseFilter()
)

APP_NAME = "pitchmirror"
POST_APP_NAME = "pitchmirror_post"

# Minimum gap between any two coach audio responses forwarded to the browser.
# This is the hard server-side gate — the tool-level cooldown in tools.py is a
# softer hint to the model, but native audio models can speak without calling
# the tool first, so we enforce silence here regardless.
#
# Set to 45s (midpoint between tools.py _GLOBAL_COOLDOWN_S=30 and
# _PER_TYPE_COOLDOWN_S=90) so spontaneous speech (bypass of flag_issue) can't
# refire before the per-type gate would have expired. Previous value of 28s
# was shorter than the global tool cooldown, allowing repeated identical
# feedback when the user paused for ≥30s between breaks.
COACH_SPEECH_COOLDOWN_S = 30.0
CONFIRMED_ISSUE_WINDOW_S = 14.0

# Hard word-count cap per coach turn.  The system prompt says "4-10 words,
# never above 12" — this server-side gate enforces that even if the model
# ignores the instruction and produces a longer response.
MAX_COACH_WORDS_PER_TURN = 14
ROLEPLAY_MAX_WORDS_PER_TURN = 90   # a partner question/reaction, not a monologue
_PROSODY_SPEECH_RMS_MIN = 0.010

# ── Singletons (created once at startup) ─────────────────────────

# Firestore client — reused across all sessions
_db = get_db()

# ── Binary frame type IDs ─────────────────────────────────────────
_T_AUDIO_IN  = 0x01   # client→server: PCM 16-bit 16kHz
_T_VIDEO_IN  = 0x02   # client→server: JPEG frame
_T_STOP      = 0x03   # client→server: end session
_T_SCREEN_IN = 0x04   # client→server: screen-share JPEG frame
_T_SLIDE_IN  = 0x05   # client→server: uploaded slide JPEG frame
_T_OTHER_AUDIO_IN = 0x06  # client→server: meeting tab/system audio (other participants), PCM 16 kHz
_T_AUDIO_OUT = 0x10   # server→client: coach PCM 24kHz


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default=%d", name, raw, default)
        return default


_SLIDE_MAX_UPLOAD_BYTES = _int_env("SLIDE_MAX_UPLOAD_BYTES", 20 * 1024 * 1024)
_SLIDE_MAX_PAGES = _int_env("SLIDE_MAX_PAGES", 20)
_SLIDE_MAX_WIDTH = _int_env("SLIDE_MAX_WIDTH", 1024)
_SLIDE_STORE_MAX_USERS = _int_env("SLIDE_STORE_MAX_USERS", 64)
_SLIDE_STORE_TTL_S = _int_env("SLIDE_STORE_TTL_S", 6 * 60 * 60)
_slides_store: dict[str, dict] = {}

# Thread pool for CPU-bound PDF rendering — keeps async event loop unblocked.
_pdf_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pdf-conv")


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or [default]


def _client_ip(forwarded_for: str | None, fallback: str | None) -> str:
    if forwarded_for:
        # X-Forwarded-For may contain a spoofable chain. Cloud Run appends the
        # real connecting IP as the LAST entry — never trust the first entry,
        # which the client controls. Iterate from the right and return the first
        # valid IP we find (which is the last non-empty segment).
        for raw in reversed(forwarded_for.split(",")):
            candidate = raw.strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                continue

    if fallback:
        try:
            ipaddress.ip_address(fallback)
            return fallback
        except ValueError:
            pass

    return "unknown"


def _bool_query(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_normal_live_close(exc: Exception) -> bool:
    """
    ADK/GenAI may surface graceful websocket termination as APIError(1000/1001).
    Treat these as normal end-of-session, not runtime failures.
    """
    status_code = getattr(exc, "status_code", None)
    code = getattr(exc, "code", None)
    if status_code in {1000, 1001} or code in {1000, 1001}:
        return True

    class_name = exc.__class__.__name__.lower()
    if "connectionclosedok" in class_name:
        return True

    err_str = str(exc).lower()
    return any(
        token in err_str
        for token in (
            "1000 none",
            "1001 none",
            "sent 1000 (ok)",
            "received 1000 (ok)",
            "connectionclosedok",
        )
    )


_USER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
_CTRL_TOKEN_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)
_ASR_TAG_RE = re.compile(r"<(?:spoken_[^>]*|noise|inaudible|unk|unknown)>", re.IGNORECASE)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
from backend.pipeline_utils import (
    extract_ai_scores as _extract_ai_scores,
    validate_synthesis as _validate_synthesis,
    missing_synthesis_sections as _missing_synthesis_sections,
)


def _hash_scope(value: str, prefix: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _normalize_user_id(raw_user_id: str | None) -> str | None:
    if not raw_user_id:
        return None
    candidate = raw_user_id.strip()
    if _USER_ID_RE.fullmatch(candidate):
        return candidate
    return None


def _resolve_user_scope(raw_user_id: str | None, *, client_ip: str) -> str:
    explicit = _normalize_user_id(raw_user_id)
    if explicit:
        return explicit
    return _hash_scope(client_ip or "unknown", "anon")


def _sanitize_transcript_chunk(text: str | None) -> str:
    """
    Like _sanitize_transcript_text but keeps leading/trailing whitespace: Live API
    transcription streams sub-word chunks whose edge spaces mark word boundaries.
    """
    raw = text or ""
    clean = _CONTROL_CHAR_RE.sub(" ", raw)
    clean = _CTRL_TOKEN_RE.sub(" ", clean)
    clean = _ASR_TAG_RE.sub(" ", clean)
    return re.sub(r"\s+", " ", clean)


_VALID_PLATFORMS = ("zoom", "google_meet", "teams", "webex", "other")
# Meetings run far longer than practice rehearsals.
MEETING_MAX_MINUTES = max(5, _int_env("MEETING_MAX_MINUTES", 90))
PRACTICE_MAX_MINUTES = 15

_MEASURED_CUES = {
    "filler": "Pause instead of fillers.",
    "pace": "Slow down — breathe between sentences.",
    "monotone": "Add energy — vary your tone.",
}

_EYE_CUES = {
    "notes": "Eyes up — look at the camera.",
    "away": "Look back at the camera.",
    "no_face": "Step back into frame and face the camera.",
}
_GAZE_LABELS = {"notes": "looking down", "away": "looking sideways", "no_face": "out of frame"}


def _sanitize_transcript_text(text: str | None) -> str:
    """
    Remove low-level ASR/control artifacts before forwarding transcript text to UI.
    Examples: <ctrl46>, <spoken_no...>, non-printable control chars.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    clean = _CONTROL_CHAR_RE.sub(" ", raw)
    clean = _CTRL_TOKEN_RE.sub(" ", clean)
    clean = _ASR_TAG_RE.sub(" ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _decode_pcm16_le(payload: bytes) -> array:
    pcm = array("h")
    if not payload:
        return pcm
    pcm.frombytes(payload)
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm


def _estimate_pitch_hz(samples: array, sample_rate: int = 16000) -> float | None:
    # Lightweight autocorrelation pitch estimate tuned for 100ms speech chunks.
    if len(samples) < 400:
        return None
    down = [float(samples[i]) for i in range(0, len(samples), 2)]
    if len(down) < 180:
        return None
    sr = sample_rate // 2
    mean = sum(down) / len(down)
    centered = [x - mean for x in down]
    energy = sum(x * x for x in centered)
    if energy <= 1e7:
        return None

    min_lag = max(8, sr // 320)   # ~320 Hz
    max_lag = min(len(centered) - 1, sr // 80)  # ~80 Hz
    if max_lag <= min_lag:
        return None

    best_lag = 0
    best_corr = 0.0
    for lag in range(min_lag, max_lag + 1):
        num = 0.0
        den1 = 0.0
        den2 = 0.0
        lim = len(centered) - lag
        for i in range(lim):
            a = centered[i]
            b = centered[i + lag]
            num += a * b
            den1 += a * a
            den2 += b * b
        if den1 <= 0.0 or den2 <= 0.0:
            continue
        corr = num / math.sqrt(den1 * den2)
        if corr > best_corr:
            best_corr = corr
            best_lag = lag

    if best_lag <= 0 or best_corr < 0.25:
        return None
    pitch = sr / float(best_lag)
    if 75.0 <= pitch <= 340.0:
        return pitch
    return None


def _audio_chunk_features(payload: bytes) -> dict:
    pcm = _decode_pcm16_le(payload)
    n = len(pcm)
    if n == 0:
        return {"rms": 0.0, "speech": False, "pitch_hz": None, "duration_s": 0.0}

    inv = 1.0 / 32768.0
    sum_sq = 0.0
    for s in pcm:
        x = float(s) * inv
        sum_sq += x * x
    rms = math.sqrt(sum_sq / n) if n else 0.0
    speech = rms >= _PROSODY_SPEECH_RMS_MIN
    pitch = _estimate_pitch_hz(pcm) if speech else None
    duration_s = n / 16000.0
    return {
        "rms": round(rms, 5),
        "speech": speech,
        "pitch_hz": round(pitch, 1) if pitch else None,
        "duration_s": round(duration_s, 4),
    }


def _request_user_scope(request: Request) -> str:
    if AUTH_ACCOUNTS:
        # The signed-in account is the only identity; a client-supplied id is ignored.
        user = getattr(request.state, "user", None) or accounts.user_from_request(request)
        if not user:
            raise HTTPException(status_code=401, detail="Sign in required")
        return user["user_id"]
    raw_user = request.headers.get("x-user-id") or request.query_params.get("user")
    client_ip = _client_ip(
        request.headers.get("x-forwarded-for"),
        request.client.host if request.client else None,
    )
    return _resolve_user_scope(raw_user, client_ip=client_ip)


def _filter_sessions_for_user(sessions: list[dict], user_id: str, limit: int | None = None) -> list[dict]:
    filtered = [s for s in sessions if isinstance(s, dict) and s.get("user_id") == user_id]
    if limit is None:
        return filtered
    return filtered[: max(0, limit)]


def _prune_slides_store(now: float | None = None) -> None:
    current = now or time.time()
    stale_users = [
        user
        for user, payload in _slides_store.items()
        if current - float(payload.get("created_at", 0.0)) > _SLIDE_STORE_TTL_S
    ]
    for user in stale_users:
        _slides_store.pop(user, None)

    if len(_slides_store) <= _SLIDE_STORE_MAX_USERS:
        return

    keep = sorted(
        _slides_store.items(),
        key=lambda kv: float(kv[1].get("created_at", 0.0)),
        reverse=True,
    )[:_SLIDE_STORE_MAX_USERS]
    _slides_store.clear()
    _slides_store.update(dict(keep))


def _slides_for_user(user_id: str) -> dict | None:
    _prune_slides_store()
    payload = _slides_store.get(user_id)
    if not payload:
        return None
    slides = payload.get("slides")
    if not isinstance(slides, list) or not slides:
        return None
    return payload


def _store_slides_for_user(user_id: str, file_name: str, slides: list[bytes]) -> dict:
    now = time.time()
    payload = {
        "deck_id": f"deck-{uuid.uuid4().hex[:12]}",
        "created_at": now,
        "file_name": file_name,
        "slides": slides,
    }
    _slides_store[user_id] = payload
    _prune_slides_store(now)
    return payload


def _convert_pdf_to_jpegs(pdf_bytes: bytes) -> list[bytes]:
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(pdf_bytes) > _SLIDE_MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF too large. Max {_SLIDE_MAX_UPLOAD_BYTES // (1024 * 1024)}MB.",
        )

    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="PyMuPDF not installed in backend image.") from exc

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid PDF upload.") from exc

    if doc.page_count == 0:
        raise HTTPException(status_code=400, detail="PDF has no pages.")

    page_count = min(doc.page_count, _SLIDE_MAX_PAGES)
    if doc.page_count > _SLIDE_MAX_PAGES:
        logger.info("Slide upload truncated from %d to %d pages", doc.page_count, _SLIDE_MAX_PAGES)

    slides: list[bytes] = []
    for idx in range(page_count):
        page = doc.load_page(idx)
        width = max(1.0, float(page.rect.width))
        scale = max(1.0, min(2.5, _SLIDE_MAX_WIDTH / width))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        jpeg = pix.tobytes(output="jpeg", jpg_quality=82)
        if not jpeg:
            continue
        slides.append(jpeg)

    doc.close()
    if not slides:
        raise HTTPException(status_code=400, detail="Could not extract slide images from PDF.")
    return slides


class SlidingWindowLimiter:
    """Per-key sliding-window request limiter."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit = max(1, limit)
        self.window_seconds = max(1, window_seconds)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        cutoff = now - self.window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()

        # Prune empty entries to bound memory when many distinct IPs have been seen.
        if len(self._hits) > 5_000:
            stale = [k for k, v in self._hits.items() if not v]
            for k in stale:
                del self._hits[k]

        if len(hits) >= self.limit:
            return False

        hits.append(now)
        return True


class ConcurrentSessionLimiter:
    """Per-key concurrent session cap."""

    def __init__(self, max_concurrent: int):
        self.max_concurrent = max(1, max_concurrent)
        self._active: dict[str, int] = defaultdict(int)

    def acquire(self, key: str) -> bool:
        if self._active[key] >= self.max_concurrent:
            return False
        self._active[key] += 1
        return True

    def release(self, key: str) -> None:
        current = self._active.get(key, 0)
        if current <= 1:
            self._active.pop(key, None)
        else:
            self._active[key] = current - 1


class PerSecondTrafficLimiter:
    """Simple per-session throughput guard for WebSocket frames."""

    def __init__(self, max_messages_per_sec: int, max_bytes_per_sec: int):
        self.max_messages_per_sec = max(1, max_messages_per_sec)
        self.max_bytes_per_sec = max(1, max_bytes_per_sec)
        self._window_start = time.monotonic()
        self._messages = 0
        self._bytes = 0

    def allow(self, frame_bytes: int) -> bool:
        now = time.monotonic()
        if now - self._window_start >= 1.0:
            self._window_start = now
            self._messages = 0
            self._bytes = 0

        self._messages += 1
        self._bytes += max(0, frame_bytes)
        return (
            self._messages <= self.max_messages_per_sec
            and self._bytes <= self.max_bytes_per_sec
        )


ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
CORS_ALLOWED_ORIGINS = _csv_env("CORS_ALLOWED_ORIGINS", "*")
_CORS_ALLOW_CREDENTIALS = CORS_ALLOWED_ORIGINS != ["*"]
API_BEARER_TOKEN = os.getenv("API_BEARER_TOKEN", "").strip()
AUTH_ENABLED = bool(API_BEARER_TOKEN)
# Built-in accounts (backend/auth.py): each person signs in and only sees their own data.
AUTH_ACCOUNTS = accounts.accounts_enabled()

if ENVIRONMENT == "production" and not API_BEARER_TOKEN and not AUTH_ACCOUNTS:
    raise RuntimeError(
        "In production set AUTH_MODE=accounts (people sign in) or API_BEARER_TOKEN. "
        "Generate a token with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )


def _extract_auth_token(
    authorization_header: str | None,
    api_key_header: str | None,
    query_token: str | None = None,
) -> str | None:
    if query_token:
        return query_token.strip()

    if api_key_header:
        return api_key_header.strip()

    if not authorization_header:
        return None

    parts = authorization_header.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization_header.strip()


def _token_valid(token: str | None) -> bool:
    if not AUTH_ENABLED:
        return True
    return bool(token and token == API_BEARER_TOKEN)


def _auth_required_for_path(path: str) -> bool:
    return path.startswith("/api")


def _ws_origin_allowed(origin: str | None) -> bool:
    """
    Return True when the WebSocket upgrade Origin is acceptable.

    In development (CORS_ALLOWED_ORIGINS == ["*"]) every origin is allowed.
    In production the Origin header must be present and in the allow-list.
    """
    if CORS_ALLOWED_ORIGINS == ["*"]:
        return True
    if not origin:
        return False
    return origin in CORS_ALLOWED_ORIGINS

HTTP_MAX_REQUESTS_PER_MINUTE = _int_env("HTTP_MAX_REQUESTS_PER_MINUTE", 120)
WS_MAX_CONNECTIONS_PER_MINUTE = _int_env("WS_MAX_CONNECTIONS_PER_MINUTE", 20)
WS_MAX_CONCURRENT_PER_IP = _int_env("WS_MAX_CONCURRENT_PER_IP", 2)
WS_MAX_CONCURRENT_PER_USER = _int_env("WS_MAX_CONCURRENT_PER_USER", 3)
WS_MAX_MESSAGES_PER_SECOND = _int_env("WS_MAX_MESSAGES_PER_SECOND", 30)
WS_MAX_BYTES_PER_SECOND = _int_env("WS_MAX_BYTES_PER_SECOND", 1_000_000)
WS_MAX_BINARY_FRAME_BYTES = _int_env("WS_MAX_BINARY_FRAME_BYTES", 600_000)
WS_MAX_TEXT_FRAME_BYTES = _int_env("WS_MAX_TEXT_FRAME_BYTES", 4_096)
# The pre-call brief (notes + points) is the one larger text message a client sends.
_BRIEF_MAX_FRAME_BYTES = 24_000
ENABLE_SCREEN_SHARE = _bool_env("ENABLE_SCREEN_SHARE", True)
ENABLE_IMAGE_GENERATION = _bool_env("ENABLE_IMAGE_GENERATION", True)
IMAGE_GENERATION_TIMEOUT_S = _int_env("IMAGE_GENERATION_TIMEOUT_S", 24)
IMAGE_GENERATION_RETRIES = _int_env("IMAGE_GENERATION_RETRIES", 1)
DEMO_MODE_DEFAULT = _bool_env("DEMO_MODE_DEFAULT", False)

_http_rate_limiter = SlidingWindowLimiter(
    limit=HTTP_MAX_REQUESTS_PER_MINUTE,
    window_seconds=60,
)
_ws_connection_limiter = SlidingWindowLimiter(
    limit=WS_MAX_CONNECTIONS_PER_MINUTE,
    window_seconds=60,
)
_ws_concurrent_limiter = ConcurrentSessionLimiter(WS_MAX_CONCURRENT_PER_IP)
# With accounts on, limits follow the signed-in person (an office network shares one IP address).
_ws_user_limiter = ConcurrentSessionLimiter(WS_MAX_CONCURRENT_PER_USER)


def _make_run_config() -> RunConfig:
    return RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=[types.Modality.AUDIO],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        session_resumption=types.SessionResumptionConfig(),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow(),
        ),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Pitchlight starting up")
    if not _db.durable:
        logger.warning(
            "No durable storage — session history will not persist across restarts. "
            "Set GOOGLE_CLOUD_PROJECT (Firestore) or LOCAL_DB_PATH (SQLite)."
        )
    yield
    logger.info("Pitchlight shutting down")
    # Cancel queued (not-yet-started) PDF jobs; running conversions finish naturally.
    _pdf_executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Pitchlight", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=_CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="frontend"), name="static")
app.include_router(accounts.router)


# With accounts on, these need no sign-in; everything else redirects (pages) or answers 401 (API).
_PUBLIC_PATHS = {"/", "/health", "/api/health", "/api/config", "/login", "/guide", "/favicon.ico"}
_PUBLIC_PREFIXES = ("/static/", "/auth/")
_ADMIN_PATHS = {"/pilot", "/api/feedback/summary"}


@app.middleware("http")
async def http_hardening_middleware(request: Request, call_next):
    path = request.url.path
    user = None
    if AUTH_ACCOUNTS:
        user = accounts.user_from_request(request)
        request.state.user = user
        if user is None and path not in _PUBLIC_PATHS and not path.startswith(_PUBLIC_PREFIXES):
            if path.startswith("/api"):
                return JSONResponse(status_code=401, content={"detail": "Sign in required"})
            return RedirectResponse(url=f"/login?next={quote(path)}", status_code=302)
        if path in _ADMIN_PATHS and not accounts.is_admin(user):
            if path.startswith("/api"):
                return JSONResponse(status_code=403, content={"detail": "Admins only"})
            return RedirectResponse(url="/app", status_code=302)
    elif _auth_required_for_path(path):
        token = _extract_auth_token(
            request.headers.get("authorization"),
            request.headers.get("x-api-key"),
        )
        if not _token_valid(token):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    client_ip = _client_ip(
        request.headers.get("x-forwarded-for"),
        request.client.host if request.client else None,
    )
    if not _http_rate_limiter.allow(user["user_id"] if user else client_ip):
        return JSONResponse(status_code=429, content={"detail": "Too Many Requests"})

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self)")
    if ENVIRONMENT == "production":
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return response


@app.get("/")
async def root():
    return FileResponse("frontend/landing.html", headers=_NO_CACHE)


# HTML pages must revalidate so a rebuilt app is picked up immediately; static
# assets are cache-busted with ?v= in the pages instead.
_NO_CACHE = {"Cache-Control": "no-cache"}


@app.get("/login")
async def login_page(request: Request):
    if AUTH_ACCOUNTS and getattr(request.state, "user", None):
        return RedirectResponse(url="/app", status_code=302)
    return FileResponse("frontend/login.html", headers=_NO_CACHE)


@app.get("/app")
async def app_page():
    return FileResponse("frontend/index.html", headers=_NO_CACHE)


@app.get("/meeting")
async def meeting_page():
    return FileResponse("frontend/meeting.html", headers=_NO_CACHE)


# ── Scripts, own documents and the readiness check ────────────────

def _load_script(state: SessionState, payload: dict) -> None:
    """The browser picked a saved script and/or turned on "use my documents"."""
    store = get_userdata()
    script = store.get_script(state.user_id, str(payload.get("script_id") or "")) if payload.get("script_id") else None
    if script:
        sections = script_follow.parse_script_text(script["text"])
        state.script_follower = script_follow.ScriptFollower(sections) or None
        state.script_id, state.script_name = script["script_id"], script["name"]
        state.script_target_minutes = int(script.get("minutes") or 0)
        state.record_timeline("script", f"Script: {script['name']} ({len(state.script_follower.lines) if state.script_follower else 0} lines)")
    if payload.get("use_library"):
        lib = doc_library.Library(store.get_docs(state.user_id))
        state.library = lib if lib else None
    state._enqueue({"type": "script_loaded", "ok": bool(script), "library": bool(state.library),
                    "sections": script["sections"] if script else None,
                    "status": state.script_follower.status() if state.script_follower else None})


def _script_feed(state: SessionState, text: str) -> None:
    follower = state.script_follower
    if follower and follower.feed(text):
        state._enqueue({"type": "script_pos", "status": follower.status()})


def _script_goto(state: SessionState, index) -> None:
    follower = state.script_follower
    if not follower or not isinstance(index, int):
        return
    follower.goto(index)
    state._enqueue({"type": "script_pos", "status": follower.status()})


async def _script_lost(state: SessionState) -> None:
    """"I'm lost": work out where the presenter is from their last words and say which line comes next."""
    follower = state.script_follower
    if not follower or state.script_lost_pending:
        return
    state.script_lost_pending = True
    try:
        user_tail = " ".join(seg["t"] for seg in state.transcript if seg.get("s") == "u")
        words = user_tail.split()[-60:]
        recent = " ".join(words)
        idx, conf = follower.locate(recent) if recent else (-1, 0.0)
        if conf >= 0.45:
            follower.set_position(idx)
        elif recent and claude_client.claude_enabled():
            numbered = "\n".join(f"{ln['idx']}: {ln['text']}" for ln in follower.lines)
            try:
                reply = await claude_client.fast_text(
                    f"A presenter is following this script:\n{numbered}\n\nTheir last words were: \"{recent}\"\n"
                    "Which script line did they most recently say, even in other words? "
                    "Reply with exactly: LINE: <number>   (or LINE: none if it matches nothing)", max_tokens=12)
                m = re.search(r"LINE\s*:\s*(\d+)", reply or "", re.IGNORECASE)
                if m and int(m.group(1)) < len(follower.lines):
                    follower.set_position(int(m.group(1)))
            except Exception as exc:
                logger.warning("script_lost lookup failed (%s)", str(exc)[:100])
        where = follower.where_you_are()
        state.record_timeline("script_lost", f"I'm lost → next: {(where['next'] or [''])[0]}"[:200])
        state._enqueue({"type": "script_where", **where, "status": follower.status()})
    finally:
        state.script_lost_pending = False


def _ground_in_library(state: SessionState, question: str, *, store_key="q") -> str:
    """Prompt text with the presenter's own documents that match the question (and a "don't invent" rule)."""
    if not state.library:
        return ""
    hits = state.library.search(question)
    if store_key:
        state.hint_sources[question] = ({"sources": doc_library.source_titles(hits)} if hits
                                        else {"not_in_notes": True})
    if hits:
        return "\n\n" + doc_library.facts_block(hits) + "\nUse these facts; never state a figure that is not in them."
    return ("\n\nThe presenter's documents contain nothing about this. Do NOT state any number, price or customer name; "
            "make one of the points \"confirm the exact figure, then follow up\".")


def _compute_script_result(state: SessionState) -> None:
    """After a run against a script: coverage per section and the Ready / Almost / Not yet check."""
    follower = state.script_follower
    if not follower:
        return
    status = follower.status()
    sections = follower.section_coverage()
    skipped = [follower.sections[i]["title"] for i in status["skipped"]]
    state.script_result = {"script_id": state.script_id, "name": state.script_name, "sections": sections,
                           "skipped": skipped, "pct": status["pct"], "total": status["total"]}
    duration = state.duration_seconds()
    minutes = max(duration / 60, 0.1)
    words = state.user_word_count()
    points = (state.pitch or {}).get("points") or state.brief_points or []
    state.readiness = readiness_mod.readiness_check(
        sections=sections, skipped=skipped, coverage_pct=status["pct"],
        key_points_hit=sum(1 for r in points if r.get("covered")), key_points_total=len(points),
        duration_s=duration, target_minutes=state.script_target_minutes or state.planned_minutes,
        clutter_per_min=round(state.scored_filler_count() / minutes, 1) if words >= 25 else None,
        wpm=round(words / minutes) if words >= 25 else None, roleplay=bool(state.roleplay_role),
    )
    logger.info("readiness session=%s verdict=%s pct=%s", state.session_id, state.readiness["verdict"], status["pct"])


@app.get("/prepare")
async def prepare_page():
    return FileResponse("frontend/prepare.html", headers=_NO_CACHE)


@app.get("/api/scripts")
async def api_list_scripts(request: Request):
    return {"scripts": get_userdata().list_scripts(_request_user_scope(request))}


@app.get("/api/scripts/{script_id}")
async def api_get_script(script_id: str, request: Request):
    script = get_userdata().get_script(_request_user_scope(request), script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    return script


@app.post("/api/scripts")
async def api_create_script(request: Request):
    user_id = _request_user_scope(request)
    try:
        return get_userdata().save_script(user_id, await request.json())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/scripts/{script_id}")
async def api_update_script(script_id: str, request: Request):
    user_id = _request_user_scope(request)
    try:
        return get_userdata().save_script(user_id, await request.json(), script_id=script_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Script not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/scripts/{script_id}")
async def api_delete_script(script_id: str, request: Request):
    if not get_userdata().delete_script(_request_user_scope(request), script_id):
        raise HTTPException(status_code=404, detail="Script not found")
    return {"ok": True}


@app.get("/api/library")
async def api_list_docs(request: Request):
    return {"docs": get_userdata().list_docs(_request_user_scope(request))}


@app.post("/api/library")
async def api_add_doc(request: Request, file: UploadFile | None = File(None)):
    """Add a document: multipart upload (.pdf/.txt/.md) or JSON {title, text}."""
    user_id = _request_user_scope(request)
    try:
        if file is not None:
            data = await file.read(5_000_001)
            if len(data) > 5_000_000:
                raise ValueError("That file is over 5 MB.")
            name = (file.filename or "document").strip()
            text = doc_library.extract_text(name, data)
            title = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name)
        else:
            body = await request.json()
            title, text = str(body.get("title") or ""), str(body.get("text") or "")
        return get_userdata().add_doc(user_id, title, text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/library/{doc_id}")
async def api_delete_doc(doc_id: str, request: Request):
    if not get_userdata().delete_doc(_request_user_scope(request), doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"ok": True}


@app.post("/api/prepare")
async def api_prepare(request: Request):
    """Prepare me: material (+ optionally the user's saved documents) → script, key points, answers, cheat sheet."""
    user_id = _request_user_scope(request)
    body = await request.json()
    material = str(body.get("material") or "").strip()
    if body.get("use_library"):
        docs = get_userdata().get_docs(user_id)
        material += "".join(f"\n\n[{d['title']}]\n{d['text']}" for d in docs)
    if len(material) < 80:
        raise HTTPException(status_code=400, detail="Paste a bit more material (a product page, FAQ or notes), at least a few sentences.")
    try:
        minutes = max(0, min(240, int(body.get("minutes") or 0)))
    except (TypeError, ValueError):
        minutes = 0
    prompt = prepare_mod.build_prompt(material, client=str(body.get("client") or "")[:80], goal=str(body.get("goal") or "")[:200],
                                      minutes=minutes, language=str(body.get("language") or "English")[:30])
    raw = ""
    try:
        raw = await claude_client.report_text(prepare_mod.SYSTEM, prompt, max_tokens=6000) if claude_client.claude_enabled() else ""
    except Exception as exc:
        logger.warning("prepare on Claude failed (%s) - trying Gemini", str(exc)[:120])
    prepared = prepare_mod.parse_prepared(raw)
    if not prepared:
        raw = await _quick_text(prepare_mod.SYSTEM + "\n\n" + prompt, max_tokens=4000, timeout=60.0)
        prepared = prepare_mod.parse_prepared(raw)
    if not prepared:
        raise HTTPException(status_code=502, detail="Couldn't draft the script right now. Try again in a moment.")
    prepared["minutes"] = minutes
    prepared["client"] = str(body.get("client") or "")[:80]
    return prepared


@app.get("/health")
async def health():
    return {"status": "ok", "service": "pitchmirror", "version": "2.0.0-adk"}


@app.get("/api/health")
async def api_health():
    return await health()


@app.post("/api/slides/upload")
async def api_upload_slides(request: Request, file: UploadFile = File(...)):
    user_id = _request_user_scope(request)
    filename = (file.filename or "slides.pdf").strip() or "slides.pdf"
    lower_name = filename.lower()
    content_type = (file.content_type or "").lower()
    if not lower_name.endswith(".pdf") and "pdf" not in content_type:
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported.")

    pdf_bytes = await file.read()
    # Run CPU-heavy PDF rendering in a thread pool to avoid blocking the event loop.
    loop = asyncio.get_running_loop()
    slides = await loop.run_in_executor(_pdf_executor, _convert_pdf_to_jpegs, pdf_bytes)
    deck = _store_slides_for_user(user_id, filename, slides)

    # Return only the first slide eagerly; the rest are available via
    # GET /api/slides/{deck_id}/{index} and should be fetched lazily/in background.
    first_slide = deck["slides"][0] if deck["slides"] else None
    eager_slides = []
    if first_slide:
        eager_slides = [{
            "index": 0,
            "mime_type": "image/jpeg",
            "data_base64": base64.b64encode(first_slide).decode("ascii"),
        }]
    return {
        "deck_id": deck["deck_id"],
        "file_name": deck["file_name"],
        "total_slides": len(deck["slides"]),
        "current_slide_index": 0,
        "slides": eager_slides,
    }


@app.get("/api/slides/{deck_id}/{slide_index}")
async def api_get_slide(request: Request, deck_id: str, slide_index: int):
    """Return a single slide by index for lazy/background loading by the browser."""
    if not deck_id or len(deck_id) > 64:
        raise HTTPException(status_code=400, detail="Invalid deck_id")
    user_id = _request_user_scope(request)
    payload = _slides_for_user(user_id)
    if not payload or payload.get("deck_id") != deck_id:
        raise HTTPException(status_code=404, detail="Slide deck not found")
    slides = payload["slides"]
    if slide_index < 0 or slide_index >= len(slides):
        raise HTTPException(status_code=404, detail="Slide index out of range")
    return {
        "index": slide_index,
        "mime_type": "image/jpeg",
        "data_base64": base64.b64encode(slides[slide_index]).decode("ascii"),
    }


def _session_summary(payload: dict) -> dict:
    categories = payload.get("categories") or {}
    return {
        "session_id": payload.get("session_id"),
        "user_id": payload.get("user_id"),
        "created_at": payload.get("created_at"),
        "coach_mode": payload.get("coach_mode", "general"),
        "session_kind": payload.get("session_kind", "practice"),
        "insufficient_speech": bool(payload.get("insufficient_speech")),
        "platform": payload.get("platform", ""),
        "delivery_context": payload.get("delivery_context", "virtual"),
        "primary_goal": payload.get("primary_goal", "balanced"),
        "screen_enabled": bool(payload.get("screen_enabled")),
        "total_slides": int(payload.get("total_slides") or 0),
        "current_slide_index": int(payload.get("current_slide_index") or 0),
        "duration_seconds": payload.get("duration_seconds", 0),
        "overall_score": payload.get("overall_score", 0),
        "filler_score": (categories.get("filler_words") or {}).get("score"),
        "eye_score": (categories.get("eye_contact") or {}).get("score"),
        "pace_score": (categories.get("pace") or {}).get("score"),
        "clarity_score": (categories.get("clarity") or {}).get("score"),
        "visual_score": (categories.get("visual_delivery") or {}).get("score"),
        "coaching_moments": len(payload.get("coaching_events") or []),
        "client": payload.get("client", ""),
        "pitch_name": (payload.get("pitch") or {}).get("name", ""),
        "pitch_version": (payload.get("pitch") or {}).get("version"),
        "roleplay_role": payload.get("roleplay_role", ""),
        "words": sum(
            len(str(seg.get("t") or "").split())
            for seg in (payload.get("transcript") or [])
            if isinstance(seg, dict) and seg.get("s") == "u"
        ),
    }


async def _fetch_user_history(user_id: str, limit: int = 1) -> str:
    """Fetch prior session context: structured profile first, raw report excerpt as fallback."""
    try:
        # Prefer structured cross-session profile (written by SESSION_SUMMARY_AGENT)
        profile = await _db.get_user_profile(user_id)
        if profile:
            issues = ", ".join(profile.get("recurring_issues") or []) or "none identified"
            strengths = ", ".join(profile.get("strengths") or []) or "none identified"
            return (
                f"Prior session profile — score: {profile.get('session_score', 0)}/100\n"
                f"Recurring issues: {issues}\n"
                f"Strengths: {strengths}\n"
                f"Trajectory: {profile.get('trajectory', '')}\n"
                f"Recommended focus: {profile.get('next_focus', 'balanced')}"
            )

        # First-time user — fall back to raw report excerpt
        sessions = await _db.list_recent(limit=limit, user_id=user_id)
        if not sessions:
            return ""
        blocks = []
        for s in sessions:
            score = s.get("overall_score", 0)
            report = s.get("final_report", "")
            summary = report.split("**Delivery**")[0].strip() if "**Delivery**" in report else report[:300]
            blocks.append(f"- Previous session (Score: {score}/100):\n{summary}")
        return "\n\n".join(blocks)
    except Exception as e:
        logger.warning("Failed to fetch user history: %s", e)
        return ""


_FEEDBACK_WOULD_USE = ("yes", "maybe", "no")


@app.post("/api/feedback")
async def api_save_feedback(request: Request):
    """Pilot feedback after a session (rating, what helped, what was annoying, would use)."""
    user_id = _request_user_scope(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    session_id = str(body.get("session_id") or "").strip()[:64]
    if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id")
    session = await _db.get_session(session_id)
    if not session or session.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        rating = int(body.get("rating"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="rating must be 1-5")
    if not 1 <= rating <= 5:
        raise HTTPException(status_code=400, detail="rating must be 1-5")
    would_use = str(body.get("would_use") or "").lower()
    feedback = {
        "user_id": user_id,
        "tester": (request.state.user["name"] if AUTH_ACCOUNTS and getattr(request.state, "user", None)
                   else str(body.get("tester") or "")).strip()[:60],
        "rating": rating,
        "helped": str(body.get("helped") or "").strip()[:1000],
        "annoying": str(body.get("annoying") or "").strip()[:1000],
        "would_use": would_use if would_use in _FEEDBACK_WOULD_USE else "",
        "session_kind": session.get("session_kind", "practice"),
        "platform": session.get("platform", ""),
        "coach_mode": session.get("coach_mode", ""),
        "overall_score": session.get("overall_score"),
        "duration_seconds": session.get("duration_seconds"),
        "created_at": int(time.time()),
    }
    await _db.save_feedback(session_id, feedback)
    return {"ok": True}


@app.get("/api/feedback/summary")
async def api_feedback_summary(limit: int = Query(default=500, ge=1, le=2000)):
    """
    Aggregate pilot feedback across all testers on this deployment.
    Pilot-only: on a shared deployment put this behind admin auth.
    """
    rows = await _db.list_feedback(limit=limit)
    n = len(rows)
    ratings = [r["rating"] for r in rows if isinstance(r.get("rating"), int)]
    would = {k: sum(1 for r in rows if r.get("would_use") == k) for k in _FEEDBACK_WOULD_USE}
    testers = sorted({r.get("tester") or "Unnamed" for r in rows})
    by_kind: dict[str, dict] = {}
    for r in rows:
        k = r.get("session_kind") or "practice"
        b = by_kind.setdefault(k, {"count": 0, "rating_sum": 0})
        b["count"] += 1
        b["rating_sum"] += r.get("rating") or 0
    # 👍/👎 that testers gave individual nudges during sessions, by nudge type.
    nudge_ratings: dict[str, dict[str, int]] = {}
    for sess in await _db.list_recent(limit=100):
        full = await _db.get_session(sess.get("session_id", "")) if "coaching_events" not in sess else sess
        for ev in (full or {}).get("coaching_events") or []:
            vote = ev.get("user_rating")
            if vote in ("up", "down"):
                bucket = nudge_ratings.setdefault(ev.get("type") or "other", {"up": 0, "down": 0})
                bucket[vote] += 1
    return {
        "count": n,
        "nudge_ratings": nudge_ratings,
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "rating_histogram": {str(i): ratings.count(i) for i in range(1, 6)},
        "would_use": would,
        "would_use_yes_pct": round(100 * would["yes"] / n) if n else None,
        "testers": testers,
        "by_kind": {k: {"count": v["count"], "avg_rating": round(v["rating_sum"] / v["count"], 2)} for k, v in by_kind.items()},
        "comments": [
            {
                "tester": r.get("tester") or "Unnamed",
                "rating": r.get("rating"),
                "would_use": r.get("would_use"),
                "helped": r.get("helped"),
                "annoying": r.get("annoying"),
                "session_kind": r.get("session_kind"),
                "created_at": r.get("created_at"),
            }
            for r in rows if r.get("helped") or r.get("annoying")
        ][:200],
    }


@app.get("/pilot")
async def pilot_page():
    return FileResponse("frontend/pilot.html", headers=_NO_CACHE)


@app.get("/guide")
async def guide_page():
    return FileResponse("frontend/guide.html", headers=_NO_CACHE)


@app.get("/api/sessions/stats")
async def api_session_stats(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    user_id = _request_user_scope(request)
    sessions = await _db.list_recent(limit=limit, user_id=user_id)
    if not sessions:
        return {
            "count": 0,
            "avg_overall_score": 0,
            "avg_duration_seconds": 0,
            "best_score": 0,
        }

    scores = [int(s.get("overall_score") or 0) for s in sessions]
    durations = [float(s.get("duration_seconds") or 0) for s in sessions]
    return {
        "count": len(sessions),
        "avg_overall_score": round(sum(scores) / len(scores), 1),
        "avg_duration_seconds": round(sum(durations) / len(durations), 1),
        "best_score": max(scores),
    }


@app.get("/api/sessions/trends")
async def api_session_trends(request: Request, limit: int = Query(default=40, ge=1, le=100)):
    """Per-session numbers for the progress charts, oldest first."""
    user_id = _request_user_scope(request)
    sessions = await _db.list_recent(limit=limit, user_id=user_id)
    points = [coach_extras.session_trend_point(s) for s in sessions]
    points.sort(key=lambda p: p.get("created_at") or 0)
    return {"points": points}


@app.get("/api/sessions")
async def api_list_sessions(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    user_id = _request_user_scope(request)
    sessions = await _db.list_recent(limit=limit, user_id=user_id)
    return {"sessions": [_session_summary(s) for s in sessions]}


@app.get("/api/sessions/{session_id}")
async def api_get_session(request: Request, session_id: str):
    sid = session_id.strip()
    if not sid or len(sid) > 128:
        raise HTTPException(status_code=400, detail="Invalid session_id")

    user_id = _request_user_scope(request)
    payload = await _db.get_session(sid)
    if not payload:
        raise HTTPException(status_code=404, detail="Session not found")
    if payload.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return payload


# ── Clients, rehearsal, trends, goals, export ────────────────────

EXPORT_WEBHOOK_URL = os.getenv("PITCHMIRROR_EXPORT_WEBHOOK_URL", "").strip()
EXPORT_WEBHOOK_LABEL = (os.getenv("PITCHMIRROR_EXPORT_WEBHOOK_LABEL", "") or "CRM").strip()[:30]


async def _request_json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Expected a JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")
    return body


@app.get("/api/config")
async def api_config():
    return {
        "export_webhook": bool(EXPORT_WEBHOOK_URL),
        "export_label": EXPORT_WEBHOOK_LABEL,
        "transcription": transcription_enabled(),
        "auth": "accounts" if AUTH_ACCOUNTS else "off",
    }


@app.get("/api/clients")
async def api_clients(request: Request):
    """Clients this user has had meetings with, most recent first (for the brief's autocomplete)."""
    user_id = _request_user_scope(request)
    seen: dict[str, dict] = {}
    for payload in await _db.list_recent(limit=100, user_id=user_id):
        name = coach_extras.normalize_client(payload.get("client", ""))
        if payload.get("session_kind") != "meeting" or not name:
            continue
        row = seen.setdefault(name.lower(), {"name": name, "last_at": payload.get("created_at"), "meetings": 0})
        row["meetings"] += 1
    return {"clients": list(seen.values())[:50]}


@app.get("/api/clients/last")
async def api_client_last_call(request: Request, name: str = Query(default="", max_length=120)):
    user_id = _request_user_scope(request)
    return {"call": await _previous_call_for(user_id, name)}


@app.post("/api/rehearsal/questions")
async def api_rehearsal_questions(request: Request):
    """The likely hard questions for a pre-call rehearsal (the AI plays the other side)."""
    user_id = _request_user_scope(request)
    body = await _request_json(request)
    mode = normalize_coach_mode(body.get("mode"))
    client = coach_extras.normalize_client(str(body.get("client") or ""))
    notes = str(body.get("notes") or "")[: meeting_brief.MAX_NOTES_CHARS]
    points = meeting_brief.clean_points(body.get("points") or [])
    previous = coach_extras.previous_call_context(await _previous_call_for(user_id, client)) if client else ""
    reply = await _quick_text(
        coach_extras.rehearsal_questions_prompt(mode, client, notes, points, previous), max_tokens=400, timeout=20.0,
    )
    questions = coach_extras.parse_rehearsal_questions(reply)
    source = "ai"
    if len(questions) < 2:
        questions, source = coach_extras.fallback_questions(mode), "fallback"
    logger.info("rehearsal_questions user=%s mode=%s source=%s n=%d", user_id, mode, source, len(questions))
    return {"questions": questions, "source": source}


@app.post("/api/rehearsal/review")
async def api_rehearsal_review(request: Request):
    """Rate rehearsal (or retry) answers and write stronger versions — same reviewer as meetings."""
    body = await _request_json(request)
    mode = normalize_coach_mode(body.get("mode"))
    pairs = []
    for item in (body.get("items") or [])[:6]:
        if not isinstance(item, dict):
            continue
        question = " ".join(str(item.get("question") or "").split())[:400]
        answer = " ".join(str(item.get("answer") or "").split())[:1500]
        if question and answer:
            pairs.append({"question": question, "answer": answer, "ts": 0})
    if not pairs:
        return {"review": []}
    listing = "\n\n".join(f"PAIR {i}\nQUESTION: {p['question']}\nANSWER: {p['answer']}" for i, p in enumerate(pairs))
    message = f"MEETING TYPE: {mode}\n\n{listing}"
    raw = await _quick_text(f"{QA_REVIEW_AGENT.instruction}\n\n{message}", max_tokens=1600, timeout=45.0)
    if not raw.strip():
        raw = await _run_single_agent(QA_REVIEW_AGENT, message)
    return {"review": _merge_qa_review(pairs, raw, [])}


@app.get("/api/pitches")
async def api_pitches(request: Request):
    """Named pitches with every saved version, for the intake autocomplete and the comparison."""
    user_id = _request_user_scope(request)
    return {"pitches": coach_extras.group_pitch_versions(await _db.list_recent(limit=100, user_id=user_id))}


@app.get("/api/goals")
async def api_get_goals(request: Request):
    user_id = _request_user_scope(request)
    meta = {k: {"label": v[0], "unit": v[1], "direction": v[2]} for k, v in coach_extras.GOAL_METRICS.items()}
    return {"goals": coach_extras.clean_goals(await _db.get_goals(user_id)), "metrics": meta}


@app.put("/api/goals")
async def api_put_goals(request: Request):
    user_id = _request_user_scope(request)
    body = await _request_json(request)
    goals = coach_extras.clean_goals(body.get("goals"))
    await _db.save_goals(user_id, goals)
    return {"goals": goals}


@app.post("/api/sessions/{session_id}/export")
async def api_export_session(request: Request, session_id: str):
    """Send a meeting's recap to the configured webhook (Zapier, Make, a CRM's inbound hook)."""
    if not EXPORT_WEBHOOK_URL:
        raise HTTPException(status_code=404, detail="No export webhook configured")
    user_id = _request_user_scope(request)
    payload = await _db.get_session(session_id.strip()[:128])
    if not payload or payload.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    recap = payload.get("recap") or {}
    doc = {
        "source": "pitchlight",
        "session_id": payload.get("session_id"),
        "created_at": payload.get("created_at"),
        "client": payload.get("client", ""),
        "meeting_type": payload.get("coach_mode", ""),
        "duration_seconds": payload.get("duration_seconds"),
        "summary": recap.get("summary", ""),
        "decisions": recap.get("decisions") or [],
        "action_items": recap.get("action_items") or [],
        "commitments": payload.get("commitments") or [],
        "open_questions": recap.get("open_questions") or [],
        "client_signals": recap.get("client_signals") or [],
        "follow_up_email": recap.get("follow_up_email") or {},
    }
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(EXPORT_WEBHOOK_URL, json=doc)
    except Exception as exc:
        logger.warning("export webhook failed session=%s (%s)", session_id, str(exc)[:120])
        raise HTTPException(status_code=502, detail="The export destination didn't respond")
    logger.info("export webhook session=%s status=%s", session_id, resp.status_code)
    if resp.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"The export destination returned {resp.status_code}")
    return {"ok": True}


async def _rehearsal_ws(websocket: WebSocket, session_id: str) -> None:
    """
    Transcription-only connection for pre-call rehearsals and answer retries: the browser
    streams mic PCM (0x01), the server returns finished lines. No coach, nothing saved.
    """
    out: asyncio.Queue = asyncio.Queue(maxsize=200)

    def emit(msg: dict | None) -> None:
        try:
            out.put_nowait(msg)
        except asyncio.QueueFull:
            pass

    if not transcription_enabled():
        await _safe_send(websocket, {"type": "status", "state": "error", "message": "Speech-to-text isn't configured."})
        await websocket.close()
        return
    def on_text(text: str) -> None:
        clean = _sanitize_transcript_text(text)
        if clean:
            emit({"type": "transcript", "speaker": "user", "text": clean})

    stt = LiveTranscriber(
        "rehearsal",
        on_text,
        session_id=session_id,
        on_status=lambda st: emit({"type": "stt_status", "speaker": "user", "state": st}),
    )
    limiter = PerSecondTrafficLimiter(WS_MAX_MESSAGES_PER_SECOND, WS_MAX_BYTES_PER_SECOND)

    async def sender() -> None:
        while True:
            msg = await out.get()
            if msg is None:
                return
            await _safe_send(websocket, msg)

    stt_task = asyncio.get_running_loop().create_task(stt.run())
    send_task = asyncio.get_running_loop().create_task(sender())
    await _safe_send(websocket, {"type": "status", "state": "connected", "session_id": session_id})
    try:
        async with asyncio.timeout(10 * 60):
            while True:
                frame = await websocket.receive()
                if frame.get("type") == "websocket.disconnect":
                    break
                data = frame.get("bytes")
                if data:
                    if len(data) > WS_MAX_BINARY_FRAME_BYTES or not limiter.allow(len(data)):
                        break
                    if data[0] == _T_AUDIO_IN:
                        stt.push(bytes(data[1:]))
                    elif data[0] == _T_STOP:
                        break
                elif frame.get("text"):
                    if "stop" in frame["text"][:40]:
                        break
    except (asyncio.TimeoutError, WebSocketDisconnect):
        pass
    finally:
        stt.close()
        try:
            await asyncio.wait_for(stt_task, timeout=8.0)   # lets the last sentence come back
        except (asyncio.TimeoutError, Exception):
            stt_task.cancel()
        emit({"type": "rehearsal_done"})
        emit(None)   # stops the sender after the queue drains
        try:
            await asyncio.wait_for(send_task, timeout=3.0)
        except (asyncio.TimeoutError, Exception):
            send_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass


# ── WebSocket session handler ─────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    client_ip = _client_ip(
        websocket.headers.get("x-forwarded-for"),
        websocket.client.host if websocket.client else None,
    )

    ws_user = None
    if AUTH_ACCOUNTS:
        ws_user = accounts.user_from_request(websocket)
        if not ws_user:
            logger.warning("WS rejected, not signed in: ip=%s", client_ip)
            await websocket.close(code=1008, reason="Sign in required")
            return
    else:
        token = _extract_auth_token(
            websocket.headers.get("authorization"),
            websocket.headers.get("x-api-key"),
            websocket.query_params.get("token"),
        )
        if not _token_valid(token):
            logger.warning("WS auth rejected: ip=%s", client_ip)
            await websocket.close(code=1008, reason="Unauthorized")
            return
    limit_key = ws_user["user_id"] if ws_user else client_ip
    concurrent_limiter = _ws_user_limiter if ws_user else _ws_concurrent_limiter

    origin = websocket.headers.get("origin")
    if not _ws_origin_allowed(origin):
        logger.warning("WS origin rejected: origin=%r ip=%s", origin, client_ip)
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    if not _ws_connection_limiter.allow(limit_key):
        logger.warning("WS connection rate-limited: ip=%s", client_ip)
        await websocket.close(code=1008, reason="Connection rate limit exceeded")
        return

    connection_acquired = concurrent_limiter.acquire(limit_key)
    if not connection_acquired:
        logger.warning("WS concurrent limit exceeded: ip=%s", client_ip)
        await websocket.close(code=1008, reason="Too many concurrent sessions")
        return

    try:
        await websocket.accept()
    except Exception:
        concurrent_limiter.release(limit_key)
        raise
    session_id = str(uuid.uuid4())
    if (websocket.query_params.get("kind") or "").lower() == "rehearsal":
        try:
            await _rehearsal_ws(websocket, session_id)
        finally:
            concurrent_limiter.release(limit_key)
        return
    raw_user_id = websocket.query_params.get("user") or websocket.headers.get("x-user-id")
    user_id = ws_user["user_id"] if ws_user else _resolve_user_scope(raw_user_id, client_ip=client_ip)
    coach_mode = normalize_coach_mode(websocket.query_params.get("mode"))
    delivery_context = normalize_delivery_context(websocket.query_params.get("context"))
    primary_goal = normalize_primary_goal(websocket.query_params.get("goal"))
    raw_persona = websocket.query_params.get("persona")
    persona = raw_persona if raw_persona in VALID_PERSONAS else "coach"
    screen_enabled = ENABLE_SCREEN_SHARE and _bool_query(
        websocket.query_params.get("screen"),
        default=False,
    )
    demo_mode = _bool_query(
        websocket.query_params.get("demo"),
        default=DEMO_MODE_DEFAULT,
    )
    session_kind = "meeting" if (websocket.query_params.get("kind") or "").lower() == "meeting" else "practice"
    raw_platform = (websocket.query_params.get("platform") or "").strip().lower()
    platform = raw_platform if raw_platform in _VALID_PLATFORMS else ("other" if session_kind == "meeting" else "")
    raw_chatty = (websocket.query_params.get("chatty") or "").strip().lower()
    chattiness = raw_chatty if raw_chatty in coach_extras.CHATTINESS else "balanced"
    live_feedback = (websocket.query_params.get("feedback") or "live").strip().lower() != "end"
    raw_role = (websocket.query_params.get("roleplay") or "").strip().lower()
    roleplay_role = raw_role if session_kind == "practice" and raw_role in coach_extras.ROLEPLAY_ROLES else ""
    if session_kind == "meeting":
        screen_enabled = False   # no slide/screen coaching while in a real call
        demo_mode = False
    user_slides = _slides_for_user(user_id)
    total_slides = len(user_slides["slides"]) if user_slides and session_kind == "practice" else 0

    # ── Context Caching: fetch recent session summary ─────────────
    previous_summary = await _fetch_user_history(user_id, limit=1)

    logger.info(
        "Session started: %s ip=%s user=%s persona=%s kind=%s platform=%s",
        session_id, client_ip, user_id, persona, session_kind, platform or "-",
    )

    state = SessionState(
        session_id=session_id,
        user_id=user_id,
        coach_mode=coach_mode,
        delivery_context=delivery_context,
        primary_goal=primary_goal,
        persona=persona,
        session_kind=session_kind,
        platform=platform,
        screen_enabled=screen_enabled,
        demo_mode=demo_mode,
        previous_summary=previous_summary,
        total_slides=total_slides,
        current_slide_index=0,
        roleplay_role=roleplay_role,
        chattiness=chattiness,
        live_feedback=live_feedback or session_kind == "meeting",
    )
    state.record_timeline(
        "session",
        "Session connected",
        {
            "mode": state.coach_mode,
            "context": state.delivery_context,
            "goal": state.primary_goal,
            "screen_enabled": state.screen_enabled,
            "demo_mode": state.demo_mode,
        },
    )
    live_request_queue = LiveRequestQueue()
    state.live_queue = live_request_queue
    if transcription_enabled():
        state.user_stt = LiveTranscriber(
            "user", _stt_callback(state, "user"), session_id=session_id, on_status=_stt_status_callback(state, "user"),
        )
        if session_kind == "meeting":
            state.other_stt = LiveTranscriber(
                "other", _stt_callback(state, "other"), session_id=session_id,
                on_status=_stt_status_callback(state, "other"),
            )

    try:
        live_agent = make_live_coach_agent(state)
        live_service = InMemorySessionService()
        live_runner = Runner(
            app_name=APP_NAME,
            agent=live_agent,
            session_service=live_service,
        )
        await live_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        run_config = _make_run_config()

        await websocket.send_json({
            "type": "status",
            "state": "connected",
            "session_id": session_id,
            "user_id": state.user_id,
            "coach_mode": state.coach_mode,
            "delivery_context": state.delivery_context,
            "primary_goal": state.primary_goal,
            "screen_enabled": state.screen_enabled,
            "demo_mode": state.demo_mode,
            "session_kind": state.session_kind,
            "platform": state.platform,
            "max_minutes": MEETING_MAX_MINUTES if session_kind == "meeting" else PRACTICE_MAX_MINUTES,
            "total_slides": state.total_slides,
            "current_slide_index": state.current_slide_index,
            "has_uploaded_slides": state.total_slides > 0,
        })

        max_minutes = MEETING_MAX_MINUTES if session_kind == "meeting" else PRACTICE_MAX_MINUTES
        async with asyncio.timeout(max_minutes * 60):   # hard limit per session
            async with asyncio.TaskGroup() as tg:
                tg.create_task(
                    _upstream(websocket, live_request_queue, state),
                    name="upstream",
                )
                tg.create_task(
                    _downstream(websocket, live_runner, user_id, session_id,
                                 live_request_queue, run_config, state),
                    name="downstream",
                )
                tg.create_task(
                    _drain_tool_events(websocket, state),
                    name="tool_events",
                )
                if state.demo_mode:
                    tg.create_task(
                        _demo_seed_scheduler(websocket, state),
                        name="demo_scheduler",
                    )
                if state.session_kind == "meeting":
                    tg.create_task(_meeting_nudge_watcher(state, live_request_queue), name="meeting_nudges")
                else:
                    tg.create_task(_practice_voice_followup(state, live_request_queue), name="voice_followup")
                    if state.roleplay_role:
                        tg.create_task(_roleplay_kickoff_fallback(state), name="roleplay_kickoff")
                    tg.create_task(_practice_live_coach(state), name="practice_live_coach")
                # The client-audio transcriber starts lazily, on the first meeting-audio frame
                # (see _upstream) — no idle session when nothing is shared.
                if state.user_stt is not None:
                    tg.create_task(state.user_stt.run(), name="stt_user")

    except asyncio.TimeoutError:
        logger.warning("Session %s exceeded %d-minute limit — closing", session_id, max_minutes)
        await _safe_send(websocket, {"type": "status", "state": "session_timeout"})
        await websocket.close()
    except WebSocketDisconnect:
        logger.info(f"Browser disconnected: {session_id}")
    except BaseException as exc:
        errors = exc.exceptions if isinstance(exc, ExceptionGroup) else [exc]
        for e in errors:
            if not isinstance(e, (WebSocketDisconnect, asyncio.CancelledError)):
                logger.error(f"Session {session_id}: {e}", exc_info=e)
    finally:
        state.is_active = False
        state.stop_drain()          # sentinel → drain task exits cleanly
        live_request_queue.close()
        concurrent_limiter.release(limit_key)

    if state.other_stt_task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(state.other_stt_task), timeout=8.0)
        except (asyncio.TimeoutError, Exception):
            state.other_stt_task.cancel()
    state.transcription_health = {
        stt.name: dict(stt.stats) for stt in (state.user_stt, state.other_stt)
        if stt is not None and (stt is state.user_stt or state.other_stt_task is not None)
    }

    # Setup chatter ("start bolna start listen", asides in another language) isn't part of the talk.
    ignored = state.trim_setup_chatter()
    if ignored:
        logger.info("setup_chatter_ignored session=%s %s", session_id, [r["text"][:60] for r in ignored])

    # ── Post-session analysis ─────────────────────────────────────
    try:
        await _run_post_session(websocket, state)
    except Exception as e:
        logger.error(f"Post-session failed [{session_id}]: {e}", exc_info=True)
        state.research_tips = ""
        state.generated_assets = []
        await _safe_send(websocket, {
            "type": "analysis_complete",
            "report": "Analysis unavailable — see transcript for session details.",
            "research_tips": "",
            "generated_assets": [],
        })

    # ── Persist and return final scorecard ────────────────────────
    _compute_script_result(state)
    scorecard = build_scorecard(state)
    await _db.save_session(session_id, scorecard)
    await _safe_send(websocket, {"type": "scorecard", "data": scorecard})
    logger.info(f"Session {session_id} complete. Score: {scorecard['overall_score']}")
    if callable(state.pending_profile_update):
        await state.pending_profile_update()


# ── Concurrent session tasks ──────────────────────────────────────

def _stt_callback(state: SessionState, speaker: str):
    """Store a finished transcription segment and show it in the browser as one line."""
    def on_text(text: str) -> None:
        text = _sanitize_transcript_text(text)
        if not text:
            return
        if speaker == "other":
            state.other_audio_seen = True
        state.add_transcript(speaker, text)
        state._enqueue({"type": "transcript", "speaker": speaker, "text": text})
        state._enqueue({"type": "transcript_end", "speaker": speaker})
        if speaker == "user":
            _script_feed(state, text)
        if speaker == "other" and state.session_kind == "meeting" and looks_like_question(text):
            _log_client_question(state, text)
            _request_answer_hint(state, text)
        if state.session_kind == "meeting" and coach_extras.looks_like_commitment(text):
            _track_commitment(state, speaker, text)
        if state.session_kind == "meeting" and speaker == "user":
            asked = coach_extras.count_open_questions(text)
            if asked:
                state.open_questions += asked
                state.last_open_question_ts = state.duration_seconds()
                state._enqueue({"type": "questions", "count": state.open_questions})
        objection_kind = (coach_extras.objection_class(text)
                          if state.session_kind == "meeting" and speaker == "other" and state.chattiness != "quiet" else None)
        if (objection_kind
                and time.monotonic() - state.last_objection_at >= coach_extras.OBJECTION_MIN_GAP_S
                and time.monotonic() - state.objection_class_at.get(objection_kind, -1e9) >= coach_extras.OBJECTION_GAP_S):
            state.last_objection_at = state.objection_class_at[objection_kind] = time.monotonic()
            try:
                asyncio.get_running_loop().create_task(_run_objection_card(state, text))
            except RuntimeError:
                pass
    return on_text


def _stt_status_callback(state: SessionState, speaker: str):
    """Tell the browser when a transcript stream is re-syncing (or back to normal)."""
    def on_status(status: str) -> None:
        state._enqueue({"type": "stt_status", "speaker": speaker, "state": status})
        if status != "ok":
            state.record_timeline("transcription", f"{speaker} transcript {status}")
    return on_status


HINT_TIMEOUT_S = 25.0
_HINT_FRAMEWORKS = {
    "interview": "STAR: situation, what you did, the result",
    "pitch": "problem, proof, the ask",
    "sales_demo": "their pain, your value, the next step",
    "presentation": "main point, evidence, takeaway",
    "general": "direct answer, one example, wrap-up",
}


# Fast text model for answer outlines (~1-2 s). The live coaching model can only answer
# in audio, which took ~10 s; it remains the fallback if this model is unavailable.
HINT_MODEL = os.getenv("PITCHMIRROR_HINT_MODEL", "gemini-3.1-flash-lite").strip()
HINT_MODEL_TIMEOUT_S = 8.0
_hint_client: genai.Client | None = None


def _deliver_answer_hint(state: SessionState, question: str, points: list[str]) -> None:
    hint = {"question": question, "points": points, "ts": round(state.duration_seconds(), 1),
            **state.hint_sources.get(question, {})}
    state.answer_hints.append(hint)
    state._enqueue({"type": "answer_hint", **hint})
    state.record_timeline("answer_hint", f"Hint: {' / '.join(points)}"[:220], {"question": question[:200]})
    logger.info("answer_hint session=%s points=%s", state.session_id, points)


def _request_answer_hint(state: SessionState, question: str) -> None:
    """Get a 3-point outline the presenter can follow to answer the question just asked."""
    if state.live_closed:
        return
    state.hint_question = question
    prompt = _answer_hint_prompt(state, question)
    prompt += _ground_in_library(state, question)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:   # no event loop (unit tests / shutdown)
        loop = None
    if (HINT_MODEL or claude_client.claude_enabled()) and loop is not None:
        loop.create_task(_hint_via_text_model(state, question, prompt))
    else:
        _hint_via_live_model(state, prompt)


async def _hint_via_text_model(state: SessionState, question: str, prompt: str) -> None:
    global _hint_client
    started = time.monotonic()
    if claude_client.claude_enabled():
        try:
            points = _parse_answer_hint(await claude_client.fast_text(prompt))
            if points:
                if state.hint_question == question and not state.live_closed:
                    _deliver_answer_hint(state, question, points)
                logger.info("answer_hint_latency session=%s %.1fs model=%s", state.session_id,
                            time.monotonic() - started, claude_client.FAST_MODEL)
                return
        except Exception as exc:
            logger.warning("answer_hint on Claude failed (%s) — trying Gemini", str(exc)[:120])
        started = time.monotonic()
    try:
        if not HINT_MODEL:
            raise RuntimeError("no Gemini hint model configured")
        if _hint_client is None:
            _hint_client = genai.Client()
        resp = await _hedged_generate(_hint_client, HINT_MODEL, prompt)
        points = _parse_answer_hint(resp.text or "")
        if points:
            if state.hint_question == question and not state.live_closed:   # still the latest question
                _deliver_answer_hint(state, question, points)
            logger.info("answer_hint_latency session=%s %.1fs model=%s", state.session_id, time.monotonic() - started, HINT_MODEL)
            return
        logger.info("answer_hint_unparsed session=%s text=%r", state.session_id, (resp.text or "")[:120])
    except Exception as exc:
        logger.warning("answer_hint text model failed (%s) — falling back to live model", str(exc)[:120])
    if state.hint_question == question:
        _hint_via_live_model(state, prompt)


HINT_HEDGE_AFTER_S = 2.5


async def _hedged_generate(client: genai.Client, model: str, prompt: str):
    """
    The model usually answers in <1 s but occasionally stalls for 6-12 s. If the first
    request hasn't answered after HINT_HEDGE_AFTER_S, fire a second one and take whichever
    finishes first (costs an extra request only when the first one is slow).
    """
    def call():
        return asyncio.create_task(client.aio.models.generate_content(model=model, contents=prompt))

    tasks = {call()}
    deadline = time.monotonic() + HINT_MODEL_TIMEOUT_S
    try:
        done, _ = await asyncio.wait(tasks, timeout=HINT_HEDGE_AFTER_S)
        if not done:
            tasks.add(call())
        while tasks:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            done, tasks = await asyncio.wait(tasks, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if not task.exception():
                    return task.result()
            if not tasks and done:
                raise next(iter(done)).exception()
        raise asyncio.TimeoutError()
    finally:
        for task in tasks:
            task.cancel()


def _hint_via_live_model(state: SessionState, prompt: str) -> None:
    if state.live_queue is None or state.live_closed:
        return
    state.hint_sent_at = time.monotonic()
    state.live_queue.send_content(types.Content(role="user", parts=[types.Part(text=prompt)]))


def _answer_hint_prompt(state: SessionState, question: str) -> str:
    framework = _HINT_FRAMEWORKS.get(state.coach_mode, _HINT_FRAMEWORKS["general"])
    return (
        f'[answer helper] The other participant just asked: "{question}"\n'
        f"Meeting type: {state.coach_mode.replace('_', ' ')}. Recent conversation:\n"
        f"{state.conversation_tail(seconds=90, max_chars=1200)}\n"
        + (f"{brief}\n" if (brief := _brief_block(state)) else "")
        + f"\nGive the presenter a quick outline to answer well ({framework}). Use the presenter's own "
        "facts from the conversation when possible; never invent numbers.\n"
        "Reply in exactly this spoken form and nothing else: "
        "\"Hint. First, <point>. Second, <point>. Third, <point>.\" Each point at most 8 words. Do not call tools."
    )


def _parse_answer_hint(text: str) -> list[str] | None:
    """'Hint. First, X. Second, Y. Third, Z.' → [X, Y, Z]; None if it isn't a hint."""
    raw = re.sub(r"\s+", " ", text or "").strip()
    parts = re.split(r"\b(?:first|second|third|1|2|3)\s*[,:.)-]\s*", raw, flags=re.IGNORECASE)
    points = [p.strip(" .;:-") for p in parts[1:] if p.strip(" .;:-")]
    if len(points) < 2:
        return None
    return [" ".join(p.split()[:12]) for p in points[:3]]


CONTENT_CHECK_INTERVAL_S = 25.0
CONTENT_CHECK_MIN_NEW_WORDS = 15
CONTENT_CHECK_TIMEOUT_S = 30.0

# Spoken category → issue type. The live model answers by voice, so its reply is read
# from the audio transcription ("Off topic: answer the question directly.").
_CONTENT_CATEGORIES = (
    ("not answering", "off_topic"),
    ("off topic", "off_topic"),
    ("rambling", "rambling"),
    ("repetitive", "rambling"),
    ("unclear", "clarity"),
    ("monotone", "monotone"),
    ("boring", "monotone"),
    ("hedging", "confidence"),
    ("contradiction", "contradiction"),
)
_CONTENT_DEFAULT_CUES = {
    "off_topic": "Answer the question they asked.",
    "rambling": "Wrap up — make your point.",
    "clarity": "Say it in one simple sentence.",
    "monotone": "Add energy — vary your tone.",
    "confidence": "Drop the hedging — be direct.",
    "contradiction": "Keep your story consistent.",
}


def _parse_content_verdict(text: str) -> tuple[str, str] | None:
    """
    Parse the model's one-line verdict: "NONE" → ("none", ""), "CATEGORY: nudge" →
    (issue_type, nudge). Returns None when the text isn't a verdict.
    """
    raw = re.sub(r"\s+", " ", text or "").strip()
    low = re.sub(r"[_\-]", " ", raw.lower())     # same length → positions line up with raw
    if re.match(r"^\W*(none|no nudge|nothing)\b", low):
        return ("none", "")
    for key, issue_type in _CONTENT_CATEGORIES:
        m = re.search(rf"\b{key}\b\s*[:\-–,.]?\s*", low)
        if m and m.start() <= 12:
            nudge = raw[m.end():].strip(" .:-–\"'")
            words = nudge.split()
            nudge = " ".join(words[:10]) if words else _CONTENT_DEFAULT_CUES[issue_type]
            return (issue_type, nudge)
    return None


def _handle_meeting_reply(state: SessionState, text: str) -> None:
    """Route a meeting-mode model reply to the pending answer hint or content check."""
    if state.hint_sent_at and time.monotonic() - state.hint_sent_at <= HINT_TIMEOUT_S:
        points = _parse_answer_hint(text)
        if points:
            state.hint_sent_at = 0.0
            _deliver_answer_hint(state, state.hint_question, points)
            return
    elif state.hint_sent_at:
        state.hint_sent_at = 0.0
    _handle_content_check_reply(state, text)


def _handle_content_check_reply(state: SessionState, text: str) -> None:
    if not state.content_check_sent_at:
        return   # model spoke on its own (e.g. reacting to the other participant) — ignore
    if time.monotonic() - state.content_check_sent_at > CONTENT_CHECK_TIMEOUT_S:
        state.content_check_sent_at = 0.0
        return
    verdict = _parse_content_verdict(text)
    if verdict is None:
        logger.info("content_check_unparsed session=%s text=%r", state.session_id, text.strip()[:120])
        return
    state.content_check_sent_at = 0.0
    issue_type, nudge = verdict
    logger.info("content_check session=%s verdict=%s nudge=%r", state.session_id, issue_type, nudge)
    if issue_type == "none" or not callable(state.flag_issue_fn):
        return
    def _flag(retry: bool) -> None:
        if state.live_closed:
            return
        result = state.flag_issue_fn(
            issue_type,
            f"Coach check: {nudge}",
            {"metric": "conversation_check", "threshold": "n/a", "sensor": "live_model_review"},
        )
        if result.get("recorded"):
            state._enqueue({"type": "coach_cue", "index": len(state.events) - 1, "text": nudge})
        elif retry and result.get("reason") == "global_cooldown":
            # Another nudge was just shown; deliver this one as soon as the gap allows.
            asyncio.get_running_loop().call_later(
                float(result.get("wait_seconds") or 1) + 0.5, _flag, False,
            )

    _flag(retry=True)


def _content_check_prompt(state: SessionState) -> str:
    p = state.prosody_metrics
    m = state.recent_speech_metrics()
    return (
        "[coach check] Live meeting, type: " + state.coach_mode.replace("_", " ") + ".\n"
        "Recent conversation (You = the presenter you coach, Them = other participant"
        + ("" if state.other_audio_seen else "; the other side is not being captured") + "):\n"
        f"{state.conversation_tail() or '(nothing yet)'}\n\n"
        f"Presenter voice right now: {m['wpm']:.0f} wpm, tone monotony {p.get('monotony_score', 0)}/100, "
        f"energy {p.get('speaking_energy', 0)}.\n"
        + (f"{brief}\n\n" if (brief := _brief_block(state)) else "")
        + "Does the presenter need ONE nudge right now? Categories:\n"
        "OFF_TOPIC (drifting / not answering what Them asked), RAMBLING (too long, repeating), "
        "UNCLEAR, MONOTONE (flat, boring delivery), HEDGING (unsure, 'I think maybe'), CONTRADICTION.\n"
        "Reply with exactly one short line and nothing else: NONE — or — CATEGORY: nudge of at most 7 words. "
        "Do not call tools."
    )


def _apply_brief(state: SessionState, payload: dict) -> None:
    """Store the pre-call brief; derive points from the notes when none were listed."""
    client = coach_extras.normalize_client(str(payload.get("client") or ""))
    if client and client != state.client_name:
        state.client_name = client
        try:
            asyncio.get_running_loop().create_task(_load_previous_call(state))
        except RuntimeError:
            pass
    state.prepared_answers = coach_extras.parse_prepared_answers(str(payload.get("objections") or "")[:4000])
    state.brief_notes = str(payload.get("notes") or "")[: meeting_brief.MAX_NOTES_CHARS].strip()
    try:
        state.planned_minutes = max(0, min(240, int(payload.get("planned_minutes") or 0)))
    except (TypeError, ValueError):
        state.planned_minutes = 0
    points = meeting_brief.clean_points(payload.get("points") or [])
    state.brief_points = meeting_brief.new_point_rows(points)
    state.record_timeline("brief", f"Brief: {len(points)} points, {state.planned_minutes} min planned")
    logger.info("brief session=%s points=%d notes_chars=%d planned=%s",
                state.session_id, len(points), len(state.brief_notes), state.planned_minutes)
    if points or not state.brief_notes:
        state._enqueue({"type": "brief_points", "points": state.brief_points, "derived": False})
        return
    try:
        asyncio.get_running_loop().create_task(_derive_brief_points(state))
    except RuntimeError:
        pass


async def _derive_brief_points(state: SessionState) -> None:
    if not claude_client.claude_enabled():
        return
    try:
        reply = await claude_client.fast_text(
            meeting_brief.extract_points_prompt(state.brief_notes, state.coach_mode), max_tokens=400,
        )
        points = meeting_brief.parse_points_reply(reply)
        if points and not state.brief_points:
            state.brief_points = meeting_brief.new_point_rows(points)
            state._enqueue({"type": "brief_points", "points": state.brief_points, "derived": True})
            logger.info("brief_points_derived session=%s points=%s", state.session_id, points)
    except Exception as exc:
        logger.warning("brief point extraction failed (%s)", str(exc)[:120])


async def _run_coverage_check(state: SessionState) -> None:
    """Ask the fast model which brief points the presenter has covered so far."""
    pending = [p for p in state.brief_points if not p["covered"]]
    if not pending or not claude_client.claude_enabled():
        return
    try:
        reply = await claude_client.fast_text(
            meeting_brief.coverage_prompt(state.brief_points, state.conversation_tail(seconds=3600, max_chars=6000)),
            max_tokens=60,
        )
    except Exception as exc:
        logger.warning("coverage check failed (%s)", str(exc)[:120])
        return
    covered = meeting_brief.parse_coverage_reply(reply, len(state.brief_points))
    if not covered:
        return
    changed = False
    for i in covered:
        row = state.brief_points[i]
        if not row["covered"]:
            row["covered"], row["ts"] = True, round(state.duration_seconds(), 1)
            changed = True
            state.record_timeline("brief_point", f"Covered: {row['text']}"[:220])
    if changed:
        state._enqueue({"type": "brief_points", "points": state.brief_points})
        logger.info("brief_coverage session=%s covered=%d/%d", state.session_id,
                    sum(p["covered"] for p in state.brief_points), len(state.brief_points))


def _maybe_brief_reminder(state: SessionState) -> None:
    """Near the planned end, remind the presenter of points still not covered (once)."""
    if state.brief_reminded or not state.planned_minutes or not state.brief_points:
        return
    if state.duration_seconds() < state.planned_minutes * 60 * meeting_brief.REMINDER_AT_FRACTION:
        return
    state.brief_reminded = True
    remaining = [p["text"] for p in state.brief_points if not p["covered"]]
    if remaining:
        state._enqueue({"type": "brief_reminder", "points": remaining})
        state.record_timeline("brief_reminder", f"Still to cover: {'; '.join(remaining)}"[:220])


# ── On-demand help, client mood, nudge ratings, interruptions (Meeting Mode) ──

_HELP_KINDS = ("answer", "catchup", "ask")
_HELP_MIN_GAP_S = 3.0
_MOODS = ("engaged", "positive", "neutral", "confused", "skeptical", "disengaged")
_MOOD_ALERTS = {
    "confused": "Client seems confused — pause and check in.",
    "skeptical": "Client sounds skeptical — give proof or an example.",
    "disengaged": "Client is drifting — ask them a question.",
}


MAX_CLIENT_QUESTIONS = 40


def _log_client_question(state: SessionState, text: str) -> None:
    """Keep every question the other side asks and tell the answers panel."""
    text = " ".join(text.split())[:300]
    if not text or (state.client_questions and state.client_questions[-1]["text"] == text):
        return
    state.client_questions.append({"ts": round(state.duration_seconds(), 1), "text": text})
    del state.client_questions[:-MAX_CLIENT_QUESTIONS]
    state._enqueue({"type": "client_questions", "items": state.client_questions})


def _last_other_question(state: SessionState) -> str:
    for seg in reversed(state.transcript):
        if seg.get("s") == "o" and looks_like_question(seg.get("t") or ""):
            return seg["t"].strip()
    for seg in reversed(state.transcript):
        if seg.get("s") == "o" and (seg.get("t") or "").strip():
            return seg["t"].strip()
    return ""


def _help_prompt(state: SessionState, kind: str) -> str:
    brief = _brief_block(state)
    convo = state.conversation_tail(seconds=240 if kind == "catchup" else 150, max_chars=3200) or "(nothing yet)"
    if kind == "catchup":
        return (
            "A presenter in a live meeting lost focus for a moment. Recent conversation "
            "(You = presenter, Them = other participants):\n"
            f"{convo}\n\nCatch them up in at most 4 short lines (max 18 words each): what was just discussed, "
            "what Them asked or worried about, and what Them is waiting for now. "
            "Plain lines, no bullets, no preamble."
        )
    # kind == "ask"
    return (
        f"Live {state.coach_mode.replace('_', ' ')}. Recent conversation (You = presenter, Them = other participants):\n"
        f"{convo}\n" + (f"{brief}\n" if brief else "")
        + "\nSuggest ONE smart question the presenter could ask Them right now to move the meeting toward "
        "its goals (uncover needs, handle a concern, or agree a next step). "
        "Reply exactly: QUESTION: <question, max 20 words> | WHY: <max 10 words>"
    )


def _parse_help_reply(kind: str, text: str) -> list[str]:
    raw = (text or "").strip()
    if kind == "ask":
        m = re.search(r"question\s*:\s*(.+?)(?:\|\s*why\s*:\s*(.+))?$", raw, re.IGNORECASE | re.DOTALL)
        if m:
            lines = [" ".join(m.group(1).split())]
            if m.group(2):
                lines.append(f"Why: {' '.join(m.group(2).split())}")
            return lines
    lines = [ln.strip(" -•*") for ln in raw.splitlines() if ln.strip(" -•*")]
    return [" ".join(ln.split()[:24]) for ln in lines[:4]]


def _request_help(state: SessionState, kind: str, question_index=None) -> None:
    """Presenter tapped a help button in the bubble (or "Answer this" on an earlier client question)."""
    if kind not in _HELP_KINDS or state.live_closed:
        return
    last = getattr(state, "_help_last", {})
    now = time.monotonic()
    if now - last.get(kind, 0.0) < _HELP_MIN_GAP_S:
        return
    last[kind] = now
    state._help_last = last
    state.record_timeline("help", f"Asked for help: {kind}")
    if kind == "answer":
        question = ""
        if isinstance(question_index, int) and 0 <= question_index < len(state.client_questions):
            question = state.client_questions[question_index]["text"]      # an earlier question picked from the list
        question = question or _last_other_question(state)
        if not question:
            state._enqueue({"type": "help_reply", "kind": kind, "title": "Help me answer",
                            "lines": ["No question from them yet — share the meeting audio so I can hear them."]})
            return
        _request_answer_hint(state, question)
        return
    try:
        asyncio.get_running_loop().create_task(_run_help(state, kind))
    except RuntimeError:
        pass


async def _run_help(state: SessionState, kind: str) -> None:
    title = {"catchup": "Catch me up", "ask": "Question to ask"}[kind]
    lines: list[str] = []
    if claude_client.claude_enabled():
        try:
            lines = _parse_help_reply(kind, await claude_client.fast_text(_help_prompt(state, kind), max_tokens=200))
        except Exception as exc:
            logger.warning("help %s failed (%s)", kind, str(exc)[:120])
    if not lines:
        lines = ["Couldn't get that right now — try again in a moment."]
    state._enqueue({"type": "help_reply", "kind": kind, "title": title, "lines": lines})
    logger.info("help_reply session=%s kind=%s lines=%s", state.session_id, kind, lines)


def _mood_prompt(state: SessionState) -> str:
    return (
        "Live meeting. Recent conversation (You = presenter, Them = the client/other side):\n"
        f"{state.conversation_tail(seconds=120, max_chars=2500)}\n\n"
        "Judge only how THEM comes across right now. Reply exactly one line: "
        "MOOD: ENGAGED | POSITIVE | NEUTRAL | CONFUSED | SKEPTICAL | DISENGAGED — <reason, max 8 words>"
    )


def _parse_mood(text: str) -> tuple[str, str] | None:
    m = re.search(r"mood\s*:\s*([a-z]+)\s*(?:[—–:-]+\s*(.*))?", text or "", re.IGNORECASE)
    if not m or m.group(1).lower() not in _MOODS:
        return None
    return m.group(1).lower(), " ".join((m.group(2) or "").split())[:80]


async def _run_mood_check(state: SessionState) -> None:
    if not claude_client.claude_enabled():
        return
    try:
        parsed = _parse_mood(await claude_client.fast_text(_mood_prompt(state), max_tokens=60))
    except Exception as exc:
        logger.warning("mood check failed (%s)", str(exc)[:120])
        return
    if not parsed:
        return
    mood, reason = parsed
    prev = state.client_moods[-1]["mood"] if state.client_moods else None
    row = {"ts": round(state.duration_seconds(), 1), "mood": mood, "reason": reason}
    state.client_moods.append(row)
    alert = _MOOD_ALERTS.get(mood) if mood != prev else None
    state._enqueue({"type": "client_mood", **row, "alert": alert})
    if mood != prev:
        state.record_timeline("client_mood", f"Client {mood}: {reason}"[:220])
    logger.info("client_mood session=%s %s (%s)", state.session_id, mood, reason)


def _rate_nudge(state: SessionState, index, useful) -> None:
    try:
        i = int(index)
    except (TypeError, ValueError):
        return
    if 0 <= i < len(state.events) and isinstance(useful, bool):
        state.events[i].user_rating = "up" if useful else "down"


def _record_interruption(state: SessionState, overlap_s) -> None:
    """Browser detected the presenter talking over the other participant."""
    state.interruptions += 1
    try:
        overlap = round(float(overlap_s), 1)
    except (TypeError, ValueError):
        overlap = None
    if callable(state.flag_issue_fn):
        result = state.flag_issue_fn(
            "interruption",
            "Started talking while they were still speaking.",
            {"metric": "overlap_seconds", "threshold": ">=0.8s", "measured": overlap, "sensor": "audio_overlap"},
        )
        if result.get("recorded"):
            state._enqueue({"type": "coach_cue", "index": len(state.events) - 1, "text": "Let them finish."})
        elif result.get("reason") == "global_cooldown":
            # Another tip was just shown: keep the measurement for the review without a nudge.
            state.record_event(
                "interruption", "Started talking while they were still speaking.",
                {"metric": "overlap_seconds", "threshold": ">=0.8s", "measured": overlap, "sensor": "audio_overlap"},
                source="sensor",
            )


def _record_talk_time(state: SessionState, payload: dict) -> None:
    """
    Browser voice-activity report (meeting mode).
    kind="stats": running talk-time totals; kind="monologue": you've held the floor for
    `seconds`; kind="talk_share": you've done `share` of the talking over the last few minutes.
    """
    kind = str(payload.get("kind") or "")
    if kind == "stats":
        try:
            you_s, them_s = float(payload.get("you_s") or 0), float(payload.get("them_s") or 0)
            longest = float(payload.get("longest_monologue_s") or 0)
        except (TypeError, ValueError):
            return
        state.talk_stats = {
            **coach_extras.talk_summary(you_s, them_s),
            "longest_monologue_s": round(max(longest, float(state.talk_stats.get("longest_monologue_s") or 0)), 1),
            "monologues": int(state.talk_stats.get("monologues") or 0),
        }
        return
    if not callable(state.flag_issue_fn):
        return
    if kind == "monologue":
        limit = coach_extras.monologue_limit_s(state.coach_mode)
        try:
            seconds = round(float(payload.get("seconds") or 0))
        except (TypeError, ValueError):
            return
        if not limit or seconds < limit:
            return
        state.talk_stats["monologues"] = int(state.talk_stats.get("monologues") or 0) + 1
        issue = ("monologue", f"Talked for {seconds} seconds without letting them in.",
                 {"metric": "monologue_seconds", "threshold": f">={limit}s", "measured": seconds,
                  "sensor": "voice_activity"}, coach_extras.MONOLOGUE_CUE)
    elif kind == "talk_share":
        if state.coach_mode not in coach_extras.TALK_SHARE_MODES or not state.other_audio_seen:
            return
        try:
            share = float(payload.get("share") or 0)
        except (TypeError, ValueError):
            return
        if share < coach_extras.TALK_SHARE_LIMIT:
            return
        pct = round(share * 100)
        issue = ("talk_share", f"You did {pct}% of the talking over the last few minutes.",
                 {"metric": "talk_share_5min", "threshold": f">={round(coach_extras.TALK_SHARE_LIMIT * 100)}%",
                  "measured": pct, "sensor": "voice_activity"}, coach_extras.TALK_SHARE_CUE)
    else:
        return
    issue_type, description, evidence, cue = issue
    result = state.flag_issue_fn(issue_type, description, evidence)
    if result.get("recorded"):
        state._enqueue({"type": "coach_cue", "index": len(state.events) - 1, "text": cue})
        logger.info("talk_time_nudge session=%s %s", state.session_id, description)
    elif result.get("reason") == "global_cooldown":
        # Another tip was just shown: keep the measurement for the review without a nudge.
        state.record_event(issue_type, description, evidence, source="sensor")


# ── Quick text jobs (commitments, previous call, rehearsal) ──────────────────

async def _quick_text(prompt: str, *, max_tokens: int = 400, timeout: float | None = None) -> str:
    """Fast model first (Claude Haiku), then the Gemini text models; "" if all fail."""
    global _hint_client
    if claude_client.claude_enabled():
        try:
            return await claude_client.fast_text(prompt, max_tokens=max_tokens, timeout=timeout)
        except Exception as exc:
            logger.warning("quick_text on Claude failed (%s) — trying Gemini", str(exc)[:120])
    models = [m for m in dict.fromkeys([HINT_MODEL, *_FALLBACK_MODELS]) if m]
    for model in models:
        try:
            if _hint_client is None:
                _hint_client = genai.Client()
            resp = await asyncio.wait_for(
                _hint_client.aio.models.generate_content(model=model, contents=prompt), timeout=timeout or 20.0,
            )
            if (resp.text or "").strip():
                return resp.text
        except Exception as exc:
            logger.warning("quick_text on %s failed (%s)", model, str(exc)[:120])
    return ""


def _track_commitment(state: SessionState, speaker: str, text: str) -> None:
    try:
        asyncio.get_running_loop().create_task(_extract_commitments(state, speaker, text))
    except RuntimeError:
        pass


async def _extract_commitments(state: SessionState, speaker: str, text: str) -> None:
    """A finished line sounded like a promise: confirm it and add it to the live list."""
    ts = round(state.duration_seconds(), 1)
    items: list[dict] = []
    reply = await _quick_text(
        coach_extras.commitment_prompt(speaker, text, state.conversation_tail(seconds=60, max_chars=1200)),
        max_tokens=120,
    )
    if reply.strip().upper().startswith("NONE"):
        return
    items = coach_extras.parse_commitments(reply) if reply else [coach_extras.fallback_commitment(text)]
    owner = "you" if speaker == "user" else "them"
    added = []
    for item in items:
        if len(state.commitments) >= coach_extras.MAX_COMMITMENTS:
            break
        if coach_extras.is_duplicate_commitment(item["task"], state.commitments):
            continue
        row = {"ts": ts, "owner": owner, "task": item["task"], "due": item.get("due", "")}
        state.commitments.append(row)
        added.append(row)
        state.record_timeline("commitment", f"{'You' if owner == 'you' else 'Them'}: {item['task']}"[:220])
    if added:
        state._enqueue({"type": "commitments", "items": state.commitments, "added": added})
        logger.info("commitments session=%s added=%s", state.session_id, [r["task"] for r in added])


async def _run_facts_check(state: SessionState) -> None:
    """Pull names, numbers, dates, needs and tools out of the last couple of minutes."""
    if len(state.facts) >= coach_extras.MAX_FACTS:
        return
    reply = await _quick_text(
        coach_extras.facts_prompt(state.conversation_tail(seconds=120, max_chars=2400), state.facts),
        max_tokens=220,
    )
    if not reply or reply.strip().upper().startswith("NONE"):
        return
    ts = round(state.duration_seconds(), 1)
    added = []
    for fact in coach_extras.parse_facts(reply):
        if len(state.facts) >= coach_extras.MAX_FACTS:
            break
        if coach_extras.is_duplicate_fact(fact, state.facts):
            continue
        row = {"ts": ts, **fact}
        state.facts.append(row)
        added.append(row)
        state.record_timeline("fact", f"{fact['kind']}: {fact['text']}"[:220])
    if added:
        state._enqueue({"type": "facts", "items": state.facts, "added": added})
        logger.info("facts session=%s added=%s", state.session_id, [f["text"] for f in added])


def _send_advice(state: SessionState, kind: str, text: str, **extra) -> bool:
    """Show one piece of live advice (meeting notch / practice side panel), if it isn't noise."""
    now = time.monotonic()
    recent = [a["text"] for a in state.live_advice]
    if not text or coach_extras.is_repeat_advice(text, recent):
        return False
    if kind not in ("missed", "objection") and now - state.last_advice_at < coach_extras.ADVICE_GAP_S:
        return False
    if kind == "good":
        if now - state.last_good_at < coach_extras.POSITIVE_GAP_S:
            return False
        state.last_good_at = now
    state.last_advice_at = now
    row = {"ts": round(state.duration_seconds(), 1), "kind": kind, "text": text, **extra}
    state.live_advice.append(row)
    state.record_timeline("advice", f"{kind}: {text}"[:220])
    state._enqueue({"type": "advice", **row})
    logger.info("live_advice session=%s kind=%s text=%r", state.session_id, kind, text)
    return True


async def _run_meeting_advice(state: SessionState) -> None:
    """Next move / missed question / (rarely) a positive note, from the last two minutes."""
    last = next((seg for seg in reversed(state.transcript) if seg.get("s") in ("u", "o")), None)
    if last and last["s"] == "o" and state.duration_seconds() - last.get("te", last["ts"]) < 10.0:
        return      # the other side still has the floor — wait for the presenter to respond
    state.advice_pending = True
    try:
        allow_good = time.monotonic() - state.last_good_at >= coach_extras.POSITIVE_GAP_S
        reply = await _quick_text(coach_extras.meeting_advice_prompt(
            state.coach_mode, state.conversation_tail(seconds=120, max_chars=2400), _brief_block(state, max_chars=800),
            [a["text"] for a in state.live_advice], allow_good,
        ), max_tokens=60)
        parsed = coach_extras.parse_advice(reply)
        if parsed and parsed[0] in ("next", "missed", "good"):
            _send_advice(state, *parsed)
    finally:
        state.advice_pending = False


async def _run_objection_card(state: SessionState, quote: str) -> None:
    """Them pushed back: show the prepared answer (if any) and a 3-step way to respond."""
    grounding = _ground_in_library(state, quote, store_key=None)
    reply = await _quick_text(coach_extras.objection_prompt(
        quote, state.conversation_tail(seconds=90, max_chars=1500), state.prepared_answers,
        _brief_block(state, max_chars=600) + grounding,
    ), max_tokens=160)
    card = coach_extras.parse_objection(reply, state.prepared_answers)
    if not card:
        return
    hits = state.library.search(quote) if state.library else []
    card = {"ts": round(state.duration_seconds(), 1), "quote": quote[:200], **card,
            **({"sources": doc_library.source_titles(hits)} if hits else {})}
    state.objections.append(card)
    state.record_timeline("objection", f"{card['kind']}: {card['concern']}"[:220])
    state.last_advice_at = time.monotonic()
    state._enqueue({"type": "objection_card", **card})
    logger.info("objection_card session=%s kind=%s prepared=%s", state.session_id, card["kind"], bool(card["prepared"]))


async def _run_next_step_check(state: SessionState) -> None:
    reply = await _quick_text(coach_extras.next_step_prompt(
        state.conversation_tail(seconds=10 ** 6, max_chars=6000)), max_tokens=5)
    if reply.strip().upper().startswith("NO"):
        _send_advice(state, "next", "No next step yet — propose a follow-up with a date.")


async def _run_practice_advice(state: SessionState) -> None:
    state.advice_pending = True
    try:
        rows = meeting_brief.new_point_rows(state.pitch_points)
        covered = {p["text"] for p in state.brief_points if p.get("covered")}
        for r in rows:
            r["covered"] = r["text"] in covered
        user_tail = "\n".join(seg["t"] for seg in state.transcript
                              if seg.get("s") == "u" and seg.get("te", seg["ts"]) >= state.duration_seconds() - 75)
        reply = await _quick_text(coach_extras.practice_advice_prompt(
            state.coach_mode, user_tail[-2000:], rows, [a["text"] for a in state.live_advice]), max_tokens=50)
        parsed = coach_extras.parse_advice(reply)
        if parsed and parsed[0] in ("tip", "good"):
            _send_advice(state, *parsed)
    finally:
        state.advice_pending = False


async def _run_say_better(state: SessionState) -> None:
    last = " ".join(seg["t"] for seg in [s for s in state.transcript if s.get("s") == "u"][-2:]).strip()
    if len(last.split()) < 5:
        state._enqueue({"type": "say_better", "error": "Say a sentence or two first."})
        return
    last = " ".join(last.split()[-70:])
    rewrite = coach_extras.parse_say_better(await _quick_text(coach_extras.say_better_prompt(state.coach_mode, last),
                                                              max_tokens=200, timeout=15.0))
    if not rewrite:
        state._enqueue({"type": "say_better", "error": "Couldn't rewrite that right now — try again."})
        return
    state.record_timeline("say_better", rewrite[:220])
    state._enqueue({"type": "say_better", "original": last, "rewrite": rewrite})


async def _practice_coverage(state: SessionState) -> None:
    before = sum(p["covered"] for p in state.brief_points)
    await _run_coverage_check(state)
    if sum(p["covered"] for p in state.brief_points) != before:
        state._enqueue({"type": "pitch_points", "points": state.brief_points})


async def _practice_live_coach(state: SessionState) -> None:
    """
    Practice side panel, live: speaking stats every 3 s; key points ticked off as they're said;
    and a content advice card about every 20 s of new speech (structure, clarity, evidence, praise).
    """
    last_cov_at = last_adv_at = time.monotonic()
    words_at_cov = words_at_adv = 0
    if state.pitch_points and not state.brief_points:
        state.brief_points = meeting_brief.new_point_rows(state.pitch_points)
    while not state.live_closed and state.is_active:
        await asyncio.sleep(3.0)
        if state.live_closed:
            break
        m = state.recent_speech_metrics()
        state._enqueue({
            "type": "live_stats", "wpm": round(m["wpm"]), "fillers": state.transcript_filler_count(),
            "repeats": state.transcript_stumble_count(), "eye_drops": state.scored_eye_drops(),
            "gaze": state.browser_gaze, "elapsed": round(state.duration_seconds()),
        })
        if not state.live_feedback:
            continue
        if state.pitch_points and not state.brief_points:
            state.brief_points = meeting_brief.new_point_rows(state.pitch_points)
            state._enqueue({"type": "pitch_points", "points": state.brief_points})
        words = state.user_word_count()
        now = time.monotonic()
        if state.brief_points and now - last_cov_at >= 20.0 and words - words_at_cov >= 12:
            last_cov_at, words_at_cov = now, words
            asyncio.get_running_loop().create_task(_practice_coverage(state))
        if (not state.advice_pending and now - last_adv_at >= coach_extras.PRACTICE_ADVICE_EVERY_S
                and words - words_at_adv >= coach_extras.PRACTICE_ADVICE_MIN_NEW_WORDS):
            last_adv_at, words_at_adv = now, words
            asyncio.get_running_loop().create_task(_run_practice_advice(state))


async def _previous_call_for(user_id: str, client: str, *, exclude: str = "") -> dict:
    """Most recent saved meeting this user had with `client` (case-insensitive)."""
    key = coach_extras.client_key(client)
    if not key:
        return {}
    for payload in await _db.list_recent(limit=100, user_id=user_id):
        if (payload.get("session_kind") == "meeting" and payload.get("session_id") != exclude
                and coach_extras.client_key(payload.get("client", "")) == key):
            return coach_extras.previous_call_summary(payload)
    return {}


async def _load_previous_call(state: SessionState) -> None:
    try:
        state.previous_call = await _previous_call_for(state.user_id, state.client_name, exclude=state.session_id)
    except Exception as exc:
        logger.warning("previous call lookup failed (%s)", str(exc)[:120])
        return
    if state.previous_call:
        state._enqueue({"type": "previous_call", "call": state.previous_call})
        logger.info("previous_call session=%s client=%r from=%s", state.session_id, state.client_name,
                    state.previous_call.get("session_id"))


def _apply_pitch(state: SessionState, payload: dict) -> None:
    """Practice: which named pitch this run is a version of, and the points it should hit."""
    state.pitch_name = coach_extras.normalize_pitch(str(payload.get("name") or ""))
    state.pitch_points = meeting_brief.clean_points(payload.get("points") or [])[: coach_extras.MAX_PITCH_POINTS]
    if state.pitch_name:
        state.record_timeline("pitch", f"Pitch: {state.pitch_name} ({len(state.pitch_points)} key points)")
        logger.info("pitch session=%s name=%r points=%d", state.session_id, state.pitch_name, len(state.pitch_points))
    _roleplay_kickoff(state)


def _roleplay_kickoff(state: SessionState) -> None:
    """Role-play: the partner opens the conversation (once), knowing what is being pitched."""
    if not state.roleplay_role or state.roleplay_started or state.live_queue is None or state.live_closed:
        return
    state.roleplay_started = True
    about = ""
    if state.pitch_name:
        about = f" They are presenting: {state.pitch_name}."
        if state.pitch_points:
            about += " Points they plan to make: " + "; ".join(state.pitch_points) + "."
    state.live_queue.send_content(types.Content(role="user", parts=[types.Part(text=(
        f"[start] Begin the role-play now.{about} Greet them in one short sentence and ask them to start "
        "(for an interview, ask your first question). Then listen."
    ))]))
    state.record_timeline("roleplay", f"Role-play started as {state.roleplay_role}")
    logger.info("roleplay_start session=%s role=%s", state.session_id, state.roleplay_role)


async def _roleplay_kickoff_fallback(state: SessionState) -> None:
    await asyncio.sleep(2.0)    # give the browser a moment to send the pitch details first
    _roleplay_kickoff(state)


async def _score_pitch_version(state: SessionState) -> None:
    """Version number for this run of the named pitch + which key points it hit."""
    if not state.pitch_name:
        return
    key = coach_extras.pitch_key(state.pitch_name)
    previous = [s for s in await _db.list_recent(limit=100, user_id=state.user_id)
                if coach_extras.pitch_key((s.get("pitch") or {}).get("name", "")) == key]
    version = 1 + max([int((s.get("pitch") or {}).get("version") or 0) for s in previous] + [0])
    rows = meeting_brief.new_point_rows(state.pitch_points)
    if rows and state.user_word_count() >= 15:
        reply = await _quick_text(
            meeting_brief.coverage_prompt(rows, state.conversation_tail(seconds=10 ** 6, max_chars=8000)),
            max_tokens=60, timeout=20.0,
        )
        for i in meeting_brief.parse_coverage_reply(reply, len(rows)) or set():
            rows[i]["covered"] = True
    state.pitch = {"name": state.pitch_name, "version": version, "points": rows}
    logger.info("pitch_version session=%s %r v%d points=%d/%d", state.session_id, state.pitch_name, version,
                sum(r["covered"] for r in rows), len(rows))


def _brief_block(state: SessionState, *, max_chars: int = 1200) -> str:
    """Everything the live helpers and the recap should know before reading the call."""
    parts = []
    if state.client_name:
        parts.append(f"Meeting with: {state.client_name}")
    if state.brief_notes or state.brief_points:
        parts.append(meeting_brief.brief_context(state.brief_notes, state.brief_points, max_chars=max_chars))
    previous = coach_extras.previous_call_context(state.previous_call)
    if previous:
        parts.append(previous)
    if state.commitments:
        parts.append("Commitments made so far:\n" + coach_extras.commitments_text(state.commitments))
    if state.facts:
        parts.append("Facts noted so far:\n" + coach_extras.facts_text(state.facts))
    if state.prepared_answers:
        parts.append("Presenter's prepared answers: " + "; ".join(f"{p['label']}: {p['answer']}" for p in state.prepared_answers))
    return "\n".join(parts)


async def _run_content_check(state: SessionState, live_request_queue: LiveRequestQueue, prompt: str) -> None:
    """Conversation check on Claude's fast model; the live Gemini model answers if Claude can't."""
    if claude_client.claude_enabled():
        started = time.monotonic()
        try:
            reply = await claude_client.fast_text(prompt, max_tokens=120)
            logger.info("content_check_reply session=%s %.1fs %r", state.session_id, time.monotonic() - started, reply[:100])
            _handle_content_check_reply(state, reply)
            return
        except Exception as exc:
            logger.warning("content check on Claude failed (%s) — asking the live model", str(exc)[:120])
    if not state.live_closed:
        live_request_queue.send_content(types.Content(role="user", parts=[types.Part(text=prompt)]))


_PRACTICE_CUES = {
    "filler": "Pause instead of saying um.",
    "pace": "Slow down — you're rushing.",
    "clarity": "Say that again in one clear sentence.",
    "contradiction": "That contradicts what you said earlier.",
    "slide_clarity": "Simplify this slide.",
    "slide_mismatch": "Your words don't match the slide.",
}
VOICE_FOLLOWUP_AFTER_S = 2.0


async def _practice_voice_followup(state: SessionState, live_request_queue: LiveRequestQueue) -> None:
    """
    Practice mode: measured filler / pace checks, plus a spoken fallback — if no coach
    audio followed a confirmed issue within a couple of seconds, the browser speaks one
    fixed short cue (the live model often checks tools instead of speaking).
    """
    followed_up = 0.0
    fast_checks = 0
    tick = 0
    while not state.live_closed and state.is_active:
        await asyncio.sleep(1.0)
        tick += 1
        # Measured checks every 3 s, so fillers / fast pace are caught even when the
        # model doesn't call flag_issue itself.
        if tick % 3 == 0 and callable(state.flag_issue_fn):
            m = state.recent_speech_metrics()
            fast_checks = fast_checks + 1 if (m["wpm"] > 180 and m["words"] >= 25) else 0
            issue = None
            if m["fillers"] >= 3:
                words = ", ".join(f"'{w}'" for w in dict.fromkeys(m["filler_words"]))
                issue = ("filler", f"{m['fillers']} filler words in 30s ({words}).",
                         {"metric": "filler_count_30s", "threshold": ">=3", "measured": m["fillers"]})
            elif fast_checks >= 2:
                issue = ("pace", f"Speaking at {m['wpm']:.0f} words per minute.",
                         {"metric": "wpm_20s", "threshold": ">180", "measured": m["wpm"]})
            if issue:
                issue_type, description, evidence = issue
                evidence["sensor"] = "transcript_metrics"
                if state.flag_issue_fn(issue_type, description, evidence).get("recorded"):
                    fast_checks = 0
                    logger.info("practice_measured_issue session=%s %s", state.session_id, description)
        issue_at, issue_type = state.last_confirmed_issue_time, state.last_confirmed_issue_type
        if not issue_at or issue_at == followed_up or time.monotonic() - issue_at < VOICE_FOLLOWUP_AFTER_S:
            continue
        followed_up = issue_at
        cue = _PRACTICE_CUES.get(issue_type)
        if not cue or state.last_coach_speech_time >= issue_at:
            continue   # coach already spoke, or the issue has its own cue path (eye contact)
        # The live model, told to measure before speaking, tends to call tools instead of
        # voicing a requested cue — so the browser speaks it (speak=True) with its own voice.
        # Role-play: the partner stays in character, so the cue is shown silently.
        state._enqueue({"type": "coach_cue", "index": len(state.events) - 1, "text": cue,
                        "speak": not state.roleplay_role and state.live_feedback,
                        "silent": bool(state.roleplay_role) or not state.live_feedback})
        logger.info("voice_followup session=%s issue=%s cue=%r", state.session_id, issue_type, cue)


async def _meeting_nudge_watcher(state: SessionState, live_request_queue: LiveRequestQueue) -> None:
    """
    Meeting mode real-time coaching, independent of the live model's own turn-taking:
    - every 3 s: fillers, sustained fast pace and flat/boring tone from measurements;
    - every ~25 s of new speech: a content check of the recent You/Them conversation
      (off-topic, rambling, unclear, hedging, contradiction) answered by the live model.
    All nudges go through flag_issue cooldowns, so they stay rare.
    """
    fast_checks = 0
    flat_checks = 0
    last_check_at = time.monotonic()
    words_at_last_check = 0
    last_coverage_at = time.monotonic()
    words_at_last_coverage = 0
    last_mood_at = time.monotonic()
    them_words_at_last_mood = 0
    last_facts_at = time.monotonic()
    facts_since_ts = 0.0
    last_advice_check = time.monotonic()
    words_at_advice = 0
    while not state.live_closed and state.is_active:
        await asyncio.sleep(3.0)
        if state.live_closed or not callable(state.flag_issue_fn):
            continue
        _maybe_brief_reminder(state)
        # Live advice (next move / missed question / positives), question coach and next-step guard.
        if state.chattiness != "quiet":
            every = coach_extras.ADVICE_EVERY_S.get(state.chattiness, 30.0)
            convo_words = sum(len(seg["t"].split()) for seg in state.transcript if seg.get("s") in ("u", "o"))
            if (not state.advice_pending and time.monotonic() - last_advice_check >= every
                    and convo_words - words_at_advice >= coach_extras.ADVICE_MIN_NEW_WORDS):
                last_advice_check, words_at_advice = time.monotonic(), convo_words
                asyncio.get_running_loop().create_task(_run_meeting_advice(state))
            now_rel = state.duration_seconds()
            if (state.coach_mode in coach_extras.QUESTION_COACH_MODES
                    and now_rel >= coach_extras.QUESTION_COACH_AFTER_S
                    and now_rel - max(state.last_open_question_ts, state.question_nudged_at) >= coach_extras.QUESTION_COACH_AFTER_S
                    and state.user_word_count() >= 60):
                state.question_nudged_at = now_rel
                _send_advice(state, "next", "Ask an open question — what, how or why.")
            minutes = state.planned_minutes or 0
            due = (minutes and now_rel >= minutes * 60 - 300) or (not minutes and now_rel >= 20 * 60)
            if (state.coach_mode in coach_extras.NEXT_STEP_MODES and due and not state.next_step_checked
                    and state.other_audio_seen):
                state.next_step_checked = True
                asyncio.get_running_loop().create_task(_run_next_step_check(state))
        if time.monotonic() - last_facts_at >= coach_extras.FACTS_CHECK_EVERY_S:
            new_lines = [seg["t"] for seg in state.transcript
                         if seg.get("s") in ("u", "o") and seg.get("te", seg["ts"]) > facts_since_ts]
            if sum(len(t.split()) for t in new_lines) >= coach_extras.FACTS_MIN_NEW_WORDS:
                last_facts_at = time.monotonic()
                facts_since_ts = state.duration_seconds()
                if coach_extras.FACT_HINT_RE.search(" ".join(new_lines)):
                    asyncio.get_running_loop().create_task(_run_facts_check(state))
        them_words = sum(len(seg["t"].split()) for seg in state.transcript if seg.get("s") == "o")
        if time.monotonic() - last_mood_at >= 30.0 and them_words - them_words_at_last_mood >= 12:
            last_mood_at, them_words_at_last_mood = time.monotonic(), them_words
            asyncio.get_running_loop().create_task(_run_mood_check(state))
        now_c = time.monotonic()
        if (
            state.brief_points and now_c - last_coverage_at >= 30.0
            and state.user_word_count() - words_at_last_coverage >= 15
        ):
            last_coverage_at, words_at_last_coverage = now_c, state.user_word_count()
            asyncio.get_running_loop().create_task(_run_coverage_check(state))

        m = state.recent_speech_metrics()
        prosody = state.prosody_metrics
        fast_checks = fast_checks + 1 if (m["wpm"] > 180 and m["words"] >= 25) else 0
        voiced = float(prosody.get("voiced_seconds_20s") or 0.0)
        flat_checks = flat_checks + 1 if (voiced >= 8 and float(prosody.get("monotony_score") or 0) >= 75) else 0

        issue = None
        if m["fillers"] >= 3:
            words = ", ".join(f"'{w}'" for w in dict.fromkeys(m["filler_words"]))
            issue = ("filler", f"{m['fillers']} filler words in 30s ({words}).",
                     {"metric": "filler_count_30s", "threshold": ">=3", "measured": m["fillers"]})
        elif fast_checks >= 2:   # sustained for ~6s, not one rushed sentence
            issue = ("pace", f"Speaking at {m['wpm']:.0f} words per minute.",
                     {"metric": "wpm_20s", "threshold": ">180", "measured": m["wpm"]})
        elif flat_checks >= 4:   # ~12s of flat delivery
            issue = ("monotone", "Flat, low-variation tone for the last 20 seconds.",
                     {"metric": "monotony_score", "threshold": ">=75", "measured": prosody.get("monotony_score")})
        if issue:
            issue_type, description, evidence = issue
            evidence["sensor"] = "voice_and_transcript_metrics"
            result = state.flag_issue_fn(issue_type, description, evidence)
            if result.get("recorded"):
                fast_checks = flat_checks = 0
                cue = _MEASURED_CUES.get(issue_type) or _CONTENT_DEFAULT_CUES.get(issue_type, "")
                state._enqueue({"type": "coach_cue", "index": len(state.events) - 1, "text": cue})
                logger.info("meeting_nudge session=%s issue=%s %s", state.session_id, issue_type, description)
                continue

        # Conversation-level check by the live model.
        now = time.monotonic()
        pending = (
            (state.content_check_sent_at and now - state.content_check_sent_at < CONTENT_CHECK_TIMEOUT_S)
            or (state.hint_sent_at and now - state.hint_sent_at < HINT_TIMEOUT_S)
        )
        new_words = state.user_word_count() - words_at_last_check
        if not pending and now - last_check_at >= CONTENT_CHECK_INTERVAL_S and new_words >= CONTENT_CHECK_MIN_NEW_WORDS:
            last_check_at = now
            words_at_last_check = state.user_word_count()
            state.content_check_sent_at = now
            asyncio.get_running_loop().create_task(
                _run_content_check(state, live_request_queue, _content_check_prompt(state))
            )
            logger.info("content_check_sent session=%s new_words=%d", state.session_id, new_words)


def _handle_confirmed_lookaway(
    state: SessionState,
    live_request_queue: LiveRequestQueue,
    confirmed: dict,
) -> None:
    """
    Turn a look-away measured by browser face tracking into a coaching moment.

    The issue is recorded server-side (through flag_issue's gates/cooldowns) rather
    than by asking the live model to call a tool: the native-audio model tends to
    *speak* the tool call as text ("tool_code ...") instead of emitting one. When the
    gates pass, the model is only asked to say a fixed short cue out loud.
    """
    gaze = confirmed["gaze"]
    secs = confirmed["seconds_away"]
    cue = _EYE_CUES.get(gaze, _EYE_CUES["away"])
    description = f"Eye contact lost for {secs}s ({_GAZE_LABELS.get(gaze, gaze)})."
    evidence = {
        "metric": "seconds_away",
        "threshold": f">={confirmed['threshold_s']}s",
        "threshold_s": confirmed["threshold_s"],
        "measured": secs,
        "gaze": gaze,
        "context": state.delivery_context,
        "sensor": "browser_face_tracking",
    }
    result = state.flag_issue_fn("eye_contact", description, evidence) if callable(state.flag_issue_fn) else {}
    if result.get("recorded") and (state.session_kind == "meeting" or state.roleplay_role or not state.live_feedback):
        # In a real call the cue is shown silently in the bubble; no model turn needed.
        state._enqueue({"type": "coach_cue", "index": len(state.events) - 1, "text": cue})
        logger.info("lookaway_cue session=%s cue=%r (meeting, silent)", state.session_id, cue)
    elif result.get("recorded"):
        state._enqueue({"type": "coach_cue", "index": len(state.events) - 1, "text": cue})
        live_request_queue.send_content(types.Content(role="user", parts=[types.Part(text=(
            "[coach system] The presenter lost eye contact. Say exactly this sentence out loud "
            f"and nothing else: \"{cue}\" Do not call any tools. Do not write code."
        ))]))
        logger.info("lookaway_cue session=%s cue=%r", state.session_id, cue)
    else:
        # Cooldown/gate active: keep the measurement as a silent coaching note.
        state.record_event("eye_contact", description, evidence, source="sensor")
        state._enqueue({"type": "coach_cue", "index": len(state.events) - 1, "text": cue, "silent": True})
        logger.info(
            "lookaway_noted_silently session=%s reason=%s",
            state.session_id, result.get("reason", "no_flag_fn"),
        )


async def _upstream(
    websocket: WebSocket,
    live_request_queue: LiveRequestQueue,
    state: SessionState,
):
    """
    Receive binary frames from browser and route media into ADK LiveRequestQueue.

    Frame format: byte[0] = type, bytes[1..] = payload.
    No JSON parsing, no base64 decode on the hot path.
    """
    traffic_limiter = PerSecondTrafficLimiter(
        max_messages_per_sec=WS_MAX_MESSAGES_PER_SECOND,
        max_bytes_per_sec=WS_MAX_BYTES_PER_SECOND,
    )

    try:
        while True:
            frame = await websocket.receive()

            # Binary media frame
            if "bytes" in frame and frame["bytes"]:
                data = frame["bytes"]
                if len(data) > WS_MAX_BINARY_FRAME_BYTES:
                    logger.warning("WS frame too large session=%s bytes=%d", state.session_id, len(data))
                    await websocket.close(code=1009, reason="Binary frame too large")
                    break
                if not traffic_limiter.allow(len(data)):
                    logger.warning("WS traffic rate-limited session=%s", state.session_id)
                    await websocket.close(code=1008, reason="WebSocket rate limit exceeded")
                    break

                msg_type = data[0]
                payload = bytes(data[1:])

                if msg_type == _T_AUDIO_IN:
                    # The dedicated transcriber writes the presenter's words. The coaching
                    # model hears the mic in practice mode; in a meeting it works from the
                    # text transcript (content checks), unless transcription failed.
                    if state.user_stt is not None:
                        state.user_stt.push(payload)
                    if state.session_kind != "meeting" or not state.user_stt_active():
                        live_request_queue.send_realtime(
                            types.Blob(data=payload, mime_type="audio/pcm;rate=16000")
                        )
                    features = _audio_chunk_features(payload)
                    prosody = state.update_prosody(
                        rms=features["rms"],
                        pitch_hz=features["pitch_hz"],
                        speech_active=features["speech"],
                        chunk_duration_s=features["duration_s"] or 0.1,
                    )
                    if prosody.get("voiced_seconds_20s", 0.0) >= 6.0:
                        if prosody.get("pause_ratio_20s", 1.0) < 0.10:
                            state.record_signal(
                                "pause_low",
                                measured=prosody.get("pause_ratio_20s"),
                                threshold="<0.10",
                                source="raw_audio",
                            )
                        if prosody.get("monotony_score", 0.0) >= 70.0:
                            state.record_signal(
                                "monotony_high",
                                measured=prosody.get("monotony_score"),
                                threshold=">=70",
                                source="raw_audio",
                            )
                elif msg_type == _T_OTHER_AUDIO_IN:
                    if state.session_kind != "meeting":
                        continue
                    if state.other_stt is not None:
                        if state.other_stt_task is None:
                            state.other_stt_task = asyncio.get_running_loop().create_task(state.other_stt.run())
                            logger.info("meeting_audio_started session=%s", state.session_id)
                        state.other_stt.push(payload)
                elif msg_type == _T_VIDEO_IN:
                    live_request_queue.send_realtime(
                        types.Blob(data=payload, mime_type="image/jpeg")
                    )
                elif msg_type == _T_SCREEN_IN:
                    if not state.screen_enabled:
                        continue
                    live_request_queue.send_realtime(
                        types.Blob(data=payload, mime_type="image/jpeg")
                    )
                    # Cache for slide before/after visual hint
                    state.last_slide_frame_b64 = base64.b64encode(payload).decode("ascii")
                elif msg_type == _T_SLIDE_IN:
                    # Uploaded slide frames are streamed when the user/agent changes slides.
                    live_request_queue.send_realtime(
                        types.Blob(data=payload, mime_type="image/jpeg")
                    )
                    # Cache as before-frame for visual hint redesign comparison
                    state.last_slide_frame_b64 = base64.b64encode(payload).decode("ascii")
                elif msg_type == _T_STOP:
                    logger.info(f"Stop signal received: {state.session_id}")
                    state.record_timeline("session", "User ended session")
                    break
                else:
                    logger.warning("Unsupported WS binary frame type=%s session=%s", msg_type, state.session_id)
                    await websocket.close(code=1003, reason="Unsupported frame type")
                    break

            # Graceful fallback: text "stop" message
            elif "text" in frame and frame["text"]:
                text = frame["text"]
                frame_limit = (
                    _BRIEF_MAX_FRAME_BYTES if text.startswith('{"type":"brief"') else WS_MAX_TEXT_FRAME_BYTES
                )
                if len(text) > frame_limit:
                    logger.warning("WS text frame too large session=%s bytes=%d", state.session_id, len(text))
                    await websocket.close(code=1009, reason="Text frame too large")
                    break
                if not traffic_limiter.allow(len(text)):
                    logger.warning("WS traffic rate-limited session=%s", state.session_id)
                    await websocket.close(code=1008, reason="WebSocket rate limit exceeded")
                    break

                try:
                    payload_json = json.loads(text)
                    msg_type = payload_json.get("type")
                    if msg_type == "stop":
                        break
                    if msg_type == "brief" and state.session_kind == "meeting":
                        _apply_brief(state, payload_json)
                        continue
                    if msg_type == "script":
                        _load_script(state, payload_json)
                        continue
                    if msg_type == "script_lost":
                        asyncio.get_running_loop().create_task(_script_lost(state))
                        continue
                    if msg_type == "script_goto":
                        _script_goto(state, payload_json.get("index"))
                        continue
                    if msg_type == "help" and state.session_kind == "meeting":
                        qi = payload_json.get("question_index")
                        _request_help(state, str(payload_json.get("kind") or ""), qi if isinstance(qi, int) else None)
                        continue
                    if msg_type == "nudge_feedback":
                        _rate_nudge(state, payload_json.get("index"), payload_json.get("useful"))
                        continue
                    if msg_type == "interruption" and state.session_kind == "meeting":
                        _record_interruption(state, payload_json.get("overlap_s"))
                    if msg_type == "talk_time" and state.session_kind == "meeting":
                        _record_talk_time(state, payload_json)
                    if msg_type == "pitch" and state.session_kind == "practice":
                        _apply_pitch(state, payload_json)
                    if msg_type == "say_better" and state.session_kind == "practice":
                        asyncio.get_running_loop().create_task(_run_say_better(state))
                        continue
                    if msg_type == "gaze":
                        confirmed = state.update_browser_gaze(str(payload_json.get("state") or ""))
                        if confirmed:
                            logger.info(
                                "browser_gaze_confirmed session=%s gaze=%s seconds=%.1f streaks=%d",
                                state.session_id, confirmed["gaze"], confirmed["seconds_away"],
                                state.browser_gaze_streaks,
                            )
                            _handle_confirmed_lookaway(state, live_request_queue, confirmed)
                        continue
                    if msg_type == "slide_index":
                        total = int(payload_json.get("total_slides") or 0)
                        idx = int(payload_json.get("current_slide_index") or 0)
                        if total > 0:
                            state.total_slides = total
                            state.current_slide_index = max(0, min(total - 1, idx))
                except Exception:
                    pass

            elif frame.get("type") == "websocket.disconnect":
                break

    finally:
        for stt in (state.user_stt, state.other_stt):
            if stt is not None:
                stt.close()
        live_request_queue.close()


async def _demo_seed_scheduler(websocket: WebSocket, state: SessionState):
    """
    Demo-mode reliability helper.
    Ensures three visible checkpoints appear in the first ~90 seconds even when
    the model chooses not to interrupt on its own.
    """
    if not state.demo_mode:
        return

    start = time.monotonic()
    checkpoints = [
        (
            30.0,
            "pace_flag",
            lambda: state.pace_violations > 0,
            "Demo checkpoint: trigger pace coaching with one 15s fast segment.",
        ),
        (
            60.0,
            "slide_flag",
            lambda: (state.visual_flags + state.mismatch_flags) > 0,
            "Demo checkpoint: move to a dense slide so slide critique triggers.",
        ),
        (
            85.0,
            "visual_hint",
            lambda: state.live_visual_hints_ready > 0,
            "Demo checkpoint: ask for an improved slide visual hint now.",
        ),
    ]

    for target_s, seed, completed, message in checkpoints:
        wait_s = target_s - (time.monotonic() - start)
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        if not state.is_active:
            return
        if completed():
            continue

        payload = {"seed": seed, "message": message}
        state.record_timeline("demo_seed", message, payload)
        await _safe_send(websocket, {"type": "demo_seed", **payload})
        await _safe_send(
            websocket,
            {
                "type": "telemetry",
                "phase": "demo_checkpoint",
                "detail": seed,
                "data": payload,
            },
        )


async def _downstream(
    websocket: WebSocket,
    runner: Runner,
    user_id: str,
    session_id: str,
    live_request_queue: LiveRequestQueue,
    run_config: RunConfig,
    state: SessionState,
):
    """
    Consume ADK run_live() events.
    Coach audio → binary frames (no base64).
    Transcripts → JSON text frames (low frequency).
    Status → JSON text, emitted only on state transitions.

    Speech cooldown: native audio models can generate speech without first calling
    flag_issue (the audio is streamed as a side-effect of model generation).  We
    enforce COACH_SPEECH_COOLDOWN_S at the server level — suppressing both audio
    bytes and the coach transcript for any turn that starts within the cooldown
    window.  This is the hard gate; the tool-level cooldown in tools.py is a
    softer hint to the model.
    """
    current_status: str | None = None

    async def _emit_status(new_state: str):
        nonlocal current_status
        if new_state != current_status:
            current_status = new_state
            await websocket.send_json({"type": "status", "state": new_state})
            state.record_timeline("status", f"Status: {new_state}")
            await _safe_send(
                websocket,
                {
                    "type": "telemetry",
                    "phase": "status",
                    "detail": new_state,
                    "data": {"session_id": state.session_id},
                },
            )

    await _emit_status("listening")

    # Per-turn gate: set at the first audio chunk of each coach turn.
    _suppress_turn: bool = False      # True → drop audio + transcript for this turn
    _turn_committed: bool = False     # True → we already decided for this turn
    _in_audio_stream: bool = False    # True → currently receiving audio for an utterance
    _turn_index: int = 1
    _turn_text: str = ""              # Coach transcription accumulated for the current turn
    _pending_coach_chunks: list[str] = []  # coach text that arrived before the turn's gate decision
    _user_chunk_seen: bool = False    # partial user transcription seen since the last "finished"
    _coach_chunk_seen: bool = False   # partial coach transcription seen since the last "finished"
    _meeting_reply: str = ""          # meeting mode: model text for the pending content check
    # Transcript of the presenter from the coach model's own input transcription —
    # only used when the dedicated transcriber is off or failed.
    _heard = "user"

    async def _emit_coach_chunk(chunk: str) -> None:
        """Store + forward an allowed coach transcription chunk and attach it to the latest coaching event."""
        if state.roleplay_role:
            # The role-play partner is "the other side" — stored like a meeting's Them lines so
            # the question-by-question review pairs its questions with the presenter's answers.
            state.add_transcript("other", chunk, raw=True)
            await websocket.send_json({"type": "transcript", "speaker": "partner", "text": chunk, "raw": True})
            return
        state.add_transcript("coach", chunk, raw=True)
        await websocket.send_json({"type": "transcript", "speaker": "coach", "text": chunk, "raw": True})
        said = state.attach_coach_said(chunk)
        if said:
            await websocket.send_json({"type": "coach_said", **said})

    # Reply-level observability for docker logs.
    _reply_index: int = 0
    _reply_started_at: float = 0.0
    _reply_chunks: int = 0
    _reply_bytes: int = 0
    _reply_action: str = "unknown"  # allowed | suppressed
    _reply_reason: str = "unknown"
    _reply_preview_logged: bool = False

    def _start_reply(action: str, reason: str) -> None:
        nonlocal _reply_index, _reply_started_at, _reply_chunks, _reply_bytes
        nonlocal _reply_action, _reply_reason, _reply_preview_logged
        _reply_index += 1
        _reply_started_at = time.monotonic()
        _reply_chunks = 0
        _reply_bytes = 0
        _reply_action = action
        _reply_reason = reason
        _reply_preview_logged = False
        logger.info(
            "coach_reply_start session=%s turn=%d reply=%d action=%s reason=%s",
            state.session_id,
            _turn_index,
            _reply_index,
            _reply_action,
            _reply_reason,
        )
        state._enqueue(
            {
                "type": "telemetry",
                "phase": "coach_reply_start",
                "detail": reason,
                "data": {
                    "turn": _turn_index,
                    "reply": _reply_index,
                    "action": action,
                },
            }
        )

    def _end_reply(trigger: str) -> None:
        nonlocal _reply_started_at, _reply_chunks, _reply_bytes
        nonlocal _reply_action, _reply_reason
        if _reply_started_at <= 0:
            return
        duration_ms = int((time.monotonic() - _reply_started_at) * 1000)
        logger.info(
            "coach_reply_end session=%s turn=%d reply=%d action=%s reason=%s trigger=%s chunks=%d bytes=%d duration_ms=%d",
            state.session_id,
            _turn_index,
            _reply_index,
            _reply_action,
            _reply_reason,
            trigger,
            _reply_chunks,
            _reply_bytes,
            duration_ms,
        )
        state._enqueue(
            {
                "type": "telemetry",
                "phase": "coach_reply_end",
                "detail": trigger,
                "data": {
                    "turn": _turn_index,
                    "reply": _reply_index,
                    "action": _reply_action,
                    "chunks": _reply_chunks,
                    "bytes": _reply_bytes,
                    "duration_ms": duration_ms,
                },
            }
        )
        _reply_started_at = 0.0
        _reply_chunks = 0
        _reply_bytes = 0
        _reply_action = "unknown"
        _reply_reason = "unknown"

    try:
        await _safe_send(
            websocket,
            {
                "type": "telemetry",
                "phase": "model_connect",
                "detail": "run_live_started",
                "data": {"model": str(getattr(getattr(runner, "agent", None), "model", "unknown"))},
            },
        )
        async for event in runner.run_live(
            user_id=user_id,
            session_id=session_id,
            live_request_queue=live_request_queue,
            run_config=run_config,
        ):
            if not state.is_active:
                break

            # ── Coach audio ────────────────────────────────────────────
            if event.content and event.content.parts:
                has_audio = any(
                    p.inline_data and p.inline_data.data for p in event.content.parts
                )

                if has_audio:
                    if not _in_audio_stream:
                        # Start of a new speech segment within this turn.
                        if not _turn_committed:
                            # First speech in this turn — evaluate cooldown.
                            now = time.monotonic()
                            elapsed = now - state.last_coach_speech_time
                            issue_age = (
                                now - state.last_confirmed_issue_time
                                if state.last_confirmed_issue_time > 0
                                else 9e9
                            )
                            has_recent_confirmed_issue = (
                                state.last_confirmed_issue_time > 0
                                and issue_age <= CONFIRMED_ISSUE_WINDOW_S
                            )
                            if not state.roleplay_role and not state.live_feedback:
                                _suppress_turn = True      # practice set to "feedback only at the end"
                                _turn_committed = True
                                _start_reply("suppressed", "feedback_at_end")
                            elif state.roleplay_role or (elapsed >= COACH_SPEECH_COOLDOWN_S and has_recent_confirmed_issue):
                                _suppress_turn = False
                                _turn_committed = True
                                state.last_coach_speech_time = now
                                _start_reply(
                                    "allowed",
                                    f"cooldown_pass_issue={state.last_confirmed_issue_type or 'unknown'}",
                                )
                                if _pending_coach_chunks and not looks_like_tool_text("".join(_pending_coach_chunks)):
                                    for _chunk in _pending_coach_chunks:
                                        await _emit_coach_chunk(_chunk)
                                _pending_coach_chunks.clear()
                                logger.debug(
                                    "Coach speech allowed (%.1fs since last)", elapsed
                                )
                            elif elapsed < COACH_SPEECH_COOLDOWN_S:
                                _suppress_turn = True
                                _turn_committed = True
                                _start_reply(
                                    "suppressed",
                                    f"cooldown_active_{int(max(0.0, COACH_SPEECH_COOLDOWN_S - elapsed))}s",
                                )
                                logger.info(
                                    "Coach speech suppressed — cooldown %.0fs remaining (session=%s)",
                                    COACH_SPEECH_COOLDOWN_S - elapsed,
                                    state.session_id,
                                )
                            else:
                                _suppress_turn = True
                                _turn_committed = True
                                _start_reply(
                                    "suppressed",
                                    f"no_recent_confirmed_issue_{int(issue_age)}s",
                                )
                                logger.info(
                                    "Coach speech suppressed — no confirmed issue in window (age=%.1fs, session=%s)",
                                    issue_age,
                                    state.session_id,
                                )
                        elif state.roleplay_role:
                            pass    # role-play partner: a turn may have several speech segments
                        else:
                            # Second+ speech segment in the same turn — always suppress.
                            # This prevents duplicate coach feedback when the model speaks
                            # both before and after a tool call (e.g. flag_issue → draw_overlay).
                            _suppress_turn = True
                            _start_reply("suppressed", "duplicate_segment_same_turn")
                            logger.info(
                                "Coach duplicate speech suppressed in same turn (session=%s)",
                                state.session_id,
                            )
                    _in_audio_stream = True

                    if not _suppress_turn:
                        for part in event.content.parts:
                            if part.inline_data and part.inline_data.data:
                                _reply_chunks += 1
                                _reply_bytes += len(part.inline_data.data)
                                # Meeting mode: the coach must never be heard in the call —
                                # its words reach the presenter as text only.
                                if state.session_kind != "meeting":
                                    frame = bytes([_T_AUDIO_OUT]) + part.inline_data.data
                                    await websocket.send_bytes(frame)
                        await _emit_status("coaching")
                else:
                    # Non-audio content event (e.g. function_call) — marks a gap
                    # between utterances so the next audio is treated as a new segment.
                    if _in_audio_stream:
                        _end_reply("non_audio_gap")
                    _in_audio_stream = False

            # ── User speech transcription ──────────────────────────────
            # ADK streams sub-word chunks (finished=False, partial=True) and then repeats
            # the whole utterance once more with finished=True. Only the chunks are kept
            # (concatenated as-is, they carry their own spacing); the finished copy is a
            # duplicate and used only as an utterance boundary.
            if event.input_transcription and event.input_transcription.text and not state.user_stt_active():
                if getattr(event.input_transcription, "finished", False):
                    if not _user_chunk_seen:
                        text = _sanitize_transcript_text(event.input_transcription.text)
                        if text:
                            state.add_transcript(_heard, text)
                            await websocket.send_json({"type": "transcript", "speaker": _heard, "text": text})
                    _user_chunk_seen = False
                    await websocket.send_json({"type": "transcript_end", "speaker": _heard})
                else:
                    chunk = _sanitize_transcript_chunk(event.input_transcription.text)
                    if chunk:
                        _user_chunk_seen = True
                        state.add_transcript(_heard, chunk, raw=True)
                        await websocket.send_json({"type": "transcript", "speaker": _heard, "text": chunk, "raw": True})

            # ── Coach speech transcription ─────────────────────────────
            # Only forwarded/stored when the turn's audio was allowed through the gate.
            if event.output_transcription and event.output_transcription.text:
                finished = getattr(event.output_transcription, "finished", False)
                if finished and _coach_chunk_seen:
                    chunk = ""          # duplicate of the chunks already handled
                elif finished:
                    chunk = " " + _sanitize_transcript_text(event.output_transcription.text) + " "
                else:
                    chunk = _sanitize_transcript_chunk(event.output_transcription.text)
                    _coach_chunk_seen = True
                if finished:
                    _coach_chunk_seen = False

                if state.session_kind == "meeting":
                    # Never voiced or shown as-is in a meeting; collected only as the
                    # answer to a content check, parsed at turn end.
                    _meeting_reply += chunk
                    chunk = ""

                if chunk.strip():
                    _turn_text += chunk
                    if not _suppress_turn and looks_like_tool_text(_turn_text):
                        # The model verbalised a tool call instead of making one.
                        _suppress_turn = True
                        _turn_committed = True
                        logger.info(
                            "coach_tool_text_suppressed session=%s turn=%d preview=%r",
                            state.session_id, _turn_index, _turn_text.strip()[:120],
                        )
                    # Hard word-count gate — enforce the short-correction rule even when
                    # the model ignores it.
                    word_cap = ROLEPLAY_MAX_WORDS_PER_TURN if state.roleplay_role else MAX_COACH_WORDS_PER_TURN
                    if not _suppress_turn and len(_turn_text.split()) > word_cap:
                        _suppress_turn = True
                        logger.info(
                            "coach_speech_too_long session=%s words=%d limit=%d — suppressing remainder",
                            state.session_id, len(_turn_text.split()), word_cap,
                        )

                    if _suppress_turn:
                        # Suppressed speech never reached the presenter, so it is not
                        # added to the transcript (or the post-session report input).
                        if not _reply_preview_logged:
                            logger.info(
                                "coach_reply_text_suppressed session=%s turn=%d reply=%d preview=%r",
                                state.session_id, _turn_index, _reply_index, _turn_text.strip()[:120],
                            )
                            _reply_preview_logged = True
                    elif not _turn_committed:
                        # Text can arrive before the first audio chunk decides the gate.
                        _pending_coach_chunks.append(chunk)
                    else:
                        if not _reply_preview_logged:
                            logger.info(
                                "coach_reply_text session=%s turn=%d reply=%d preview=%r",
                                state.session_id, _turn_index, _reply_index, _turn_text.strip()[:160],
                            )
                            _reply_preview_logged = True
                        await _emit_coach_chunk(chunk)

            # ── Turn complete — reset per-turn gate ────────────────────
            if event.turn_complete:
                if _in_audio_stream:
                    _end_reply("turn_complete")
                _suppress_turn = False
                _turn_committed = False
                _in_audio_stream = False
                _turn_text = ""
                _pending_coach_chunks.clear()
                if _meeting_reply.strip():
                    _handle_meeting_reply(state, _meeting_reply)
                _meeting_reply = ""
                logger.info("coach_turn_complete session=%s turn=%d", state.session_id, _turn_index)
                _turn_index += 1
                await _emit_status("listening")

    except Exception as exc:
        if _is_normal_live_close(exc):
            logger.info("Live session closed normally (session=%s)", state.session_id)
            state.is_active = False
            return

        # Quota exhaustion, context overflow, and transient API errors all surface
        # here as generic exceptions from the run_live generator.  Rather than
        # silently dying we surface a status message so the browser shows an
        # actionable error instead of hanging on "Listening".
        err_str = str(exc)
        if any(kw in err_str.lower() for kw in ("quota", "resource_exhausted", "context", "token")):
            logger.warning("Live session quota/context error (session=%s): %s", state.session_id, exc)
            await _safe_send(websocket, {"type": "status", "state": "error", "message": "API quota reached — session ended."})
        else:
            logger.error("Live session error (session=%s): %s", state.session_id, exc, exc_info=True)
            await _safe_send(websocket, {"type": "status", "state": "error", "message": "Session error — please try again."})
        state.is_active = False
    finally:
        # Signal _drain_tool_events to exit so the TaskGroup can complete and
        # post-session analysis can start.  Without this, _drain_tool_events
        # blocks forever on queue.get() — the TaskGroup never finishes — and
        # the scorecard + report are never generated.
        state.stop_drain()


async def _drain_tool_events(websocket: WebSocket, state: SessionState):
    """
    Drain the ws_event_queue populated by ADK tool calls.
    Uses blocking await instead of timeout polling — zero wakeups when queue is idle.
    Exits when it receives the None sentinel (put by state.stop_drain()).
    """
    while True:
        item = await state.ws_event_queue.get()
        if item is None:   # sentinel — session is done
            break
        await websocket.send_json(item)


# ── Post-session analysis pipeline ───────────────────────────────

# Per-model attempt ceiling. Preview models are sometimes overloaded and hang until a
# 504; rather than wait, the agent is retried on the next fallback model.
_POST_AGENT_TIMEOUT_S = max(10, _int_env("POST_AGENT_TIMEOUT_S", 40))
# Tried in order after the agent's own model fails, times out or is overloaded.
_FALLBACK_MODELS = [
    m.strip() for m in os.getenv(
        "PITCHMIRROR_FALLBACK_MODELS", "gemini-3.5-flash,gemini-2.5-flash,gemini-3.1-flash-lite"
    ).split(",") if m.strip()
]

class _QuotaExhausted(Exception):
    """Raised by _run_single_agent(raise_quota=True) on a 429 RESOURCE_EXHAUSTED."""


async def _run_single_agent(agent, message: str, *, raise_quota: bool = False) -> str:
    """
    Run one post-session LlmAgent in an isolated session and return its text output.

    Each call creates a fresh InMemorySessionService + Runner so concurrent
    agent runs (via asyncio.gather) don't share session state.
    Each model attempt has a hard timeout; on timeout, overload (503/504) or quota
    errors the same agent is re-run on the next model in _FALLBACK_MODELS, so one
    slow preview model can't stall the whole report.
    """
    async def _run(run_agent) -> str:
        service = InMemorySessionService()
        runner = Runner(app_name=POST_APP_NAME, agent=run_agent, session_service=service)
        sid = uuid.uuid4().hex
        await service.create_session(app_name=POST_APP_NAME, user_id="presenter", session_id=sid)
        parts: list[str] = []
        async for event in runner.run_async(
            user_id="presenter",
            session_id=sid,
            new_message=types.Content(role="user", parts=[types.Part(text=message)]),
        ):
            if event.is_final_response() and event.content:
                for part in event.content.parts:
                    if part.text:
                        parts.append(part.text)
        return parts[-1] if parts else ""

    if claude_client.claude_enabled():
        started = time.monotonic()
        try:
            text = await claude_client.report_text(str(agent.instruction), message)
            logger.info("Post-session agent %s done on Claude in %.1fs", agent.name, time.monotonic() - started)
            return text
        except Exception as exc:
            logger.warning(
                "Post-session agent %s failed on Claude after %.1fs (%s) — falling back to Gemini",
                agent.name, time.monotonic() - started, str(exc)[:160],
            )

    models = [agent.model] + [m for m in _FALLBACK_MODELS if m != agent.model]
    quota_hits = 0
    for attempt, model in enumerate(models):
        run_agent = agent if attempt == 0 else agent.clone(update={"model": model})
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(_run(run_agent), timeout=_POST_AGENT_TIMEOUT_S)
            if attempt:
                logger.info("Post-session agent %s succeeded on fallback model %s", agent.name, model)
            if result.strip() or attempt == len(models) - 1:
                return result
            logger.warning("Post-session agent %s returned nothing on %s — trying next model", agent.name, model)
        except asyncio.TimeoutError:
            logger.warning(
                "Post-session agent %s timed out on %s after %ds — trying next model",
                agent.name, model, _POST_AGENT_TIMEOUT_S,
            )
        except Exception as exc:
            if "RESOURCE_EXHAUSTED" in str(exc):
                quota_hits += 1
            logger.warning(
                "Post-session agent %s failed on %s after %.1fs: %s",
                agent.name, model, time.monotonic() - started, str(exc)[:160],
            )
    if raise_quota and quota_hits == len(models):
        raise _QuotaExhausted(f"all models exhausted for {agent.name}")
    return ""


async def _run_with_validation(
    agent,
    message: str,
    *,
    min_chars: int = 100,
    repair_suffix: str = "",
) -> str:
    """
    Run an agent and validate the output meets minimum quality requirements.
    If output is empty or too short, retry once with a repair prompt.
    """
    try:
        result = await _run_single_agent(agent, message, raise_quota=True)
    except _QuotaExhausted:
        # Retrying immediately only burns more quota on the same rate limit.
        logger.warning("Agent %s hit API quota — skipping repair retry", agent.name)
        return ""
    if len((result or "").strip()) >= min_chars:
        return result
    if not (result or "").strip():
        # Every model already failed or timed out — a repair prompt won't help.
        return ""

    logger.warning(
        "Agent %s returned insufficient output (%d chars < %d min) — retrying with repair prompt",
        agent.name, len((result or "").strip()), min_chars,
    )
    repair_message = (
        f"{message}\n\n"
        "REPAIR: Your previous response was incomplete or empty. "
        "Provide a thorough, properly formatted response. Do not skip or truncate any sections."
        + (f"\n{repair_suffix}" if repair_suffix else "")
    )
    return await _run_single_agent(agent, repair_message)


def _parse_recap(raw: str) -> dict:
    """Parse the recap agent's JSON (tolerating fences); keep only known, well-typed fields."""
    text = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        logger.warning("Recap JSON could not be parsed")
        return {}

    def str_list(key: str) -> list[str]:
        return [str(x).strip() for x in data.get(key) or [] if str(x).strip()][:15]

    actions = []
    for item in data.get("action_items") or []:
        if isinstance(item, dict) and str(item.get("task") or "").strip():
            actions.append({
                "owner": str(item.get("owner") or "").strip(),
                "task": str(item["task"]).strip(),
                "due": str(item.get("due") or "").strip(),
            })
    email = data.get("follow_up_email") if isinstance(data.get("follow_up_email"), dict) else {}
    return {
        "summary": str(data.get("summary") or "").strip(),
        "decisions": str_list("decisions"),
        "action_items": actions[:15],
        "open_questions": str_list("open_questions"),
        "client_signals": str_list("client_signals"),
        "follow_up_email": {
            "subject": str(email.get("subject") or "").strip(),
            "body": str(email.get("body") or "").strip(),
        },
    }


def _merge_qa_review(pairs: list[dict], raw: str, hints: list[dict]) -> list[dict]:
    """Combine Q/A pairs with the reviewer's JSON (tolerating fences/garbage) and live hints."""
    reviews: dict[int, dict] = {}
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            for item in json.loads(text[start:end + 1]):
                if isinstance(item, dict) and isinstance(item.get("index"), int):
                    reviews[item["index"]] = item
        except (ValueError, TypeError):
            logger.warning("Q&A review JSON could not be parsed")
    merged = []
    for i, p in enumerate(pairs):
        r = reviews.get(i, {})
        try:
            rating = max(1, min(5, int(r.get("rating")))) if r.get("rating") is not None else None
        except (TypeError, ValueError):
            rating = None
        hint = next((h for h in hints if h.get("question") and h["question"][:60] in p["question"]), None)
        merged.append({
            "question": p["question"],
            "answer": p["answer"],
            "ts": p.get("ts", 0),
            "rating": rating,
            "verdict": str(r.get("verdict") or ""),
            "worked": str(r.get("worked") or ""),
            "improve": str(r.get("improve") or ""),
            "better_answer": str(r.get("better_answer") or ""),
            "hint_points": hint["points"] if hint else [],
        })
    return merged


async def _run_post_session(websocket: WebSocket, state: SessionState):
    if state.user_word_count() < MIN_SCORABLE_WORDS:
        logger.info(
            "Too little presenter speech for %s (%d words) — skipping analysis",
            state.session_id, state.user_word_count(),
        )
        state.research_tips = ""
        state.generated_assets = []
        return

    await _safe_send(websocket, {"type": "status", "state": "analyzing"})
    await _safe_send(
        websocket,
        {"type": "telemetry", "phase": "post_session", "detail": "pipeline_start", "data": {}},
    )
    state.record_timeline("pipeline", "Post-session pipeline started")
    context = _build_context_message(state)

    # ── Parallel phase: three agents run concurrently ─────────────
    # Each emits a pipeline_step event the moment it finishes so the browser
    # can mark that step done in real time rather than using fake timers.

    async def run_delivery() -> str:
        await _safe_send(
            websocket,
            {"type": "telemetry", "phase": "agent_step", "detail": "delivery_start", "data": {"agent": "delivery_analyst"}},
        )
        # min_chars=100 ensures the agent didn't return a fragment; retry once if so
        result = await _run_with_validation(DELIVERY_ANALYST, context, min_chars=100)
        await _safe_send(websocket, {"type": "pipeline_step", "step": "delivery_done"})
        await _safe_send(
            websocket,
            {"type": "telemetry", "phase": "agent_step", "detail": "delivery_done", "data": {"agent": "delivery_analyst"}},
        )
        logger.info("Post-session delivery analysis done: %s", state.session_id)
        return result

    async def run_content() -> str:
        await _safe_send(
            websocket,
            {"type": "telemetry", "phase": "agent_step", "detail": "content_start", "data": {"agent": "content_analyst"}},
        )
        result = await _run_with_validation(CONTENT_ANALYST, context, min_chars=100)
        await _safe_send(websocket, {"type": "pipeline_step", "step": "content_done"})
        await _safe_send(
            websocket,
            {"type": "telemetry", "phase": "agent_step", "detail": "content_done", "data": {"agent": "content_analyst"}},
        )
        logger.info("Post-session content analysis done: %s", state.session_id)
        return result

    async def run_research() -> str:
        await _safe_send(
            websocket,
            {"type": "telemetry", "phase": "agent_step", "detail": "research_start", "data": {"agent": "research_agent"}},
        )
        result = await _run_with_validation(RESEARCH_AGENT, context, min_chars=60)
        await _safe_send(websocket, {"type": "pipeline_step", "step": "research_done"})
        await _safe_send(
            websocket,
            {"type": "telemetry", "phase": "agent_step", "detail": "research_done", "data": {"agent": "research_agent"}},
        )
        logger.info("Post-session research done: %s", state.session_id)
        return result

    async def run_qa() -> None:
        pairs = extract_qa_pairs(state.transcript)
        if not pairs:
            return
        listing = "\n\n".join(
            f"PAIR {i}\nQUESTION: {p['question']}\nANSWER: {p['answer']}" for i, p in enumerate(pairs)
        )
        raw = await _run_single_agent(
            QA_REVIEW_AGENT,
            f"MEETING TYPE: {state.coach_mode}\n\n{listing}",
        )
        state.qa_review = _merge_qa_review(pairs, raw, state.answer_hints)
        await _safe_send(websocket, {"type": "pipeline_step", "step": "qa_done"})
        logger.info("Post-session Q&A review done: %s (%d questions)", state.session_id, len(state.qa_review))

    async def run_recap() -> None:
        if state.session_kind != "meeting":
            return
        # Final coverage pass: points said in the last seconds missed the 30 s live check.
        if any(not p["covered"] for p in state.brief_points):
            await _run_coverage_check(state)
        brief = _brief_block(state, max_chars=3000)
        talk = state.talk_stats
        raw = await _run_single_agent(
            RECAP_AGENT,
            f"MEETING TYPE: {state.coach_mode}\n"
            + (f"PRE-CALL BRIEF:\n{brief}\n\n" if brief else "")
            + (f"COMMITMENTS NOTED LIVE:\n{coach_extras.commitments_text(state.commitments)}\n\n" if state.commitments else "")
            + (f"FACTS NOTED LIVE (use the names and numbers in the email):\n{coach_extras.facts_text(state.facts)}\n\n"
               if state.facts else "")
            + (f"TALK TIME: presenter {round(talk['you_share'] * 100)}% of the conversation\n\n"
               if talk.get("you_share") is not None else "")
            + f"TRANSCRIPT:\n{state.transcript_text()}",
        )
        state.recap = _parse_recap(raw)
        await _safe_send(websocket, {"type": "pipeline_step", "step": "recap_done"})
        logger.info("Post-session recap done: %s (%s)", state.session_id, "ok" if state.recap else "empty")

    async def run_rewrite() -> None:
        if state.session_kind == "meeting" or state.user_word_count() < 40:
            return
        raw = await _run_single_agent(
            REWRITE_AGENT,
            f"SESSION TYPE: {state.coach_mode}\nPRESENTER LINES:\n{coach_extras.rewrite_input(state.transcript)}",
        )
        state.rewrites = coach_extras.parse_rewrites(raw)
        await _safe_send(websocket, {"type": "pipeline_step", "step": "rewrite_done"})
        logger.info("Post-session rewrites done: %s (%d)", state.session_id, len(state.rewrites))

    delivery_out, content_out, research_out, _, _, _, _ = await asyncio.gather(
        run_delivery(), run_content(), run_research(), run_qa(), run_recap(), run_rewrite(),
        _score_pitch_version(state),
    )

    # Surface degraded-quality risk when agents silently returned empty strings.
    empty_agents = [
        name for name, out in (
            ("delivery", delivery_out),
            ("content", content_out),
            ("research", research_out),
        ) if not out
    ]
    if empty_agents:
        logger.warning(
            "Post-session agents returned empty output for %s: %s — report quality may be degraded",
            state.session_id, ", ".join(empty_agents),
        )

    # ── Synthesis phase: combine all three outputs ─────────────────
    synthesis_context = (
        f"{context}\n\n"
        f"DELIVERY ANALYSIS:\n{delivery_out or '(unavailable)'}\n\n"
        f"CONTENT ANALYSIS:\n{content_out or '(unavailable)'}\n\n"
        f"RESEARCH TIPS:\n{research_out or '(unavailable)'}"
    )
    final_report_raw = await _run_single_agent(SYNTHESIS_AGENT, synthesis_context)

    # Validate synthesis has all required section headers; retry once with
    # explicit repair prompt listing exactly which sections are missing.
    if final_report_raw and not _validate_synthesis(final_report_raw):
        missing = _missing_synthesis_sections(final_report_raw)
        logger.warning(
            "Synthesis missing sections for %s: %s — retrying",
            state.session_id, missing,
        )
        repair_suffix = (
            "MISSING SECTIONS (you must include all of them):\n"
            + "\n".join(f"  {s}" for s in missing)
        )
        repair_message = (
            f"{synthesis_context}\n\n"
            "REPAIR: Your previous response was missing required section headers. "
            "Rewrite the complete report including EVERY required section.\n"
            + repair_suffix
        )
        final_report_raw = await _run_single_agent(SYNTHESIS_AGENT, repair_message)

    if not final_report_raw:
        logger.error(
            "Post-session synthesis returned empty output for %s — using fallback report",
            state.session_id,
        )
        final_report_raw = (
            "**Analysis incomplete.** The AI synthesis step did not produce a report for this session. "
            "Check server logs for agent timeout or API quota errors."
        )
    final_report, image_prompts = extract_image_prompts(final_report_raw)
    state.ai_scores = _extract_ai_scores(final_report_raw)
    if len(state.ai_scores) == 5:
        logger.info("AI scores for %s: %s", state.session_id, state.ai_scores)
    else:
        logger.warning(
            "Incomplete AI scores for %s — got %d/5 (filler/pace/eye/clarity/visual); "
            "falling back to metric-based scoring for missing dimensions",
            state.session_id, len(state.ai_scores),
        )
    await _safe_send(websocket, {"type": "pipeline_step", "step": "synthesis_done"})
    await _safe_send(
        websocket,
        {"type": "telemetry", "phase": "agent_step", "detail": "synthesis_done", "data": {"agent": "synthesis_agent"}},
    )
    logger.info("Post-session synthesis done: %s", state.session_id)

    # ── Session summary agent: cross-session user skill profile ───────
    # Runs after the scorecard has been sent (see websocket_endpoint): the presenter
    # shouldn't wait for memory bookkeeping to see their review.
    async def update_profile() -> None:
        try:
            profile_context = (
                f"{synthesis_context}\n\n"
                f"SYNTHESIS REPORT:\n{final_report_raw or ''}\n\n"
                f"AI SCORES: {state.ai_scores}\n"
            )
            profile_json_str = await _run_single_agent(SESSION_SUMMARY_AGENT, profile_context)
            if profile_json_str:
                raw = re.sub(r"^```(?:json)?|```$", "", profile_json_str.strip(), flags=re.MULTILINE).strip()
                start, end = raw.find("{"), raw.rfind("}")
                profile = json.loads(raw[start:end + 1] if start != -1 else raw)
                scorecard_preview = build_scorecard(state)
                profile["session_score"] = int(scorecard_preview.get("overall_score", 0))
                await _db.save_user_profile(state.user_id, profile)
                logger.info("User profile updated for %s: %s", state.user_id, profile)
        except Exception as e:
            logger.warning("Session profile parse/save failed for %s: %s", state.session_id, e)

    state.pending_profile_update = update_profile
    await _safe_send(websocket, {"type": "pipeline_step", "step": "memory_updated"})

    generated_assets: list[dict] = []
    if ENABLE_IMAGE_GENERATION:
        await _safe_send(websocket, {"type": "pipeline_step", "step": "visuals_start"})
        await _safe_send(
            websocket,
            {"type": "telemetry", "phase": "agent_step", "detail": "visuals_start", "data": {"agent": "imagen_pipeline"}},
        )
        generated_assets = await generate_session_assets(
            state,
            final_report,
            custom_prompts=image_prompts,
            timeout_s=IMAGE_GENERATION_TIMEOUT_S,
            retries=IMAGE_GENERATION_RETRIES,
        )
        await _safe_send(websocket, {"type": "pipeline_step", "step": "visuals_done"})
        await _safe_send(
            websocket,
            {"type": "telemetry", "phase": "agent_step", "detail": "visuals_done", "data": {"agent": "imagen_pipeline"}},
        )

    state.final_report = final_report
    state.research_tips = research_out
    state.generated_assets = generated_assets
    await _safe_send(websocket, {
        "type": "analysis_complete",
        "report": final_report,
        "research_tips": research_out,   # raw output for citation card rendering
        "generated_assets": generated_assets,
        "agents_used": [
            "delivery_analyst",
            "content_analyst",
            "research_agent (google_search)",
            "synthesis_agent",
            "session_summary_agent",
        ],
    })
    await _safe_send(
        websocket,
        {"type": "telemetry", "phase": "post_session", "detail": "pipeline_complete", "data": {}},
    )
    state.record_timeline("pipeline", "Post-session pipeline complete")
    logger.info(f"Post-session analysis complete: {state.session_id}")


def _build_context_message(state: SessionState) -> str:
    return (
        "You are analyzing a completed Pitchlight coaching session.\n\n"
        f"USER ID: {state.user_id}\n"
        f"SESSION MODE: {state.coach_mode}\n"
        + (
            f"SESSION TYPE: LIVE MEETING on {state.platform or 'a video-call platform'} — a real "
            "interview/pitch/call, not a rehearsal. Other participants may appear in the transcript as "
            "'Presenter' (shared microphone); judge only the presenter's delivery. Coach nudges were "
            "silent text, never heard by others.\n"
            if state.session_kind == "meeting" else "SESSION TYPE: practice rehearsal\n"
        ) +
        f"DELIVERY CONTEXT: {state.delivery_context}\n"
        f"PRIMARY GOAL: {state.primary_goal}\n"
        f"SCREEN SHARE ENABLED: {state.screen_enabled}\n"
        f"SLIDES UPLOADED: {state.total_slides}\n"
        f"CURRENT SLIDE INDEX: {state.current_slide_index}\n"
        f"DEMO MODE: {state.demo_mode}\n\n"
        f"SESSION TRANSCRIPT:\n{state.transcript_text()}\n\n"
        f"COACHING EVENTS (tool calls during live session):\n{state.events_json()}\n"
        f"COACH ALERTS SPOKEN ALOUD: {sum(1 for e in state.events if e.coach_said)} of {len(state.events)} events. "
        "Only events whose \"said\" field is set were voiced to the presenter; the rest were recorded "
        "silently (cooldowns). Never claim the coach alerted or told the presenter something unless "
        "\"said\" is set.\n"
        f"LOOK-AWAYS MEASURED BY FACE TRACKING: {state.browser_gaze_streaks}\n\n"
        f"TIMELINE EVENTS (for replay):\n{state.timeline_json()}\n\n"
        f"METRICS: fillers={state.scored_filler_count()} (incl. {state.transcript_stumble_count()} repeated words) eye_drops={state.scored_eye_drops()} "
        f"pace={state.pace_violations} contradictions={state.contradictions} "
        f"clarity={state.clarity_flags} slide_clarity={state.visual_flags} "
        f"slide_mismatch={state.mismatch_flags} duration={round(state.duration_seconds())}s\n"
        f"PROSODY: {state.prosody_metrics}\n\n"
        "Analyze this session and provide coaching as instructed."
    )


async def _safe_send(websocket: WebSocket, data: dict):
    try:
        await websocket.send_json(data)
    except Exception:
        pass

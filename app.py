"""
voice-lab-example — demo app for clone-voice-client's local STT mode.

Proves the "run in app library, not lazy run" loop end-to-end: a mic
recording is transcribed by Whisper running IN THIS PROCESS (via
clone-voice-client's `local` extra), never calling clone-voice-station over
the network — as opposed to the remote mode (VoiceStationClient.transcribe(),
HTTP to a running voice-station). The chat itself is a trimmed clone of
rag-legal-assistant-master's /get contract, backed by a real (if tiny)
retrieval index instead of the full legal RAG stack.

Setup
-----
Requires the `clone-voice-client` repo checked out as a SIBLING directory
(../clone-voice-client) -- requirements.txt installs it via
`-e ../clone-voice-client[local]`, not from PyPI; missing that folder makes
the install below fail outright. Full terminal-command + ngrok walkthrough
for this whole 4-repo system (clone-voice-station, clone-voice-client,
rag-legal-assistant, voice-lab-example) is in
../clone-voice-station/HUONG_DAN_CHAY_TOAN_HE_THONG.md.

    pip install -r requirements.txt          # pulls in clone-voice-client[local]
                                              # (openai-whisper + torch — heavy,
                                              # first install takes a few minutes)
    python app.py                            # http://127.0.0.1:8091

Optional: to see hotword-biased (or Tier 2 LoRA) local transcription, run
clone-voice-station, register/login at /stt-lab, create an adapter, download
its .stt-pack.zip, then upload it at /settings ("Local Whisper pack") --
takes effect on the next /transcribe call, no restart needed. Multiple packs
can be uploaded; at most one is active at a time.

Optional: to point this app's "remote" comparison mode (/compare) at a
clone-voice-station running elsewhere (e.g. exposed via ngrok for a demo),
open /settings and paste its URL -- takes effect immediately, no restart.
"""

import json
import os
import shutil
import uuid

# ffmpeg auto-detect -- must happen before clone_voice_client.local_stt is
# imported, since that module reads CLONE_VOICE_FFMPEG_DIR at its own import
# time. Same fix rag-legal-assistant's voice/station_client.py applies for
# the same reason: the system/conda-forge ffmpeg on this machine crashes
# decoding browser mic audio (STATUS_STACK_BUFFER_OVERRUN), but
# clone-voice-station ships a known-good static ffmpeg.exe at bin/ -- reuse
# that instead of requiring every dev session to set the env var by hand. A
# no-op if the sibling repo isn't checked out at this relative path.
_SIBLING_FFMPEG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "clone-voice-station", "bin")
if os.path.isfile(os.path.join(_SIBLING_FFMPEG_DIR, "ffmpeg.exe")):
    os.environ.setdefault("CLONE_VOICE_FFMPEG_DIR", os.path.abspath(_SIBLING_FFMPEG_DIR))

from clone_voice_client import VoiceStationClient, VoiceStationError
from clone_voice_client import local_stt
from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from engine import retrieval_engine

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Live-adjustable clone-voice-station URL (added 2026-08-12), same idea as
# rag-legal-assistant's voice/station_client.py: for a local demo where
# clone-voice-station is tunnelled through ngrok (a different URL every run),
# hardcoding VOICE_STATION_URL and restarting is slower than pasting the new
# URL into /settings. This file, if present, wins over the env var at
# startup. VoiceStationClient.base_url is a plain mutable attribute read
# fresh on every call, so updating it at runtime (see /settings/station_url
# below) takes effect immediately, no restart needed.
STATION_URL_OVERRIDE_PATH = os.path.join(BASE_DIR, "voice_station_url_override.txt")


def _load_station_url_override():
    if os.path.isfile(STATION_URL_OVERRIDE_PATH):
        with open(STATION_URL_OVERRIDE_PATH, "r", encoding="utf-8") as f:
            url = f.read().strip()
            if url:
                return url
    return None


# API key: prefer voice_station_key.txt (see HUONG_DAN_DEMO.md §5) over
# requiring VOICE_STATION_API_KEY to be exported by hand every session --
# forgetting that step doesn't fail loudly at startup (VoiceStationClient
# happily constructs with api_key=None), it only surfaces later as "Invalid
# or missing X-Api-Key" from clone-voice-station on the first /compare
# request. The env var still wins if both are absent/empty is fine too --
# VoiceStationClient itself falls back to VOICE_STATION_API_KEY when
# api_key=None is passed through.
_KEY_PATH = os.path.join(BASE_DIR, "voice_station_key.txt")
if os.path.isfile(_KEY_PATH):
    voice_client = VoiceStationClient.from_key_file(_KEY_PATH, base_url=_load_station_url_override())
else:
    voice_client = VoiceStationClient(base_url=_load_station_url_override())

# ── Local STT packs (ported from rag-legal-assistant's voice/station_client.py) ─
# Multiple .stt-pack.zip files can be uploaded via /settings; at most one is
# "active" at a time (or none, for plain untrained-Whisper local mode). Disk
# (index.json) is the source of truth for which pack is active -- checked on
# every /transcribe call -- with an in-memory cache purely so the (possibly
# large) LoRA adapter isn't re-extracted/re-loaded from the zip on every
# single request.
STT_PACKS_DIR = os.path.join(BASE_DIR, "stt_pack")
STT_PACKS_INDEX_PATH = os.path.join(STT_PACKS_DIR, "index.json")

_loaded_pack_cache = {}  # pack_id -> local_stt.load_pack() result


def _read_stt_packs_index() -> dict:
    if os.path.isfile(STT_PACKS_INDEX_PATH):
        with open(STT_PACKS_INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"packs": [], "active_id": None}


def _write_stt_packs_index(index: dict):
    os.makedirs(STT_PACKS_DIR, exist_ok=True)
    with open(STT_PACKS_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def list_stt_local_packs() -> dict:
    index = _read_stt_packs_index()
    for p in index["packs"]:
        p["is_warm"] = p["id"] in _loaded_pack_cache
    return index


def upload_stt_local_pack(filename: str, content: bytes) -> dict:
    """Saves an uploaded .stt-pack.zip and validates it can actually be
    loaded (catches a corrupt/wrong zip immediately instead of only failing
    the first time someone tries to transcribe with it)."""
    os.makedirs(STT_PACKS_DIR, exist_ok=True)
    pack_id  = uuid.uuid4().hex[:12]
    zip_path = os.path.join(STT_PACKS_DIR, f"{pack_id}.stt-pack.zip")
    with open(zip_path, "wb") as f:
        f.write(content)

    try:
        pack = local_stt.load_pack(zip_path)
    except Exception as e:
        os.remove(zip_path)
        raise ValueError(f"File pack không hợp lệ: {e}")

    entry = {
        "id": pack_id,
        "label": filename or f"pack-{pack_id}",
        "tier": pack["tier"],
        "base_model": pack["base_model"],
        "zip_path": zip_path,
    }
    index = _read_stt_packs_index()
    index["packs"].append(entry)
    if not index.get("active_id"):
        index["active_id"] = pack_id
    _write_stt_packs_index(index)
    return entry


def delete_stt_local_pack(pack_id: str):
    index = _read_stt_packs_index()
    entry = next((p for p in index["packs"] if p["id"] == pack_id), None)
    if not entry:
        raise ValueError("Không tìm thấy pack.")
    if os.path.isfile(entry["zip_path"]):
        os.remove(entry["zip_path"])
    extracted_dir = f"{entry['zip_path']}_extracted"
    if os.path.isdir(extracted_dir):
        shutil.rmtree(extracted_dir, ignore_errors=True)
    index["packs"] = [p for p in index["packs"] if p["id"] != pack_id]
    if index.get("active_id") == pack_id:
        index["active_id"] = index["packs"][0]["id"] if index["packs"] else None
    _write_stt_packs_index(index)
    _loaded_pack_cache.pop(pack_id, None)


def set_active_stt_local_pack(pack_id):
    index = _read_stt_packs_index()
    if pack_id is not None and not any(p["id"] == pack_id for p in index["packs"]):
        raise ValueError("Không tìm thấy pack.")
    index["active_id"] = pack_id
    _write_stt_packs_index(index)


def _get_active_pack_loaded():
    """Returns the loaded pack dict for transcribe_local(), or None if no
    pack is active (falls through to plain untrained Whisper)."""
    index     = _read_stt_packs_index()
    active_id = index.get("active_id")
    if not active_id:
        return None
    if active_id in _loaded_pack_cache:
        return _loaded_pack_cache[active_id]
    entry = next((p for p in index["packs"] if p["id"] == active_id), None)
    if not entry:
        return None
    pack = local_stt.load_pack(entry["zip_path"])
    _loaded_pack_cache[active_id] = pack
    return pack


_active_on_startup = _get_active_pack_loaded()
if _active_on_startup:
    kind = "Tier 2 LoRA adapter" if _active_on_startup.get("adapter_dir") else "Tier 1 hotwords"
    print(f"[voice-lab-example] Active STT pack on startup: {kind}, "
          f"{len(_active_on_startup['hotwords'])} hotwords (manage at /settings).")
else:
    print("[voice-lab-example] No active STT pack — plain transcription (manage at /settings).")

print("[voice-lab-example] Building retrieval index...")
_pair_count = retrieval_engine.load_index()
print(f"[voice-lab-example] Retrieval index ready ({_pair_count} Q/A pairs).")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/compare", response_class=HTMLResponse)
async def compare(request: Request):
    return templates.TemplateResponse(request, "compare.html", {})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html", {})


@app.get("/settings/station_url")
async def get_station_url_route():
    return {"url": voice_client.base_url, "available": voice_client.is_available()}


@app.post("/settings/station_url")
async def set_station_url_route(request: Request):
    data = await request.json()
    url = (data.get("url") or "").strip().rstrip("/")
    if not url:
        return JSONResponse({"status": "error", "message": "URL không được để trống."}, status_code=400)
    voice_client.base_url = url
    with open(STATION_URL_OVERRIDE_PATH, "w", encoding="utf-8") as f:
        f.write(url)
    return {"url": url, "available": voice_client.is_available()}


# No login/roles in this demo app (unlike rag-legal-assistant, where these
# same routes are admin-only) -- /settings itself is already unauthenticated,
# so there's nothing to gate these behind either.
@app.get("/settings/stt_local_packs")
async def list_stt_local_packs_route():
    return list_stt_local_packs()


@app.post("/settings/stt_local_packs")
async def upload_stt_local_pack_route(pack: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    """Uploads a .stt-pack.zip downloaded from clone-voice-station's STT Lab
    (/stt-lab, "Tải xuống" on a finished adapter) so this app can run it as a
    local Whisper model."""
    content = await pack.read()
    try:
        entry = upload_stt_local_pack(pack.filename, content)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
    # The first pack ever uploaded becomes active automatically (see
    # upload_stt_local_pack) -- pre-warm it too in that case, same as an
    # explicit activate below.
    if _read_stt_packs_index().get("active_id") == entry["id"]:
        background_tasks.add_task(_get_active_pack_loaded)
    return {"status": "ok", "pack": entry}


@app.post("/settings/stt_local_packs/{pack_id}/activate")
async def activate_stt_local_pack_route(pack_id: str, background_tasks: BackgroundTasks):
    try:
        set_active_stt_local_pack(pack_id)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
    # Pre-warm in the background so the FIRST recording after switching packs
    # isn't the one paying for zip-extraction + (Tier 2) loading the base
    # Whisper model + LoRA adapter into memory -- without this, whichever
    # /transcribe call happened to land first ate that cost instead.
    background_tasks.add_task(_get_active_pack_loaded)
    return {"status": "ok"}


@app.delete("/settings/stt_local_packs/{pack_id}")
async def delete_stt_local_pack_route(pack_id: str):
    try:
        delete_stt_local_pack(pack_id)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
    return {"status": "ok"}


@app.post("/get")
async def get_route(request: Request):
    data   = await request.json()
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return {"status": "error", "text": "Vui lòng nhập câu hỏi."}
    return {"status": "success", "text": retrieval_engine.answer(prompt)}


@app.post("/transcribe")
async def transcribe_route(audio: UploadFile = File(...), language: str = Form("vi"),
                            mode: str = Form("local")):
    """mode="local"  — in-process Whisper via clone_voice_client.local_stt (no
                        network call to clone-voice-station; what index.html uses).
       mode="remote" — "lazy" path: ships the recording over HTTP to a running
                        clone-voice-station (VOICE_STATION_URL/VOICE_STATION_API_KEY),
                        which does the transcription. Used by compare.html to show
                        both paths side by side."""
    content = await audio.read()
    try:
        if mode == "remote":
            result = voice_client.transcribe(
                audio.filename, content, mime=audio.content_type, language=language,
            )
        else:
            result = voice_client.transcribe_local(
                audio.filename, content, mime=audio.content_type, language=language,
                pack=_get_active_pack_loaded(),
            )
    except VoiceStationError as e:
        return {"status": "error", "text": e.message}
    except Exception as e:
        # Any other failure (e.g. ffmpeg/decoding crashes inside
        # clone_voice_client.local_stt) must still come back as JSON --
        # letting it propagate hits Starlette's default handler, which
        # returns a *plain-text* 500 body that the frontend's res.json()
        # can't parse ("Unexpected token 'I', "Internal S"... is not valid
        # JSON"), hiding the real error behind a JS parse error instead.
        return JSONResponse({"status": "error", "text": str(e)}, status_code=500)
    return {"status": "success", "text": result["text"]}


if __name__ == "__main__":
    import uvicorn
    # Pass the app object directly, not the "app:app" string form — that form
    # makes uvicorn re-import this module by name even though it's already
    # running as __main__, which double-runs the startup code above (dataset
    # download, index build, pack loading). The string form only earns its
    # keep when reload=True needs a re-importable reference for the reloader
    # subprocess; with reload=False there's no reason to pay for it.
    uvicorn.run(app, host="127.0.0.1", port=8091)

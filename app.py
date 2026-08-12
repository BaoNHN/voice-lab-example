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
../rag-legal-assistant/HUONG_DAN_CHAY_TOAN_HE_THONG.md.

    pip install -r requirements.txt          # pulls in clone-voice-client[local]
                                              # (openai-whisper + torch — heavy,
                                              # first install takes a few minutes)
    python app.py                            # http://127.0.0.1:8091

Optional: to see hotword-biased local transcription, run clone-voice-station,
register/login at /stt-lab, create an adapter with some hotwords, download
its .stt-pack.zip, and drop it into this repo's stt_pack/ folder before
starting this app.

Optional: to point this app's "remote" comparison mode (/compare) at a
clone-voice-station running elsewhere (e.g. exposed via ngrok for a demo),
open /settings and paste its URL -- takes effect immediately, no restart.
"""

import glob
import os

from clone_voice_client import VoiceStationClient, VoiceStationError
from clone_voice_client import local_stt
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from engine import retrieval_engine

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
STT_PACK_DIR = os.path.join(BASE_DIR, "stt_pack")

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


voice_client = VoiceStationClient(base_url=_load_station_url_override())

ACTIVE_PACK = None
_packs = glob.glob(os.path.join(STT_PACK_DIR, "*.zip"))
if _packs:
    ACTIVE_PACK = local_stt.load_pack(_packs[0])
    kind = "Tier 2 LoRA adapter" if ACTIVE_PACK.get("adapter_dir") else "Tier 1 hotwords"
    print(f"[voice-lab-example] Loaded STT Lab pack: {os.path.basename(_packs[0])} "
          f"({kind}, {len(ACTIVE_PACK['hotwords'])} hotwords)")
else:
    print("[voice-lab-example] No .stt-pack.zip found in stt_pack/ — plain transcription.")

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
                pack=ACTIVE_PACK,
            )
    except VoiceStationError as e:
        return {"status": "error", "text": e.message}
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

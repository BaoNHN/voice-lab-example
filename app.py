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
    pip install -r requirements.txt          # pulls in clone-voice-client[local]
                                              # (openai-whisper + torch — heavy,
                                              # first install takes a few minutes)
    python app.py                            # http://127.0.0.1:8091

Optional: to see hotword-biased local transcription, run clone-voice-station,
register/login at /stt-lab, create an adapter with some hotwords, download
its .stt-pack.zip, and drop it into this repo's stt_pack/ folder before
starting this app.
"""

import glob
import os

from clone_voice_client import VoiceStationClient, VoiceStationError
from clone_voice_client import local_stt
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from engine import retrieval_engine

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
STT_PACK_DIR = os.path.join(BASE_DIR, "stt_pack")

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

voice_client = VoiceStationClient()

ACTIVE_HOTWORDS = []
_packs = glob.glob(os.path.join(STT_PACK_DIR, "*.zip"))
if _packs:
    ACTIVE_HOTWORDS = local_stt.load_hotwords_from_pack(_packs[0])
    print(f"[voice-lab-example] Loaded STT Lab pack: {os.path.basename(_packs[0])} "
          f"({len(ACTIVE_HOTWORDS)} hotwords)")
else:
    print("[voice-lab-example] No .stt-pack.zip found in stt_pack/ — plain transcription.")

print("[voice-lab-example] Building retrieval index...")
_pair_count = retrieval_engine.load_index()
print(f"[voice-lab-example] Retrieval index ready ({_pair_count} Q/A pairs).")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.post("/get")
async def get_route(request: Request):
    data   = await request.json()
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return {"status": "error", "text": "Vui lòng nhập câu hỏi."}
    return {"status": "success", "text": retrieval_engine.answer(prompt)}


@app.post("/transcribe")
async def transcribe_route(audio: UploadFile = File(...), language: str = Form("vi")):
    content = await audio.read()
    try:
        result = voice_client.transcribe_local(
            audio.filename, content, mime=audio.content_type, language=language,
            hotwords=ACTIVE_HOTWORDS,
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

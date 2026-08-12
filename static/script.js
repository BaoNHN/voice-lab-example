const sendButton    = document.getElementById("sendButton");
const micButton      = document.getElementById("micButton");
const chatInput      = document.getElementById("chatInput");
const chatbox        = document.getElementById("chatbox");
const mainContainer  = document.getElementById("mainContainer");

// ── Welcome / Chat mode toggle ────────────────────
function enterChatMode() {
    mainContainer.classList.add("is-chatting");
}

// ── Suggestion chips ──────────────────────────────
document.querySelectorAll(".chip").forEach(chip => {
    chip.addEventListener("click", () => {
        chatInput.value = chip.dataset.prompt;
        chatInput.focus();
    });
});

// Escapes text for safe insertion into innerHTML.
function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s ?? '';
    return div.innerHTML;
}

// Display chat bubbles
async function displayMessage(message, isUser) {
    const msgElem = document.createElement('div');
    msgElem.innerHTML = escapeHtml(message).replace(/\n/g, "<br>");
    msgElem.className = `chat-message ${isUser ? 'user-message' : 'assistant-message'}`;
    chatbox.appendChild(msgElem);
    chatbox.scrollTop = chatbox.scrollHeight;
}

async function callApi(prompt) {
    try {
        const response = await fetch("/get", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prompt: prompt })
        });
        return await response.json();
    } catch (err) {
        console.error("FETCH ERROR:", err);
        return { status: "error", text: "❌ Không kết nối được server" };
    }
}

chatInput.focus();

sendButton.addEventListener('click', async () => {
    const message = chatInput.value.trim();
    if (!message) return;

    enterChatMode();
    displayMessage(message, true);
    chatInput.value = "";
    chatInput.disabled = true;
    sendButton.disabled = true;

    displayMessage("⏳ Đang tìm câu trả lời...", false);
    const data = await callApi(message);
    chatbox.lastChild.remove();

    displayMessage(data?.text || "❌ Lỗi không xác định", false);
    chatInput.disabled = false;
    sendButton.disabled = false;
    chatInput.focus();
});

chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendButton.click();
});

// ── Mic recording → live-transcribe while still recording, POST /transcribe
// (mode=local, same in-process Whisper this page demos) fills the input box
// (not auto-send, so the user can review/edit before sending) — same
// sliding-window / stale-response-guard rules as compare.js's "local" side
// and rag-legal-assistant's static/script.js, added 2026-08-12 so this page
// isn't the odd one out (it was still single-shot: record → stop → one
// /transcribe call, no mid-recording preview). Local runs in-process (no
// network hop), so it can afford the tighter 1s cadence compare.js's own
// local side uses.
let mediaRecorder = null;
let audioChunks = [];
let liveTranscribeTimer = null;
let liveTranscribeInFlight = false;
let lastLiveTranscript = null;
const LIVE_TRANSCRIBE_INTERVAL_MS = 1000;

// Stale-response guard: a live tick's request and the final stop-triggered
// request can both be in flight at once (Stop doesn't cancel a tick that's
// already mid-fetch), and nothing about HTTP guarantees they resolve in the
// order they were sent — a slow, older/partial-audio tick response can land
// after the final, complete-audio response and clobber it. Tag every
// request with a sequence number at send time; when a response comes back,
// only apply it if it's newer than the last one actually applied.
let requestSeq = 0;
let appliedSeq  = 0;

// Sliding window for live ticks — caps how much audio a tick re-transcribes
// so cost stays flat instead of growing with total recording length.
// audioChunks[0] carries the WebM header (Matroska Segment/Tracks info) that
// every later chunk depends on to decode — later chunks are just codec
// SimpleBlocks with no header of their own — so it's always kept even once
// it ages out of the time window, with the most recent chunks appended after it.
const WINDOW_CHUNK_COUNT = 14; // ~7s at the 500ms MediaRecorder timeslice below

function windowedChunks() {
    if (audioChunks.length <= WINDOW_CHUNK_COUNT) return audioChunks;
    const header = audioChunks[0];
    const recent = audioChunks.slice(-WINDOW_CHUNK_COUNT);
    return recent.includes(header) ? recent : [header, ...recent];
}

// Sends the given chunks (the full recording for the final transcribe, or a
// capped window for a live tick — see windowedChunks()) and returns the
// transcript.
async function transcribeChunksSoFar(chunks) {
    const formData = new FormData();
    formData.append("audio", new Blob(chunks, { type: mediaRecorder.mimeType || "audio/webm" }), "recording.webm");
    formData.append("language", "vi");
    const res  = await fetch("/transcribe", { method: "POST", body: formData });
    const data = await res.json();
    if (data.status !== "success") throw new Error(data.text || "Lỗi nhận dạng giọng nói.");
    return data.text || "";
}

// Applies a transcript to the chat input only if `seq` is newer than the
// last one actually applied (drops a late-arriving, now-superseded response
// instead of clobbering fresher text), and only if the user hasn't started
// editing the input themselves mid-recording (don't clobber their own
// typing either).
function applyLiveTranscript(seq, text) {
    if (!text || seq < appliedSeq) return;
    if (!(chatInput.value === "" || chatInput.value === lastLiveTranscript)) return;
    appliedSeq = seq;
    chatInput.value = text;
    lastLiveTranscript = text;
}

async function liveTranscribeTick() {
    if (liveTranscribeInFlight || audioChunks.length === 0) return;
    liveTranscribeInFlight = true;
    const seq = ++requestSeq;
    try {
        const text = await transcribeChunksSoFar(windowedChunks());
        applyLiveTranscript(seq, text);
    } catch (e) {
        // Best-effort — the final transcribe on stop is authoritative, so a
        // dropped live tick just means one less mid-recording preview.
        console.warn("[Voice] Live transcribe tick failed:", e);
    } finally {
        liveTranscribeInFlight = false;
    }
}

micButton.addEventListener('click', async () => {
    if (mediaRecorder && mediaRecorder.state === "recording") {
        mediaRecorder.stop();
        return;
    }

    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
        alert("Không truy cập được microphone: " + err.message);
        return;
    }

    audioChunks = [];
    lastLiveTranscript = null;
    requestSeq = 0;
    appliedSeq = 0;
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };
    mediaRecorder.onstop = async () => {
        clearInterval(liveTranscribeTimer);
        liveTranscribeTimer = null;
        stream.getTracks().forEach(t => t.stop());
        micButton.textContent = "🎤";
        micButton.disabled = true;

        // Issued after every live tick for this recording, so this seq
        // number is guaranteed the highest — it always wins
        // applyLiveTranscript's check, even if a straggling tick response
        // arrives after this one does.
        const seq = ++requestSeq;
        try {
            const text = await transcribeChunksSoFar(audioChunks); // full recording, not windowed
            applyLiveTranscript(seq, text);
            chatInput.focus();
        } catch (err) {
            alert(err.message || "Lỗi nhận dạng giọng nói.");
        } finally {
            micButton.disabled = false;
        }
    };

    // 500ms timeslice so chunks accumulate progressively instead of only at
    // stop — required for liveTranscribeTick to have growing audio to send.
    mediaRecorder.start(500);
    micButton.textContent = "⏹";
    liveTranscribeTimer = setInterval(liveTranscribeTick, LIVE_TRANSCRIBE_INTERVAL_MS);
});

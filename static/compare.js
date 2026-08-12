const manualText = document.getElementById("manualText");
const localText   = document.getElementById("localText");
const remoteText  = document.getElementById("remoteText");

const TARGET_TEXTAREA = { local: localText, remote: remoteText };
const TARGET_DIFF      = { local: document.getElementById("localDiff"), remote: document.getElementById("remoteDiff") };
const TIME_BADGE        = { local: document.getElementById("localTimeBadge"), remote: document.getElementById("remoteTimeBadge") };

const recordBtn        = document.getElementById("recordBtn");
const recordPanel      = document.getElementById("recordPanel");
const recordTimerEl    = document.getElementById("recordTimer");
const recordStatusEl   = document.getElementById("recordStatus");
const recordStopBtn    = document.getElementById("recordStopBtn");
const recordCancelBtn  = document.getElementById("recordCancelBtn");

// ── Word-level diff: highlights words in `text` that aren't part of the
// longest common subsequence with `reference`, so the user can spot where a
// transcription drifted from what they typed by hand. ─────────────────────
function tokenize(s) {
    return (s || "").trim().split(/\s+/).filter(Boolean);
}

function diffAgainst(reference, text) {
    const refWords = tokenize(reference);
    const words    = tokenize(text);
    const n = refWords.length, m = words.length;
    if (m === 0) return "";
    if (n === 0) return escapeHtml(text);

    const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
    for (let i = n - 1; i >= 0; i--) {
        for (let j = m - 1; j >= 0; j--) {
            dp[i][j] = refWords[i] === words[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
        }
    }

    let html = "";
    let i = 0, j = 0;
    while (i < n && j < m) {
        if (refWords[i] === words[j]) {
            html += escapeHtml(words[j]) + " ";
            i++; j++;
        } else if (dp[i + 1][j] >= dp[i][j + 1]) {
            i++;
        } else {
            html += `<mark class="diff-add">${escapeHtml(words[j])}</mark> `;
            j++;
        }
    }
    while (j < m) {
        html += `<mark class="diff-add">${escapeHtml(words[j])}</mark> `;
        j++;
    }
    return html.trim();
}

function escapeHtml(s) {
    const div = document.createElement("div");
    div.textContent = s ?? "";
    return div.innerHTML;
}

function refreshDiff(mode) {
    const target = TARGET_TEXTAREA[mode];
    const diffEl = TARGET_DIFF[mode];
    if (!target.value.trim()) { diffEl.innerHTML = ""; return; }
    diffEl.innerHTML = "So với ô nhập tay: " + (diffAgainst(manualText.value, target.value) || "<em>giống hệt</em>");
}

manualText.addEventListener("input", () => {
    refreshDiff("local");
    refreshDiff("remote");
});

// ── Recording state — one shared take feeds both Local and Lazy at once ──
let mediaRecorder  = null;
let audioChunks    = [];
let activeStream    = null;
let elapsedSeconds  = 0;
let tickTimer       = null;

// Local runs in-process (no network hop), so it can afford to re-transcribe
// what's captured so far on a tight cadence. Lazy pays for an HTTP round
// trip to clone-voice-station plus its own inference, so it ticks slower —
// same tradeoff rag-legal-assistant's live-transcribe mode makes (2.5s there).
// Both loops run concurrently off the same recording once Ghi âm is clicked.
const LIVE_INTERVAL_MS = { local: 1000, remote: 2000 };

let liveTickTimer  = { local: null, remote: null };
let liveInFlight    = { local: false, remote: false };

// Stale-response guard: a live tick's request and the final stop-triggered
// request can both be in flight at once (Stop doesn't cancel a tick that's
// already mid-fetch), and nothing about HTTP guarantees they resolve in the
// order they were sent — a slow, older/partial-audio tick response can land
// after the final, complete-audio response and clobber it. Tag every request
// with a per-mode sequence number at send time; when a response comes back,
// only apply it if it's newer than the last one actually applied.
let requestSeq      = { local: 0, remote: 0 };
let appliedSeq       = { local: 0, remote: 0 };

function formatTime(totalSeconds) {
    const mm = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
    const ss = String(totalSeconds % 60).padStart(2, "0");
    return `${mm}:${ss}`;
}

function openRecordPanel() {
    elapsedSeconds = 0;
    recordTimerEl.textContent = formatTime(0);
    recordStatusEl.textContent = "Đang ghi âm — Local cập nhật mỗi 1s, Lazy mỗi 2s…";
    recordStopBtn.disabled = false;
    recordStopBtn.textContent = "⏹ Dừng & so sánh";
    recordPanel.hidden = false;
    recordPanel.classList.add("recording");
    recordBtn.disabled = true;
}

function closeRecordPanel() {
    recordPanel.hidden = true;
    recordPanel.classList.remove("recording");
    recordBtn.disabled = false;
    clearInterval(tickTimer);
    tickTimer = null;
}

function startTimer() {
    clearInterval(tickTimer);
    tickTimer = setInterval(() => {
        elapsedSeconds += 1;
        recordTimerEl.textContent = formatTime(elapsedSeconds);
    }, 1000);
}

// Sliding window for live ticks — caps how much audio a tick re-transcribes
// so cost stays flat instead of growing with total recording length (the
// original bug: tick N had to re-send/re-infer all N*500ms recorded so far).
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

// Sends the given chunks (a growing prefix for the final transcribe, or a
// capped window for a live tick — see windowedChunks()) and returns the
// transcript.
async function transcribeChunksSoFar(mode, chunks) {
    const formData = new FormData();
    formData.append("audio", new Blob(chunks, { type: mediaRecorder.mimeType || "audio/webm" }), "recording.webm");
    formData.append("language", "vi");
    formData.append("mode", mode);
    const res = await fetch("/transcribe", { method: "POST", body: formData });
    let data;
    try {
        data = await res.json();
    } catch {
        throw new Error(`Máy chủ lỗi (${res.status}): không đọc được phản hồi.`);
    }
    if (data.status !== "success") throw new Error(data.text || "Lỗi nhận dạng giọng nói.");
    return data.text || "";
}

// Same as transcribeChunksSoFar, but also measures wall-clock round-trip time —
// used for the final Stop-triggered call, whose duration is what gets shown in
// the time badge and saved into the timing history.
async function timedTranscribe(mode, chunks) {
    const t0 = performance.now();
    try {
        const text = await transcribeChunksSoFar(mode, chunks);
        return { mode, ok: true, text, ms: performance.now() - t0 };
    } catch (err) {
        return { mode, ok: false, error: err.message, ms: performance.now() - t0 };
    }
}

// Applies a transcript to `mode`'s textarea only if `seq` is newer than
// whatever's already showing — drops a late-arriving response from an
// earlier, now-superseded request instead of clobbering fresher text.
function applyTranscript(mode, seq, text) {
    if (!text || seq < appliedSeq[mode]) return;
    appliedSeq[mode] = seq;
    TARGET_TEXTAREA[mode].value = text;
    refreshDiff(mode);
}

async function liveTranscribeTick(mode) {
    // Skip while a request is still in flight, or before any audio has
    // landed yet — avoids piling up overlapping transcribe calls.
    if (liveInFlight[mode] || audioChunks.length === 0) return;
    liveInFlight[mode] = true;
    const seq = ++requestSeq[mode];
    try {
        const text = await transcribeChunksSoFar(mode, windowedChunks());
        applyTranscript(mode, seq, text);
    } catch (e) {
        // Best-effort — the final transcribe on stop is authoritative, so a
        // dropped live tick just means one less mid-recording preview.
        console.warn(`[compare] ${mode} live transcribe tick failed:`, e);
    } finally {
        liveInFlight[mode] = false;
    }
}

function startLiveTranscribe(mode) {
    clearInterval(liveTickTimer[mode]);
    liveInFlight[mode] = false;
    liveTickTimer[mode] = setInterval(() => liveTranscribeTick(mode), LIVE_INTERVAL_MS[mode]);
}

function stopLiveTranscribe(mode) {
    clearInterval(liveTickTimer[mode]);
    liveTickTimer[mode] = null;
}

// ── Timing history — every completed round this session, kept in
// sessionStorage so it survives a reload within the same tab. ─────────────
const HISTORY_KEY = "voiceLabCompareTimingHistory";
const timingHistoryBox  = document.getElementById("timingHistory");
const timingHistoryBody = document.getElementById("timingHistoryBody");

function loadHistory() {
    try {
        return JSON.parse(sessionStorage.getItem(HISTORY_KEY) || "[]");
    } catch {
        return [];
    }
}

function formatBadge(side) {
    if (!side) return { text: "", error: false };
    return side.ok ? { text: (side.ms / 1000).toFixed(1) + "s", error: false }
                    : { text: "❌ lỗi", error: true };
}

function renderTimeBadge(mode, side) {
    const badge = TIME_BADGE[mode];
    const { text, error } = formatBadge(side);
    badge.textContent = text;
    badge.classList.toggle("error", error);
    badge.title = side && !side.ok ? side.error : "";
}

function renderHistory(history) {
    timingHistoryBox.hidden = history.length === 0;
    timingHistoryBody.innerHTML = history.map((round, i) => {
        const local  = formatBadge(round.local);
        const remote = formatBadge(round.remote);
        return `<tr>
            <td>${i + 1}</td>
            <td class="${local.error ? 'error' : ''}">${local.text || '—'}</td>
            <td class="${remote.error ? 'error' : ''}">${remote.text || '—'}</td>
        </tr>`;
    }).join("");
}

function saveRound(localResult, remoteResult) {
    const history = loadHistory();
    history.push({ at: Date.now(), local: localResult, remote: remoteResult });
    sessionStorage.setItem(HISTORY_KEY, JSON.stringify(history));
    renderHistory(history);
}

renderHistory(loadHistory());

recordBtn.addEventListener("click", async () => {
    recordBtn.disabled = true; // closes the click-vs-getUserMedia-prompt gap below

    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
        recordBtn.disabled = false;
        alert("Không truy cập được microphone: " + err.message);
        return;
    }

    activeStream = stream;
    audioChunks = [];
    TIME_BADGE.local.textContent = "";
    TIME_BADGE.remote.textContent = "";
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };
    mediaRecorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        activeStream = null;
    };

    // 500ms timeslice so chunks accumulate progressively instead of only
    // at stop — required for the live ticks to have growing audio to send.
    mediaRecorder.start(500);
    openRecordPanel();
    startTimer();
    startLiveTranscribe("local");
    startLiveTranscribe("remote");
});

recordCancelBtn.addEventListener("click", () => {
    stopLiveTranscribe("local");
    stopLiveTranscribe("remote");
    if (mediaRecorder && mediaRecorder.state === "recording") {
        mediaRecorder.onstop = () => { activeStream?.getTracks().forEach(t => t.stop()); activeStream = null; };
        mediaRecorder.stop();
    }
    closeRecordPanel();
});

recordStopBtn.addEventListener("click", async () => {
    if (!mediaRecorder || mediaRecorder.state !== "recording") return;

    stopLiveTranscribe("local");
    stopLiveTranscribe("remote");
    clearInterval(tickTimer);
    recordStopBtn.disabled = true;
    recordCancelBtn.disabled = true;
    recordStatusEl.textContent = "Đang nhận dạng — so sánh cả hai bên…";
    recordPanel.classList.remove("recording");

    const stopped = new Promise(resolve => { mediaRecorder.onstop = resolve; });
    mediaRecorder.stop();
    await stopped;
    activeStream?.getTracks().forEach(t => t.stop());
    activeStream = null;

    // Issued after every live tick for this recording, so these seq numbers
    // are guaranteed the highest for each mode — always win applyTranscript's
    // check, even if a straggling tick response arrives after this does.
    const seqLocal  = ++requestSeq.local;
    const seqRemote = ++requestSeq.remote;

    // Fired in parallel (not sequentially) so each side's timing reflects its
    // own latency, not however long the other one happened to take first.
    const [localResult, remoteResult] = await Promise.all([
        timedTranscribe("local", audioChunks),   // full recording, not windowed
        timedTranscribe("remote", audioChunks),
    ]);

    if (localResult.ok)  applyTranscript("local", seqLocal, localResult.text);
    if (remoteResult.ok) applyTranscript("remote", seqRemote, remoteResult.text);
    renderTimeBadge("local", localResult);
    renderTimeBadge("remote", remoteResult);
    saveRound(localResult, remoteResult);

    recordCancelBtn.disabled = false;
    closeRecordPanel();
});

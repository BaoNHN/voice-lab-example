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

// ── Mic recording → POST /transcribe → fill input box (not auto-send, so
// the user can review/edit the transcription before sending) ─────────────
let mediaRecorder = null;
let audioChunks = [];

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
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => audioChunks.push(e.data);
    mediaRecorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        micButton.textContent = "🎤";
        micButton.disabled = true;

        const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || "audio/webm" });
        const formData = new FormData();
        formData.append("audio", blob, "recording.webm");
        formData.append("language", "vi");

        try {
            const res  = await fetch("/transcribe", { method: "POST", body: formData });
            const data = await res.json();
            if (data.status === "success") {
                chatInput.value = data.text;
                chatInput.focus();
            } else {
                alert(data.text || "Lỗi nhận dạng giọng nói.");
            }
        } catch (err) {
            alert("Không kết nối được server: " + err.message);
        } finally {
            micButton.disabled = false;
        }
    };

    mediaRecorder.start();
    micButton.textContent = "⏹";
});

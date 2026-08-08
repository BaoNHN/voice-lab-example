# Hướng Dẫn Demo — voice-lab-example

> Ứng dụng demo nhỏ cho `clone-voice-client`: (1) chat hỏi-đáp tiếng Việt (retrieval TF-IDF thật, không phải câu trả lời cứng) với input bằng giọng nói chạy Whisper **local** (trong tiến trình app, không gọi mạng); (2) trang `/compare` để so sánh nhập tay với nhập giọng nói qua hai đường transcribe khác nhau của `clone-voice-client` — **Local** (trong thư viện) và **Lazy** (qua `clone-voice-station`) — kèm đo thời gian phản hồi từng bên.

---

## Mục Lục

1. [Yêu Cầu & Cài Đặt](#1-yêu-cầu--cài-đặt)
2. [Khởi Động](#2-khởi-động)
3. [Trang Chat (`/`)](#3-trang-chat-)
4. [Trang So Sánh (`/compare`)](#4-trang-so-sánh-compare)
5. [Bật Chế Độ Lazy — Cần `clone-voice-station`](#5-bật-chế-độ-lazy--cần-clone-voice-station)
6. [STT Lab — Hotword/LoRA (tuỳ chọn)](#6-stt-lab--hotwordlora-tuỳ-chọn)
7. [Kiến Trúc Kỹ Thuật](#7-kiến-trúc-kỹ-thuật)
8. [Xử Lý Sự Cố](#8-xử-lý-sự-cố)

---

## 1. Yêu Cầu & Cài Đặt

```bash
pip install -r requirements.txt   # kéo theo clone-voice-client[local] (openai-whisper + torch —
                                   # nặng, lần cài đầu mất vài phút)
```

Cần **ffmpeg** trong PATH (Whisper dùng để giải mã webm/ogg/m4a/mp3 từ trình duyệt). Nếu ffmpeg hệ thống không chạy được (hay gặp trên Windows với bản conda-forge), trỏ vào một bản ffmpeg tĩnh khác qua biến môi trường:

```bash
set CLONE_VOICE_FFMPEG_DIR=D:\hoc\project\clone-voice-station\bin   # Windows cmd
$env:CLONE_VOICE_FFMPEG_DIR = "D:\hoc\project\clone-voice-station\bin"   # PowerShell
```

## 2. Khởi Động

```bash
python app.py
```

Chạy trên **http://127.0.0.1:8091**. Lần đầu khởi động sẽ tải bộ dữ liệu hội thoại tiếng Việt (`MrCookieDev/Vietnamese-Chatting-Dataset`, ~2k dòng) về `data/` và dựng chỉ mục TF-IDF trong bộ nhớ — vài giây, không cần cấu hình gì thêm.

## 3. Trang Chat (`/`)

Demo gốc: gõ câu hỏi hoặc bấm 🎤 để nói, Whisper chạy **local** (trong tiến trình app này, `transcribe_local()` của `clone-voice-client` — không gọi mạng), điền kết quả vào ô nhập (không tự gửi, để bạn xem/sửa trước). Câu trả lời lấy từ retrieval TF-IDF trên bộ dữ liệu hội thoại, không phải LLM hay câu trả lời cứng.

## 4. Trang So Sánh (`/compare`)

Bấm **"🧪 So sánh nhập tay vs giọng nói →"** ở góc phải header của trang chat, hoặc vào thẳng `/compare`. Có 3 ô:

- **✍️ Nhập tay** — gõ tay bình thường, dùng làm câu tham chiếu để so sánh.
- **📦 Giọng nói — Local** — transcribe qua `transcribe_local()`, trong tiến trình app này.
- **🌐 Giọng nói — Lazy** — transcribe qua `VoiceStationClient.transcribe()`, gửi HTTP tới một `clone-voice-station` đang chạy.

Chỉ có **một nút "🎤 Ghi âm & so sánh"** duy nhất — bấm, nói một câu, bấm Dừng. Bản ghi được gửi **đồng thời** (song song) cho cả hai đường transcribe, mỗi bên đo thời gian phản hồi riêng bằng `performance.now()`.

- **Trong lúc ghi**: cả hai ô có preview trực tiếp, cập nhật liên tục — Local mỗi 1 giây, Lazy mỗi 2 giây (Lazy chậm hơn vì phải qua mạng + suy luận phía server). Preview dùng "sliding window" (giới hạn ~7 giây audio gần nhất mỗi lần gửi) để chi phí không tăng dần theo thời gian ghi.
- **Khi bấm Dừng**: gửi lại **toàn bộ** bản ghi (không giới hạn cửa sổ) cho cả hai bên — đây là kết quả **chính thức**, luôn ghi đè lên bản preview.
- **Badge thời gian** (VD "2.3s") hiện cạnh tiêu đề mỗi cột sau khi có kết quả; bên nào lỗi hiện "❌ lỗi" (di chuột xem chi tiết) thay vì làm gián đoạn bên còn lại.
- **Dòng so sánh** dưới mỗi ô giọng nói tô sáng những từ khác với ô nhập tay (diff theo từ, dùng LCS).
- **Lịch sử thời gian**: mỗi lượt hoàn tất được lưu vào `sessionStorage` của trình duyệt (mất khi đóng tab/trình duyệt) và hiện thành bảng bên dưới — để so sánh nhiều lượt liền nhau.

## 5. Bật Chế Độ Lazy — Cần `clone-voice-station`

Cột Lazy cần một `clone-voice-station` đang chạy và một API key hợp lệ:

```bash
# 1. Chạy clone-voice-station (repo riêng, xem HUONG_DAN_DEMO.md của nó)
cd ../clone-voice-station
python app.py     # http://127.0.0.1:8090

# 2. Đăng ký một client mới cho voice-lab-example và lấy API key
python -c "
from database.database import init_db, create_client
init_db()
print(create_client('voice-lab-example')['api_key'])
"
```

Lưu key vào `voice_station_key.txt` (đã gitignore) rồi chạy voice-lab-example với 2 biến môi trường:

```bash
# PowerShell
$env:VOICE_STATION_API_KEY = Get-Content voice_station_key.txt
$env:CLONE_VOICE_FFMPEG_DIR = "D:\hoc\project\clone-voice-station\bin"
python app.py
```

`VOICE_STATION_URL` mặc định là `http://127.0.0.1:8090`, chỉ cần đặt nếu station chạy chỗ khác. Nếu chưa cấu hình, cột Lazy sẽ báo lỗi kết nối rõ ràng thay vì treo.

## 6. STT Lab — Hotword/LoRA (tuỳ chọn)

Để xem transcribe **local** có thiên hướng từ khoá riêng (hotword bias) thay vì Whisper mặc định: chạy `clone-voice-station`, đăng ký/đăng nhập tại `/stt-lab`, tạo adapter kèm hotword, tải file `.stt-pack.zip`, bỏ vào thư mục `stt_pack/` của repo này trước khi khởi động `app.py` — log khởi động sẽ báo pack đã nạp (`[voice-lab-example] Loaded STT Lab pack: ...`).

## 7. Kiến Trúc Kỹ Thuật

```
voice-lab-example/
├── app.py                     # FastAPI: "/" (chat), "/compare", "/get", "/transcribe"
├── engine/
│   └── retrieval_engine.py    # TF-IDF trên Vietnamese-Chatting-Dataset (tải tự động vào data/)
├── templates/
│   ├── index.html             # Trang chat
│   └── compare.html           # Trang so sánh
├── static/
│   ├── style.css / script.js  # Trang chat
│   └── compare.css / compare.js  # Trang so sánh (single record button, sliding-window
│                                  #   live-tick, stale-response seq guard, timing history)
├── data/                      # Dataset tải về (gitignore)
└── stt_pack/                  # .stt-pack.zip tuỳ chọn từ STT Lab (gitignore)
```

`/transcribe` nhận `mode=local` (mặc định, `transcribe_local()`) hoặc `mode=remote` (`transcribe()`, đi qua `clone-voice-station`) — cùng một route phục vụ cả hai trang.

## 8. Xử Lý Sự Cố

### `/transcribe` báo lỗi 500, log có `FileNotFoundError` từ subprocess

**Nguyên nhân:** thiếu `ffmpeg` trên PATH của tiến trình Python đang chạy `app.py`. **Khắc phục:** đặt `CLONE_VOICE_FFMPEG_DIR` trước khi chạy (mục 1), hoặc cài ffmpeg vào PATH hệ thống.

### Cột Lazy báo "Không kết nối được tới voice station"

**Kiểm tra:** `clone-voice-station` có đang chạy không (`curl http://127.0.0.1:8090/api/health`), `VOICE_STATION_API_KEY` đã đặt đúng key của client `voice-lab-example` chưa (mục 5).

### Cột Lazy phản hồi rất chậm (nhiều giây) dù mạng local

**Nguyên nhân có thể:** `clone-voice-station` đang cấu hình `rvc_endpoint` (mục Colab) trỏ vào một địa chỉ không phản hồi được — mỗi request `/api/transcribe` phải đợi hết timeout (mặc định 20s) rồi mới fallback về Whisper local phía station. **Kiểm tra:**

```bash
cd ../clone-voice-station
python -c "from database.database import get_setting; print(repr(get_setting('rvc_endpoint')))"
```

Nếu không có Colab session nào đang chạy thật, xoá về rỗng:

```bash
python -c "from database.database import set_setting; set_setting('rvc_endpoint', '')"
```

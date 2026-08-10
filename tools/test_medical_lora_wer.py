#!/usr/bin/env python
"""
tools/test_medical_lora_wer.py
Proves the STT Lab Tier 2 (LoRA fine-tune) capability actually works: transcribes the same
held-out medical-consultation audio through base Whisper and through a LoRA-fine-tuned adapter
(clone_voice_client.local_stt.transcribe() vs .transcribe_with_lora()), and compares Word Error
Rate against the reference transcript for both -- if the adapter was trained on domain data (see
clone-voice-station/tools/import_hf_stt_dataset.py, which produces both the adapter pack and this
script's held-out eval set from HieuNguyen203/Vietnamese_Medical_Consultation on HuggingFace), its
WER on held-out medical audio should measurably beat the untrained base model.

WER math (word-level Levenshtein against lowercased, punctuation-stripped tokens) is the same
definition clone-voice-station/tools/eval_stt_wer.py uses -- duplicated here (not imported)
since this is a separate repo with no dependency on clone-voice-station's source tree, same as
every other cross-repo boundary in this demo (only the .stt-pack.zip file crosses it).

Setup
-----
1. In clone-voice-station: python tools/import_hf_stt_dataset.py --train --download-pack medical.stt-pack.zip
   (uploads real medical-consultation samples, trains a Whisper LoRA adapter locally, and writes
   a held-out eval set to tools/medical_eval_set/).
2. Copy medical.stt-pack.zip into this repo's stt_pack/ (or pass --pack directly), and point
   this script at the eval set the import script produced:

    python tools/test_medical_lora_wer.py ../clone-voice-station/tools/medical_eval_set

Usage
-----
    python tools/test_medical_lora_wer.py <eval_dir> [--pack PATH] [--language vi] [--out results.csv]

<eval_dir> is a directory of (NNN.wav, NNN.txt) sidecar pairs, same convention as
clone-voice-station/tools/eval_stt_wer.py's testset_dir.
"""
import argparse
import csv
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from clone_voice_client import local_stt

AUDIO_EXT = ".wav"  # import_hf_stt_dataset.py always writes wav


def _normalize(text: str) -> list:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)
    return text.split()


def edit_distance(reference: str, hypothesis: str) -> tuple:
    """Returns (edits, ref_word_count) via word-level Levenshtein distance."""
    ref = _normalize(reference)
    hyp = _normalize(hypothesis)
    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])
    return dp[n][m], max(n, 1)


def find_pairs(eval_dir: str) -> list:
    pairs = []
    for txt_path in sorted(glob.glob(os.path.join(eval_dir, "*.txt"))):
        stem = os.path.splitext(txt_path)[0]
        audio_path = stem + AUDIO_EXT
        if os.path.exists(audio_path):
            pairs.append((audio_path, txt_path))
        else:
            print(f"WARNING: no matching {AUDIO_EXT} for {txt_path}, skipping", file=sys.stderr)
    return pairs


def resolve_pack_path(explicit: str) -> str:
    if explicit:
        return explicit
    candidates = glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                         "stt_pack", "*.zip"))
    if not candidates:
        print("No .stt-pack.zip found -- pass --pack, or drop one into stt_pack/ "
              "(see clone-voice-station/tools/import_hf_stt_dataset.py --download-pack).", file=sys.stderr)
        sys.exit(1)
    return candidates[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("eval_dir")
    ap.add_argument("--pack", default=None)
    ap.add_argument("--language", default="vi")
    ap.add_argument("--out", default=None, help="Optional CSV path to write per-file results")
    args = ap.parse_args()

    pairs = find_pairs(args.eval_dir)
    if not pairs:
        print(f"No (audio, .txt) pairs found in {args.eval_dir}", file=sys.stderr)
        sys.exit(1)

    pack_path = resolve_pack_path(args.pack)
    print(f"Loading pack: {pack_path}")
    pack = local_stt.load_pack(pack_path)
    if not pack["adapter_dir"]:
        print("This pack is Tier 1 (hotwords only, no LoRA adapter) -- train a Tier 2 adapter "
              "first (clone-voice-station/tools/import_hf_stt_dataset.py --train).", file=sys.stderr)
        sys.exit(1)
    print(f"Base model: {pack['base_model']}  |  adapter: {pack['adapter_dir']}")

    rows = []
    base_edits = base_ref_words = 0
    lora_edits = lora_ref_words = 0

    for audio_path, txt_path in pairs:
        with open(txt_path, encoding="utf-8") as f:
            reference = f.read().strip()
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        base_text = local_stt.transcribe(audio_bytes, mime="audio/wav", language=args.language)["text"]
        lora_text = local_stt.transcribe_with_lora(
            audio_bytes, pack["base_model"], pack["adapter_dir"], mime="audio/wav", language=args.language
        )["text"]

        be, brw = edit_distance(reference, base_text)
        le, lrw = edit_distance(reference, lora_text)
        base_edits += be; base_ref_words += brw
        lora_edits += le; lora_ref_words += lrw

        base_wer = round(100 * be / brw, 1)
        lora_wer = round(100 * le / lrw, 1)
        rows.append({
            "file": os.path.basename(audio_path), "reference": reference,
            "base_hypothesis": base_text, "base_wer_pct": base_wer,
            "lora_hypothesis": lora_text, "lora_wer_pct": lora_wer,
        })
        delta = base_wer - lora_wer
        arrow = "better" if delta > 0 else ("worse" if delta < 0 else "same")
        print(f"{rows[-1]['file']:12s}  base WER={base_wer:5.1f}%   lora WER={lora_wer:5.1f}%   "
              f"({arrow}, {delta:+.1f}pp)")

    base_corpus_wer = round(100 * base_edits / max(base_ref_words, 1), 1)
    lora_corpus_wer = round(100 * lora_edits / max(lora_ref_words, 1), 1)
    delta = base_corpus_wer - lora_corpus_wer
    verdict = "LoRA adapter IMPROVES on base" if delta > 0 else "LoRA adapter does NOT improve on base"
    print(f"\n{len(rows)} files  |  base corpus WER = {base_corpus_wer}%  |  "
          f"LoRA corpus WER = {lora_corpus_wer}%  |  {verdict} ({delta:+.1f}pp)")

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "file", "reference", "base_hypothesis", "base_wer_pct", "lora_hypothesis", "lora_wer_pct",
            ])
            writer.writeheader()
            writer.writerows(rows)
        print(f"Per-file results written to {args.out}")


if __name__ == "__main__":
    main()

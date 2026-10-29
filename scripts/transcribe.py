#!/usr/bin/env python3
# 用 faster-whisper (INT8, CPU) 转写中文口播 -> work/transcript.srt + work/transcript.txt
# 模型从 HF 镜像下载（设置环境变量 HF_ENDPOINT=https://hf-mirror.com）
import os, sys

def fmt(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def main():
    audio = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "."
    model_size = os.environ.get("WHISPER_MODEL", "small")

    from faster_whisper import WhisperModel

    print(f"==> 加载模型 faster-whisper:{model_size} (int8/cpu)")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    print("==> 开始转写 ...")
    segments, info = model.transcribe(
        audio,
        language="zh",
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )
    print(f"    检测语言: {info.language} (置信度 {info.language_probability:.2f})")

    srt_path = os.path.join(out_dir, "transcript.srt")
    txt_path = os.path.join(out_dir, "transcript.txt")
    with open(srt_path, "w", encoding="utf-8") as fsrt, \
         open(txt_path, "w", encoding="utf-8") as ftxt:
        idx = 0
        for seg in segments:
            idx += 1
            text = seg.text.strip()
            if not text:
                continue
            fsrt.write(f"{idx}\n{fmt(seg.start)} --> {fmt(seg.end)}\n{text}\n\n")
            ftxt.write(text + "\n")

    print(f"==> 完成: {srt_path}, {txt_path}")

if __name__ == "__main__":
    main()

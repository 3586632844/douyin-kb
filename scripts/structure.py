#!/usr/bin/env python3
# structure.py — zsks-runner 主流程（GitHub Actions 中运行）
# 读取 PLATFORM / CONTENT_TYPE / URL / JOB_ID，调用对应平台抽取器；
# 视频 → 下载音频 → faster-whisper 转写；图文 → 图片 OCR（可选）；
# 融合文案+口播+图OCR → DeepSeek 结构化 → 回调 /api/internal/ingest。
# 铁律：日志只打 job_id / 状态 / 错误摘要，绝不打印笔记正文或视频文案。
import os, time, tempfile, shutil
from common import log, ytdlp_download, transcribe, ocr_images, structure_note, post_ingest, fail, JOB_ID
from extractors import (
    extract_douyin, extract_bilibili, extract_xiaohongshu,
)

DISPATCH = {
    ('douyin', 'video'): extract_douyin,
    ('douyin', 'article'): extract_douyin,
    ('bilibili', 'video'): extract_bilibili,
    ('bilibili', 'article'): extract_bilibili,
    ('xiaohongshu', 'video'): extract_xiaohongshu,
    ('xiaohongshu', 'article'): extract_xiaohongshu,
}


def run():
    start = time.time()
    platform = (os.environ.get('PLATFORM') or '').strip().lower()
    ctype = (os.environ.get('CONTENT_TYPE') or 'video').strip().lower()
    url = (os.environ.get('URL') or os.environ.get('VIDEO_URL') or '').strip()
    if not platform or not url:
        fail("缺少 PLATFORM 或 URL 环境变量"); return
    log(f"开始消化 platform={platform} content_type={ctype}")

    fn = DISPATCH.get((platform, ctype)) or DISPATCH.get((platform, 'video'))
    if not fn:
        fail(f"不支持的平台: {platform}"); return

    info, caption, images = fn(url)
    if not info:
        fail(f"无法解析 {platform} 内容（壳页面/链接失效/被风控；数据中心 IP 需在对应 Secrets 配置 Cookie）")
        return

    transcript = ''
    has_transcript = 0
    ocr_text = ''
    has_ocr = 0

    if ctype == 'video':
        play_url = info.get('play_url') or ''
        tmp = tempfile.mkdtemp(prefix='zsks_')
        try:
            # 优先用 play_url 直接下载；否则交给 yt-dlp
            audio = None
            if play_url:
                audio = ytdlp_download(play_url, tmp, audio_only=True)
            if not audio:
                audio = ytdlp_download(url, tmp, audio_only=True)
            if audio:
                transcript = transcribe(audio, os.environ.get('WHISPER_MODEL', 'small'))
                if transcript:
                    has_transcript = 1
            else:
                log("音频下载失败，回退到仅文案")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:  # article：可选 OCR 图片文字
        if images:
            ocr_text = ocr_images(images)
            if ocr_text:
                has_ocr = 1

    note, title = structure_note(caption, transcript, ocr_text, info)
    has_text = 1 if caption else 0

    post = {
        "platform": platform,
        "content_type": ctype,
        "external_id": info.get('external_id', ''),
        "url": info.get('url', '') or url,
        "title": title or info.get('nickname', '') or info.get('title', ''),
        "publish_time": int(info.get('publish_time', 0) or 0),
        "cover": info.get('cover', '') or '',
        "play_url": info.get('play_url', '') or '',
        "desc_text": (info.get('desc') or '')[:500],
        "has_text": has_text,
        "has_transcript": has_transcript,
        "has_ocr": has_ocr,
    }
    blogger = {
        "platform": platform,
        "uid": info.get('uid', '') or info.get('external_id', ''),
        "name": info.get('nickname', '') or '',
    }
    minutes = round((time.time() - start) / 60, 2)
    st, body = post_ingest({
        "job_id": os.environ['JOB_ID'], "ok": True,
        "blogger": blogger, "post": post,
        "note": {
            "caption": caption,
            "transcript": transcript,
            "ocr_text": ocr_text,
            "summary": note.get('summary', ''),
            "outline": note.get('outline', []),
            "quotes": note.get('quotes', []),
            "tags": note.get('tags', []),
        },
        "actions_minutes": minutes,
    })
    log(f"ingest 回写 status={st} minutes={minutes} body={body[:120]}")


if __name__ == '__main__':
    run()

#!/usr/bin/env python3
# structure.py — zsks-runner 主流程（GitHub Actions 中运行）
# 读取 PLATFORM / CONTENT_TYPE / URL / JOB_ID，调用对应平台抽取器；
# 视频 → 下载音频 → faster-whisper 转写；图文 → 图片 OCR（可选）；
# 融合文案+口播+图OCR → DeepSeek 结构化 → 回调 /api/internal/ingest。
# 额外：分阶段回传进度/ETA；解析出 external_id 后先去重，重复则跳过提取。
# 铁律：日志只打 job_id / 状态 / 错误摘要，绝不打印笔记正文或视频文案。
import os, time, tempfile, shutil, json, urllib.request, urllib.error
from common import (log, download_audio, transcribe, ocr_images, structure_note,
                    post_ingest, fail, JOB_ID)
from extractors import (
    extract_douyin, extract_bilibili, extract_xiaohongshu,
)

WORKER_URL = (os.environ.get('WORKER_URL') or '').rstrip('/')
INGEST_TOKEN = os.environ.get('INGEST_TOKEN') or ''

# 阶段与预估耗时（秒），用于 ETA 估算
STEP_ORDER = ['queued', 'resolving', 'downloading', 'transcribing', 'structuring', 'ingesting', 'done']
STEP_ETA = {'resolving': 15, 'downloading': 90, 'transcribing': 180, 'structuring': 25, 'ingesting': 8}


def report_progress(step, detail='', status=None):
    """回传进度到 zsks（/api/internal/progress）。失败静默，不阻断主线。"""
    if not WORKER_URL:
        return
    idx = STEP_ORDER.index(step) if step in STEP_ORDER else len(STEP_ORDER)
    remaining = sum(STEP_ETA.get(s, 0) for s in STEP_ORDER[idx + 1:] if s != 'done')
    payload = {
        "job_id": JOB_ID(), "step": step, "detail": detail,
        "eta_seconds": remaining,
    }
    if status:
        payload["status"] = status
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            WORKER_URL + '/api/internal/progress', data=data,
            headers={"Authorization": f"Bearer {INGEST_TOKEN}",
                     "Content-Type": "application/json", "User-Agent": "zsks-runner"},
        )
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        log(f"进度回传失败(忽略): {type(e).__name__}")


def check_dup(platform, external_id):
    """查询 (platform, external_id) 是否已存在；返回 dict 或 None。"""
    if not (WORKER_URL and external_id):
        return None
    try:
        data = json.dumps({"platform": platform, "external_id": external_id}).encode('utf-8')
        req = urllib.request.Request(
            WORKER_URL + '/api/internal/check_dup', data=data,
            headers={"Authorization": f"Bearer {INGEST_TOKEN}",
                     "Content-Type": "application/json", "User-Agent": "zsks-runner"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:
        log(f"去重查询失败(忽略): {type(e).__name__}")
        return None


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

    report_progress('resolving', f"解析 {platform} 链接")
    fn = DISPATCH.get((platform, ctype)) or DISPATCH.get((platform, 'video'))
    if not fn:
        fail(f"不支持的平台: {platform}"); return

    info, caption, images = fn(url)
    if not info:
        fail(f"无法解析 {platform} 内容（IP风控/链接失效；数据中心 IP 优先走无登录接口仍失败）")
        return

    external_id = info.get('external_id', '') or ''

    # —— 去重：解析出 external_id 后先查重，已存在则跳过提取 ——
    if external_id:
        dup = check_dup(platform, external_id)
        if dup and dup.get('exists'):
            log(f"检测到重复作品 external_id={external_id}，跳过提取")
            post_ingest({
                "job_id": JOB_ID(), "ok": True, "skipped": True,
                "existing_post_id": dup.get('post_id'),
                "post": {"platform": platform, "external_id": external_id,
                         "title": dup.get('title', ''), "publish_time": dup.get('publish_time', 0)},
            })
            return

    transcript = ''
    has_transcript = 0
    ocr_text = ''
    has_ocr = 0

    if ctype == 'video':
        play_url = info.get('play_url') or ''
        tmp = tempfile.mkdtemp(prefix='zsks_')
        try:
            report_progress('downloading', "下载音视频")
            audio = None
            if play_url:
                audio = download_audio(play_url, tmp, referer='https://www.douyin.com/')
            if not audio:
                audio = download_audio(url, tmp, referer='https://www.douyin.com/')
            if audio:
                report_progress('transcribing', "语音转写中（最耗时）")
                transcript = transcribe(audio, os.environ.get('WHISPER_MODEL', 'small'))
                if transcript:
                    has_transcript = 1
            else:
                log("音频下载失败，回退到仅文案")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:  # article：可选 OCR 图片文字
        if images:
            report_progress('transcribing', "图片 OCR 中")
            ocr_text = ocr_images(images)
            if ocr_text:
                has_ocr = 1

    report_progress('structuring', "AI 结构化（摘要/大纲/主题）")
    note, title = structure_note(caption, transcript, ocr_text, info)
    has_text = 1 if caption else 0

    post = {
        "platform": platform,
        "content_type": ctype,
        "external_id": external_id,
        "url": info.get('url', '') or url,
        "title": title or info.get('nickname', '') or info.get('title', ''),
        "publish_time": int(info.get('publish_time', 0) or 0),
        "category": note.get('category', '其他'),
        "cover": info.get('cover', '') or '',
        "play_url": info.get('play_url', '') or '',
        "desc_text": (info.get('desc') or '')[:500],
        "has_text": has_text,
        "has_transcript": has_transcript,
        "has_ocr": has_ocr,
    }
    blogger = {
        "platform": platform,
        "uid": info.get('uid', '') or external_id,
        "name": info.get('nickname', '') or '',
    }
    minutes = round((time.time() - start) / 60, 2)
    report_progress('ingesting', "回写知识库")
    st, body = post_ingest({
        "job_id": JOB_ID(), "ok": True,
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
    report_progress('done', "完成")
    log(f"ingest 回写 status={st} minutes={minutes} body={body[:120]}")


if __name__ == '__main__':
    run()

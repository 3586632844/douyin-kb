#!/usr/bin/env python3
# common.py — zsks-runner 共享工具：网络请求、yt-dlp 下载、语音转写、模型结构化、回调 zsks
# 铁律：日志只打 job_id / 状态 / 错误摘要，绝不打印笔记正文或视频文案内容。
import os, sys, json, re, time, subprocess, shutil, urllib.request, urllib.error, tempfile

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def log(msg):
    print(f"[zsks][{JOB_ID()[:8]}] {msg}", flush=True)


def JOB_ID():
    return os.environ.get('JOB_ID', 'nojob')


def http_get(url, headers=None, timeout=30, raw=False, data=None, method='GET'):
    h = {'User-Agent': MOBILE_UA, 'Accept-Language': 'zh-CN,zh;q=0.9'}
    if headers:
        h.update(headers)
    cookie = os.environ.get('DOUYIN_COOKIE') or os.environ.get('BILI_COOKIE') or os.environ.get('XHS_COOKIE') or ''
    if cookie:
        h['Cookie'] = cookie
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return body if raw else body.decode('utf-8', 'ignore')


def resolve_url(text):
    """从分享文本提取 http(s) 链接；本身就是纯链接则原样返回。"""
    m = re.search(r'https?://[^\s，。\n]+', text or '')
    return m.group(0) if m else (text or '').strip()


# ------------------------- yt-dlp（三平台通用：元数据 + 下载） -------------------------
def have_yt_dlp():
    return shutil.which('yt-dlp') is not None


def yt_dlp_available():
    return have_yt_dlp()


def ytdlp_json(url, extra=None):
    """返回 yt-dlp --dump-json 解析后的字典（含 title/description/duration/thumbnail/url/subtitles 等）。"""
    if not have_yt_dlp():
        return None
    cmd = ["yt-dlp", "--dump-json", "--no-warnings", "--skip-download"]
    if extra:
        cmd += extra
    cmd.append(url)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception as e:
        log(f"yt-dlp 元数据失败: {type(e).__name__}")
        return None


def ytdlp_download(url, dest_dir, audio_only=False):
    """下载到 dest_dir，返回首个产出文件路径；audio_only 时抽 16k 单声道 wav。"""
    if not have_yt_dlp():
        return None
    if audio_only:
        # 直接让 yt-dlp 抽音频为 wav
        outtmpl = os.path.join(dest_dir, "media.%(ext)s")
        cmd = ["yt-dlp", "-x", "--audio-format", "wav", "--no-warnings",
               "-o", outtmpl, url]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                log(f"yt-dlp 音频下载失败: {r.stderr[:120]}")
                return None
            for f in os.listdir(dest_dir):
                if f.endswith('.wav'):
                    p = os.path.join(dest_dir, f)
                    # 确保 16k 单声道
                    _ensure_wav_16k_mono(p)
                    return p
        except Exception as e:
            log(f"yt-dlp 音频异常: {type(e).__name__}")
            return None
    else:
        outtmpl = os.path.join(dest_dir, "media.%(ext)s")
        cmd = ["yt-dlp", "--no-warnings", "-o", outtmpl, url]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                return None
            files = [os.path.join(dest_dir, f) for f in os.listdir(dest_dir)
                     if not f.endswith('.part') and not f.endswith('.json')]
            return files[0] if files else None
        except Exception:
            return None


def _ensure_wav_16k_mono(path):
    if not shutil.which('ffmpeg'):
        return
    tmp = path + ".fix.wav"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", path, "-vn", "-ac", "1", "-ar",
                        "16000", "-f", "wav", tmp],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)
    except Exception:
        pass


# ------------------------- 语音转写（faster-whisper，懒加载） -------------------------
def transcribe(audio_path, model_size=None):
    if model_size is None:
        model_size = os.environ.get('WHISPER_MODEL', 'small')
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        log(f"faster-whisper 未安装: {e}")
        return ''
    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, info = model.transcribe(audio_path, language="zh", beam_size=5)
        text = "".join(s.text for s in segments).strip()
        log(f"ASR 完成 语种={info.language} 字数={len(text)}")
        return text
    except Exception as e:
        log(f"ASR 异常: {type(e).__name__}: {e}")
        return ''


# ------------------------- 图文正文抽取（readability / trafilatura） -------------------------
def extract_article(html, url=''):
    """从网页 HTML 抽取正文与图片。优先 trafilatura，回退 readability-lxml。"""
    text, images = '', []
    try:
        import trafilatura
        extracted = trafilatura.extract(html, include_comments=False, include_tables=True)
        if extracted:
            text = extracted.strip()
    except Exception:
        pass
    if not text:
        try:
            from readability import Document
            doc = Document(html)
            text = doc.summary()
        except Exception:
            pass
    # 图片：从 <img> 提取（限制数量，避免大图）
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        seen = set()
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src') or img.get('data-original')
            if not src:
                continue
            if src.startswith('//'):
                src = 'https:' + src
            if src.startswith('/'):
                from urllib.parse import urljoin
                src = urljoin(url, src)
            if src in seen:
                continue
            seen.add(src)
            images.append(src)
            if len(images) >= 9:
                break
    except Exception:
        pass
    return text, images


# ------------------------- OCR（可选，best-effort） -------------------------
def ocr_images(images, max_images=4):
    """对图片做 OCR，返回拼接文本。需 pytesseract + chi_sim；未配置则跳过。"""
    if os.environ.get('OCR_ENABLED', 'false').lower() != 'true':
        return ''
    try:
        import pytesseract
        from PIL import Image
        import requests
    except Exception as e:
        log(f"OCR 依赖未安装，跳过: {e}")
        return ''
    parts = []
    for src in images[:max_images]:
        try:
            r = requests.get(src, headers={'User-Agent': DESKTOP_UA}, timeout=30)
            if r.status_code != 200:
                continue
            img = Image.open(io.BytesIO(r.content))
            txt = pytesseract.image_to_string(img, lang='chi_sim+eng').strip()
            if txt:
                parts.append(txt)
        except Exception:
            continue
    return '\n'.join(parts).strip()


# ------------------------- 模型结构化 -------------------------
def call_deepseek(prompt, key, model='deepseek-chat'):
    data = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }).encode('utf-8')
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions", data=data,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": MOBILE_UA},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode('utf-8'))
    return resp['choices'][0]['message']['content']


def robust_json(s):
    try:
        return json.loads(s)
    except Exception:
        a, b = s.find('{'), s.rfind('}')
        if a >= 0 and b > a:
            return json.loads(s[a:b + 1])
        raise


def structure_note(caption, transcript, ocr_text, info):
    """把内容结构化 → {summary, outline, quotes, tags}。融合：文案+口播+图OCR。"""
    nickname = info.get('nickname', '') or ''
    pieces = []
    if caption:
        pieces.append("【作者文案/正文】\n" + caption[:5000])
    if transcript:
        pieces.append("【语音转写】\n" + transcript[:6000])
    if ocr_text:
        pieces.append("【图片文字(OCR)】\n" + ocr_text[:3000])
    ctx = "\n\n".join(pieces) if pieces else f"作者：{nickname}"
    hint = "（已融合 文案+口播+图片文字）" if (caption and transcript and ocr_text) else \
          ("（含 文案+口播）" if (caption and transcript) else \
           ("（含 文案+图片文字）" if (caption and ocr_text) else \
            ("（仅 口播转写）" if transcript else "（仅 文案/正文）")))
    prompt = f"""你是一个内容拆解助手，请拆解下面这段内容{hint}，严格按照 JSON 返回：
{{
  "summary": "一句话总结（30字内）",
  "outline": ["核心要点1", "核心要点2", "核心要点3"],
  "quotes": ["值得记录的金句1", "金句2"],
  "tags": ["标签1", "标签2"]
}}
只输出 JSON，不要任何额外说明。
内容：
{ctx[:12000]}"""
    content = call_deepseek(prompt, os.environ['DEEPSEEK_KEY'])
    note = robust_json(content)
    if not isinstance(note, dict):
        note = {}
    # 标题：文案首行 → 摘要 → 作者
    title = (caption.split('\n')[0].strip()[:60]) or note.get('summary', '') or nickname
    return note, title


# ------------------------- 回调 zsks -------------------------
def post_ingest(payload):
    url = os.environ['WORKER_URL'].rstrip('/') + '/api/internal/ingest'
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {os.environ['INGEST_TOKEN']}",
                 "Content-Type": "application/json", "User-Agent": MOBILE_UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode('utf-8')[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8')[:200]
    except urllib.error.URLError as e:
        return 0, f"URLError:{e.reason}"


def fail(err):
    log(f"FAILED: {err[:200]}")
    st, body = post_ingest({"job_id": os.environ['JOB_ID'], "ok": False, "error": err[:200]})
    log(f"回写失败状态 status={st}")


import io  # noqa: 放在文件末尾以兼容 ocr_images 内的 io 引用

#!/usr/bin/env python3
# structure.py — zsks 消化脚本（GitHub Actions 中运行）
# 流程：展开抖音短链拿 aweme_id → 请求移动端分享页 → 提取 window._ROUTER_DATA →
#       定位 item_list[0] → 取文案(desc)/作者/封面/发布时间/话题 → DeepSeek 结构化 → 回调 /api/internal/ingest
# 铁律：GitHub 日志只打印 job_id / 状态 / 错误摘要，绝不打印笔记或视频文案内容。
import os, sys, json, urllib.request, urllib.error, re, time, subprocess, shutil

JOB_ID = os.environ['JOB_ID']
VIDEO_URL = os.environ['VIDEO_URL']        # 用户提交的抖音链接（短链/长链/或带文案的分享文本）
DEEPSEEK_KEY = os.environ['DEEPSEEK_KEY']
INGEST_TOKEN = os.environ['INGEST_TOKEN']
WORKER_URL = os.environ['WORKER_URL'].rstrip('/')

# 抖音分享页需移动端 UA，否则返回壳页面或重定向到 App。
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
# 可选：抖音登录态 Cookie（数据中心 IP 解壳必备）。在 GitHub Secrets 配置 DOUYIN_COOKIE。
# 无 Cookie 时，正常/大陆网络仍可解析；数据中心 IP（如 Actions 美区）会拿到壳页面 → 优雅降级提示。
DOUYIN_COOKIE = os.environ.get('DOUYIN_COOKIE', '')

URL_RE = re.compile(r'https?://[^\s，。\n]+')
VIDEO_ID_RE = re.compile(r'/video/(\d+)')
LONG_DIGIT_RE = re.compile(r'(\d{15,})')
SHARE_RE = re.compile(r'v\.douyin\.com|douyin\.com|douyin\.cn|iesdouyin\.com')


def log(msg):  # 仅打状态/摘要，不打内容
    print(f"[zsks][{JOB_ID[:8]}] {msg}", flush=True)


def http_get(url, headers=None, timeout=30, raw=False):
    h = {'User-Agent': MOBILE_UA, 'Accept-Language': 'zh-CN,zh;q=0.9'}
    if headers:
        h.update(headers)
    if DOUYIN_COOKIE:
        h['Cookie'] = DOUYIN_COOKIE
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        return data if raw else data.decode('utf-8', 'ignore')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 不自动跟随，便于读取 3xx Location


def extract_url(text):
    """从分享文本中提取 http(s) 链接；若本身就是纯链接则原样返回。"""
    m = URL_RE.search(text or '')
    return m.group(0) if m else (text or '').strip()


def resolve_aweme_id(url):
    """从抖音链接解析 aweme_id（支持短链 v.douyin.com 重定向 / 长链 / 纯数字）。"""
    m = VIDEO_ID_RE.search(url)
    if m:
        return m.group(1)
    # 短链：读取 3xx Location（不跟随），再从 Location 取 id
    try:
        opener = urllib.request.build_opener(_NoRedirect())
        req = urllib.request.Request(url, headers={'User-Agent': MOBILE_UA})
        with opener.open(req, timeout=20) as r:
            loc = r.headers.get('Location') or r.geturl()
    except urllib.error.HTTPError as e:
        loc = e.headers.get('Location') or ''
    except Exception:
        loc = ''
    if loc:
        m = VIDEO_ID_RE.search(loc)
        if m:
            return m.group(1)
    # 兜底：直接跟随重定向拿最终 URL
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': MOBILE_UA}), timeout=20) as r:
            final = r.geturl()
            m = VIDEO_ID_RE.search(final)
            if m:
                return m.group(1)
    except Exception:
        pass
    m = LONG_DIGIT_RE.search(url)
    if m:
        return m.group(1)
    raise ValueError(f"无法从链接解析 aweme_id: {url}")


def fetch_share_page(aweme_id):
    """请求移动端分享页 HTML（含内联 _ROUTER_DATA）。"""
    url = f"https://www.iesdouyin.com/share/video/{aweme_id}/"
    html = http_get(url, headers={
        'Referer': 'https://www.iesdouyin.com/',
        'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
    }, timeout=30)
    if not html:
        raise ValueError("分享页空响应")
    return html


def extract_router_data(html):
    """括号深度匹配提取 window._ROUTER_DATA 内联 JSON（参考开源 douyin_crawl）。"""
    marker = "window._ROUTER_DATA = "
    idx = html.find(marker)
    if idx < 0:
        idx = html.find("_ROUTER_DATA")
        if idx < 0:
            return None
        eq = html.find("=", idx)
        if eq < 0:
            return None
        start = eq + 1
    else:
        start = idx + len(marker)
    while start < len(html) and html[start].isspace():
        start += 1
    if start >= len(html) or html[start] != "{":
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(html)):
        c = html[i]
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])
                except Exception:
                    return None
    return None


def find_item(data):
    """从 _ROUTER_DATA 定位视频/图集 item（优先 loaderData 的 video_/note_ 分页结构）。"""
    if not isinstance(data, dict):
        return None, None
    loader = data.get('loaderData')
    if isinstance(loader, dict):
        for key, node in loader.items():
            if not isinstance(node, dict):
                continue
            if (key.startswith('video_') or key.startswith('note_')) and key.endswith('/page'):
                vinfo = node.get('videoInfoRes')
                if isinstance(vinfo, dict) and isinstance(vinfo.get('item_list'), list) and vinfo['item_list']:
                    kind = 'note' if key.startswith('note_') else 'video'
                    return vinfo['item_list'][0], kind
    # 兜底：递归搜索首个含 item_list 的节点
    found = _find_item_recursive(data)
    return found, None


def _find_item_recursive(obj):
    if isinstance(obj, list):
        for v in obj:
            r = _find_item_recursive(v)
            if r:
                return r
        return None
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get('item_list'), list) and obj['item_list'] and isinstance(obj['item_list'][0], dict) and 'desc' in obj['item_list'][0]:
        return obj['item_list'][0]
    for v in obj.values():
        r = _find_item_recursive(v)
        if r:
            return r
    return None


def extract_fields(item):
    """从 item 抽取博主知识库所需字段（文案 desc 是核心文本）。"""
    desc = (item.get('desc') or '').strip()
    author = item.get('author') or {}
    nickname = (author.get('nickname') or '').strip()
    sec_uid = (author.get('sec_uid') or '').strip()
    create_time = int(item.get('create_time') or 0)
    video = item.get('video') or {}
    cover = ''
    if isinstance(video.get('cover'), dict) and isinstance(video['cover'].get('url_list'), list) and video['cover']['url_list']:
        cover = video['cover']['url_list'][0]
    play_url = ''
    if isinstance(video.get('play_addr'), dict) and isinstance(video['play_addr'].get('url_list'), list) and video['play_addr']['url_list']:
        play_url = video['play_addr']['url_list'][0].replace('playwm', 'play')
    # 话题标签
    tags = []
    for t in (item.get('text_extra') or []):
        if isinstance(t, dict) and t.get('hashtag_name'):
            tags.append(t['hashtag_name'])
    stats = item.get('statistics') or {}
    return {
        'desc': desc, 'nickname': nickname, 'sec_uid': sec_uid,
        'create_time': create_time, 'cover': cover, 'play_url': play_url,
        'tags': tags, 'stats': stats,
    }


def fetch_douyin():
    """端到端：返回 (info, caption)。caption 即视频文案（抖音核心文本）。"""
    info, caption = {}, ''
    try:
        url = extract_url(VIDEO_URL)
        if not SHARE_RE.search(url):
            log("链接非抖音域名，跳过")
            return info, caption
        aweme_id = resolve_aweme_id(url)
        log(f"aweme_id={aweme_id}")
        html = fetch_share_page(aweme_id)
        data = extract_router_data(html)
        if data is None:
            log("未找到 _ROUTER_DATA（分享页为空或异常）")
            return info, caption
        item, kind = find_item(data)
        if not item:
            log("壳页面：_ROUTER_DATA 无 item_list（数据中心 IP 风控；需 DOUYIN_COOKIE 解壳）")
            return info, caption
        f = extract_fields(item)
        info = {
            'aweme_id': aweme_id,
            'url': VIDEO_URL.strip(),
            'kind': kind or 'video',
            'nickname': f['nickname'],
            'sec_uid': f['sec_uid'],
            'timestamp': f['create_time'],
            'thumbnail': f['cover'],
            'play_url': f['play_url'],
            'tags': f['tags'],
            'stats': f['stats'],
        }
        caption = f['desc']
        log(f"解析成功 kind={info['kind']} 作者={info['nickname']} 文案长度={len(caption)}")
    except urllib.error.HTTPError as e:
        if e.code in (403, 412):
            log(f"分享页 HTTPError={e.code}（抖音风控；需 DOUYIN_COOKIE 解壳）")
        else:
            log(f"分享页 HTTPError={e.code}")
    except urllib.error.URLError as e:
        log(f"网络错误={e.reason}")
    except Exception as e:
        log(f"解析异常={type(e).__name__}: {e}")
    return info, caption


def download_file(url, dest, timeout=180):
    """下载无水印视频（含音频）。需带 Referer 与登录 Cookie 以避开风控。"""
    h = {
        'User-Agent': MOBILE_UA,
        'Referer': 'https://www.douyin.com/',
        'Accept': '*/*',
    }
    if DOUYIN_COOKIE:
        h['Cookie'] = DOUYIN_COOKIE
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if not data or len(data) < 1024:
            log(f"视频下载内容过小({len(data) if data else 0}字节)，疑似被拦截")
            return False
        with open(dest, 'wb') as f:
            f.write(data)
        log(f"视频下载完成 {len(data)//1024}KB")
        return True
    except urllib.error.HTTPError as e:
        log(f"视频下载 HTTPError={e.code}")
        return False
    except Exception as e:
        log(f"视频下载失败: {type(e).__name__}: {e}")
        return False


def extract_audio(video_path, audio_path):
    """ffmpeg 抽音频为 16k 单声道 wav（Whisper 友好）。"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
             "-f", "wav", audio_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180,
        )
        if r.returncode != 0 or not os.path.exists(audio_path):
            log("ffmpeg 抽音频失败")
            return False
        return True
    except FileNotFoundError:
        log("未找到 ffmpeg（请在运行环境安装）")
        return False
    except Exception as e:
        log(f"抽音频异常: {e}")
        return False


def transcribe(audio_path, model_size="small"):
    """faster-whisper 语音转写（懒加载，仅在需要时 import）。"""
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


def call_deepseek(prompt):
    data = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }).encode('utf-8')
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions", data=data,
        headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json",
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


def structure(text, caption, info, has_transcript=False):
    """把内容文本结构化。视频：文案 + 语音转写；图文：仅文案。"""
    nickname = info.get('nickname', '')
    if not text:
        ctx = f"作者：{nickname}"
        hint = "（无任何文本，内容可能全在画面中，笔记较粗）"
    elif has_transcript:
        ctx = text[:8000]
        hint = "（已附视频语音转写 + 视频文案）"
    else:
        ctx = text[:6000]
        hint = "（仅视频文案，未做语音转写）"
    prompt = f"""你是一个内容拆解助手，请拆解下面这段抖音内容{hint}，严格按照 JSON 返回：
{{
  "summary": "一句话总结（30字内）",
  "points": ["核心观点1", "核心观点2"],
  "quotes": ["值得记录的金句1", "金句2"],
  "tags": ["标签1", "标签2"]
}}
只输出 JSON，不要任何额外说明。
内容：
{ctx}"""
    content = call_deepseek(prompt)
    note = robust_json(content)
    # 标题：取文案首行，缺省用摘要
    title = (caption.split('\n')[0].strip()[:60]) or note.get('summary', '') or nickname
    return note, title, nickname


def post_ingest(payload):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        f"{WORKER_URL}/api/internal/ingest", data=data,
        headers={"Authorization": f"Bearer {INGEST_TOKEN}", "Content-Type": "application/json",
                 "User-Agent": MOBILE_UA},
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
    st, body = post_ingest({"job_id": JOB_ID, "ok": False, "error": err[:200]})
    log(f"回写失败状态 status={st}")


def main():
    start = time.time()
    try:
        log("开始消化抖音链接")
        info, caption = fetch_douyin()
        if not info:
            fail("无法获取视频元数据（壳页面/链接失效/被风控；数据中心 IP 需在 GitHub Secrets 配置 DOUYIN_COOKIE）")
            return
        kind = info.get('kind', 'video')
        transcript = ''
        has_transcript = 0
        if kind == 'video':
            play_url = info.get('play_url', '')
            if play_url:
                vtmp = f"/tmp/zsks_{JOB_ID}.mp4"
                if download_file(play_url, vtmp):
                    atmp = f"/tmp/zsks_{JOB_ID}.wav"
                    if extract_audio(vtmp, atmp):
                        transcript = transcribe(atmp, os.environ.get('WHISPER_MODEL', 'small'))
                        if transcript:
                            has_transcript = 1
                    for t in (vtmp, atmp):
                        try:
                            os.remove(t)
                        except Exception:
                            pass
                else:
                    log("视频下载失败，回退到仅文案")
            else:
                log("无 play_url，回退到仅文案")
        elif kind == 'note':
            log("图文类型：无音频，使用文案文本")

        # 组合文本：视频 = 文案 + 转写；图文 = 文案
        combined = caption
        if transcript:
            combined = (caption + "\n\n" + transcript) if caption else transcript

        note, title, uploader = structure(combined, caption, info, has_transcript)
        has_text = 1 if caption else 0
        post = {
            "platform": "douyin",
            "aweme_id": info.get('aweme_id', ''),
            "url": info.get('url', ''),
            "kind": kind,
            "title": title or uploader,
            "publish_time": int(info.get('timestamp', 0) or 0),
            "cover": info.get('thumbnail', '') or '',
            "play_url": info.get('play_url', '') or '',
            "desc_text": caption,
            "has_text": has_text,
            "has_transcript": has_transcript,
            "has_subtitle": 0,
            "need_asr": 0,
        }
        blogger = {
            "platform": "douyin",
            "uid": info.get('sec_uid', '') or info.get('aweme_id', ''),
            "name": uploader or '',
        }
        minutes = round((time.time() - start) / 60, 2)
        st, body = post_ingest({
            "job_id": JOB_ID, "ok": True,
            "blogger": blogger, "post": post,
            "note": note, "transcript": transcript or caption,
            "actions_minutes": minutes,
        })
        log(f"ingest 回写 status={st} minutes={minutes} body={body[:120]}")
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")


if __name__ == '__main__':
    main()

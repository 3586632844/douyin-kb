#!/usr/bin/env python3
# extractors.py — 三平台内容抽取（video + article）。每个函数返回 (info, caption, images)
# info: {platform, external_id, url, title, publish_time, cover, play_url, nickname, uid, kind}
# caption: 作者文案(视频) 或 图文正文；images: 图片 URL 列表（图文类）
# 铁律：日志只打状态/摘要，不打印正文或文案。
import os, re, json, requests, urllib.parse
from common import log, http_get, ytdlp_json, resolve_url

DOUYIN_SHARE_RE = re.compile(r'v\.douyin\.com|douyin\.com|douyin\.cn|iesdouyin\.com')
BVID_RE = re.compile(r'BV[0-9A-Za-z]{10}')
BILI_CV_RE = re.compile(r'(?:article|cv)(\d+)')
XHS_RE = re.compile(r'xhslink\.com|xiaohongshu\.com')
URL_RE = re.compile(r'https?://[^\s，。\n]+')

UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


# ============================ 抖音 ============================
# 方案：参考开源无水印下载器（qgeng1465/douyin-watermark-free-downloader）。
# 无需登录：短链跟随重定向拿到 aweme_id → 拉 iesdouyin 分享页 → 解析 _ROUTER_DATA / RENDER_DATA
# → play_addr 替换 playwm→play 得到无水印地址。已在 GitHub Actions 美国 IP 实测可下载。
def extract_douyin(url):
    url = resolve_url(url)
    if not DOUYIN_SHARE_RE.search(url):
        return None, '', []
    aweme_id = _resolve_aweme_id(url)
    if not aweme_id:
        return None, '', []
    log(f"douyin aweme_id={aweme_id}")

    data = _douyin_share_page(aweme_id)
    if not data:
        # 兜底：yt-dlp 元数据
        m = ytdlp_json(url)
        if m:
            info = _yt_douyin_item(m, aweme_id, url)
            log(f"douyin 兜底(yt-dlp) 作者={info['nickname']}")
            return info, info['desc'], []
        return None, '', []

    info = _parse_douyin_data(data, aweme_id, url)
    if not info:
        top = list(data.keys())[:12] if isinstance(data, dict) else type(data).__name__
        log(f"抖音解析为空 顶层键={top} aweme节点={'有' if _douyin_walk(data,'aweme') else '无'} item_list={'有' if _douyin_walk(data,'item_list') else '无'}")
        return None, '', []
    caption = info['desc']
    images = info.get('images', [])
    log(f"douyin 解析成功 作者={info['nickname']} 文案长度={len(caption)}")
    return info, caption, images


def _resolve_aweme_id(url):
    m = re.search(r'/(?:video|slides|note)/(\d+)', url)
    if m:
        return m.group(1)
    try:
        s = requests.Session()
        r = s.get(url, headers={'User-Agent': UA_MOBILE}, allow_redirects=True, timeout=20)
        m = re.search(r'/(?:video|slides|note)/(\d+)', r.url or url)
        if m:
            return m.group(1)
    except Exception as e:
        log(f"抖音短链重定向失败: {type(e).__name__}")
    return None


def _douyin_share_page(aweme_id):
    try:
        u = f"https://www.iesdouyin.com/share/video/{aweme_id}/"
        r = requests.get(u, headers={'User-Agent': UA_MOBILE, 'Referer': 'https://www.iesdouyin.com/'}, timeout=25)
        if r.status_code != 200 or not r.text:
            log(f"抖音分享页 HTTP={r.status_code} 空")
            return None
        return _douyin_extract_json(r.text)
    except Exception as e:
        log(f"抖音分享页失败: {type(e).__name__}")
        return None


def _douyin_extract_json(text):
    # 与开源无水印下载器一致：_ROUTER_DATA（;? 兼容有无分号） → RENDER_DATA（URL 编码） → 兜底整段
    m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\});?\s*</script>", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r'<script\s+id="RENDER_DATA"\s+type="application/json"[^>]*>(.*?)</script>', text, re.S)
    if m:
        try:
            return json.loads(urllib.parse.unquote(m.group(1)))
        except Exception:
            pass
    m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*\})\s*;\s*$", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return None


def _douyin_find_aweme(data):
    # 与开源无水印下载器一致：优先按键名 aweme / item_list 深度搜索，再回退到含特征键的递归
    if not isinstance(data, dict):
        return None
    r = _douyin_walk(data, "aweme")
    if isinstance(r, dict):
        return r
    r = _douyin_walk(data, "item_list")
    if isinstance(r, list) and r and isinstance(r[0], dict):
        return r[0]
    if any(k in data for k in ("desc", "video", "images", "aweme_id")):
        return data

    def rec(o):
        if isinstance(o, dict):
            if any(k in o for k in ("desc", "video", "images", "aweme_id")):
                return o
            for v in o.values():
                r = rec(v)
                if r is not None:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = rec(v)
                if r is not None:
                    return r
        return None
    return rec(data)


def _douyin_walk(obj, target):
    if isinstance(obj, dict):
        if target in obj:
            return obj[target]
        for v in obj.values():
            r = _douyin_walk(v, target)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _douyin_walk(v, target)
            if r is not None:
                return r
    return None


def _douyin_first_url(node):
    if not isinstance(node, dict):
        return None
    for k in ("url_list", "urlList"):
        v = node.get(k)
        if isinstance(v, list) and v:
            return v[0]
    for k in ("url", "Url", "src"):
        v = node.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    return None


def _yt_douyin_item(m, aweme_id, url):
    return {
        'platform': 'douyin', 'external_id': aweme_id, 'url': url.strip(),
        'title': (m.get('description') or '').strip()[:60], 'publish_time': int(m.get('timestamp') or 0),
        'cover': m.get('thumbnail') or '', 'play_url': m.get('url') or '',
        'nickname': (m.get('uploader') or '').strip(), 'uid': str(m.get('uploader_id') or ''),
        'kind': 'video', 'tags': (m.get('tags') or [])[:8],
        'desc': (m.get('description') or '').strip(), 'images': [],
    }


def _parse_douyin_data(data, aweme_id, url):
    detail = _douyin_find_aweme(data)
    if not isinstance(detail, dict):
        return None
    desc = (detail.get('desc') or (detail.get('share_info') or {}).get('share_title') or '').strip()
    author = detail.get('author') or {}
    nickname = (author.get('nickname') or author.get('unique_id') or '').strip()
    uid = (author.get('sec_uid') or str(author.get('uid') or '') or '').strip()
    video = detail.get('video') or {}
    play_url = ''
    if isinstance(video, dict):
        play = video.get('play_addr') or video.get('playAddr') or {}
        u = _douyin_first_url(play)
        if u:
            play_url = u.replace('/playwm/', '/play/').replace('playwm', 'play')
    cover = _douyin_first_url(video.get('cover') or {}) if isinstance(video, dict) else ''
    images = []
    for im in (detail.get('images') or []):
        if isinstance(im, dict):
            u = _douyin_first_url(im)
            if u:
                images.append(u)
    tags = [t['hashtag_name'] for t in (detail.get('text_extra') or []) if isinstance(t, dict) and t.get('hashtag_name')]
    return {
        'platform': 'douyin', 'external_id': aweme_id, 'url': url.strip(),
        'title': desc.split('\n')[0][:60], 'publish_time': int(detail.get('create_time') or 0),
        'cover': cover or '', 'play_url': play_url, 'nickname': nickname, 'uid': uid,
        'kind': 'note' if (images and not play_url) else 'video',
        'tags': tags, 'desc': desc, 'images': images,
    }


# ============================ B站 ============================
def extract_bilibili(url):
    url = resolve_url(url)
    m = BVID_RE.search(url)
    cv = BILI_CV_RE.search(url)
    if m:
        return _bili_video(m.group(0), url)
    if cv:
        return _bili_article(cv.group(1), url)
    # 兜底：尝试 yt-dlp 元数据
    return _bili_fallback(url)


def _bili_api(path, params=''):
    base = "https://api.bilibili.com"
    cookie = os.environ.get('BILI_COOKIE', '')
    h = {'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
         'Referer': 'https://www.bilibili.com/'}
    if cookie:
        h['Cookie'] = cookie
    try:
        import urllib.parse
        u = base + path + (('?' + params) if params else '')
        return json.loads(http_get(u, headers=h, timeout=25))
    except Exception as e:
        log(f"B站接口失败 {path}: {type(e).__name__}")
        return None


def _bili_video(bvid, url):
    d = _bili_api("/x/web-interface/view", f"bvid={bvid}")
    if not d or d.get('code') != 0 or not d.get('data'):
        log("B站视频接口无数据（可能需 BILI_COOKIE）")
        return _bili_fallback(url, bvid=bvid)
    v = d['data']
    owner = v.get('owner') or {}
    info = {
        'platform': 'bilibili', 'external_id': bvid, 'url': url.strip(),
        'title': (v.get('title') or '').strip(), 'publish_time': int(v.get('pubdate') or 0),
        'cover': v.get('pic') or '', 'play_url': '', 'nickname': (owner.get('name') or '').strip(),
        'uid': str(owner.get('mid') or ''), 'kind': 'video', 'tags': [],
        'desc': (v.get('desc') or '').strip(),
    }
    log(f"B站视频 bvid={bvid} 作者={info['nickname']} 标题长度={len(info['title'])}")
    return info, info['desc'], []


def _bili_article(cvid, url):
    d = _bili_api("/x/article/view", f"id={cvid}")
    if not d or d.get('code') != 0 or not d.get('data'):
        # 回退：抓取专栏页用 readability
        return _bili_fallback(url, cvid=cvid, article=True)
    v = d['data']
    author = v.get('author') or {}
    content_html = v.get('content') or ''
    from common import extract_article
    text, images = extract_article(content_html, url)
    info = {
        'platform': 'bilibili', 'external_id': cvid, 'url': url.strip(),
        'title': (v.get('title') or '').strip(), 'publish_time': int(v.get('publish_time') or 0),
        'cover': (v.get('banner_url') or ''), 'play_url': '', 'nickname': (author.get('name') or '').strip(),
        'uid': str(author.get('mid') or ''), 'kind': 'article',
        'tags': [t.get('name', '') for t in (v.get('tags') or []) if isinstance(t, dict)],
        'desc': text[:4000],
    }
    log(f"B站专栏 cvid={cvid} 正文长度={len(text)}")
    return info, text, images


def _bili_fallback(url, bvid=None, cvid=None, article=False):
    m = ytdlp_json(url)
    if not m:
        return None, '', []
    info = {
        'platform': 'bilibili',
        'external_id': bvid or cvid or (m.get('id') or ''),
        'url': url.strip(), 'title': (m.get('title') or '').strip(),
        'publish_time': int(m.get('timestamp') or 0),
        'cover': m.get('thumbnail') or '', 'play_url': '',
        'nickname': (m.get('uploader') or '').strip(),
        'uid': str(m.get('channel_id') or m.get('uploader_id') or ''),
        'kind': 'article' if article else 'video',
        'tags': (m.get('tags') or [])[:8],
        'desc': (m.get('description') or '').strip(),
    }
    log(f"B站兜底(yt-dlp) external_id={info['external_id']}")
    return info, info['desc'], []


# ============================ 小红书 ============================
def extract_xiaohongshu(url):
    url = resolve_url(url)
    if not XHS_RE.search(url):
        return None, '', []
    html = http_get(url, headers={
        'User-Agent': UA_MOBILE, 'Accept': 'text/html,*/*;q=0.8'})
    state = _xhs_state(html)
    if not state:
        return _xhs_fallback(url)
    note = _xhs_note(state)
    if not note:
        return _xhs_fallback(url)
    kind = note.get('kind', 'note')
    caption = (note.get('desc') or '').strip()
    images = note.get('images', [])
    info = {
        'platform': 'xiaohongshu', 'external_id': note.get('id', ''), 'url': url.strip(),
        'title': (note.get('title') or caption.split('\n')[0])[:60],
        'publish_time': int(note.get('time') or 0),
        'cover': images[0] if images else '', 'play_url': note.get('play_url', ''),
        'nickname': (note.get('nickname') or '').strip(),
        'uid': (note.get('uid') or '').strip(), 'kind': kind,
        'tags': note.get('tags', []), 'desc': caption,
    }
    log(f"小红书 note={info['external_id']} kind={kind} 作者={info['nickname']} 文案长度={len(caption)}")
    return info, caption, images


def _xhs_state(html):
    if not html:
        return None
    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*', html)
    if not m:
        return None
    start = m.end()
    depth = 0; in_str = False; esc = False; end = None
    for i in range(start, len(html)):
        c = html[i]
        if esc:
            esc = False; continue
        if c == '\\':
            esc = True; continue
        if c == '"':
            in_str = not in_str; continue
        if in_str:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end = i + 1; break
    if not end:
        return None
    try:
        return json.loads(html[start:end])
    except Exception:
        return None


def _xhs_note(state):
    note_detail = state.get('note', {}).get('noteDetailMap', {})
    for k, v in note_detail.items():
        if isinstance(v, dict) and v.get('note'):
            n = v['note']
            return _xhs_normalize(n)
    cd = state.get('note', {}).get('currentNote')
    if cd:
        return _xhs_normalize(cd)
    return None


def _xhs_normalize(n):
    images = []
    for im in (n.get('imageList') or []):
        if isinstance(im, dict):
            u = im.get('url') or im.get('urlDefault') or (im.get('info') or {}).get('url')
            if u:
                images.append(u)
    video = n.get('video') or {}
    play_url = ''
    if isinstance(video, dict):
        if isinstance(video.get('media'), dict) and isinstance(video['media'].get('stream'), list):
            for s in video['media']['stream']:
                if isinstance(s, dict) and s.get('url'):
                    play_url = s['url']; break
    return {
        'id': n.get('noteId') or n.get('id') or '',
        'title': n.get('title') or '', 'desc': n.get('desc') or '',
        'time': n.get('time') or 0,
        'nickname': (n.get('user') or {}).get('nickName') or '',
        'uid': (n.get('user') or {}).get('userId') or '',
        'kind': 'video' if play_url else 'note',
        'tags': [t.get('name', '') for t in (n.get('tagList') or []) if isinstance(t, dict)],
        'images': images, 'play_url': play_url,
    }


def _xhs_fallback(url):
    m = ytdlp_json(url)
    if not m:
        return None, '', []
    info = {
        'platform': 'xiaohongshu', 'external_id': (m.get('id') or ''),
        'url': url.strip(), 'title': (m.get('title') or '').strip(),
        'publish_time': int(m.get('timestamp') or 0),
        'cover': m.get('thumbnail') or '', 'play_url': m.get('url') or '',
        'nickname': (m.get('uploader') or '').strip(),
        'uid': str(m.get('uploader_id') or ''), 'kind': 'video' if m.get('url') else 'note',
        'tags': (m.get('tags') or [])[:8], 'desc': (m.get('description') or '').strip(),
    }
    log(f"小红书兜底(yt-dlp) external_id={info['external_id']}")
    return info, info['desc'], []

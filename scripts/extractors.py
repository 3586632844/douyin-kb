#!/usr/bin/env python3
# extractors.py — 三平台内容抽取（video + article）。每个函数返回 (info, caption, images)
# info: {platform, external_id, url, title, publish_time, cover, play_url, nickname, uid, kind}
# caption: 作者文案(视频) 或 图文正文；images: 图片 URL 列表（图文类）
# 铁律：日志只打状态/摘要，不打印正文或文案。
import os, re, json
from common import log, http_get, ytdlp_json, resolve_url

DOUYIN_SHARE_RE = re.compile(r'v\.douyin\.com|douyin\.com|douyin\.cn|iesdouyin\.com')
BVID_RE = re.compile(r'BV[0-9A-Za-z]{10}')
BILI_CV_RE = re.compile(r'(?:article|cv)(\d+)')
XHS_RE = re.compile(r'xhslink\.com|xiaohongshu\.com')
URL_RE = re.compile(r'https?://[^\s，。\n]+')


# ============================ 抖音 ============================
def extract_douyin(url):
    from common import MOBILE_UA, DESKTOP_UA
    url = resolve_url(url)
    if not DOUYIN_SHARE_RE.search(url):
        return None, '', []
    aweme_id = _resolve_aweme_id(url)
    if not aweme_id:
        return None, '', []
    log(f"douyin aweme_id={aweme_id}")

    item = None
    # 方法1（首选，无登录、数据中心 IP 友好）：iesdouyin iteminfo 接口
    item = _douyin_iteminfo(aweme_id)
    # 方法2（兜底）：分享页内联 _ROUTER_DATA
    if not item:
        html = http_get(f"https://www.iesdouyin.com/share/video/{aweme_id}/", headers={
            'Referer': 'https://www.iesdouyin.com/', 'Accept': 'text/html,*/*;q=0.8'})
        data = _router_data(html) if html else None
        item, _ = _find_item(data) if data else (None, None)
    # 方法3（最后兜底）：yt-dlp 元数据（可能需要 Cookie）
    if not item:
        m = ytdlp_json(url)
        if m:
            item = _yt_douyin_item(m, aweme_id, url)

    if not item:
        log("抖音解析失败（IP风控/链接失效；iteminfo 与分享页均无数据）")
        return None, '', []

    f = _douyin_fields(item, aweme_id, url)
    caption = f['desc']
    images = f.get('images', [])
    log(f"douyin 解析成功 作者={f['nickname']} 文案长度={len(caption)}")
    return f, caption, images


def _douyin_iteminfo(aweme_id):
    """无登录：依次尝试多个抖音接口，返回 item 字典或 None。
    方法A：老接口 iesdouyin iteminfo（部分环境仍可用）
    方法B：新接口 /aweme/v1/web/aweme/detail（风控权重极低，成功率近100%，开源下载器核心）
    """
    # 方法A：老接口
    try:
        u = f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={aweme_id}"
        body = http_get(u, headers={
            'User-Agent': DESKTOP_UA,
            'Referer': 'https://www.iesdouyin.com/',
            'Accept': 'application/json,text/plain,*/*',
        }, timeout=25)
        if body:
            d = json.loads(body)
            il = d.get('item_list') or []
            if not il and isinstance(d.get('data'), dict):
                il = d.get('data', {}).get('item_list', []) or []
            if il and isinstance(il[0], dict) and 'desc' in il[0]:
                return il[0]
    except Exception as e:
        log(f"iteminfo(老) 失败: {type(e).__name__}")
    # 方法B：新接口（移动端 UA，风控权重低）
    try:
        u = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={aweme_id}&device_platform=webapp&aid=6383&channel=channel_pc_web"
        body = http_get(u, headers={
            'User-Agent': MOBILE_UA,
            'Referer': 'https://www.douyin.com/',
            'Accept': 'application/json,text/plain,*/*',
        }, timeout=25)
        if body:
            d = json.loads(body)
            det = d.get('aweme_detail') or (d.get('data', {}) or {}).get('aweme_detail')
            if isinstance(det, dict) and 'desc' in det:
                return det
    except Exception as e:
        log(f"aweme/detail 失败: {type(e).__name__}")
    return None


def _yt_douyin_item(m, aweme_id, url):
    """用 yt-dlp 元数据构造与 _douyin_fields 兼容的 item 结构。"""
    return {
        'desc': (m.get('description') or '').strip(),
        'author': {'nickname': (m.get('uploader') or '').strip(),
                   'sec_uid': str(m.get('uploader_id') or '')},
        'create_time': int(m.get('timestamp') or 0),
        'video': {
            'cover': {'url_list': [m['thumbnail']]} if m.get('thumbnail') else {},
            'play_addr': {'url_list': [m['url']]} if m.get('url') else {},
        },
        'image_list': [],
        'text_extra': [],
    }


def _resolve_aweme_id(url):
    m = re.search(r'/video/(\d+)', url)
    if m:
        return m.group(1)
    # 短链：跟随重定向拿最终 URL
    try:
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
        req = urllib.request.Request(url, headers={'User-Agent': MOBILE_UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            final = r.geturl()
        m = re.search(r'/video/(\d+)', final)
        if m:
            return m.group(1)
    except Exception:
        pass
    m = re.search(r'(\d{15,})', url)
    return m.group(1) if m else None


def _router_data(html):
    if not html:
        return None
    marker = "window._ROUTER_DATA = "
    idx = html.find(marker)
    if idx < 0:
        idx = html.find("_ROUTER_DATA")
        if idx < 0:
            return None
        eq = html.find("=", idx)
        start = eq + 1
    else:
        start = idx + len(marker)
    while start < len(html) and html[start].isspace():
        start += 1
    if start >= len(html) or html[start] != '{':
        return None
    depth = 0; in_str = False; esc = False
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
                try:
                    return json.loads(html[start:i + 1])
                except Exception:
                    return None
    return None


def _find_item(data):
    if not isinstance(data, dict):
        return None, None
    loader = data.get('loaderData')
    if isinstance(loader, dict):
        for key, node in loader.items():
            if isinstance(node, dict) and (key.startswith('video_') or key.startswith('note_')) and key.endswith('/page'):
                vinfo = node.get('videoInfoRes')
                if isinstance(vinfo, dict) and vinfo.get('item_list'):
                    return vinfo['item_list'][0], ('note' if key.startswith('note_') else 'video')
    return _rec(data), None


def _rec(obj):
    if isinstance(obj, list):
        for v in obj:
            r = _rec(v)
            if r:
                return r
        return None
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get('item_list'), list) and obj['item_list'] and isinstance(obj['item_list'][0], dict) and 'desc' in obj['item_list'][0]:
        return obj['item_list'][0]
    for v in obj.values():
        r = _rec(v)
        if r:
            return r
    return None


def _douyin_fields(item, aweme_id, url):
    desc = (item.get('desc') or '').strip()
    author = item.get('author') or {}
    video = item.get('video') or {}
    cover = ''
    if isinstance(video.get('cover'), dict) and video['cover'].get('url_list'):
        cover = video['cover']['url_list'][0]
    play_url = ''
    if isinstance(video.get('play_addr'), dict) and video['play_addr'].get('url_list'):
        play_url = video['play_addr']['url_list'][0].replace('playwm', 'play')
    images = []
    if isinstance(item.get('image_list'), list):
        for im in item['image_list']:
            if isinstance(im, dict) and im.get('url_list'):
                images.append(im['url_list'][0])
    tags = [t['hashtag_name'] for t in (item.get('text_extra') or []) if isinstance(t, dict) and t.get('hashtag_name')]
    return {
        'platform': 'douyin', 'external_id': aweme_id, 'url': url.strip(),
        'title': desc.split('\n')[0][:60], 'publish_time': int(item.get('create_time') or 0),
        'cover': cover, 'play_url': play_url, 'nickname': (author.get('nickname') or '').strip(),
        'uid': (author.get('sec_uid') or '').strip(), 'kind': 'note' if images and not play_url else 'video',
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
        'User-Agent': "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        'Accept': 'text/html,*/*;q=0.8'})
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
    # 常见结构：state.note.noteDetailMap[NOTE_ID].note 或 state.note.currentNote
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

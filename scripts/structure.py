#!/usr/bin/env python3
# 用 Cloudflare Workers AI 把转写稿结构化为: 主题 / 内容大纲 / 摘要 / 关键词
# 输出: work/structure.json, work/summary.md(含大纲+摘要+全文), work/meta.json(供 D1 索引)
# 若未配置 CF 凭证或调用失败，自动回退到规则总结，保证流水线不中断。
import os, sys, re, json, datetime, urllib.request
from collections import Counter

CJK = lambda c: '\u4e00' <= c <= '\u9fff'

def split_sentences(text):
    parts = re.split(r'(?<=[。！？!?；;])\s*', text)
    return [p.strip() for p in parts if p.strip()]

def rule_based(text, n=6):
    sents = split_sentences(text)
    freq = Counter(c for c in text if CJK(c))
    def score(i, s):
        return sum(freq.get(c, 0) for c in s if CJK(c)) + (20 - min(i, 20)) * 0.4
    ranked = sorted(range(len(sents)), key=lambda i: score(i, sents[i]), reverse=True)
    top = sorted(ranked[:max(n, len(sents) // 12)])
    summary = "。".join(sents[i] for i in top).rstrip("。") + "。"
    keywords = [w for w, _ in freq.most_common(20) if freq[w] > 1][:15]
    return summary, keywords

def call_workers_ai(transcript, title, model, account, token):
    prompt = (
        "你是一名中文内容分析助手。请分析下面的短视频口播转写稿，"
        "严格只输出一个 JSON 对象（不要任何解释、不要 markdown 代码块），结构如下：\n"
        "{\n"
        '  "topic": "一句话主题分类，例如: AI变现 / 副业思路 / 工具教程 / 认知思维",\n'
        '  "outline": [{"heading":"章节标题","points":["要点1","要点2"]}],\n'
        '  "summary": "200字以内的中文摘要"\n'
        "}\n\n"
        f"视频标题：{title}\n\n转写稿：\n{transcript}\n"
    )
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    body = json.dumps({
        "messages": [
            {"role": "system", "content": "你是严谨的中文内容分析助手，只输出 JSON。"},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 1500,
        "temperature": 0.3
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    })
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    # 兼容多种返回形态:
    #  - 常见: result.response 是模型输出的 JSON 字符串
    #  - 部分情况下 Cloudflare 已把模型 JSON 自动解析为 dict, 直接就是结构化对象
    #  - 个别模型放在 result.content / result.text
    result = data.get("result") or {}
    raw = result.get("response") or result.get("content") or result.get("text")
    if isinstance(raw, dict):
        return raw  # 已经是结构化 JSON 对象, 直接返回
    if not isinstance(raw, str):
        raw = str(raw)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("AI 未返回 JSON")
    return json.loads(m.group(0))

def main():
    txt_path = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "."
    title_file = os.path.join(out_dir, "title.txt")
    title = open(title_file, encoding="utf-8").read().strip() if os.path.exists(title_file) else ""
    text = open(txt_path, encoding="utf-8").read()

    topic, outline, summary, keywords = "", [], "", []
    acct = os.environ.get("CF_ACCOUNT_ID", "")
    token = os.environ.get("CF_API_TOKEN", "")
    model = os.environ.get("WORKERS_AI_MODEL", "@cf/meta/llama-3.1-8b-instruct")
    if acct and token:
        try:
            ai = call_workers_ai(text[:4000], title, model, acct, token)
            topic = (ai.get("topic") or "").strip()
            outline = ai.get("outline") or []
            summary = (ai.get("summary") or "").strip()
            print("==> Workers AI 结构化成功")
        except Exception as e:
            print("!! Workers AI 失败，回退规则总结:", e)
    if not summary:
        summary, keywords = rule_based(text)
    if not keywords:
        keywords = [w for w, _ in Counter(c for c in text if CJK(c)).most_common(20) if _ > 1][:15]
    if not topic:
        topic = "未分类"

    dur = 0
    srt = os.path.join(out_dir, "transcript.srt")
    if os.path.exists(srt):
        mt = re.findall(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", open(srt, encoding="utf-8").read())
        if mt:
            h, mi, s, ms = map(int, mt[-1]); dur = h * 3600 + mi * 60 + s
    words = len([c for c in text if CJK(c)])
    now = int(datetime.datetime.now().timestamp())
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    structure = {"title": title, "topic": topic, "outline": outline,
                 "summary": summary, "keywords": keywords}
    open(os.path.join(out_dir, "structure.json"), "w", encoding="utf-8").write(
        json.dumps(structure, ensure_ascii=False, indent=2))

    md = [f"# {title or '抖音口播视频'}", "",
          f"- 入库时间：{now_str}",
          f"- 时长：{dur // 60} 分 {dur % 60} 秒",
          f"- 主题：{topic}",
          f"- 字数：约 {words} 字", "",
          "## 内容大纲", ""]
    if outline:
        for i, sec in enumerate(outline, 1):
            h = sec.get("heading", f"章节{i}")
            md.append(f"{i}. **{h}**")
            for p in sec.get("points", []):
                md.append(f"   - {p}")
    else:
        md.append("(暂无大纲)")
    md += ["", "## 摘要", "", summary, "", "## 关键词", "",
           "、".join(keywords), "", "## 完整转写稿", "", "```text", text, "```", ""]
    open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8").write("\n".join(md))

    meta = {"title": title, "topic": topic, "outline": outline, "summary": summary,
            "keywords": keywords, "duration": dur, "words": words, "created_at": now}
    open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8").write(
        json.dumps(meta, ensure_ascii=False, indent=2))
    print("==> 结构化完成")

if __name__ == "__main__":
    main()

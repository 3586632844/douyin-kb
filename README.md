# 抖音口播知识库（全免费架构）

把抖音口播视频自动转成**结构化、可检索**的知识库：下载无水印视频 → 语音转写 → Workers AI 生成主题/大纲/摘要 → 存入 Cloudflare R2+D1 → 在固定网址上按**时间/主题**浏览。后续可平滑升级为「接入 AI 当大脑」的 RAG 问答。

## 架构

```
你贴抖音链接 (Cloudflare Pages 表单)
      │  repository_dispatch
      ▼
GitHub Actions (公开仓库 · 无限免费分钟)
  ① 下载无水印视频         scripts/download.sh   (复用开源下载器)
  ② ffmpeg 抽音频
  ③ faster-whisper 转写    scripts/transcribe.py  (INT8/CPU, HF 镜像)
  ④ Workers AI 结构化      scripts/structure.py   (主题+大纲+摘要+关键词)
  ⑤ 上传 R2 + 写 D1        scripts/upload.sh
      │
      ├─► D1 + R2  ──► Pages 按【时间/主题/大纲】浏览
      └─► (预留) Vectorize/AutoRAG ──► 未来「问 AI 大脑」RAG 问答
```

## 免费额度（为什么能全免费）

| 组件 | 免费额度 | 角色 |
|------|---------|------|
| GitHub Actions（公开仓库） | **无限分钟** | 跑下载+转写+结构化 |
| Cloudflare Pages | 免费、自带域名、无限请求 | 固定网址 + 前端 |
| Cloudflare R2 | 10GB 免费存储 | 存转写稿/总结 |
| Cloudflare D1 | 免费额度（每日读/写限制） | 索引元数据 |
| Cloudflare Workers AI | 每日免费额度 | 生成大纲/摘要 |
| HF 镜像 hf-mirror.com | 免费 | 拉取 whisper 模型 |

## 一键部署步骤

### 1. GitHub（算力）
1. 把本仓库 **公开** 克隆/推送到你的 GitHub（公开库才有无限 Actions 分钟）。
2. 在仓库 **Settings → Secrets and variables → Actions → Repository secrets** 添加：
   - `CF_ACCOUNT_ID` — Cloudflare 账户 ID
   - `CF_API_TOKEN` — Cloudflare API Token（需 **D1:Edit** + **AI:Edit** 权限，以及账户访问）
   - `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` — R2 API 令牌
   - `R2_BUCKET` — R2 桶名（如 `douyin-kb`）
   - `D1_NAME` — D1 数据库名（如 `douyin_kb`）
   - `WORKERS_AI_MODEL`（可选，默认 `@cf/meta/llama-3.1-8b-instruct`）

### 2. Cloudflare（存储 + 前端）
1. 创建 R2 桶：`douyin-kb`。
2. 创建 D1 数据库：`douyin_kb`，并执行 `schema.sql` 建表。
3. 部署 Pages：连接本仓库，`build output` 设为 `public`，并配置绑定（见 `wrangler.toml`）：
   - D1 绑定 `DB` → `douyin_kb`
   - R2 绑定 `BUCKET` → `douyin-kb`
4. 在 Pages **Settings → Environment variables** 添加（**对 Functions 可见**）：
   - `GH_TOKEN` — 具备 `workflow` 权限的 GitHub PAT
   - `GH_REPO` — `你的用户名/仓库名`

### 3. 使用
打开 Pages 网址 → 粘贴抖音链接 → 提交 → 约 2–3 分钟后出现在列表 → 点击查看大纲/摘要/全文。可用主题筛选、按时间排序。

## ⚠️ 关键风险：GitHub 算力在美国，抖音可能限流

GitHub Actions runner 位于**美国**，抖音分享页下载对境外 IP 可能限流/返回不同内容。本仓库在能直连抖音的环境下验证通过，但美国 IP 存在失败可能。

缓解方案（按推荐度）：
1. **先实测**：用公开仓库跑一次，多数开源下载器托管在美也能工作，大概率可用。
2. **Cloudflare 中转**：若被墙，加一个 Cloudflare Worker 反代抖音视频流回 GitHub（注意 Worker 响应体/CPU 限制，大文件有风险）。
3. **职责拆分**：下载留在能连抖音的环境，GitHub 只做转写+结构化。

> 转写那一步已用 `hf-mirror.com` 绕过 HuggingFace 墙；若 HF 镜像也不通，在 `process.yml` 里把 `HF_ENDPOINT` 换成其它国内镜像。

## 数据模型（D1 索引）

| 字段 | 说明 | 检索用途 |
|------|------|---------|
| `created_at` | 入库时间戳 | 按时间排序/筛选 |
| `topic` | 主题分类 | 按主题筛选 |
| `outline` | 内容大纲（JSON） | 内容结构定位 |
| `summary` / `keywords` | 摘要 / 关键词 | 列表预览 |
| `r2_key` | R2 目录前缀 | 点开读全文 |

R2 中每条视频存 3 个文件：`videos/<id>/transcript.txt`、`summary.md`、`structure.json`——其中 `transcript.txt` 即为未来 RAG 的语料。

## 下一步（已预留，本次未做）：接入 AI 当「大脑」

知识库已 RAG-ready（全文在 R2）。要支持「直接问 AI，它从知识里回答」，二选一：
- **Cloudflare AutoRAG（最简）**：绑定 R2 桶自动向量化 + 接 Workers AI 问答，免费额度内可行。
- **手动 Vectorize + Workers AI**：用 `@cf/...` 嵌入模型写入 Vectorize，问答时检索 Top-K 片段喂给 LLM。

届时新增一个 Pages Function（如 `/api/ask`）即可，无需改动现有入库链路。

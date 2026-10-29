# 抖音口播知识库（全免费架构）

把抖音口播视频自动转成**结构化、可检索**的知识库：下载无水印视频 → 语音转写 → Workers AI 生成主题/大纲/摘要 → 存入 Cloudflare R2+D1 → 在固定网址上按**时间/主题**浏览。后续可平滑升级为「接入 AI 当大脑」的 RAG 问答。

## 实测结论（最重要）

> **GitHub 美国 runner 能连抖音并完整下载，原担心的「地理墙 / geo-block」风险不存在。**

已用 `connectivity-test.yml` + `process.yml` 在 GitHub 公开仓库的 Azure 美国 runner（centralus / eastus2 / westus3 多区域）实测：
- 解析抖音短链 → 访问分享页 → 下载 **89.4MB 无水印视频** 成功；
- 端到端：`下载 → ffmpeg 抽音频 → faster-whisper 中文转写 → Workers AI 结构化 → 上传 R2 → 写入 D1` 全链路 **`conclusion=success`**。
- 早期出现的 `HTTP=000` 是脚本 bug（短链解析多行匹配），非网络限制；修复后稳定通过。

因此**无需** Cloudflare Worker 代理或 Oracle VM 兜底，原架构（GitHub Actions 直接下载）可直接成立。

## 架构

```
你贴抖音链接 (Cloudflare Pages 表单)
      │  repository_dispatch
      ▼
GitHub Actions (公开仓库 · 无限免费分钟 · 美国 runner)
  ① 下载无水印视频         scripts/download.sh   (解析短链 + 开源下载器 + 3 次重试)
  ② ffmpeg 抽音频
  ③ faster-whisper 转写    scripts/transcribe.py  (INT8/CPU, 官方 HF 源)
  ④ Workers AI 结构化      scripts/structure.py   (主题+大纲+摘要+关键词)
  ⑤ 上传 R2 + 写 D1        scripts/upload.sh      (wrangler)
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
| HuggingFace | 免费 | 拉取 whisper 模型（美国 runner 用官方源 `huggingface.co` 即可） |

## 一键部署步骤

### 1. GitHub（算力）
1. 把本仓库 **公开** 克隆/推送到你的 GitHub（公开库才有无限 Actions 分钟）。
2. 在仓库 **Settings → Secrets and variables → Actions → Repository secrets** 添加：
   - `CF_ACCOUNT_ID` — Cloudflare 账户 ID
   - `CF_API_TOKEN` — Cloudflare API Token（**必须含 R2:Edit + D1:Edit + AI:Edit + Pages:Edit** 权限；**没有 Pages:Edit 会导致前端部署失败**）
   - `R2_BUCKET` — R2 桶名（如 `douyin-kb`）
   - `D1_NAME` — D1 数据库名（如 `douyin_kb`）
   - `GH_PAT`（可选，供自动部署前端用）— 具备 `workflow` 权限的 GitHub PAT
   - `WORKERS_AI_MODEL`（可选，默认 `@cf/meta/llama-3.1-8b-instruct`）

### 2. Cloudflare（存储）
1. 创建 R2 桶：`douyin-kb`。
2. 创建 D1 数据库：`douyin_kb`，并执行 `schema.sql` 建表。
3. （推荐·最简）**Cloudflare 控制台 → Workers & Pages → 创建 → 连接到 Git → 选本仓库**，构建命令留空、构建输出目录填 `public`。
   然后在项目 **设置 → 函数 → D1 绑定** 加 `DB → douyin_kb`；**R2 绑定** 加 `BUCKET → douyin-kb`；**环境变量（Functions 可见）** 加：
   - `GH_TOKEN` — 你的 GitHub PAT（供 submit.js 触发流水线）
   - `GH_REPO` — `你的用户名/仓库名`
4. （可选·自动部署）本仓库已带 `deploy-pages.yml`，会在有 `Pages:Edit` 权限的 `CF_API_TOKEN` 下自动 `wrangler pages deploy`。

> 也可以用 Wrangler 本地部署：`wrangler pages deploy public --project-name douyin-kb`（需 `wrangler.toml` 中 D1/R2 绑定与 `[vars]` 已配置好）。

### 3. 使用
打开 Pages 网址 → 粘贴抖音链接 → 提交 → 约 2–3 分钟后出现在列表 → 点击查看大纲/摘要/全文。可用主题筛选、按时间排序。

> **当前部署状态（2026-10-29 实测）**
> - 前端已上线：**https://a4bafbe2.douyin-kb.pages.dev**
> - 接口已验证：首页 `HTTP=200`；`/api/list` 正常返回 D1 索引；`/api/view?id=1793241036` 正常返回 R2 全文大纲+摘要。
> - 端到端管线（`process.yml` + `deploy-pages.yml`）均 `conclusion=success`；Cloudflare 全权限令牌下自动部署前端已跑通（先前遇到的 `create_http=500` 为 Cloudflare 侧偶发错误，重试即 409→部署成功）。

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

## ⚠️ 安全提醒

本项目所需的 `CF_API_TOKEN` 与 GitHub PAT 属于敏感凭证。**切勿在聊天/代码中明文粘贴**——
一旦泄露请立即到对应平台吊销并重新生成（Cloudflare: My Profile → API Tokens；GitHub: Settings → Developer settings → Personal access tokens）。

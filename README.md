# zsks-runner（重活执行层）

博主知识库 / AI人（`zsks`）项目的**重活执行层**。本仓库为**公开仓库**，专门利用 GitHub Actions 的无限免费分钟，跑所有平台的下载 / 转写 / 抓取 / 结构化重活，处理完后回调 `zsks` 应用的 `/api/internal/ingest` 写入共享存储。

> 本仓库**不是** Cloudflare Pages 站点，没有前端、不部署。应用层在私有仓库 `zsks`。

## 架构
```
用户贴链接 → zsks 应用识别 平台+类型 → repository_dispatch → zsks-runner
   → 平台抽取器(抓文案/正文/图片) + 视频下载→ffmpeg→whisper 转写 + 图文 OCR
   → DeepSeek 结构化 → 回调 zsks /api/internal/ingest 统一落库
```

## 支持平台（三平台 × 双内容）
| 平台 | video | article（图文） |
|---|---|---|
| 抖音 douyin | 下载+转写+抓文案 | 抓图文文案+图片 |
| B站 bilibili | 下载+转写+抓简介 | 抓专栏正文+图片 |
| 小红书 xiaohongshu | 下载+转写+抓文案 | 抓笔记正文+图片OCR |

- 元数据/媒体下载统一用 **yt-dlp**（原生支持三平台）；平台专属接口做增强。
- 视频：下载音频 → faster-whisper 中文转写（与文案融合）。
- 图文：readability/trafilatura 抽正文；图片 OCR 需开启 `OCR_ENABLED=true`（pytesseract+chi_sim，较重，默认关）。

## 文件
- `scripts/common.py` — 网络/下载/转写/结构化/回调 共享工具
- `scripts/extractors.py` — 抖音/B站/小红书 抽取器（video+article）
- `scripts/structure.py` — 主流程分发器
- `.github/workflows/process.yml` — 被 `repository_dispatch` 触发，按 platform+content_type 分发

## Secrets（Settings → Actions → Secrets）
- `INGEST_TOKEN` / `WORKER_URL`：与 zsks 应用一致（鉴权 + 回传地址）
- `DEEPSEEK_API_KEY`：实际由 zsks 的 `/api/internal/config` 下发，不在此配
- `WHISPER_MODEL`：转写模型（默认 small）
- `OCR_ENABLED`：`true` 开启图片 OCR（可选，较重）
- `DOUYIN_COOKIE` / `BILI_COOKIE` / `XHS_COOKIE`：各平台登录态（数据中心 IP 解风控；非必须）

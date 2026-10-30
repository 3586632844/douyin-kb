# zks-runner（重活执行层）

博主知识库 / AI人（`zsks`）项目的**重活执行层**。本仓库为**公开仓库**，专门利用 GitHub Actions 的无限免费分钟，跑所有平台的下载 / 转写 / 抓取 / 结构化重活，处理完后回调 `zsks` 应用的 `/api/internal/ingest` 写入共享存储。

> 本仓库**不是** Cloudflare Pages 站点，没有前端、不部署。应用层在私有仓库 `zsks`。

## 职责
- 接收 `zsks` 通过 `repository_dispatch` 触发的任务（含平台、链接、内容类型）
- 按平台分发到对应抽取器：下载视频/音频 → ffmpeg 抽音频 → faster-whisper 转写 → 抓文案/正文 → 调模型结构化
- 将结构化结果回调 `zsks` 的 ingest 接口，统一写入共享 D1/R2

## 当前能力
- 抖音视频：下载 + 转写 + 文案抓取（`process.yml`，已跑通）
- 连通性测试：`connectivity-test.yml`

## 计划
- ④ 接入 B站（视频 + 图文）
- ⑤ 抖音融合（文案 + 口播统一回写）
- ⑥ 图文抽取共享模块
- ⑦ 小红书（视频 + 图文）

## Secrets（Settings → Actions → Secrets）
- `CF_ACCOUNT_ID` / `CF_API_TOKEN`：Cloudflare 账户与令牌
- `R2_BUCKET` / `D1_NAME`：目标存储（由 zsks 统一管理）
- `WORKERS_AI_MODEL`：Workers AI 模型（默认 llama-3.1-8b）
- 各平台登录态（如 `DOUYIN_COOKIE`）按需配置

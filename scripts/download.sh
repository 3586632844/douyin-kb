#!/usr/bin/env bash
# 下载抖音无水印视频 -> work/video.mp4，并从文件名提取标题 -> work/title.txt
# 用法: bash download.sh "<douyin_url>" work
set -euo pipefail

URL="$1"
WORK="$2"
# 转绝对路径，避免后续 cd 后相对路径失效
WORK="$(cd "$(dirname "$WORK")" && pwd)/$(basename "$WORK")"
REPO_DIR="$WORK/dl"          # 注意：不能用下划线开头，否则会被下方 find 的 '*/_*' 排除
mkdir -p "$WORK" "$REPO_DIR"

echo "==> 解析短链并下载视频: $URL"

if [ ! -d "$REPO_DIR/.git" ]; then
  git clone --depth 1 https://github.com/qgeng1465/douyin-watermark-free-downloader.git "$REPO_DIR" 2>/dev/null \
    || git clone --depth 1 https://github.com/3348249229/douyin-downloader.git "$REPO_DIR" 2>/dev/null \
    || true
fi

cd "$REPO_DIR"
# 不依赖退出码（下载器可能非零退出但仍产出文件），直接检查产物
MP4=""
for entry in douyin_dl.py douyin_downloader.py main.py; do
  if [ -f "$entry" ]; then
    python3 "$entry" "$URL" >/dev/null 2>&1 || true
    MP4=$(find "$REPO_DIR" -path '*/downloads/*.mp4' 2>/dev/null | head -n1)
    [ -n "$MP4" ] && break
  fi
done

# 兜底：任意 mp4（排除 .git）
if [ -z "$MP4" ]; then
  MP4=$(find "$REPO_DIR" -name '*.mp4' -not -path '*/.git/*' 2>/dev/null | head -n1)
fi

if [ -z "$MP4" ]; then
  echo "!! 下载失败：可能是抖音对当前 IP 限流/风控。" >&2
  echo "!! 调试 REPO_DIR 内容:" >&2
  ls -la "$REPO_DIR" >&2 2>/dev/null || true
  exit 1
fi

cp "$MP4" "$WORK/video.mp4"

RAW=$(basename "$MP4")
TITLE=${RAW%.mp4}
TITLE=$(echo "$TITLE" | sed -E 's/_?[0-9]{6,}.*$//; s/[#@].*$//')
[ -z "$TITLE" ] && TITLE="抖音口播视频"
printf '%s' "$TITLE" > "$WORK/title.txt"

echo "==> 已保存: $WORK/video.mp4  (标题: $TITLE)"

#!/usr/bin/env bash
# 下载抖音无水印视频 -> work/video.mp4，并从文件名提取标题 -> work/title.txt
# 用法: bash download.sh "<douyin_url>" work
set -euo pipefail

URL="$1"
WORK="$2"
REPO_DIR="$WORK/_dl"
mkdir -p "$WORK" "$REPO_DIR"

echo "==> 解析短链并下载视频: $URL"

# 克隆已验证可用的开源下载器（固定到 main；如需可复现可改为具体 commit）
if [ ! -d "$REPO_DIR/.git" ]; then
  git clone --depth 1 https://github.com/qgeng1465/douyin-watermark-free-downloader.git "$REPO_DIR" 2>/dev/null \
    || git clone --depth 1 https://github.com/3348249229/douyin-downloader.git "$REPO_DIR" 2>/dev/null \
    || true
fi

cd "$REPO_DIR"
DL_OK=0
for entry in douyin_dl.py douyin_downloader.py main.py; do
  if [ -f "$entry" ]; then
    if python3 "$entry" "$URL" >/dev/null 2>&1; then DL_OK=1; break; fi
  fi
done

if [ "$DL_OK" -ne 1 ]; then
  echo "!! 下载失败：可能是抖音对当前 IP 限流/风控。详见 README 的 geo-block 备选方案。" >&2
  exit 1
fi

MP4=$(find . -name '*.mp4' -not -path '*/_*' | head -n1)
[ -n "$MP4" ] || { echo "!! 未找到下载产物" >&2; exit 1; }

cp "$MP4" "$WORK/video.mp4"

RAW=$(basename "$MP4")
TITLE=${RAW%.mp4}
TITLE=$(echo "$TITLE" | sed -E 's/_?[0-9]{6,}.*$//; s/[#@].*$//')
[ -z "$TITLE" ] && TITLE="抖音口播视频"
printf '%s' "$TITLE" > "$WORK/title.txt"

echo "==> 已保存: $WORK/video.mp4  (标题: $TITLE)"

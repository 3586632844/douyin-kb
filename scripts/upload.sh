#!/usr/bin/env bash
# 1) 上传转写稿/总结/结构到 Cloudflare R2
# 2) 把索引元数据写入 D1 (供按时间/主题浏览)
# 需要环境变量:
#   CF_ACCOUNT_ID, CLOUDFLARE_API_TOKEN  (wrangler 用, 需 D1:Edit 权限)
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY  (R2 API 令牌, 在脚本内由 R2_KEY/R2_SECRET 注入)
#   R2_BUCKET, D1_NAME, VIDEO_URL
set -euo pipefail

WORK="$1"
ID=$(date +%s)
ACCT="${CF_ACCOUNT_ID:?缺少 CF_ACCOUNT_ID}"
BUCKET="${R2_BUCKET:?缺少 R2_BUCKET}"
DB="${D1_NAME:?缺少 D1_NAME}"
EP="https://${ACCT}.r2.cloudflarestorage.com"

echo "==> 上传到 R2 (id=$ID)"
aws s3 cp "$WORK/transcript.txt"  "s3://$BUCKET/videos/$ID/transcript.txt"  --endpoint-url "$EP" --quiet
aws s3 cp "$WORK/summary.md"      "s3://$BUCKET/videos/$ID/summary.md"      --endpoint-url "$EP" --quiet
aws s3 cp "$WORK/structure.json"  "s3://$BUCKET/videos/$ID/structure.json"  --endpoint-url "$EP" --quiet

echo "==> 生成 D1 插入语句"
python3 - "$WORK" "$ID" "$VIDEO_URL" <<'PY'
import json, sys
work, vid_id, url = sys.argv[1], sys.argv[2], sys.argv[3]
m = json.load(open(f"{work}/meta.json", encoding="utf-8"))
def q(s): return "'" + str(s).replace("'", "''") + "'"
outline = json.dumps(m.get("outline", []), ensure_ascii=False).replace("'", "''")
kw = json.dumps(m.get("keywords", []), ensure_ascii=False).replace("'", "''")
sql = (
    f"INSERT INTO videos(id,title,topic,outline,summary,keywords,url,duration,words,created_at,r2_key) "
    f"VALUES({vid_id},{q(m.get('title',''))},{q(m.get('topic',''))},"
    f"'{outline}',{q(m.get('summary',''))},'{kw}',{q(url)},"
    f"{m.get('duration',0)},{m.get('words',0)},{m.get('created_at',0)},{q('videos/'+vid_id)});"
)
open(f"{work}/insert.sql", "w", encoding="utf-8").write(sql)
print(sql[:240])
PY

echo "==> 写入 D1"
CLOUDFLARE_API_TOKEN="$CLOUDFLARE_API_TOKEN" CF_ACCOUNT_ID="$ACCT" \
  npx --yes wrangler d1 execute "$DB" --remote --file="$WORK/insert.sql"
echo "==> 完成。R2 前缀: videos/$ID/"

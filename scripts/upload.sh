#!/usr/bin/env bash
# 1) 上传 transcript/summary/structure 到 R2 (wrangler)
# 2) 把索引写入 D1 (wrangler d1 execute)
# 环境变量(由 process.yml 注入):
#   CF_ACCOUNT_ID, CF_API_TOKEN  (wrangler 用，需 R2:Edit + D1:Edit 权限)
#   R2_BUCKET, D1_NAME, VIDEO_URL
set -euo pipefail

WORK="$1"
ID=$(date +%s)
ACCT="${CF_ACCOUNT_ID:?缺少 CF_ACCOUNT_ID}"
BUCKET="${R2_BUCKET:?缺少 R2_BUCKET}"
DB="${D1_NAME:?缺少 D1_NAME}"

# wrangler 读取的变量名与 job 传入的 CF_* 不同，这里统一映射
export CLOUDFLARE_API_TOKEN="${CF_API_TOKEN:?缺少 CF_API_TOKEN}"
export CLOUDFLARE_ACCOUNT_ID="$ACCT"

echo "==> 上传到 R2 (id=$ID)"
for f in transcript.txt summary.md structure.json; do
  if [ -f "$WORK/$f" ]; then
    npx --yes wrangler r2 object put "$BUCKET/videos/$ID/$f" --file "$WORK/$f" --remote
  else
    echo "!! 跳过 $f (不存在)"
  fi
done

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
npx --yes wrangler d1 execute "$DB" --remote --file="$WORK/insert.sql"
echo "==> 完成。R2 前缀: videos/$ID/"

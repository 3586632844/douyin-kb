-- Cloudflare D1 知识库索引表
-- outline / keywords 以 JSON 文本存储，便于前端解析
CREATE TABLE IF NOT EXISTS videos (
  id          INTEGER PRIMARY KEY,
  title       TEXT,
  topic       TEXT,
  outline     TEXT,          -- JSON: [{"heading": "...", "points": [...]}]
  summary     TEXT,
  keywords    TEXT,          -- JSON: ["词1","词2"]
  url         TEXT,          -- 来源抖音链接
  duration    INTEGER,       -- 秒
  words       INTEGER,       -- 中文字数
  created_at  INTEGER,       -- 入库时间戳（按时间查询/排序）
  r2_key      TEXT           -- R2 中该视频目录前缀
);

CREATE INDEX IF NOT EXISTS idx_created ON videos(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_topic ON videos(topic);

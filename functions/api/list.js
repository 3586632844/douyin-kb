// 查询知识库列表，支持按主题/时间过滤
// GET /api/list?topic=AI变现&since=1700000000
export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  const topic = url.searchParams.get("topic");
  const since = url.searchParams.get("since");

  let sql =
    "SELECT id,title,topic,summary,keywords,duration,created_at,url FROM videos WHERE 1=1";
  const args = [];
  if (topic) {
    sql += " AND topic LIKE ?";
    args.push(`%${topic}%`);
  }
  if (since) {
    sql += " AND created_at >= ?";
    args.push(Number(since));
  }
  sql += " ORDER BY created_at DESC LIMIT 100";

  const { results } = await env.DB.prepare(sql).bind(...args).all();
  return Response.json(results);
}

// 读取某条视频的完整总结（含大纲/摘要/全文）
// GET /api/view?id=1700000000
export async function onRequestGet({ request, env }) {
  const id = new URL(request.url).searchParams.get("id");
  if (!id) return new Response("缺少 id", { status: 400 });

  const obj = await env.BUCKET.get(`videos/${id}/summary.md`);
  if (!obj) return new Response("未找到", { status: 404 });

  const text = await obj.text();
  return new Response(text, {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
}

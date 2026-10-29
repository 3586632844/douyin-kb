// 触发 GitHub Actions 处理流水线（需要 GH_TOKEN / GH_REPO 两个 Pages 环境变量）
export async function onRequestPost({ request, env }) {
  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ ok: false, error: "无效的 JSON" }, { status: 400 });
  }
  const url = (body.url || "").trim();
  if (!url) return Response.json({ ok: false, error: "缺少抖音链接" }, { status: 400 });

  const res = await fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "cf-pages",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ event_type: "process", client_payload: { url } }),
  });

  if (!res.ok) {
    const t = await res.text();
    return Response.json({ ok: false, error: t.slice(0, 300) }, { status: 502 });
  }
  return Response.json({ ok: true, message: "已提交，约 2-3 分钟处理后出现在列表" });
}

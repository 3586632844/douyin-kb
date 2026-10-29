// 最小化连通性测试：验证【Cloudflare 边缘】能否直连抖音分享页
// 用法：把本文件作为 Worker 部署（wrangler deploy 或 Dashboard 粘贴），然后浏览器/curl 打开其地址。
// 返回 JSON：status=HTTP 状态码, hasRouterData=页面是否含视频数据, colo=处理请求的 Cloudflare 节点。
// 若 status=200 且 hasRouterData=true → Cloudflare 边缘能 reach 抖音，Worker 下载代理可行。
export default {
  async fetch(request, env) {
    const UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1";
    const target = "https://www.iesdouyin.com/share/video/7671311464384523505/";
    try {
      const r = await fetch(target, {
        headers: { "User-Agent": UA, "Referer": "https://www.douyin.com/" },
      });
      const body = await r.text();
      return new Response(JSON.stringify({
        status: r.status,
        ok: r.ok,
        hasRouterData: body.includes("_ROUTER_DATA"),
        length: body.length,
        colo: request.cf ? request.cf.colo : "n/a",
        edgeIp: request.headers.get("cf-connecting-ip") || "n/a",
      }), { headers: { "Content-Type": "application/json" } });
    } catch (e) {
      return new Response(JSON.stringify({ error: String(e) }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      });
    }
  },
};

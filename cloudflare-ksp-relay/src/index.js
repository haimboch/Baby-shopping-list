const KSP_ORIGIN = "https://ksp.co.il";
const KSP_API = `${KSP_ORIGIN}/m_action/api`;

const json = (value, status = 200, extraHeaders = {}) =>
  new Response(JSON.stringify(value), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      ...extraHeaders,
    },
  });

const safeEqual = (left, right) => {
  const a = new TextEncoder().encode(String(left || ""));
  const b = new TextEncoder().encode(String(right || ""));
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a[i] ^ b[i];
  return diff === 0;
};

const authorized = (request, env) => {
  if (!env.RELAY_TOKEN) return false;
  const header = request.headers.get("authorization") || "";
  const prefix = "Bearer ";
  if (!header.startsWith(prefix)) return false;
  return safeEqual(header.slice(prefix.length), env.RELAY_TOKEN);
};

const upstreamHeaders = {
  accept: "application/json",
  "accept-language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
  origin: KSP_ORIGIN,
  referer: `${KSP_ORIGIN}/web/`,
  "user-agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
};

async function fetchKsp(url) {
  const response = await fetch(url, {
    method: "GET",
    headers: upstreamHeaders,
    redirect: "follow",
    cache: "no-store",
  });
  const body = await response.text();
  const contentType = response.headers.get("content-type") || "";
  if (!response.ok) {
    return json(
      {
        success: false,
        upstream_status: response.status,
        error: "KSP upstream request failed",
      },
      response.status,
    );
  }
  if (!contentType.toLowerCase().includes("json") && !body.trimStart().startsWith("{")) {
    return json(
      { success: false, upstream_status: response.status, error: "KSP returned non-JSON content" },
      502,
    );
  }
  return new Response(body, {
    status: 200,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "x-ksp-relay": "cloudflare",
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method !== "GET") {
      return json({ success: false, error: "Method not allowed" }, 405, { allow: "GET" });
    }

    if (url.pathname === "/health") {
      return json({ success: true, service: "ksp-relay", version: "0.49" });
    }

    if (!authorized(request, env)) {
      return json({ success: false, error: "Unauthorized" }, 401);
    }

    if (url.pathname === "/ksp/category") {
      const search = (url.searchParams.get("search") || "").trim();
      const page = (url.searchParams.get("page") || "1").trim();
      if (!/^\d{8,14}$/.test(search) || !/^\d{1,3}$/.test(page)) {
        return json({ success: false, error: "Invalid barcode or page" }, 400);
      }
      const upstream = new URL(`${KSP_API}/category/`);
      upstream.searchParams.set("search", search);
      if (page !== "1") upstream.searchParams.set("page", page);
      return fetchKsp(upstream.toString());
    }

    const itemMatch = url.pathname.match(/^\/ksp\/item\/(\d{3,9})$/);
    if (itemMatch) {
      return fetchKsp(`${KSP_API}/item/${itemMatch[1]}`);
    }

    return json({ success: false, error: "Not found" }, 404);
  },
};

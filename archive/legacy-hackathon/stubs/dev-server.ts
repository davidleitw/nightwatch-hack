import { extname, join } from "node:path";

const distDir = process.env.WEB_DIST ?? join(import.meta.dir, "..", "console", "dist");
const portValue = Number(process.env.PORT ?? "5173");
const port = Number.isInteger(portValue) && portValue > 0 ? portValue : 5173;
const controlUrl = process.env.NIGHTWATCH_CONTROL_URL;

// poc/web 這個前端是對 poc 那套 control 寫的,兩邊對 /api/faults/catalog 的形狀
// 講好的不一樣:poc control 回 {"cards":[...]},hackathon control 回裸陣列
// (它自己的 fault-catalog.schema.json 就寫 "type": "array")。poc/web 讀的是
// catalog?.cards,所以拿到裸陣列會得到 undefined,卡片頁一張卡都不顯示。
//
// 兩邊的 control 都不該為對方改形狀,前端也不動,所以在代理這一層補上轉換。
// NIGHTWATCH_COMPAT=poc-web 才開。
const compat = (process.env.NIGHTWATCH_COMPAT ?? "").trim();

async function adaptForPocWeb(pathname: string, response: Response): Promise<Response> {
  if (compat !== "poc-web" || pathname !== "/api/faults/catalog" || !response.ok) return response;
  const payload: unknown = await response.json().catch(() => null);
  if (!Array.isArray(payload)) return Response.json(payload, { status: response.status });
  return Response.json({ schema_version: "nightwatch.cards.v2", cards: payload });
}

function isControlPath(pathname: string): boolean {
  return pathname === "/events" || pathname.startsWith("/api/") || pathname === "/api";
}

async function proxy(request: Request, incoming: URL): Promise<Response> {
  if (!controlUrl) return new Response("Control proxy is not configured", { status: 503 });
  const target = new URL(controlUrl);
  target.pathname = incoming.pathname;
  target.search = incoming.search;
  const headers = new Headers(request.headers);
  headers.delete("host");
  const methodHasBody = request.method !== "GET" && request.method !== "HEAD";
  const response = await fetch(target, {
    method: request.method,
    headers,
    body: methodHasBody ? request.body : undefined,
    redirect: "manual",
  });
  return adaptForPocWeb(incoming.pathname, response);
}

async function serveStatic(incoming: URL): Promise<Response> {
  let pathname: string;
  try {
    pathname = decodeURIComponent(incoming.pathname);
  } catch {
    return new Response("Bad request", { status: 400 });
  }

  const relativePath = pathname.replace(/^\/+/, "");
  if (relativePath.split("/").includes("..")) return new Response("Bad request", { status: 400 });

  if (pathname === "/" || extname(pathname) === "") {
    return new Response(Bun.file(join(distDir, "index.html")), { headers: { "Cache-Control": "no-store" } });
  }

  const file = Bun.file(join(distDir, relativePath));
  if (await file.exists()) return new Response(file, { headers: { "Cache-Control": "no-store" } });
  return new Response("Not found", { status: 404 });
}

// 預設只綁本機;要讓同網段的人看得到就給 HOST=0.0.0.0
const hostname = process.env.HOST ?? "127.0.0.1";

Bun.serve({
  hostname,
  port,
  fetch(request) {
    const incoming = new URL(request.url);
    if (controlUrl && isControlPath(incoming.pathname)) return proxy(request, incoming);
    return serveStatic(incoming);
  },
});

console.log(`NightWatch web dev server: ${hostname}:${port}`);

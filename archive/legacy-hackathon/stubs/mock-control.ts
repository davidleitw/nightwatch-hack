import { readFile } from "node:fs/promises";
import { join } from "node:path";

type JsonRecord = Record<string, any>;
type EventName = "state" | "incident" | "graph" | "faults" | "readiness";
type EventLine = { event: EventName; data: any };

const card = process.env.FIXTURE_CARD ?? "catalog_pool_leak";
if (!/^[A-Za-z0-9_-]+$/.test(card)) throw new Error(`invalid FIXTURE_CARD: ${card}`);

const portValue = Number(process.env.PORT ?? "3999");
const port = Number.isInteger(portValue) && portValue > 0 ? portValue : 3999;
const rateValue = Number(process.env.RATE ?? "1");
const rate = Number.isFinite(rateValue) && rateValue > 0 ? rateValue : 1;
const fixtureDir = join(import.meta.dir, "..", "contracts", "fixtures", card);

async function readJson(name: string): Promise<any> {
  return JSON.parse(await readFile(join(fixtureDir, name), "utf8"));
}

async function readJsonl(name: string): Promise<any[]> {
  const text = await readFile(join(fixtureDir, name), "utf8");
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line, index) => {
      try {
        return JSON.parse(line);
      } catch (error) {
        throw new Error(`${name} line ${index + 1}: ${String(error)}`);
      }
    });
}

const [initialState, closedState, rawEvents, snapshots, report, timeline] = await Promise.all([
  readJson("state.initial.json"),
  readJson("state.json"),
  readJsonl("events.jsonl"),
  readJsonl("snapshots.jsonl"),
  readJson("report.json"),
  readJson("timeline.json"),
]);

const events: EventLine[] = rawEvents.map((line, index) => {
  if (!line || typeof line !== "object") throw new Error(`events.jsonl line ${index + 1} is not an object`);
  if (typeof line.event === "string") {
    if (!["state", "incident", "graph", "faults", "readiness"].includes(line.event)) {
      throw new Error(`events.jsonl line ${index + 1} has unsupported event ${line.event}`);
    }
    return { event: line.event as EventName, data: line.data };
  }
  if (line.event && typeof line.event === "object") return { event: "incident", data: line };
  throw new Error(`events.jsonl line ${index + 1} has no event name`);
});

const detectedAtMs = Date.parse(timeline.detected_at);

function readAt(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function readInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? Math.round(value) : null;
}

function eventT(line: EventLine, fallbackT: number): number {
  const data = line.data as JsonRecord | null;
  const nested = data && typeof data.event === "object" ? (data.event as JsonRecord) : data;
  const explicit = readInteger(data?.t) ?? readInteger(nested?.t);
  if (explicit !== null) return explicit;
  const absolute = readAt(data?.at) ?? readAt(nested?.occurred_at) ?? readAt(data?.server_now);
  if (absolute !== null) return Math.round((absolute - detectedAtMs) / 1000);

  if (line.event === "readiness") {
    const baseline = Array.isArray(data?.checks)
      ? data.checks.find((check: JsonRecord) => check?.id === "baseline")
      : null;
    const collected = typeof baseline?.detail_zh === "string" ? baseline.detail_zh.match(/(\d+)\s*\//)?.[1] : null;
    if (collected) return timeline.from_t + Number(collected);
    if (baseline?.status === "ok") return timeline.from_t + initialState.run.baseline.required_secs;
  }

  if (line.event === "faults") {
    const instance = data?.instances?.[0] as JsonRecord | undefined;
    const appliedAt = readAt(instance?.applied_at);
    if (appliedAt !== null) {
      const appliedT = Math.round((appliedAt - detectedAtMs) / 1000);
      const offset = ["restored", "restoring", "aborted", "expired"].includes(instance?.status)
        ? (instance?.elapsed_secs ?? instance?.detected_after_secs ?? 0)
        : (instance?.detected_after_secs ?? instance?.elapsed_secs ?? 0);
      return appliedT + offset;
    }
  }
  return fallbackT;
}

const timedEvents = events.map((line, index) => ({ line, t: eventT(line, index ? timeline.from_t : timeline.from_t), index }));

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "access-control-allow-origin": "*",
    },
  });
}

function notFound(): Response {
  return new Response("Not found", { status: 404 });
}

function snapshotT(snapshot: JsonRecord): number {
  if (typeof snapshot.t === "number") return snapshot.t;
  const parsed = Date.parse(snapshot.at);
  return Number.isFinite(parsed) ? Math.round((parsed - detectedAtMs) / 1000) : Number.NEGATIVE_INFINITY;
}

function snapshotAt(requested: string | null): JsonRecord | null {
  if (requested === null) return closedState.graph_now;
  const numeric = /^-?\d+(?:\.\d+)?$/.test(requested) ? Number(requested) : null;
  const absolute = numeric === null ? Date.parse(requested) : null;
  let selected: JsonRecord | null = null;
  for (const snapshot of snapshots as JsonRecord[]) {
    const candidate = numeric === null ? Date.parse(snapshot.at) : snapshotT(snapshot);
    const target = numeric === null ? absolute : numeric;
    if (!Number.isFinite(candidate) || !Number.isFinite(target) || candidate > target) continue;
    const selectedValue = selected
      ? numeric === null
        ? Date.parse(selected.at)
        : snapshotT(selected)
      : Number.NEGATIVE_INFINITY;
    if (!selected || candidate >= selectedValue) selected = snapshot;
  }
  return selected;
}

function median(values: number[]): number {
  const sorted = values.filter(Number.isFinite).sort((left, right) => left - right);
  if (!sorted.length) return 0;
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle];
}

function average(values: number[]): number {
  const finite = values.filter(Number.isFinite);
  return finite.length ? finite.reduce((total, value) => total + value, 0) / finite.length : 0;
}

function historyFor(nodeId: string, windowSecs: number): JsonRecord | null {
  const samples = (snapshots as JsonRecord[])
    .flatMap((snapshot) => {
      const node = snapshot.nodes?.find((candidate: JsonRecord) => candidate.id === nodeId);
      return node ? [{ snapshot, node, t: snapshotT(snapshot) }] : [];
    })
    .sort((left, right) => left.t - right.t);
  if (!samples.length) return null;
  const currentT = closedState.graph_now.t ?? timeline.to_t;
  const requestedWindow = Number.isFinite(windowSecs) && windowSecs > 0 ? Math.floor(windowSecs) : 60;
  const fromT = Math.max(samples[0].t, currentT - requestedWindow);
  const toT = Math.min(samples.at(-1)!.t, currentT);
  const stepSecs = Math.max(5, Math.ceil(Math.ceil(requestedWindow / 25) / 5) * 5);
  const points: number[][] = [];
  for (let start = fromT; start <= toT; start += stepSecs) {
    const end = Math.min(toT, start + stepSecs);
    const bucket = samples.filter((sample) => sample.t >= start && (end === toT ? sample.t <= end : sample.t < end));
    if (!bucket.length) continue;
    points.push([
      end === toT ? toT : start,
      average(bucket.map((sample) => sample.node.traffic).filter((value): value is number => typeof value === "number")),
      median(
        bucket
          .map((sample) => (typeof sample.node.errors === "number" ? sample.node.errors * 100 : null))
          .filter((value): value is number => typeof value === "number"),
      ),
      median(bucket.map((sample) => sample.node.p95_ms).filter((value): value is number => typeof value === "number")),
      median(
        bucket
          .map((sample) => (typeof sample.node.saturation === "number" ? sample.node.saturation * 100 : null))
          .filter((value): value is number => typeof value === "number"),
      ),
      average(bucket.map((sample) => (sample.node.alive ? 1 : 0))) >= 0.5 ? 1 : 0,
    ]);
  }
  const baselineSamples = samples.filter((sample) => sample.t < 0).slice(0, 24);
  const baseline = baselineSamples.length ? baselineSamples : samples.slice(0, 24);
  const baselineValue = {
    rps: average(baseline.map((sample) => sample.node.traffic).filter((value): value is number => typeof value === "number")),
    err: median(
      baseline
        .map((sample) => (typeof sample.node.errors === "number" ? sample.node.errors * 100 : null))
        .filter((value): value is number => typeof value === "number"),
    ),
    p95: median(baseline.map((sample) => sample.node.p95_ms).filter((value): value is number => typeof value === "number")),
    sat: median(
      baseline
        .map((sample) => (typeof sample.node.saturation === "number" ? sample.node.saturation * 100 : null))
        .filter((value): value is number => typeof value === "number"),
    ),
  };
  if (points.length > 26) points.splice(0, points.length - 26);
  return {
    node: nodeId,
    kind: samples[0].node.kind,
    t_from: points[0]?.[0] ?? fromT,
    t_to: points.at(-1)?.[0] ?? toT,
    step_secs: stepSecs,
    axes: ["t", "rps", "err", "p95", "sat", "up"],
    points,
    baseline: baselineValue,
    band: {
      err_max: baselineValue.err + 2,
      p95_max: Math.max(baselineValue.p95 * 1.5, baselineValue.p95 + 50),
      sat_max: baselineValue.sat + 15,
    },
    sat_label: samples[0].node.sat_label,
  };
}

async function catalog(): Promise<JsonRecord> {
  const payload = JSON.parse(await readFile(join(import.meta.dir, "..", "contracts", "fixtures", "catalog.json"), "utf8"));
  return Array.isArray(payload) ? { schema_version: "nightwatch.cards.v2", cards: payload } : payload;
}

function sseFrame(event: string, data: unknown): Uint8Array {
  return new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function cursorRevision(value: string | null): number | null {
  if (!value) return null;
  const parsed = Number(value.split(":").at(-1));
  return Number.isInteger(parsed) ? parsed : null;
}

function streamEvents(request: Request): Response {
  const cursor = cursorRevision(new URL(request.url).searchParams.get("cursor"));
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      void (async () => {
        let previousT = timeline.from_t;
        try {
          controller.enqueue(sseFrame("state", initialState));
          for (const { line, t } of timedEvents) {
            if (request.signal.aborted) break;
            const delay = Math.min(1000, Math.max(0, ((t - previousT) * 1000) / rate));
            previousT = t;
            if (delay) await sleep(delay);
            if (request.signal.aborted) break;
            const data = line.data as JsonRecord;
            if (line.event === "incident" && cursor !== null && Number(data?.revision) <= cursor) continue;
            controller.enqueue(sseFrame(line.event, line.data));
          }
        } catch {
          // The browser closing an SSE stream is the normal cancellation path.
        } finally {
          try {
            controller.close();
          } catch {
            // The stream was already cancelled.
          }
        }
      })();
    },
  });
  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache",
      connection: "keep-alive",
      "access-control-allow-origin": "*",
    },
  });
}

async function handle(request: Request): Promise<Response> {
  const incoming = new URL(request.url);
  if (request.method === "POST" && (incoming.pathname === "/api" || incoming.pathname.startsWith("/api/"))) {
    const text = await request.text();
    let body: unknown = {};
    if (text.trim()) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    return jsonResponse({ accepted: true, echo: body }, 202);
  }

  if (request.method !== "GET") return notFound();
  if (incoming.pathname === "/events") return streamEvents(request);
  if (incoming.pathname === "/api/state") return jsonResponse(process.env.INITIAL === "1" ? initialState : closedState);
  if (incoming.pathname === "/api/readiness") return jsonResponse(closedState.readiness);
  if (incoming.pathname === "/api/faults/catalog") return jsonResponse(await catalog());
  if (incoming.pathname === "/api/graph") {
    const graph = snapshotAt(incoming.searchParams.get("at"));
    return graph ? jsonResponse(graph) : notFound();
  }
  if (incoming.pathname === "/api/graph/history") {
    const node = incoming.searchParams.get("node");
    const windowSecs = Number(incoming.searchParams.get("window_secs") ?? "600");
    const history = node ? historyFor(node, windowSecs) : null;
    return history ? jsonResponse(history) : notFound();
  }

  const incidentMatch = incoming.pathname.match(/^\/api\/incidents\/([^/]+)\/(snapshots|timeline|report)$/);
  if (incidentMatch) {
    const id = decodeURIComponent(incidentMatch[1]);
    if (id !== report.incident_id) return notFound();
    if (incidentMatch[2] === "timeline") return jsonResponse(timeline);
    if (incidentMatch[2] === "report") return jsonResponse(report);
    return jsonResponse({ pinned_window: report.pinned_window ?? null, snapshots });
  }

  return notFound();
}

Bun.serve({
  hostname: "127.0.0.1",
  port,
  fetch: handle,
});

console.log(`NightWatch mock control: 127.0.0.1:${port} (${card})`);

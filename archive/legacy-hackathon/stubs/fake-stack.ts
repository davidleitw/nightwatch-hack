#!/usr/bin/env bun

/**
 * A dependency-free host-side stand-in for the shop, Prometheus, and Jaeger.
 *
 * It deliberately recognises the small PromQL vocabulary used by
 * poc/internal/graph/queries.go and by the control tools. It is not a PromQL
 * implementation: an unrecognised expression is an HTTP 400 so a missing
 * integration query cannot look like a healthy zero.
 */

type Scalar = string | number | boolean | null;
type Knobs = Record<string, Scalar>;

const SHOP_SERVICES = [
  "storefront",
  "catalog",
  "cart",
  "checkout",
  "payment",
  "shipping",
  "fulfillment",
  "shopper",
] as const;

const SERVER_SERVICES = SHOP_SERVICES.filter((service) => service !== "shopper");

const PRISTINE_KNOBS: Record<string, Knobs> = {
  storefront: {},
  catalog: { "db.leak_per_minute": 0 },
  cart: { error_rate: 0 },
  checkout: {},
  payment: {
    "provider.active": "primary",
    "provider.primary.error_rate": 0,
    "provider.primary.latency_ms": 80,
  },
  shipping: { "audit.verbosity": "info" },
  fulfillment: { "worker.process_ms": 200 },
  shopper: {},
};

const METRIC_NAMES = [
  "shop_db_pool_in_use",
  "shop_db_pool_max",
  "shop_db_pool_wait_total",
  "shop_queue_depth",
  "shop_queue_oldest_age_seconds",
  "shop_queue_enqueued_total",
  "shop_queue_processed_total",
  "shop_queue_process_duration_ms",
  "shop_queue_process_duration_ms_bucket",
  "shop_queue_process_duration_ms_count",
  "shop_volume_used_ratio",
  "shop_volume_write_errors_total",
  "shop_volume_write_duration_ms_milliseconds",
  "shop_volume_write_duration_ms_milliseconds_bucket",
  "shop_volume_write_duration_ms_milliseconds_count",
  "shop_shopper_orders_total",
  "shop_shopper_order_duration_ms",
  "shop_shopper_order_duration_ms_bucket",
  "shop_shopper_order_duration_ms_count",
  "shop_shopper_inflight",
  "shop_config_revision",
  "traces_span_metrics_calls_total",
  "traces_span_metrics_duration_milliseconds",
  "traces_span_metrics_duration_milliseconds_bucket",
  "traces_span_metrics_duration_milliseconds_count",
  "traces_service_graph_request_total",
  "traces_service_graph_request_failed_total",
  "traces_service_graph_request_server_seconds",
  "traces_service_graph_request_server_seconds_bucket",
];

const KNOWN_METRICS = new Set(
  METRIC_NAMES.map((name) => canonicalMetricName(name)),
);

interface ConfigState {
  revision: string;
  knobs: Knobs;
  appliedAt: string;
}

interface ServiceMetric {
  rps: number;
  errors: number;
  p95Ms: number;
}

interface EdgeMetric {
  from: string;
  to: string;
  rps: number;
  errors: number;
  p95Ms: number;
}

interface MetricsSnapshot {
  services: Record<string, ServiceMetric>;
  shopper: {
    ordersRate: number;
    failRate: number;
    p95Ms: number;
    outcomes: Record<string, number>;
  };
  poolInUse: number;
  poolMax: number;
  poolWaitTotal: number;
  queue: {
    depth: number;
    oldestAgeSecs: number;
    enqueuedRate: number;
    processedRate: number;
    processP95Ms: number;
  };
  volume: {
    usedRatio: number;
    writesRate: number;
    errorsRate: number;
    p95Ms: number;
    errorsTotal: number;
    enospcTotal: number;
  };
  edges: EdgeMetric[];
}

interface MetricSeries {
  labels: Record<string, string>;
  rate: number;
  value: number;
  p95: number;
  counterKey?: string;
}

interface LabelMatcher {
  name: string;
  operator: "=" | "!=" | "=~" | "!~";
  value: string;
}

interface ParsedMetric {
  metric: string;
  selector: string;
  matchers: LabelMatcher[];
}

interface JaegerTag {
  key: string;
  type: string;
  value: Scalar;
}

interface JaegerReference {
  refType: string;
  traceID: string;
  spanID: string;
}

interface JaegerSpan {
  traceID: string;
  spanID: string;
  operationName: string;
  startTime: number;
  duration: number;
  tags: JaegerTag[];
  references: JaegerReference[];
  processID: string;
}

interface JaegerTrace {
  traceID: string;
  spans: JaegerSpan[];
  processes: Record<string, { serviceName: string }>;
}

interface StoredTrace {
  trace: JaegerTrace;
  startTimeMicros: number;
}

function cloneKnobs(knobs: Knobs): Knobs {
  return { ...knobs };
}

function cloneConfig(config: ConfigState): { revision: string; knobs: Knobs } {
  return { revision: config.revision, knobs: cloneKnobs(config.knobs) };
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}

function finiteNumber(value: number, fallback = 0): number {
  return Number.isFinite(value) ? value : fallback;
}

function scalarNumber(value: Scalar | undefined, fallback: number): number {
  if (typeof value === "number") {
    return finiteNumber(value, fallback);
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  return fallback;
}

function scalarString(value: Scalar | undefined, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

function normalizeQuery(query: string): string {
  return query.replace(/\s+/g, " ").trim();
}

function canonicalMetricName(metric: string): string {
  if (metric.endsWith("_bucket") || metric.endsWith("_count")) {
    return metric.slice(0, metric.lastIndexOf("_"));
  }
  return metric;
}

function parseMatchers(selector: string): LabelMatcher[] {
  const matchers: LabelMatcher[] = [];
  const pattern = /([a-zA-Z_][a-zA-Z0-9_]*)(=~|!~|!=|=)\s*"((?:\\.|[^"])*)"/g;
  for (const match of selector.matchAll(pattern)) {
    let value = match[3];
    try {
      value = JSON.parse(`"${value}"`);
    } catch {
      // The query vocabulary uses ordinary quoted strings. Keeping the raw
      // value makes a malformed matcher fail as a non-match, not as a crash.
    }
    matchers.push({
      name: match[1],
      operator: match[2] as LabelMatcher["operator"],
      value,
    });
  }
  return matchers;
}

function matchesLabels(labels: Record<string, string>, matchers: LabelMatcher[]): boolean {
  return matchers.every((matcher) => {
    const actual = labels[matcher.name] ?? "";
    switch (matcher.operator) {
      case "=":
        return actual === matcher.value;
      case "!=":
        return actual !== matcher.value;
      case "=~":
        try {
          return new RegExp(`^(?:${matcher.value})$`).test(actual);
        } catch {
          return false;
        }
      case "!~":
        try {
          return !new RegExp(`^(?:${matcher.value})$`).test(actual);
        } catch {
          return false;
        }
    }
  });
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function textResponse(value: string, status = 200): Response {
  return new Response(value, {
    status,
    headers: { "content-type": "text/plain; charset=utf-8" },
  });
}

function parseDurationSeconds(value: string | null): number {
  if (!value) return 900;
  const match = value.trim().match(/^([0-9]+(?:\.[0-9]+)?)([smhd]?)$/i);
  if (!match) return 900;
  const amount = Number(match[1]);
  const multiplier = { "": 1, s: 1, m: 60, h: 3600, d: 86400 }[match[2].toLowerCase()] ?? 1;
  return finiteNumber(amount * multiplier, 900);
}

function formatPromValue(value: number): string {
  return String(finiteNumber(value));
}

function operationForService(service: string): string {
  return {
    storefront: "GET /",
    catalog: "CatalogService/GetProduct",
    cart: "CartService/Get",
    checkout: "CheckoutService/PlaceOrder",
    payment: "PaymentService/Charge",
    shipping: "ShippingService/CreateLabel",
    fulfillment: "FulfillmentWorker/Process",
    shopper: "Shopper/PlaceOrder",
    "payment-provider-primary": "HTTP POST payment-provider-primary",
    "payment-provider-secondary": "HTTP POST payment-provider-secondary",
  }[service] ?? `${service}/request`;
}

class Simulation {
  readonly speed: number;
  readonly seed: number;
  readonly logSinkPort: number;

  private readonly configs = new Map<string, ConfigState>();
  private readonly traces = new Map<string, StoredTrace>();
  private readonly spanCounters = new Map<string, number>();
  private readonly rng: () => number;
  private traceSequence = 0;
  private logicalSeconds = 0;
  private nextTraceAt = 5;
  private nextLogAt = 5;

  private poolInUse = 2;
  private poolWaitTotal = 0;
  private queueDepth = 1;
  private usedRatio = 0.1;
  private queueEnqueuedTotal = 0;
  private queueProcessedTotal = 0;
  private volumeWritesTotal = 0;
  private volumeErrorsTotal = 0;
  private volumeENOSPCTotal = 0;
  private shopperOutcomeTotals: Record<string, number> = {
    ok: 0,
    http_error: 0,
    payment_declined: 0,
    fulfillment_timeout: 0,
  };
  private current: MetricsSnapshot;

  constructor(speed: number, seed: number, logSinkPort: number) {
    this.speed = speed;
    this.seed = seed;
    this.logSinkPort = logSinkPort;
    this.rng = this.makeRng(seed);
    for (const service of SHOP_SERVICES) {
      this.configs.set(service, {
        revision: "v1",
        knobs: cloneKnobs(PRISTINE_KNOBS[service]),
        appliedAt: new Date().toISOString(),
      });
    }
    this.current = this.computeMetrics();
  }

  getConfig(service: string): { revision: string; knobs: Knobs } | null {
    const config = this.configs.get(service);
    return config ? cloneConfig(config) : null;
  }

  putConfig(service: string, revision: string, knobs: Knobs): { revision: string; knobs: Knobs } | null {
    if (!this.configs.has(service)) return null;
    // Merge over the current knobs (same semantics as the real shop): the
    // curve engine only re-sends the knobs its card owns, and a mitigation
    // such as provider.active=secondary must survive those pushes.
    const base = cloneKnobs(PRISTINE_KNOBS[service] ?? {});
    const current = this.configs.get(service)?.knobs ?? {};
    const next: ConfigState = {
      revision: revision.trim() || "v1",
      knobs: { ...base, ...current, ...knobs },
      appliedAt: new Date().toISOString(),
    };
    this.configs.set(service, next);
    return cloneConfig(next);
  }

  tick(): void {
    const dt = this.speed;
    this.logicalSeconds += dt;

    const leakPerMinute = Math.max(0, this.knobNumber("catalog", "db.leak_per_minute", 0));
    if (leakPerMinute > 0) {
      this.poolInUse = clamp(this.poolInUse + (leakPerMinute * dt) / 60, 2, 12);
      if (this.poolInUse >= 12) {
        this.poolWaitTotal += 0.8 * dt;
      }
    } else {
      this.poolInUse = Math.max(2, this.poolInUse - 2 * dt);
    }

    const processMs = Math.max(1, this.knobNumber("fulfillment", "worker.process_ms", 200));
    const enqueueRate = 0.48;
    const capacity = 1000 / processMs;
    if (processMs > 300) {
      this.queueDepth = clamp(this.queueDepth + (enqueueRate - capacity) * dt, 0, 200);
    } else {
      const target = clamp(
        1 + 0.6 * Math.sin(this.logicalSeconds / 5) + (this.rng() - 0.5) * 0.16,
        0,
        2,
      );
      this.queueDepth += (target - this.queueDepth) * Math.min(1, dt);
    }

    const debugAudit = this.knobString("shipping", "audit.verbosity", "info") === "debug";
    if (debugAudit) {
      this.usedRatio = clamp(this.usedRatio + 0.01 * dt, 0.1, 1);
    } else {
      this.usedRatio = Math.max(0.1, this.usedRatio - 0.05 * dt);
    }

    const snapshot = this.computeMetrics();
    this.queueEnqueuedTotal += snapshot.queue.enqueuedRate * dt;
    this.queueProcessedTotal += snapshot.queue.processedRate * dt;
    this.volumeWritesTotal += snapshot.volume.writesRate * dt;
    this.volumeErrorsTotal += snapshot.volume.errorsRate * dt;
    if (snapshot.volume.errorsRate > 0 && this.usedRatio >= 0.999) {
      this.volumeENOSPCTotal += snapshot.volume.errorsRate * dt;
    }

    for (const outcome of Object.keys(this.shopperOutcomeTotals)) {
      this.shopperOutcomeTotals[outcome] += (snapshot.shopper.outcomes[outcome] ?? 0) * dt;
    }
    this.integrateSpanCounters(snapshot, dt);
    this.current = snapshot;

    let tracesMade = 0;
    while (this.logicalSeconds >= this.nextTraceAt && tracesMade < 20) {
      this.addTrace(this.nextTraceAt, snapshot);
      this.nextTraceAt += 5;
      tracesMade++;
    }

    let logsMade = 0;
    while (this.logicalSeconds >= this.nextLogAt && logsMade < 20) {
      void this.sendErrorLogs();
      this.nextLogAt += 5;
      logsMade++;
    }
  }

  serviceFailing(service: string): boolean {
    const metric = this.current.services[service];
    if (!metric) return false;
    if (service === "catalog") return this.current.poolInUse >= this.current.poolMax - 0.001;
    if (service === "shipping") return this.current.volume.usedRatio >= 0.999;
    if (service === "fulfillment") return this.current.queue.oldestAgeSecs >= 30;
    return metric.errors >= 0.05;
  }

  queryPrometheus(query: string): Array<{ metric: Record<string, string>; value: number }> {
    const normalized = normalizeQuery(query);
    if (!normalized) throw new Error("query is required");

    if (normalized.includes("timestamp(shop_config_revision")) {
      return this.queryRevision(normalized);
    }
    if (normalized.startsWith("topk(")) {
      return this.queryTopSpans(normalized);
    }
    if (
      normalized.includes("sum by (service_name)") &&
      normalized.includes("traces_span_metrics_calls_total") &&
      normalized.includes('status_code="STATUS_CODE_ERROR"')
    ) {
      return this.serviceVector("errors");
    }
    if (
      normalized.includes("sum by (service_name)") &&
      normalized.includes("traces_span_metrics_calls_total")
    ) {
      return this.serviceVector("traffic");
    }
    if (
      normalized.includes("histogram_quantile(0.95") &&
      normalized.includes("sum by (service_name, le)") &&
      normalized.includes("traces_span_metrics_duration_milliseconds_bucket")
    ) {
      return this.serviceVector("latency");
    }
    if (
      normalized.includes("count by (service_name)") &&
      normalized.includes("count_over_time")
    ) {
      return this.serviceVector("liveness");
    }
    if (normalized.includes("sum by (client, server)")) {
      return this.serviceGraphVector(normalized);
    }

    if (
      normalized.includes("shop_db_pool_in_use") &&
      normalized.includes("clamp_min(shop_db_pool_max")
    ) {
      return [{ metric: {}, value: this.current.poolInUse / Math.max(this.current.poolMax, 0.001) }];
    }
    if (normalized.includes("shop_queue_depth") && normalized.includes("/ 200")) {
      return [{ metric: {}, value: this.current.queue.depth / 200 }];
    }
    if (
      normalized.includes("shop_queue_oldest_age_seconds") &&
      normalized.endsWith("* 1000")
    ) {
      return [{ metric: {}, value: this.current.queue.oldestAgeSecs * 1000 }];
    }

    return this.queryGenericMetric(normalized);
  }

  metricNames(): string[] {
    return [...METRIC_NAMES];
  }

  tracesFor(service: string, lookbackSecs: number, limit: number, errorOnly: boolean): JaegerTrace[] {
    const cutoff = Date.now() - lookbackSecs * 1000;
    const traces = [...this.traces.values()]
      .filter((stored) => stored.startTimeMicros / 1000 >= cutoff)
      .filter((stored) => {
        if (!service) return true;
        return Object.values(stored.trace.processes).some(
          (process) => process.serviceName === service,
        );
      })
      .filter((stored) => {
        if (!errorOnly) return true;
        return stored.trace.spans.some((span) =>
          span.tags.some((tag) => tag.key === "error" && tag.value === true),
        );
      })
      .sort((left, right) => right.startTimeMicros - left.startTimeMicros)
      .slice(0, Math.max(0, limit));
    return traces.map((stored) => stored.trace);
  }

  traceByID(traceID: string): JaegerTrace | null {
    return this.traces.get(traceID)?.trace ?? null;
  }

  rotateVolume(): void {
    this.usedRatio = 0.1;
    this.current = this.computeMetrics();
  }

  private makeRng(seed: number): () => number {
    let state = (Math.trunc(seed) >>> 0) || 1;
    return () => {
      state ^= state << 13;
      state ^= state >>> 17;
      state ^= state << 5;
      return (state >>> 0) / 0x100000000;
    };
  }

  private config(service: string): ConfigState {
    return this.configs.get(service) ?? {
      revision: "v1",
      knobs: {},
      appliedAt: new Date().toISOString(),
    };
  }

  private knobNumber(service: string, name: string, fallback: number): number {
    return scalarNumber(this.config(service).knobs[name], fallback);
  }

  private knobString(service: string, name: string, fallback: string): string {
    return scalarString(this.config(service).knobs[name], fallback);
  }

  private computeMetrics(): MetricsSnapshot {
    const poolMax = 12;
    const catalogError = this.poolInUse >= poolMax - 0.001 ? 0.6 : 0;
    const cartError = clamp(this.knobNumber("cart", "error_rate", 0), 0, 1);
    const activeProvider = this.knobString("payment", "provider.active", "primary");
    const primaryProviderError = clamp(
      this.knobNumber("payment", "provider.primary.error_rate", 0),
      0,
      1,
    );
    // Keep the payment service at its pristine zero-error baseline while the
    // provider span still has the requested small 0.1% background error rate.
    const providerError = activeProvider === "secondary" ? 0 : primaryProviderError;
    const providerSpanError = Math.max(0.001, providerError);
    const providerLatency = activeProvider === "secondary"
      ? 90
      : Math.max(1, this.knobNumber("payment", "provider.primary.latency_ms", 80));
    const processMs = Math.max(1, this.knobNumber("fulfillment", "worker.process_ms", 200));
    const queueCapacity = 1000 / processMs;
    const processedRate = processMs > 300
      ? Math.min(0.48, queueCapacity)
      : 0.48;
    const queueOldestAgeSecs = this.queueDepth <= 2
      ? 0.5 + 0.3 * Math.abs(Math.sin(this.logicalSeconds / 7))
      : clamp(this.queueDepth / Math.max(processedRate, 0.01), 0, 600);
    const fulfillmentError = queueOldestAgeSecs >= 30
      ? clamp((queueOldestAgeSecs - 30) / 100, 0, 0.3)
      : 0;
    const shippingError = this.usedRatio >= 0.999 ? 1 : 0;
    const paymentFailure = providerError * 0.8;
    const catalogFailure = catalogError >= 0.5 ? 0.3 : 0;
    const httpFailure = Math.max(catalogFailure, cartError * 0.5, shippingError * 0.8);
    const shopperFailRate = clamp(
      Math.max(httpFailure, paymentFailure, fulfillmentError),
      0,
      0.99,
    );
    const checkoutError = shopperFailRate;
    const checkoutP95 = 220 + catalogError * 850 + paymentFailure * 450 + cartError * 180 + shippingError * 650 + fulfillmentError * 300;
    const catalogP95 = 140 + Math.max(0, this.poolInUse - 2) * 50;
    const shippingP95 = 130 + this.usedRatio * 240 + shippingError * 500;
    const fulfillmentP95 = 200 + Math.max(0, processMs - 200) * 0.25;
    const shopperP95 = 140 + checkoutError * 700 + fulfillmentError * 150;

    const services: Record<string, ServiceMetric> = {
      storefront: { rps: 0.5, errors: checkoutError, p95Ms: 180 + checkoutError * 500 },
      catalog: { rps: 0.6, errors: catalogError, p95Ms: catalogP95 },
      cart: { rps: 0.5, errors: cartError, p95Ms: 110 + cartError * 250 },
      checkout: { rps: 0.5, errors: checkoutError, p95Ms: checkoutP95 },
      payment: { rps: 0.45, errors: providerError, p95Ms: providerLatency + 20 },
      shipping: { rps: 0.4, errors: shippingError, p95Ms: shippingP95 },
      fulfillment: { rps: 0.48, errors: fulfillmentError, p95Ms: fulfillmentP95 },
      shopper: { rps: 1.5, errors: shopperFailRate, p95Ms: shopperP95 },
    };

    // ≥ 1 order/s so the control's shopper_rate readiness check (spec 02 §6: ≥ 1/s) passes.
    const ordersRate = 1.5;
    const paymentFailedRate = ordersRate * Math.min(paymentFailure, shopperFailRate);
    const timeoutRate = ordersRate * Math.min(fulfillmentError, Math.max(0, shopperFailRate - paymentFailure));
    const httpErrorRate = ordersRate * Math.max(
      0,
      Math.min(shopperFailRate - paymentFailedRate / ordersRate - timeoutRate / ordersRate, 1),
    );
    const okRate = Math.max(0, ordersRate - paymentFailedRate - timeoutRate - httpErrorRate);

    const edges: EdgeMetric[] = [
      { from: "storefront", to: "checkout", rps: 0.5, errors: checkoutError, p95Ms: checkoutP95 },
      { from: "checkout", to: "cart", rps: 0.5, errors: cartError, p95Ms: 110 },
      { from: "checkout", to: "catalog", rps: 0.5, errors: catalogError, p95Ms: catalogP95 },
      { from: "checkout", to: "payment", rps: 0.45, errors: providerError, p95Ms: providerLatency },
      { from: "checkout", to: "shipping", rps: 0.4, errors: shippingError, p95Ms: shippingP95 },
      // Database client spans only exist once a connection was acquired: a
      // caller whose own pool is exhausted times out in catalog.db.acquire (an
      // internal span of the caller) and never reaches postgres. So the calls
      // to postgres thin out while their error rate and latency stay normal.
      { from: "catalog", to: "postgres", rps: 0.6 * (1 - catalogError), errors: 0, p95Ms: 140 },
      { from: "checkout", to: "postgres", rps: 0.5 * (1 - checkoutError), errors: 0, p95Ms: 120 },
      { from: "fulfillment", to: "postgres", rps: 0.48, errors: 0, p95Ms: 60 },
      { from: "checkout", to: "orders-queue", rps: 0.48, errors: checkoutError, p95Ms: 15 },
      { from: "fulfillment", to: "orders-queue", rps: processedRate, errors: fulfillmentError, p95Ms: processMs },
      { from: "payment", to: activeProvider === "secondary" ? "payment-provider-secondary" : "payment-provider-primary", rps: 0.45, errors: providerSpanError, p95Ms: providerLatency },
      { from: "shipping", to: "shipping-audit", rps: 0.4, errors: shippingError, p95Ms: shippingP95 },
    ];

    const writeRate = debugRate(this.config("shipping")) ? 1.2 : 0.4;
    const volumeErrorsRate = writeRate * shippingError;
    const volumeP95 = debugRate(this.config("shipping"))
      ? 220 + this.usedRatio * 500 + shippingError * 500
      : 35;

    return {
      services,
      shopper: {
        ordersRate,
        failRate: shopperFailRate,
        p95Ms: shopperP95,
        outcomes: {
          ok: okRate,
          http_error: httpErrorRate,
          payment_declined: paymentFailedRate,
          fulfillment_timeout: timeoutRate,
        },
      },
      poolInUse: this.poolInUse,
      poolMax,
      poolWaitTotal: this.poolWaitTotal,
      queue: {
        depth: this.queueDepth,
        oldestAgeSecs: queueOldestAgeSecs,
        enqueuedRate: 0.48,
        processedRate,
        processP95Ms: processMs,
      },
      volume: {
        usedRatio: this.usedRatio,
        writesRate: writeRate,
        errorsRate: volumeErrorsRate,
        p95Ms: volumeP95,
        errorsTotal: this.volumeErrorsTotal,
        enospcTotal: this.volumeENOSPCTotal,
      },
      edges,
    };
  }

  private integrateSpanCounters(snapshot: MetricsSnapshot, dt: number): void {
    for (const series of this.spanSeries("traces_span_metrics_calls_total", snapshot)) {
      if (!series.counterKey) continue;
      this.spanCounters.set(
        series.counterKey,
        (this.spanCounters.get(series.counterKey) ?? 0) + series.rate * dt,
      );
    }
  }

  private queryRevision(query: string): Array<{ metric: Record<string, string>; value: number }> {
    const job = query.match(/job="([^"]+)"/)?.[1] ?? "";
    const service = job.startsWith("shop/") ? job.slice("shop/".length) : "";
    if (!this.configs.has(service)) {
      throw new Error(`unknown shop revision job ${job || "<empty>"}`);
    }
    return [{
      metric: {
        job,
        service_name: service,
        revision: this.config(service).revision,
      },
      value: Date.now() / 1000,
    }];
  }

  private serviceVector(axis: "traffic" | "errors" | "latency" | "liveness"): Array<{ metric: Record<string, string>; value: number }> {
    return SERVER_SERVICES.map((service) => ({
      metric: { service_name: service },
      value: axis === "traffic"
        ? this.current.services[service].rps
        : axis === "errors"
          ? this.current.services[service].errors
          : axis === "latency"
            ? this.current.services[service].p95Ms
            : 1,
    }));
  }

  private serviceGraphVector(query: string): Array<{ metric: Record<string, string>; value: number }> {
    const failed = query.includes("traces_service_graph_request_failed_total");
    const latency = query.includes("histogram_quantile");
    return this.graphEdges().map((edge) => ({
      metric: { client: edge.from, server: edge.to },
      value: latency ? edge.p95Ms / 1000 : failed ? edge.errors : edge.rps,
    }));
  }

  private queryTopSpans(query: string): Array<{ metric: Record<string, string>; value: number }> {
    const service = query.match(/service_name="([^"]+)"/)?.[1] ?? "";
    const metric = this.current.services[service];
    if (!metric) throw new Error(`unknown service in topk query: ${service || "<empty>"}`);
    if (query.includes('status_code="STATUS_CODE_ERROR"')) {
      if (metric.errors <= 0) return [];
      return [{ metric: { span_name: operationForService(service) }, value: metric.errors }];
    }
    return [{ metric: { span_name: operationForService(service) }, value: metric.p95Ms }];
  }

  private queryGenericMetric(query: string): Array<{ metric: Record<string, string>; value: number }> {
    const rates = [...query.matchAll(/rate\(\s*([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^{}]*)\})?\s*\[/g)].map((match) => this.parsedMetric(match[1], match[2] ?? ""));

    if (query.includes("histogram_quantile(0.95")) {
      if (rates.length === 0) throw new Error(`unknown histogram pattern: ${query}`);
      const parsed = rates[0];
      this.requireKnownMetric(parsed.metric);
      return [{ metric: {}, value: this.metricP95(parsed.metric, parsed.matchers) }];
    }

    if (query.includes("clamp_min") && rates.length >= 2) {
      const numerator = rates[0];
      const denominator = this.findDenominator(rates, numerator) ?? rates[1];
      this.requireKnownMetric(numerator.metric);
      this.requireKnownMetric(denominator.metric);
      const denominatorValue = this.metricRate(denominator.metric, denominator.matchers);
      const numeratorValue = this.metricRate(numerator.metric, numerator.matchers);
      return [{
        metric: {},
        value: numeratorValue / Math.max(denominatorValue, 0.001),
      }];
    }

    if (query.includes("increase(")) {
      const match = query.match(/increase\(\s*([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^{}]*)\})?\s*\[/);
      if (!match) throw new Error(`unknown increase pattern: ${query}`);
      const parsed = this.parsedMetric(match[1], match[2] ?? "");
      this.requireKnownMetric(parsed.metric);
      return [{ metric: {}, value: this.metricRate(parsed.metric, parsed.matchers) * 30 }];
    }

    if (rates.length > 0) {
      const parsed = rates[0];
      this.requireKnownMetric(parsed.metric);
      return [{ metric: {}, value: this.metricRate(parsed.metric, parsed.matchers) }];
    }

    const direct = query.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^{}]*)\})?$/);
    if (direct) {
      const parsed = this.parsedMetric(direct[1], direct[2] ?? "");
      this.requireKnownMetric(parsed.metric);
      const series = this.seriesForMetric(parsed.metric).filter((item) => matchesLabels(item.labels, parsed.matchers));
      return series.map((item) => ({ metric: item.labels, value: item.value }));
    }

    const sumValue = query.match(/^sum\(\s*([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^{}]*)\})?\s*\)$/);
    if (sumValue) {
      const parsed = this.parsedMetric(sumValue[1], sumValue[2] ?? "");
      this.requireKnownMetric(parsed.metric);
      return [{ metric: {}, value: this.metricValue(parsed.metric, parsed.matchers) }];
    }

    throw new Error(`unknown PromQL pattern: ${query}`);
  }

  private findDenominator(rates: ParsedMetric[], numerator: ParsedMetric): ParsedMetric | null {
    return rates.find((candidate, index) => {
      if (index === 0) return false;
      if (canonicalMetricName(candidate.metric) !== canonicalMetricName(numerator.metric)) return false;
      return !candidate.matchers.some((matcher) =>
        (matcher.name === "status_code" && matcher.operator === "=") ||
        (matcher.name === "outcome" && matcher.operator !== "=") ||
        (matcher.name === "reason" && matcher.operator === "="),
      );
    }) ?? null;
  }

  private parsedMetric(metric: string, selector: string): ParsedMetric {
    return { metric, selector, matchers: parseMatchers(selector) };
  }

  private requireKnownMetric(metric: string): void {
    const canonical = canonicalMetricName(metric);
    if (!KNOWN_METRICS.has(canonical)) {
      throw new Error(`unknown metric ${metric}`);
    }
  }

  private metricRate(metric: string, matchers: LabelMatcher[]): number {
    return this.seriesForMetric(metric)
      .filter((series) => matchesLabels(series.labels, matchers))
      .reduce((total, series) => total + series.rate, 0);
  }

  private metricValue(metric: string, matchers: LabelMatcher[]): number {
    return this.seriesForMetric(metric)
      .filter((series) => matchesLabels(series.labels, matchers))
      .reduce((total, series) => total + series.value, 0);
  }

  private metricP95(metric: string, matchers: LabelMatcher[]): number {
    const values = this.seriesForMetric(metric)
      .filter((series) => matchesLabels(series.labels, matchers))
      .map((series) => series.p95)
      .filter((value) => value > 0);
    return values.length > 0 ? Math.max(...values) : 0;
  }

  private seriesForMetric(metric: string): MetricSeries[] {
    const canonical = canonicalMetricName(metric);
    if (canonical === "traces_span_metrics_calls_total" || canonical === "traces_span_metrics_duration_milliseconds") {
      return this.spanSeries(canonical, this.current);
    }
    if (canonical === "traces_service_graph_request_total" || canonical === "traces_service_graph_request_failed_total" || canonical === "traces_service_graph_request_server_seconds") {
      return this.graphMetricSeries(canonical, this.current);
    }
    return this.appMetricSeries(canonical, this.current);
  }

  private spanSeries(metric: string, snapshot: MetricsSnapshot): MetricSeries[] {
    const series: MetricSeries[] = [];
    const add = (labels: Record<string, string>, rate: number, p95: number, errors: number) => {
      const base = { ...labels };
      const key = this.spanCounterKey(base, "ok");
      series.push({
        labels: { ...base, status_code: "STATUS_CODE_OK" },
        rate: rate * (1 - errors),
        value: this.spanCounters.get(key) ?? 0,
        p95,
        counterKey: key,
      });
      if (errors > 0) {
        const errorKey = this.spanCounterKey(base, "error");
        series.push({
          labels: { ...base, status_code: "STATUS_CODE_ERROR" },
          rate: rate * errors,
          value: this.spanCounters.get(errorKey) ?? 0,
          p95,
          counterKey: errorKey,
        });
      }
    };

    for (const service of SERVER_SERVICES) {
      const metric = snapshot.services[service];
      add({ service_name: service, span_kind: "SPAN_KIND_SERVER" }, metric.rps, metric.p95Ms, metric.errors);
    }

    for (const edge of snapshot.edges) {
      let spanKind = "SPAN_KIND_CLIENT";
      const labels: Record<string, string> = {
        service_name: edge.from,
        peer_service: edge.to,
        span_kind: spanKind,
      };
      if (edge.to === "postgres") labels.db_system = "postgresql";
      if (edge.to === "orders-queue") {
        spanKind = edge.from === "fulfillment" ? "SPAN_KIND_CONSUMER" : "SPAN_KIND_PRODUCER";
        labels.span_kind = spanKind;
        labels.messaging_system = "shopqueue";
        labels.messaging_destination_name = "orders";
      }
      add(labels, edge.rps, edge.p95Ms, edge.errors);
    }

    return series;
  }

  private graphMetricSeries(metric: string, snapshot: MetricsSnapshot): MetricSeries[] {
    return this.graphEdges().map((edge) => {
      const value = metric === "traces_service_graph_request_total"
        ? edge.rps
        : metric === "traces_service_graph_request_failed_total"
          ? edge.rps * edge.errors
          : edge.p95Ms / 1000;
      return {
        labels: { client: edge.from, server: edge.to },
        rate: value,
        value: value * Math.max(this.logicalSeconds, 1),
        p95: edge.p95Ms / 1000,
      };
    });
  }

  private appMetricSeries(metric: string, snapshot: MetricsSnapshot): MetricSeries[] {
    switch (metric) {
      case "shop_db_pool_in_use":
        return [{ labels: { pool: "catalog" }, rate: 0, value: snapshot.poolInUse, p95: snapshot.poolInUse }];
      case "shop_db_pool_max":
        return [{ labels: { pool: "catalog" }, rate: 0, value: snapshot.poolMax, p95: snapshot.poolMax }];
      case "shop_db_pool_wait_total":
        return [{ labels: { pool: "catalog" }, rate: 0, value: snapshot.poolWaitTotal, p95: 0 }];
      case "shop_queue_depth":
        return [{ labels: { queue: "orders" }, rate: 0, value: snapshot.queue.depth, p95: snapshot.queue.depth }];
      case "shop_queue_oldest_age_seconds":
        return [{ labels: { queue: "orders" }, rate: 0, value: snapshot.queue.oldestAgeSecs, p95: snapshot.queue.oldestAgeSecs }];
      case "shop_queue_enqueued_total":
        return [{ labels: { queue: "orders" }, rate: snapshot.queue.enqueuedRate, value: this.queueEnqueuedTotal, p95: 0 }];
      case "shop_queue_processed_total":
        return [{ labels: { queue: "orders" }, rate: snapshot.queue.processedRate, value: this.queueProcessedTotal, p95: snapshot.queue.processP95Ms }];
      case "shop_queue_process_duration_ms":
        return [{ labels: { queue: "orders" }, rate: snapshot.queue.processedRate, value: this.queueProcessedTotal, p95: snapshot.queue.processP95Ms }];
      case "shop_volume_used_ratio":
        return [{ labels: { volume: "shipping-audit" }, rate: 0, value: snapshot.volume.usedRatio, p95: snapshot.volume.usedRatio }];
      case "shop_volume_write_errors_total":
        return [
          { labels: { volume: "shipping-audit", reason: "enospc" }, rate: snapshot.volume.errorsRate, value: this.volumeENOSPCTotal, p95: 0 },
          { labels: { volume: "shipping-audit", reason: "other" }, rate: 0, value: Math.max(0, this.volumeErrorsTotal - this.volumeENOSPCTotal), p95: 0 },
        ];
      case "shop_volume_write_duration_ms_milliseconds":
        return [{ labels: { volume: "shipping-audit" }, rate: snapshot.volume.writesRate, value: this.volumeWritesTotal, p95: snapshot.volume.p95Ms }];
      case "shop_shopper_orders_total":
        return Object.entries(snapshot.shopper.outcomes).map(([outcome, rate]) => ({
          labels: { outcome },
          rate,
          value: this.shopperOutcomeTotals[outcome] ?? 0,
          p95: snapshot.shopper.p95Ms,
        }));
      case "shop_shopper_order_duration_ms":
        return [{
          labels: { step: "fulfilled" },
          rate: snapshot.shopper.ordersRate,
          value: Object.values(this.shopperOutcomeTotals).reduce((total, value) => total + value, 0),
          p95: snapshot.shopper.p95Ms,
        }];
      case "shop_shopper_inflight":
        return [{ labels: {}, rate: 0, value: 0.1, p95: 0 }];
      default:
        return [];
    }
  }

  private spanCounterKey(labels: Record<string, string>, status: "ok" | "error"): string {
    return `${status}|${Object.entries(labels).sort(([left], [right]) => left.localeCompare(right)).map(([key, value]) => `${key}=${value}`).join("|")}`;
  }

  private graphEdges(): EdgeMetric[] {
    const edges = this.current.edges;
    return edges.filter((edge) =>
      (edge.from === "storefront" && edge.to === "checkout") ||
      (edge.from === "checkout" && ["cart", "catalog", "payment", "shipping"].includes(edge.to)) ||
      (edge.from === "payment" && edge.to.startsWith("payment-provider")),
    );
  }

  private async sendErrorLogs(): Promise<void> {
    // Every service emits an INFO heartbeat line (the real shop logs constantly,
    // and the control's readiness check wants to see *some* log traffic);
    // failing services emit an ERROR line instead.
    const timeUnixNano = String(BigInt(Date.now()) * 1_000_000n);
    const payload = {
      resourceLogs: SERVER_SERVICES.map((service) => {
        const failing = this.serviceFailing(service);
        return {
          resource: {
            attributes: [{ key: "service.name", value: { stringValue: service } }],
          },
          scopeLogs: [{
            logRecords: [{
              timeUnixNano,
              severityNumber: failing ? 17 : 9,
              severityText: failing ? "ERROR" : "INFO",
              body: { stringValue: failing ? this.errorMessage(service) : `${service} heartbeat ok` },
            }],
          }],
        };
      }),
    };
    try {
      await fetch(`http://127.0.0.1:${this.logSinkPort}/internal/otlp/v1/logs`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch {
      // The sink is optional while the fake is used by itself.
    }
  }

  private errorMessage(service: string): string {
    if (service === "catalog") return `catalog database pool exhausted (${this.current.poolInUse.toFixed(1)}/${this.current.poolMax})`;
    if (service === "payment") return "payment provider returned an upstream error";
    if (service === "shipping") return "shipping audit volume is full";
    if (service === "fulfillment") return `fulfillment queue oldest age ${this.current.queue.oldestAgeSecs.toFixed(1)}s`;
    return `${service} request failed`;
  }

  private addTrace(scheduledLogicalSeconds: number, snapshot: MetricsSnapshot): void {
    this.traceSequence++;
    const traceID = ((BigInt(this.seed >>> 0) << 96n) | BigInt(this.traceSequence)).toString(16).padStart(32, "0");
    const startTime = Math.round((Date.now() * 1000) - Math.max(0, this.logicalSeconds - scheduledLogicalSeconds) * 1_000_000);
    const processes: Record<string, { serviceName: string }> = {};
    const spans: JaegerSpan[] = [];
    const addSpan = (service: string, parentSpanID: string | null, durationMs: number): string => {
      const index = spans.length + 1;
      const spanID = (BigInt(this.traceSequence) * 1000n + BigInt(index)).toString(16).padStart(16, "0");
      const processID = `p${index}`;
      processes[processID] = { serviceName: service };
      const start = startTime + spans.reduce((total, span) => total + span.duration, 0);
      const metric = snapshot.services[service];
      const providerError = service === "payment-provider-primary" || service === "payment-provider-secondary"
        ? snapshot.services.payment.errors
        : metric?.errors ?? 0;
      const error = providerError >= 0.01;
      const tags: JaegerTag[] = [];
      if (error) {
        tags.push({ key: "error", type: "bool", value: true });
        tags.push({ key: "http.status_code", type: "int", value: service === "shipping" ? 503 : 500 });
      }
      spans.push({
        traceID,
        spanID,
        operationName: operationForService(service),
        startTime: start,
        duration: Math.round(Math.max(1, durationMs) * 1000),
        tags,
        references: parentSpanID ? [{ refType: "CHILD_OF", traceID, spanID: parentSpanID }] : [],
        processID,
      });
      return spanID;
    };

    const root = addSpan("storefront", null, snapshot.shopper.p95Ms);
    const checkout = addSpan("checkout", root, snapshot.services.checkout.p95Ms * 0.75);
    addSpan("cart", checkout, snapshot.services.cart.p95Ms * 0.55);
    addSpan("catalog", checkout, snapshot.services.catalog.p95Ms * 0.55);
    const payment = addSpan("payment", checkout, snapshot.services.payment.p95Ms * 0.7);
    addSpan(
      snapshot.edges.find((edge) => edge.from === "payment" && edge.to.startsWith("payment-provider"))?.to ?? "payment-provider-primary",
      payment,
      snapshot.services.payment.p95Ms * 0.7,
    );
    addSpan("shipping", checkout, snapshot.services.shipping.p95Ms * 0.55);

    const stored: StoredTrace = { trace: { traceID, spans, processes }, startTimeMicros: startTime };
    this.traces.set(traceID, stored);
    while (this.traces.size > 240) {
      const oldest = this.traces.keys().next().value;
      if (!oldest) break;
      this.traces.delete(oldest);
    }
  }
}

function debugRate(config: ConfigState): boolean {
  return scalarString(config.knobs["audit.verbosity"], "info") === "debug";
}

async function readJSON(request: Request): Promise<unknown> {
  const body = await request.text();
  if (body.length > 1_000_000) throw new Error("request body too large");
  return JSON.parse(body);
}

function knownService(value: string): value is (typeof SHOP_SERVICES)[number] {
  return (SHOP_SERVICES as readonly string[]).includes(value);
}

function shopTarget(request: Request): { service: string; path: string } | null {
  const url = new URL(request.url);
  const explicit = url.pathname.match(/^\/svc\/([^/]+)(\/.*)?$/);
  if (explicit) {
    return { service: explicit[1], path: explicit[2] || "/" };
  }

  const firstPath = url.pathname.match(/^\/([^/]+)(\/.*)?$/);
  if (firstPath && knownService(firstPath[1])) {
    return { service: firstPath[1], path: firstPath[2] || "/" };
  }

  const host = (request.headers.get("host") ?? "").split(":")[0];
  if (knownService(host)) return { service: host, path: url.pathname };
  if (["/healthz", "/readyz", "/", "/api/products", "/products"].includes(url.pathname)) {
    return { service: "storefront", path: url.pathname };
  }
  return null;
}

async function handleShop(request: Request, simulation: Simulation): Promise<Response> {
  const target = shopTarget(request);
  if (!target || !knownService(target.service)) return jsonResponse({ error: "unknown shop service" }, 404);
  const service = target.service;
  const path = target.path;

  if (path === "/internal/config" && request.method === "GET") {
    return jsonResponse(simulation.getConfig(service));
  }

  if (path === "/internal/config" && request.method === "PUT") {
    try {
      const body = await readJSON(request) as { revision?: unknown; knobs?: unknown };
      if (typeof body.revision !== "string" || typeof body.knobs !== "object" || body.knobs === null || Array.isArray(body.knobs)) {
        return jsonResponse({ error: "body must be {revision, knobs}" }, 400);
      }
      const knobs: Knobs = {};
      for (const [name, value] of Object.entries(body.knobs as Record<string, unknown>)) {
        if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
          knobs[name] = value;
        } else {
          return jsonResponse({ error: `knob ${name} must be a scalar` }, 400);
        }
      }
      const result = simulation.putConfig(service, body.revision, knobs);
      return result ? jsonResponse(result) : jsonResponse({ error: "unknown shop service" }, 404);
    } catch (error) {
      return jsonResponse({ error: String(error instanceof Error ? error.message : error) }, 400);
    }
  }

  if (path === "/healthz" || path === "/readyz") {
    const failing = simulation.serviceFailing(service);
    return jsonResponse({ status: failing ? "error" : "ok" }, failing ? 503 : 200);
  }

  if (path === "/internal/volume/rotate" && request.method === "POST" && service === "shipping") {
    simulation.rotateVolume();
    return jsonResponse({ freed_bytes: 64 * 1024 * 1024 });
  }

  if (path === "/api/products" || path === "/products") {
    const failing = simulation.serviceFailing("storefront") || simulation.serviceFailing("catalog");
    return jsonResponse({ products: failing ? [] : [{ id: "coffee", name: "NightWatch Coffee" }] }, failing ? 503 : 200);
  }

  if (path === "/") {
    const failing = simulation.serviceFailing("storefront");
    return jsonResponse({ status: failing ? "error" : "ok", service: "storefront" }, failing ? 503 : 200);
  }

  return jsonResponse({ error: "not found" }, 404);
}

async function readPrometheusQuery(request: Request): Promise<string> {
  const url = new URL(request.url);
  const fromURL = url.searchParams.get("query");
  if (fromURL) return fromURL;
  if (request.method !== "POST") return "";
  const body = await request.text();
  if (!body) return "";
  const contentType = request.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    const parsed = JSON.parse(body) as { query?: unknown };
    return typeof parsed.query === "string" ? parsed.query : "";
  }
  return new URLSearchParams(body).get("query") ?? "";
}

function promVectorResponse(result: Array<{ metric: Record<string, string>; value: number }>): Response {
  const now = Date.now() / 1000;
  return jsonResponse({
    status: "success",
    data: {
      resultType: "vector",
      result: result.map((item) => ({
        metric: item.metric,
        value: [now, formatPromValue(item.value)],
      })),
    },
  });
}

async function handlePrometheus(request: Request, simulation: Simulation): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname === "/-/ready" && request.method === "GET") {
    return textResponse("Prometheus Server is Ready\n");
  }
  if (url.pathname === "/api/v1/label/__name__/values" && request.method === "GET") {
    return jsonResponse({ status: "success", data: simulation.metricNames() });
  }
  if (url.pathname !== "/api/v1/query" || (request.method !== "GET" && request.method !== "POST")) {
    return jsonResponse({ status: "error", errorType: "not_found", error: "not found" }, 404);
  }
  try {
    const query = await readPrometheusQuery(request);
    return promVectorResponse(simulation.queryPrometheus(query));
  } catch (error) {
    const message = String(error instanceof Error ? error.message : error);
    console.error(`[fake-stack] PromQL rejected: ${message}`);
    return jsonResponse({ status: "error", errorType: "bad_data", error: message }, 400);
  }
}

async function handleJaeger(request: Request, simulation: Simulation): Promise<Response> {
  const url = new URL(request.url);
  if (url.pathname === "/" && request.method === "GET") {
    return textResponse("Jaeger UI (fake-stack)\n");
  }
  if (url.pathname === "/api/services" && request.method === "GET") {
    return jsonResponse({ data: [...SHOP_SERVICES] });
  }
  if (url.pathname === "/api/traces" && request.method === "GET") {
    const traces = simulation.tracesFor(
      url.searchParams.get("service") ?? "",
      parseDurationSeconds(url.searchParams.get("lookback")),
      Number(url.searchParams.get("limit") ?? "20") || 20,
      (url.searchParams.get("tags") ?? "").includes('"error":"true"'),
    );
    return jsonResponse({ data: traces });
  }
  const traceMatch = url.pathname.match(/^\/api\/traces\/([0-9a-fA-F]{32})$/);
  if (traceMatch && request.method === "GET") {
    const trace = simulation.traceByID(traceMatch[1]);
    return trace ? jsonResponse({ data: [trace] }) : jsonResponse({ data: [] }, 404);
  }
  return jsonResponse({ error: "not found" }, 404);
}

function parseArguments(argv: string[]): { speed: number; seed: number } {
  let speed = 1;
  let seed = 1;
  for (let index = 0; index < argv.length; index++) {
    const argument = argv[index];
    if (argument === "--speed") {
      speed = Number(argv[++index]);
    } else if (argument === "--seed") {
      seed = Number(argv[++index]);
    } else if (argument === "--help" || argument === "-h") {
      console.log("Usage: bun poc/tools/fake-stack.ts [--speed N] [--seed N]");
      process.exit(0);
    } else {
      throw new Error(`unknown argument ${argument}`);
    }
  }
  if (!Number.isFinite(speed) || speed <= 0) throw new Error("--speed must be a positive number");
  if (!Number.isFinite(seed)) throw new Error("--seed must be a number");
  return { speed, seed: Math.trunc(seed) };
}

const args = parseArguments(process.argv.slice(2));
const sinkPortValue = Number(process.env.NIGHTWATCH_LOG_SINK_PORT ?? "3001");
const logSinkPort = Number.isInteger(sinkPortValue) && sinkPortValue > 0 ? sinkPortValue : 3001;
const simulation = new Simulation(args.speed, args.seed, logSinkPort);

const prometheusServer = Bun.serve({
  hostname: "127.0.0.1",
  port: 19090,
  fetch: (request) => handlePrometheus(request, simulation),
});
const jaegerServer = Bun.serve({
  hostname: "127.0.0.1",
  port: 19686,
  fetch: (request) => handleJaeger(request, simulation),
});
const shopServer = Bun.serve({
  hostname: "127.0.0.1",
  port: 19080,
  fetch: (request) => handleShop(request, simulation),
});

const tickTimer = setInterval(() => simulation.tick(), 1000);

console.log(`[fake-stack] speed=${args.speed} seed=${args.seed}`);
console.log("[fake-stack] Prometheus: http://127.0.0.1:19090");
console.log("[fake-stack] Jaeger:     http://127.0.0.1:19686");
console.log("[fake-stack] Shop:        http://127.0.0.1:19080");
console.log("NIGHTWATCH_PROMETHEUS_URL=http://127.0.0.1:19090 NIGHTWATCH_JAEGER_URL=http://127.0.0.1:19686 NIGHTWATCH_SHOP_URL_TEMPLATE=http://127.0.0.1:19080/svc/%s");

function shutdown(): void {
  clearInterval(tickTimer);
  prometheusServer.stop(true);
  jaegerServer.stop(true);
  shopServer.stop(true);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

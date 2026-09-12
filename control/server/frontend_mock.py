"""Explicitly enabled, in-memory frontend demo. Never calls a real service."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from time_utils import timestamp


def identifier(kind):
    return f"mock-{kind}-{uuid4().hex[:12]}"


CARD_TARGETS = {
    "payment_failure_ramp": ("payment", "restore_payment_config", "payment"),
    "payment_failure_with_cart_decoy": ("payment", "restore_payment_config", "payment"),
    "recommendation_cache_growth": ("recommendation", "restore_recommendation_config", "recommendation"),
    "kafka_consumer_backlog": ("kafka", "restore_kafka_config", "checkout"),
    "email_memory_growth": ("email", "restore_email_config", "email"),
}


class MockStore:
    def __init__(self, error, graph_provider):
        self.error = error
        self.graph_provider = graph_provider
        self.lock = RLock()
        self.requests = {}
        self.operations = {}
        self.incidents = {}
        self.journals = {}
        self.incident_cards = {}
        self.instances = []
        self.generation = 0
        self.current = None
        self.cards = self.catalog()
        self.new_run()

    def new_run(self):
        self.run = {"id": identifier("run"), "started_at": timestamp(),
                    "baseline": {"status": "ready", "collected_secs": 120, "required_secs": 120}}
        self.revision = 0
        self.journals[self.run["id"]] = []
        self.current = None

    @staticmethod
    def catalog():
        rows = [
            ("payment_failure_ramp", "付款失敗率逐步攀升", "payment_auth_failure", "paymentFailure", "ratio", "primary", "medium", [60, 120]),
            ("recommendation_cache_growth", "推薦快取異常膨脹", "recommendation_cache_growth", "recommendationCacheFailure", "boolean", "secondary", "hard", [90, 240]),
            ("kafka_consumer_backlog", "訂單後處理積壓", "kafka_consumer_backlog", "kafkaQueueProblems", "count", "backup", "hard", [90, 240]),
            ("payment_failure_with_cart_decoy", "付款劣化與購物車假線索", "payment_auth_failure", "paymentFailure", "ratio", "off", "hard", [60, 120]),
            ("email_memory_growth", "訂單確認信記憶體成長", "email_memory_growth", "emailMemoryLeak", "count", "off", "hard", [120, 240]),
        ]
        return [{"id": key, "title_zh": f"[模擬資料] {title}", "symptom_zh": f"[模擬資料] {title}",
                 "story_zh": "僅供前端介面操作，不會注入真實故障", "difficulty": difficulty,
                 "on_stage": stage, "lease_secs": 1200, "expect": {"detect_after_secs": detect},
                 "mechanism_family": family, "curve_preview": [[i * 5, i / 23] for i in range(24)],
                 "curve_unit": unit, "curve_knob": knob}
                for key, title, family, knob, unit, stage, difficulty, detect in rows]

    def readiness(self):
        active = any(item["status"] == "active" for item in self.instances)
        closed = self.current is not None and self.current["closed_at"] is not None
        step = "已結案,按『開始下一輪』" if closed else "故障進行中:模擬故障" if active else "可以按故障卡"
        ids = ["prometheus_reachable", "jaeger_reachable", "logstore_receiving", "nodes_alive", "shopper_rate", "baseline", "model", "fault_clear"]
        return {"ready": not active and not closed,
                "checks": [{"id": key, "status": "failed" if key == "model" else "waiting" if key == "fault_clear" and active else "ok",
                            "detail_zh": "[模擬資料] 未連接模型" if key == "model" else "[模擬資料] 故障進行中" if key == "fault_clear" and active else "[模擬資料] 介面示範"} for key in ids],
                "next_step_zh": step}

    def state(self):
        graph = self.graph_provider().model_dump(by_alias=True)
        return {"schema_version": "nightwatch.state.v2", "server_now": timestamp(), "run": deepcopy(self.run),
                "readiness": self.readiness(), "model": {"available": False, "model": "", "effort": ""},
                "graph_now": graph,
                "faults": {"instances": deepcopy(self.instances), "generation": self.generation},
                "incident": deepcopy(self.current),
                "capabilities": {"nodes": [{"id": node["id"], "kind": node["kind"], "layout": {"row": i // 3, "col": i % 3}} for i, node in enumerate(graph["nodes"])],
                                 "tools": [], "actions": [], "max_calls": 0, "hard_timeout_secs": 0,
                                 "links": {"storefront": None, "jaeger": None, "grafana": None}},
                "next_step_zh": self.readiness()["next_step_zh"]}

    def stream_snapshot(self):
        with self.lock:
            return self.state(), deepcopy(self.journals[self.run["id"]])

    def commit(self, kind, message):
        base = self.revision
        self.revision += 1
        incident = self.current
        if incident:
            incident["revision"] = self.revision
        event = {"id": identifier("event"), "seq": self.revision, "type": kind, "occurred_at": timestamp(),
                 "t": 0, "actor": {"kind": "go", "id": "mock-frontend-api"},
                 "timeline": {"title": "[模擬資料] " + message, "summary_zh": "[模擬資料] " + message},
                 "refs": {"node_ids": [], "evidence_ids": []}}
        commit = {"schema_version": "nightwatch.incident-commit.v2", "incident_id": incident["id"] if incident else "",
                  "run_id": self.run["id"], "base_revision": base, "revision": self.revision, "event": event,
                  "fault_instances": deepcopy(self.instances)}
        if incident:
            commit["incident"] = deepcopy(incident)
        self.journals[self.run["id"]].append(commit)

    def incident(self, key):
        if key not in self.incidents:
            raise self.error(404, "not_found", "找不到這件模擬事故")
        return self.incidents[key]

    def read(self, name, params, query):
        with self.lock:
            if name == "catalog":
                result = self.cards
            elif name == "faults":
                result = {"instances": self.instances, "generation": self.generation}
            elif name == "logs":
                result = [{"time": self.run["started_at"], "service": "payment", "severity": "ERROR",
                           "body": "[模擬資料] 付款請求逾時；沒有呼叫真實服務", "trace_id": ""}]
                if query.get("service"):
                    result = [row for row in result if row["service"] == query["service"]]
                result = result[:int(query.get("limit", 20))]
            elif name == "operation":
                if params["id"] not in self.operations:
                    raise self.error(404, "not_found", "找不到這項模擬操作")
                result = self.operations[params["id"]]
            elif name == "incidents":
                fields = ("id", "card_id", "phase", "outcome", "detected_at", "closed_at")
                result = [{key: item[key] for key in fields} for item in reversed(list(self.incidents.values()))]
            else:
                incident = self.incident(params["id"])
                if name == "incident":
                    result = incident
                elif name == "snapshots":
                    result = {"pinned_window": incident["pinned_window"], "snapshots": []}
                elif name == "incident_events":
                    result = [event for event in self.journals[incident["run_id"]] if event["incident_id"] == incident["id"]]
                elif name == "timeline":
                    result = self.timeline(incident)
                elif name == "report":
                    if incident["closed_at"] is None:
                        raise self.error(404, "not_found", "模擬事故尚未結案，報告尚未產生")
                    result = self.report(incident)
                else:
                    raise self.error(404, "not_found", "找不到這個 API")
            return deepcopy(result)

    def timeline(self, incident):
        root = CARD_TARGETS[self.incident_cards[incident["id"]]][0]
        return {"detected_at": incident["detected_at"], "from_t": 0, "to_t": 0,
                "system": [{"t": e["event"]["t"], "seq": e["revision"], "kind": e["event"]["type"],
                            "label_zh": e["event"]["timeline"]["title"]} for e in self.journals[incident["run_id"]] if e["incident_id"] == incident["id"]],
                "ai": {"status": "rejected", "onset": {"node": root, "t": 0, "signal": "err"},
                       "propagation": [], "reasons": ["[模擬資料] 未執行 AI 調查"]},
                "measured": {"deviations": []}}

    def report(self, incident):
        root = CARD_TARGETS[self.incident_cards[incident["id"]]][0]
        return {"incident_id": incident["id"], "run_id": incident["run_id"], "card_id": incident["card_id"],
                "outcome": incident["outcome"], "counted_recovery_success": incident["outcome"] == "recovered",
                "detected_at": incident["detected_at"], "closed_at": incident["closed_at"],
                "durations": {"inject_to_detect_secs": None, "detect_to_close_secs": None, "approve_to_verified_secs": None},
                "root_cause": {"node": root, "mechanism": "mock", "summary_zh": "[模擬資料] 未執行真實根因調查"},
                "confidence": 0, "audit": incident["audit"],
                "ai_timeline": {"onset": {"node": root, "t": 0, "signal": "err", "evidence_id": "mock-no-evidence"}, "propagation": []},
                "measured": {"deviations": []},
                "comparison": {key: None for key in ("ai_onset_minus_truth_secs", "ai_onset_minus_measured_secs", "detect_minus_truth_secs", "detect_minus_ai_onset_secs")},
                "early_sign": None, "action": None, "verification": incident["verification"], "residual": [],
                "pinned_window": {"from_t": 0, "to_t": 0, "step_secs": 5, "count": 0}}

    def create_incident(self, card):
        at = timestamp()
        _, action, target = CARD_TARGETS[card["id"]]
        incident = {"id": identifier("incident"), "run_id": self.run["id"], "phase": "awaiting_approval", "outcome": None,
                    "detected_at": at, "closed_at": None, "card_id": "",
                    "pinned_window": {"from": at, "to": at, "from_t": 0, "to_t": 0, "count": 0},
                    "detection": {"source": "builtin_graph", "rule": "R1", "signals": [], "summary_zh": "[模擬資料] 示範事故，不是監測結果"},
                    "nodes": [], "evidence": [], "hypothesis": None,
                    "audit": {"status": "passed", "timeline_status": "rejected", "checks": []},
                    "proposal": {"id": identifier("proposal"), "action": action, "target": target,
                                 "description_zh": "[模擬資料] 模擬修復操作", "scope_zh": "僅修改記憶體中的示範資料", "evidence_ids": [], "verify_zh": "[模擬資料] 立即產生兩個示範驗證窗口"},
                    "approval": {"status": "pending", "proposal_id": "", "at": None},
                    "execution": {"status": "pending", "steps": []}, "verification": {"windows": [], "safety_recovery": False},
                    "usage": {"calls": 0, "elapsed_secs": 0, "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0}, "revision": self.revision}
        self.incidents[incident["id"]] = incident
        self.incident_cards[incident["id"]] = card["id"]
        self.current = incident
        self.commit("incident.detected", "建立示範事故")
        self.commit("action.proposed", "等待操作員批准示範提案")

    def close_incident(self, incident, outcome):
        incident["phase"] = "recovered" if outcome == "recovered" else "closed"
        incident["outcome"] = outcome
        incident["closed_at"] = timestamp()
        incident["card_id"] = self.incident_cards[incident["id"]]
        self.commit("incident.completed", "示範事故已結束")

    def restore(self, instances):
        for item in instances:
            if item["status"] == "active":
                item["status"] = "restored"
                item["revision"] = "v1"
                self.generation += 1

    def execute(self, name, params, body, path):
        with self.lock:
            request_id = body["request_id"]
            signature = (path, body)
            if request_id in self.requests:
                old_signature, response = self.requests[request_id]
                if old_signature != signature:
                    raise self.error(400, "invalid_request", "同一 request_id 不可用於不同內容")
                return deepcopy(response)
            result = {}
            if name == "inject":
                card = next((c for c in self.cards if c["id"] == body["card_id"]), None)
                if card is None:
                    raise self.error(404, "card_unknown", "找不到這張故障卡")
                if self.current and self.current["closed_at"] is None:
                    raise self.error(409, "incident_active", "模擬事故仍在進行")
                if any(item["status"] == "active" for item in self.instances):
                    raise self.error(409, "fault_active", "仍有模擬故障未還原")
                if not self.readiness()["ready"]:
                    raise self.error(409, "round_not_ready", "請先開始下一輪")
                duration = body.get("lease_secs", card["lease_secs"])
                item = {"instance_id": identifier("fault"), "card_id": card["id"], "status": "active", "revision": "v2",
                        "applied_at": timestamp(), "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=duration)).isoformat().replace("+00:00", "Z"),
                        "curve_progress": 0, "elapsed_secs": 0, "ttl_remaining_secs": duration, "detected_after_secs": 0,
                        "mitigated": False, "owned_resources": []}
                self.instances.append(item);self.generation += 1
                self.create_incident(card)
                result["instance_id"] = item["instance_id"]
            elif name in ("restore", "restore_all"):
                if name == "restore":
                    selected = [item for item in self.instances if item["instance_id"] == params["id"]]
                    if not selected:
                        raise self.error(404, "not_found", "找不到這個模擬故障實例")
                else:
                    if self.current and self.current["closed_at"] is None and not body["force"]:
                        raise self.error(409, "incident_active", "事故進行中；force 才會中止並清理")
                    selected = self.instances
                self.restore(selected)
                self.commit("fault.restored", "已還原示範故障；不算修復")
                if name == "restore_all" and self.current and self.current["closed_at"] is None:
                    self.close_incident(self.current, "aborted_by_operator")
            elif name == "round":
                if self.current is None:
                    raise self.error(409, "round_not_needed", "目前沒有事故，不需要換輪")
                if self.current["closed_at"] is None or any(item["status"] == "active" for item in self.instances):
                    raise self.error(409, "cleanup_not_verified", "請先結束事故並還原模擬故障")
                self.instances = [];self.generation += 1;self.new_run()
                self.commit("run.started", "新示範回合開始")
                result["run_id"] = self.run["id"]
            else:
                incident = self.incident(params["id"])
                if name == "approve":
                    if body["proposal_id"] != incident["proposal"]["id"]:
                        raise self.error(409, "proposal_stale", "提案已變更")
                    if incident is not self.current or incident["phase"] != "awaiting_approval":
                        raise self.error(409, "approval_not_allowed", "目前不能批准這個提案")
                    incident["approval"] = {"status": "approved", "proposal_id": body["proposal_id"], "at": timestamp()}
                    self.commit("approval.recorded", "批准示範提案")
                    incident["phase"] = "executing";self.commit("action.started", "模擬修復開始")
                    incident["execution"] = {"status": "done", "steps": []}
                    incident["phase"] = "verifying";self.restore(self.instances)
                    self.commit("action.completed", "模擬修復完成，未操作真實服務")
                    for index in (1, 2):
                        incident["verification"]["windows"].append({"index": index, "passed": True, "observed": {}})
                        self.commit("verification.window_completed", f"示範驗證窗口 {index} 完成")
                    self.close_incident(incident, "recovered")
                elif name == "abort":
                    if incident is not self.current or incident["closed_at"] is not None:
                        raise self.error(409, "approval_not_allowed", "事故已結束，不能再中止")
                    self.restore(self.instances);self.commit("operator.aborted", "操作員中止示範事故")
                    self.close_incident(incident, "aborted_by_operator")
            operation_id = identifier("operation")
            self.operations[operation_id] = {"status": "done", "step_zh": "[模擬資料] 操作已完成，未呼叫真實服務"}
            result["operation_id"] = operation_id
            self.requests[request_id] = (deepcopy(signature), deepcopy(result))
            return result

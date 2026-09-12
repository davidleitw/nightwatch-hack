"""Read-only console projections of monitor observations and saved investigations."""
from copy import deepcopy
from datetime import datetime
from uuid import uuid4

from time_utils import timestamp


def saved_snapshots(detail):
    return [{"evidence_id": evidence["id"], "snapshot": deepcopy(evidence["result"])}
            for evidence in detail["evidence"]
            if evidence.get("tool") == "get_graph"
            and evidence.get("result", {}).get("schema_version") == "nightwatch.snapshot.v2"]


def seconds(at, start):
    return int((datetime.fromisoformat(at.replace("Z", "+00:00"))
                - datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds())


class LiveStore:
    def __init__(self, app, error):
        self.app, self.error = app, error
        self.run = {"id": "live-" + uuid4().hex, "started_at": timestamp(),
                    "baseline": {"status": "collecting", "collected_secs": 0, "required_secs": 120}}

    @property
    def investigations(self):
        return self.app.state.investigation_manager.store

    @property
    def graph_store(self):
        value = getattr(self.app.state, "graph_store", None)
        if value is None:
            raise self.error(503, "internal", "Monitor graph 尚未啟動")
        return value

    def readiness(self):
        source = self.graph_store.snapshot["sources"]["logstore"]
        receiving = source["ok"] and source["age_secs"] <= 60
        reasons = {
            "prometheus_reachable": "尚未接入 Prometheus",
            "jaeger_reachable": "尚未接入 Jaeger",
            "logstore_receiving": "60 秒內有收到 monitor log" if receiving else "60 秒內沒有 monitor log",
            "nodes_alive": "Monitor 活動不等於服務探活；尚未接入 health check",
            "shopper_rate": "尚未接入合成顧客訂單率",
            "baseline": "尚未實作可信基線",
            "model": "尚未探測模型；調查執行結果請讀 investigations",
            "fault_clear": "尚未接入故障控制，無法確認外部故障是否已清理",
        }
        return {"ready": False, "checks": [
            {"id": key, "status": "ok" if key == "logstore_receiving" and receiving
             else "failed" if key == "model" else "waiting", "detail_zh": reason}
            for key, reason in reasons.items()], "next_step_zh": "等待服務啟動"}

    def capabilities(self):
        return {"nodes": [{"id": node["id"], "kind": node["kind"],
                           "layout": {"row": index // 3, "col": index % 3},
                           "sat_label": node["sat_label"]}
                          for index, node in enumerate(self.graph_store.snapshot["nodes"])],
                "tools": [], "actions": [], "max_calls": 0, "hard_timeout_secs": 0,
                "links": {"storefront": None, "jaeger": None, "grafana": None}}

    def incident(self, detail):
        terminal = detail["closed_at"] is not None
        report = detail.get("report")
        latest = self.investigations.events(detail["id"], max(0, detail["event_seq"] - 1), 1)["items"]
        return {"id": detail["id"], "run_id": self.run["id"],
                "phase": "unresolved" if terminal else "investigating",
                "outcome": ("budget_exhausted" if detail["outcome"] == "budget_exhausted" else "unresolved") if terminal else None,
                "detected_at": detail["started_at"], "closed_at": detail["closed_at"], "card_id": "",
                "detection": {"source": detail["trigger"]["source"], "rule": "", "signals": [],
                              "summary_zh": "調查啟動（非自動故障偵測）：" + detail["trigger"]["reason"]
                              + "；" + detail["summary_zh"]},
                "nodes": [], "evidence": deepcopy(detail["evidence"]),
                "hypothesis": deepcopy(report["agent_report"]) if report else None,
                "usage": deepcopy(detail["usage"]), "revision": latest[-1]["cursor"] if latest else 0}

    def state(self):
        base = self.investigations.state_base()
        detail = self.investigations.detail(base["active_investigation_id"]) if base["active_investigation_id"] else None
        readiness = self.readiness()
        return {"schema_version": "nightwatch.state.v2", "server_now": timestamp(), "run": deepcopy(self.run),
                "readiness": readiness, "model": {"available": False, "model": "", "effort": ""},
                "graph_now": deepcopy(self.graph_store.snapshot),
                "faults": {"instances": [], "generation": 0},
                "incident": self.incident(detail) if detail else None,
                "capabilities": self.capabilities(), "next_step_zh": readiness["next_step_zh"]}

    def stream_snapshot(self):
        # Live journals are read in bounded batches via commits_after().
        return self.state(), []

    def commits_after(self, cursor):
        return [self.commit(event) for event in self.investigations.events_after_cursor(cursor)]

    def commit(self, event):
        payload = deepcopy(event["payload"])
        evidence = payload.get("evidence", {})
        message = payload.get("reason") or payload.get("error") or evidence.get("summary_zh")
        message = message or payload.get("tool") or payload.get("trigger", {}).get("reason") or event["type"]
        event_type = {"investigation.finished": "incident.completed", "tool.failed": "observation.recorded"}.get(event["type"], event["type"])
        actor = "tool" if event["type"] in ("observation.recorded", "tool.failed") else "model" if event["type"] == "tool.started" else "go"
        return {"schema_version": "nightwatch.incident-commit.v2", "incident_id": event["investigation_id"],
                "run_id": self.run["id"], "base_revision": event["cursor"] - 1, "revision": event["cursor"],
                "event": {"id": f"{event['investigation_id']}:{event['seq']}", "seq": event["seq"],
                          "type": event_type, "occurred_at": event["at"],
                          "actor": {"kind": actor, "id": "investigation"},
                          "timeline": {"title": event_type, "summary_zh": message}, "payload": payload}}

    def read(self, name, params, query):
        if name == "logs":
            rows = []
            for log in self.graph_store.recent.values():
                for node in log["refs"]["node_ids"]:
                    if query.get("service") and node != query["service"]:
                        continue
                    rows.append({"time": log["occurred_at"], "service": node, "severity": log["level"],
                                 "body": log["message"], "trace_id": log.get("attributes", {}).get("trace_id", "")})
            rows.sort(key=lambda row: datetime.fromisoformat(row["time"].replace("Z", "+00:00")), reverse=True)
            return rows[:int(query.get("limit", 20))]
        if name == "incidents":
            result, before = [], None
            while True:
                page = self.investigations.list(100, before)
                for row in page["items"]:
                    result.append({"id": row["id"], "card_id": "",
                                   "phase": "investigating" if row["status"] == "running" else "unresolved",
                                   "outcome": None if row["status"] == "running" else "budget_exhausted" if row["outcome"] == "budget_exhausted" else "unresolved",
                                   "detected_at": row["started_at"], "closed_at": row["closed_at"]})
                before = page["next_before"]
                if before is None:
                    return result
        if name in {"incident", "incident_events", "report", "snapshots", "timeline"}:
            detail = self.investigations.detail(params["id"])
            if detail is None:
                raise self.error(404, "not_found", "找不到這件調查")
            if name == "incident":
                return self.incident(detail)
            if name == "incident_events":
                result, after = [], 0
                while True:
                    page = self.investigations.events(detail["id"], after, 500)
                    result.extend(self.commit(event) for event in page["items"])
                    after = page["next_after"]
                    if after is None:
                        return result
            if name == "snapshots":
                snapshots = [item["snapshot"] for item in saved_snapshots(detail)]
                for snapshot in snapshots:
                    snapshot["t"] = seconds(snapshot["at"], detail["started_at"])
                snapshots.sort(key=lambda snapshot: snapshot["t"])
                start = snapshots[0]["at"] if snapshots else detail["started_at"]
                end = snapshots[-1]["at"] if snapshots else detail["started_at"]
                return {"pinned_window": {"from": start, "to": end,
                                          "from_t": seconds(start, detail["started_at"]),
                                          "to_t": seconds(end, detail["started_at"]), "count": len(snapshots)},
                        "snapshots": snapshots}
            error = self.error(503, "internal", "尚無完整實驗報告所需的根因稽核、注入時刻、基線比較及修復驗證；請讀取已保存的調查報告")
            error.body["error"]["details"] = {"report_url": f"/api/investigations/{detail['id']}/report"}
            raise error
        raise self.error(503, "internal", "尚未接入真實故障控制或修復服務")

    def execute(self, *args):
        raise self.error(503, "internal", "尚未接入真實故障控制或修復服務；沒有執行操作")

#!/usr/bin/env python3
"""Read-only GitHub PR monitor and offline dashboard. Python standard library only."""

import argparse
import copy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HEADINGS = ("做了什麼", "依據什麼驗證的", "沒有驗證的", "契約疑問")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def repository():
    # Read the literal setting, never execute the shell configuration.
    match = re.search(r'^REPO="([^"$]+)"', (ROOT / "hackathon.conf").read_text(), re.M)
    return match.group(1) if match else None


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
            name = f.name
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def gh(endpoint, stop, paginated=False):
    args = ["gh", "api", "--method", "GET", endpoint]
    if paginated:
        args += ["--paginate", "--slurp"]
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          cwd=ROOT, env={**os.environ, "GH_PROMPT_DISABLED": "1"}) as proc:
        for _ in range(180):
            try:
                stdout, stderr = proc.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if stop.is_set():
                    proc.kill()
                    proc.communicate()
                    raise RuntimeError("監控已停止")
        else:
            proc.kill()
            proc.communicate()
            raise RuntimeError(f"GitHub 讀取逾時: {endpoint}")
    if proc.returncode:
        raise RuntimeError(f"GitHub 讀取失敗: {stderr.strip()[:1500]}")
    value = json.loads(stdout)
    return [item for page in value for item in page] if paginated else value


def parse_report(body):
    """Keep rounds separate, ignoring headings inside fenced code examples."""
    rounds = []
    current = {"round": None, "sections": {}}
    section = None
    fence = None
    for line in body.replace("\r\n", "\n").splitlines():
        stripped = line.lstrip()
        marker = re.match(r"^(`{3,}|~{3,})", stripped)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            if section:
                current["sections"][section].append(line)
            continue
        if fence:
            if section:
                current["sections"][section].append(line)
            continue
        match = re.match(r"^##\s+第\s*(\d+)\s*輪\s*$", line)
        if match:
            if current["sections"] or current["round"] is not None:
                rounds.append(current)
            current = {"round": int(match.group(1)), "sections": {}}
            section = None
            continue
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            name = heading.group(1)
            section = name if name in HEADINGS else None
            if section:
                current["sections"][section] = []
        elif re.match(r"^#\s|^---\s*$", line):
            section = None
        elif section:
            current["sections"][section].append(line)
    if current["sections"] or current["round"] is not None:
        rounds.append(current)
    for item in rounds:
        item["sections"] = {k: "\n".join(v).strip() for k, v in item["sections"].items()}
    latest = rounds[-1]["sections"] if rounds else {}
    return {"rounds": rounds, "latest": latest,
            "missing_sections": [h for h in HEADINGS if not latest.get(h)], "body": body}


def normalize(pr, files):
    branch = pr["head"]["ref"]
    match = re.fullmatch(r"(shop|control|console)/(\d{2})-[A-Za-z0-9._-]+", branch)
    body = pr.get("body") or ""
    report = parse_report(body)
    changes = report["latest"].get("做了什麼", "")
    summary_lines = [x.strip().lstrip("-* ") for x in changes.splitlines() if x.strip()]
    first_line = body.splitlines()[0] if body.splitlines() else ""
    annotation = {}
    if first_line.startswith("<!-- nightwatch "):
        annotation = dict(re.findall(r"(part|nn|check|secs|files)=([^\s>]+)", first_line))
    return {
        "number": pr["number"], "url": pr["html_url"], "title": pr["title"],
        "author": (pr.get("user") or {}).get("login", "unknown"),
        "state": "MERGED" if pr.get("merged_at") else pr["state"].upper(),
        "draft": bool(pr.get("draft")), "branch": branch, "base_branch": pr["base"]["ref"],
        "sha": pr["head"]["sha"], "base_sha": pr["base"]["sha"],
        "part": match.group(1) if match else None, "nn": match.group(2) if match else None,
        "created_at": pr["created_at"], "updated_at": pr["updated_at"],
        "merged_at": pr.get("merged_at"), "closed_at": pr.get("closed_at"),
        "additions": pr["additions"], "deletions": pr["deletions"],
        "changed_files": pr["changed_files"], "files_complete": len(files) == pr["changed_files"],
        "files": [{"path": f["filename"], "previous_path": f.get("previous_filename"),
                   "status": f["status"], "additions": f["additions"], "deletions": f["deletions"],
                   "patch": f.get("patch"), "patch_source": "github_excerpt" if "patch" in f else "unavailable"}
                  for f in files],
        "summary": {"text": "；".join(summary_lines)[:600] if summary_lines else pr["title"],
                    "source": "author_report" if summary_lines else "pr_title",
                    "verified": False},
        "self_reported": annotation, "report": report,
    }


def read_events():
    events, errors = [], []
    path = HERE / "events.jsonl"
    if path.exists():
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict) or not isinstance(event.get("kind"), str):
                    raise ValueError("缺少事件 kind")
                events.append(event)
            except (ValueError, TypeError) as exc:
                errors.append(f"events.jsonl 第 {index} 行: {exc}")
    return events, errors


class Monitor:
    def __init__(self, repo, interval, offline, state_dir):
        self.repo, self.interval, self.offline = repo, interval, offline
        self.state_dir = state_dir
        self.path = state_dir / "snapshot.json"
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.mrs = []
        self.cached_gate = {}
        self.sync = {"mode": "offline" if offline else "live", "interval_secs": interval,
                     "last_attempt_at": None, "last_success_at": None, "error": None,
                     "syncing": False, "cycles": 0}
        if self.path.exists():
            cached = json.loads(self.path.read_text(encoding="utf-8"))
            if cached.get("schema_version") != 1 or cached.get("repo") != repo:
                raise ValueError("快取版本或 repo 不符，請指定不同 --state-dir")
            self.mrs = cached["mrs"]
            self.cached_gate = cached.get("gate", {})
            self.sync["last_success_at"] = cached["sync"]["last_success_at"]

    def snapshot(self):
        with self.lock:
            mrs, sync = copy.deepcopy(self.mrs), dict(self.sync)
        events, errors = read_events()
        if self.offline and not (HERE / "events.jsonl").exists():
            events = self.cached_gate.get("events", [])
            errors = self.cached_gate.get("errors", [])
        heartbeat = HERE / "state" / "heartbeat"
        heartbeat_at = self.cached_gate.get("heartbeat_at") if self.offline else None
        if heartbeat.exists():
            try:
                heartbeat_at = datetime.fromisoformat(heartbeat.read_text().strip()).isoformat()
            except ValueError:
                errors.append("gate/state/heartbeat 不是 ISO 時間")
        for mr in mrs:
            history = [e for e in events if e.get("pr") == mr["number"]]
            current = [e for e in history if isinstance(e.get("sha"), str)
                       and len(e["sha"]) >= 12 and mr["sha"].startswith(e["sha"])]
            verdicts = [e for e in current if e["kind"] in ("held", "merged")]
            mr["gate"] = {"history": history, "latest": current[-1] if current else None,
                          "verdict": verdicts[-1] if verdicts else None,
                          "has_previous_head_events": len(history) > len(current)}
            mr["review"] = None
            review_paths = sorted((HERE / "reviews").glob(f"{mr['number']}-{mr['sha'][:12]}-*.json"),
                                  key=lambda p: p.stat().st_mtime, reverse=True)
            if review_paths:
                try:
                    review = json.loads(review_paths[0].read_text())
                    if review.get("head_sha") == mr["sha"]:
                        mr["review"] = review
                except (OSError, ValueError) as exc:
                    errors.append(f"審查結果讀取失敗: {exc}")
        automation = None
        automation_path = HERE / "state" / "automerge" / "status.json"
        if automation_path.exists():
            try:
                automation = json.loads(automation_path.read_text())
            except (OSError, ValueError) as exc:
                errors.append(f"自動合併狀態讀取失敗: {exc}")
        return {"schema_version": 1, "repo": self.repo, "generated_at": now(), "sync": sync,
                "gate": {"heartbeat_at": heartbeat_at, "events": events, "errors": errors},
                "automation": automation, "mrs": mrs}

    def persist(self):
        atomic_write(self.path, json.dumps(self.snapshot(), ensure_ascii=False, indent=2) + "\n")

    def scan(self):
        with self.lock:
            self.sync.update(last_attempt_at=now(), syncing=True)
        try:
            pulls = gh(f"repos/{self.repo}/pulls?state=all&per_page=100&sort=updated&direction=desc", self.stop, True)
            old = {mr["number"]: mr for mr in self.mrs}
            fresh = []
            for pr in pulls:
                if self.stop.is_set():
                    raise RuntimeError("監控已停止")
                cached = old.get(pr["number"])
                if cached and all((cached["updated_at"] == pr["updated_at"], cached["sha"] == pr["head"]["sha"],
                                   cached["base_sha"] == pr["base"]["sha"])):
                    fresh.append(cached)
                    continue
                endpoint = f"repos/{self.repo}/pulls/{pr['number']}"
                detail = gh(endpoint, self.stop)
                files = gh(endpoint + "/files?per_page=100", self.stop, True)
                after = gh(endpoint, self.stop)
                if any(detail[key] != after[key] for key in ("updated_at", "head", "base")):
                    raise RuntimeError(f"PR #{pr['number']} 在讀取期間更新，下一輪重抓；保留上次快取")
                fresh.append(normalize(detail, files))
            with self.lock:
                self.mrs = fresh
                self.sync.update(last_success_at=now(), error=None, syncing=False,
                                 cycles=self.sync["cycles"] + 1)
            self.persist()
            print(f"[{now()}] sync ok: {len(fresh)} PR(s), cycle {self.sync['cycles']}", flush=True)
            return True
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            with self.lock:
                self.sync.update(error=str(exc), syncing=False)
            print(f"[{now()}] sync error: {exc}", file=sys.stderr, flush=True)
            return False

    def watch(self):
        while not self.stop.is_set():
            self.scan()
            self.stop.wait(self.interval)


def page(snapshot, exported=False):
    # Escape data before putting it in an HTML script element, even for plain JSON.
    data = json.dumps(snapshot, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (HERE / "web" / "index.html").read_text(encoding="utf-8")
    return (template.replace("/* STYLES */", (HERE / "web" / "style.css").read_text(encoding="utf-8"))
            .replace("/* APP */", (HERE / "web" / "app.js").read_text(encoding="utf-8"))
            .replace("__EXPORTED__", "true" if exported else "false")
            .replace("__SNAPSHOT__", data)).encode("utf-8")


def handler_for(monitor):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path not in ("/", "/api/snapshot", "/snapshot.json", "/export.html"):
                self.send_error(404)
                return
            try:
                snapshot = monitor.snapshot()
                if path in ("/api/snapshot", "/snapshot.json"):
                    body = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
                    mime = "application/json; charset=utf-8"
                else:
                    body = page(snapshot, path == "/export.html")
                    mime = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                if path in ("/snapshot.json", "/export.html"):
                    self.send_header("Content-Disposition", f'attachment; filename="nightwatch-{path[1:]}"')
                self.end_headers()
                self.wfile.write(body)
            except (OSError, ValueError) as exc:
                self.send_error(500, "Cannot read dashboard data")
                print(f"dashboard error: {exc}", file=sys.stderr, flush=True)

        def log_message(self, fmt, *args):
            if args and str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
                super().log_message(fmt, *args)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=repository())
    parser.add_argument("--interval", type=int, default=5, help="seconds between completed scans (default: 5)")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--offline", action="store_true", help="read local cache only; never call GitHub")
    parser.add_argument("--background", action="store_true", help="detach server; log and PID are saved in state-dir")
    parser.add_argument("--once", action="store_true", help="sync once, print snapshot JSON and exit")
    parser.add_argument("--export", type=Path, help="write standalone offline HTML and exit")
    parser.add_argument("--state-dir", type=Path, default=HERE / "state" / "harness")
    args = parser.parse_args()
    if not args.repo or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("--repo must be owner/repository")
    if args.interval < 5:
        parser.error("--interval must be at least 5 seconds")
    if args.background:
        if args.once or args.export:
            parser.error("--background cannot be combined with --once or --export")
        args.state_dir.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-B", str(HERE / "harness.py"), "--repo", args.repo,
                   "--interval", str(args.interval), "--port", str(args.port),
                   "--state-dir", str(args.state_dir.resolve())]
        if args.offline:
            command.append("--offline")
        log_path = args.state_dir / "server.log"
        with log_path.open("a", encoding="utf-8") as log:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     cwd=ROOT, start_new_session=True)
        time.sleep(1)
        if child.poll() is not None:
            raise RuntimeError(f"背景啟動失敗，請查看 {log_path}")
        atomic_write(args.state_dir / "server.pid", f"{child.pid}\n")
        print(f"background PID {child.pid}: http://127.0.0.1:{args.port} (log: {log_path})")
        return 0
    monitor = Monitor(args.repo, args.interval, args.offline, args.state_dir)
    signal.signal(signal.SIGTERM, lambda *_: monitor.stop.set())
    signal.signal(signal.SIGINT, lambda *_: monitor.stop.set())
    if args.once or args.export:
        # Keep stdout valid JSON; progress messages go to stderr for one-shot mode.
        previous_stdout = sys.stdout
        try:
            sys.stdout = sys.stderr
            ok = args.offline or monitor.scan()
        finally:
            sys.stdout = previous_stdout
        snapshot = monitor.snapshot()
        if args.export:
            atomic_write(args.export, page(snapshot, True).decode("utf-8"))
            print(f"export: {args.export}")
        else:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(monitor))
    server.timeout = 0.5
    worker = None
    if not args.offline:
        worker = threading.Thread(target=monitor.watch, name="github-monitor")
        worker.start()
    print(f"NightWatch harness: http://127.0.0.1:{server.server_port} ({monitor.sync['mode']})", flush=True)
    try:
        while not monitor.stop.is_set():
            server.handle_request()
    finally:
        monitor.stop.set()
        server.server_close()
        if worker:
            worker.join()
        print("monitor stopped", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"harness: {error}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""Continuously merge conflict-free, non-draft PRs through the shared gate."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from harness import HERE, ROOT, atomic_write, gh, now, repository

STATE = HERE / "state" / "merge-ready"
STOP = threading.Event()
STATUS = {"enabled": True, "pid": os.getpid(), "policy": "merge_without_conflicts",
          "heartbeat_at": None, "last_scan_at": None, "processing_pr": None,
          "error": None, "mrs": {}}


def status(**updates):
    STATUS.update(updates, heartbeat_at=now())
    atomic_write(STATE / "status.json", json.dumps(STATUS, ensure_ascii=False, indent=2) + "\n")


def process(repo, pr):
    number = pr["number"]
    if pr["state"] != "open" or pr["draft"] or pr["base"]["ref"] != "master":
        return
    head = pr["head"]["sha"]
    entry = STATUS["mrs"].setdefault(str(number), {})
    if entry.get("sha") != head:
        entry.clear()
    entry.update(sha=head, title=pr["title"], error=None)
    if pr.get("mergeable") is not True:
        entry["decision"] = "conflict" if pr.get("mergeable") is False else "pending"
        entry["error"] = "與 master 衝突" if pr.get("mergeable") is False else "等待 GitHub 計算合併狀態"
        status()
        return
    base = gh(f"repos/{repo}/git/ref/heads/master", STOP)["object"]["sha"]
    if entry.get("base_sha") == base and time.time() - entry.get("last_gate_at", 0) < 60:
        return
    entry.update(base_sha=base, last_gate_at=time.time(), decision="merging")
    status(processing_pr=number)
    env = {**os.environ, "GATE_BASE": "master", "GATE_BY": "leader", "GATE_NO_COMMENTS": "1",
           "GATE_WORKTREE": str(STATE / "worktree"),
           "GATE_EXPECT_HEAD": head, "GATE_EXPECT_BASE": base,
           "GATE_CONTENT_REVIEW": "1", "GATE_CHECK_CMD": "",
           "GATE_REVIEW_WAIVERS": "bad_format,outside_dir,no_screenshot,contract_question",
           "GATE_REVIEW_NOTE": "依使用者最新指示，沒有合併衝突就直接 merge；審查意見與契約疑問另行保留，不作合併門檻。本次未跑產品驗證，仍遵守 GitHub 分支保護。"}
    log = STATE / f"{number}-{head[:12]}.gate.log"
    with log.open("w") as output:
        with subprocess.Popen(["bash", str(HERE / "gate.sh"), "merge", str(number)],
                              cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                              stdout=output, stderr=subprocess.STDOUT) as child:
            # Finish an in-flight gate operation even when asked to stop polling.
            # The shared gate lock serializes this worker with the existing reviewer.
            while child.poll() is None:
                status()
                time.sleep(1)
            code = child.returncode
    after = gh(f"repos/{repo}/pulls/{number}", STOP)
    if after.get("merged_at"):
        entry.update(decision="merged", merge_sha=after["merge_commit_sha"], error=None)
    else:
        entry.update(decision="held", error=f"gate exit {code}，尚未合併；詳見 {log.relative_to(HERE)}")
    status()
    print(f"[{now()}] #{number}: {entry['decision']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--interval", type=int, default=5)
    args = parser.parse_args()
    if args.interval < 5:
        parser.error("interval must be at least 5 seconds")
    STATE.mkdir(parents=True, exist_ok=True)
    if args.background:
        with (STATE / "server.log").open("a") as log:
            child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                                      "--interval", str(args.interval)], cwd=ROOT,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     start_new_session=True)
        time.sleep(1)
        if child.poll() is not None:
            raise RuntimeError("無衝突合併輪詢啟動失敗，請看 gate/state/merge-ready/server.log")
        print(f"merge-ready background PID {child.pid}")
        return
    with (STATE / "lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("已有無衝突合併輪詢在執行")
        saved = STATE / "status.json"
        if saved.exists():
            entries = json.loads(saved.read_text()).get("mrs", {})
            if not isinstance(entries, dict):
                raise ValueError("既有合併紀錄格式不符")
            STATUS["mrs"] = entries
        atomic_write(STATE / "server.pid", f"{os.getpid()}\n")
        signal.signal(signal.SIGTERM, lambda *_: STOP.set())
        signal.signal(signal.SIGINT, lambda *_: STOP.set())
        repo = repository()
        try:
            while not STOP.is_set():
                try:
                    status(error=None, processing_pr=None)
                    prs = gh(f"repos/{repo}/pulls?state=open&base=master&per_page=100&sort=created&direction=asc", STOP, True)
                    status(last_scan_at=now())
                    for pr in prs:
                        if STOP.is_set():
                            break
                        if pr["draft"]:
                            continue
                        try:
                            process(repo, gh(f"repos/{repo}/pulls/{pr['number']}", STOP))
                        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                            STATUS["mrs"].setdefault(str(pr["number"]), {})["error"] = str(exc)
                            status(error=str(exc))
                            print(f"[{now()}] #{pr['number']}: {exc}", flush=True)
                except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                    status(error=str(exc))
                    print(f"[{now()}] scan error: {exc}", flush=True)
                status(processing_pr=None)
                STOP.wait(args.interval)
        finally:
            status(enabled=False, processing_pr=None)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"merge-ready: {exc}", file=sys.stderr)
        sys.exit(1)

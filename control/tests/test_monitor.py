import asyncio
import json
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monitor import JsonlSink, MonitorConfig, MonitorRuntime, install_logging, monitor
from monitor.runtime import current_invocation


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.runtime = MonitorRuntime()
        self.logger = logging.getLogger("monitor.tests")
        self.previous_level = self.logger.level
        self.logger.setLevel(logging.DEBUG)
        self.addCleanup(self.logger.setLevel, self.previous_level)

    def test_bare_and_empty_decorators_and_config_validation(self):
        with patch("monitor._default_runtime", self.runtime):
            @monitor
            def bare():
                return "bare"

            @monitor()
            def empty():
                return "empty"

        self.assertEqual(bare(), "bare")
        self.assertEqual(empty(), "empty")
        with self.assertRaises(ValueError):
            MonitorConfig(name=" ")
        with self.assertRaises(ValueError):
            MonitorConfig(level="INVALID")
        with self.assertRaises(TypeError):
            monitor(MonitorConfig(), config=MonitorConfig())
        with self.assertRaises(TypeError):
            @monitor(runtime=self.runtime)
            def unsupported():
                yield 1

    def test_defaults_and_error_log_are_not_failure(self):
        @monitor(runtime=self.runtime)
        def work(value):
            self.logger.debug("skip")
            self.logger.info("value=%s", value)
            self.logger.error("recovered")
            return value

        self.assertEqual(work(42), 42)
        detail = self.runtime.get_detail(work.__qualname__)
        self.assertEqual(detail["config"]["level"], "INFO")
        self.assertEqual(detail["state"]["status"], "ok")
        logs = [e for e in detail["events"] if e["kind"] == "log"]
        self.assertEqual([e["message"] for e in logs], ["value=42", "recovered"])
        self.assertEqual(len({e["invocation_id"] for e in detail["events"]}), 1)
        self.assertIsNone(current_invocation.get())

    def test_exception_identity_and_traceback(self):
        failure = ValueError("original")

        @monitor(MonitorConfig(name="failure"), runtime=self.runtime)
        def work():
            raise failure

        with self.assertRaises(ValueError) as caught:
            work()
        self.assertIs(caught.exception, failure)
        detail = self.runtime.get_detail("failure")
        self.assertEqual(detail["state"]["status"], "error")
        self.assertIn("ValueError: original", detail["events"][-1]["traceback"])
        self.assertEqual(detail["active_invocations"], [])
        self.assertIsNone(current_invocation.get())

    def test_nested_context_and_exception_log(self):
        @monitor(name="child", runtime=self.runtime)
        def child():
            try:
                raise ValueError("handled")
            except ValueError:
                self.logger.exception("child recovered")

        @monitor(config=MonitorConfig(name="parent"), runtime=self.runtime)
        def parent():
            child()
            self.logger.info("parent resumed")

        parent()
        p = self.runtime.get_detail("parent")
        c = self.runtime.get_detail("child")
        self.assertEqual(c["state"]["parent_invocation_id"], p["state"]["invocation_id"])
        self.assertEqual(c["state"]["status"], "ok")
        self.assertIn("ValueError: handled", c["events"][1]["traceback"])
        self.assertEqual(p["events"][1]["message"], "parent resumed")

    def test_async_concurrency_and_cancellation(self):
        async def scenario():
            entered = asyncio.Event()
            release = asyncio.Event()

            @monitor(name="parallel", runtime=self.runtime)
            async def work(label):
                self.logger.info("start %s", label)
                if len(self.runtime.get_detail("parallel")["active_invocations"]) == 2:
                    entered.set()
                await release.wait()
                self.logger.info("end %s", label)
                return label

            a = asyncio.create_task(work("a"))
            b = asyncio.create_task(work("b"))
            await asyncio.wait_for(entered.wait(), 1)
            self.assertEqual(len(self.runtime.get_detail("parallel")["active_invocations"]), 2)
            a.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await a
            release.set()
            self.assertEqual(await b, "b")

        asyncio.run(scenario())
        detail = self.runtime.get_detail("parallel")
        logs = [e for e in detail["events"] if e["kind"] == "log"]
        self.assertNotEqual(logs[0]["invocation_id"], logs[1]["invocation_id"])
        self.assertEqual(logs[1]["invocation_id"], logs[2]["invocation_id"])
        self.assertEqual(detail["active_invocations"], [])
        self.assertTrue(any("CancelledError" in (e["error"] or "") for e in detail["events"]))

    def test_jsonl_and_bounded_details(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            runtime = MonitorRuntime((JsonlSink(path),), max_events=2)

            @monitor(name="json", runtime=runtime)
            def work():
                self.logger.info("中文")

            work()
            events = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(events), 3)
            self.assertEqual(events[1]["message"], "中文")
            self.assertTrue(events[0]["timestamp"].endswith("Z"))
            self.assertEqual(len(runtime.get_detail("json")["events"]), 2)

    def test_sink_failure_preserves_return_and_exception(self):
        class BrokenSink:
            def emit(self, event):
                raise OSError("disk unavailable")

        runtime = MonitorRuntime((BrokenSink(),))

        @monitor(name="sink", runtime=runtime)
        def work(fail=False):
            if fail:
                raise ValueError("application error")
            return 10

        self.assertEqual(work(), 10)
        with self.assertRaisesRegex(ValueError, "application error"):
            work(True)
        self.assertEqual(runtime.sink_errors, 4)

    def test_filter_capture_toggle_and_nonpropagating_logger(self):
        logger = logging.getLogger("monitor.tests.private")
        old = logger.propagate
        self.addCleanup(setattr, logger, "propagate", old)
        logger.propagate = False
        handlers = logger.handlers[:]
        self.addCleanup(setattr, logger, "handlers", handlers)
        install_logging(logger)
        install_logging(logger)

        @monitor(MonitorConfig(name="filtered", level="WARNING"), runtime=self.runtime)
        def work():
            logger.info("skip")
            logger.warning("captured")

        work()
        self.assertEqual(len(self.runtime.get_detail("filtered")["events"]), 3)
        logger.propagate = True
        work()
        self.assertEqual(len(self.runtime.get_detail("filtered")["events"]), 6)

        @monitor(MonitorConfig(name="disabled", capture_logs=False), runtime=self.runtime)
        def disabled():
            logger.error("skip too")

        disabled()
        self.assertEqual(len(self.runtime.get_detail("disabled")["events"]), 2)

    def test_late_background_logs_are_not_attached_to_finished_call(self):
        async def scenario():
            release = asyncio.Event()

            async def background():
                await release.wait()
                self.logger.info("too late")

            @monitor(name="background", runtime=self.runtime)
            async def work():
                return asyncio.create_task(background())

            task = await work()
            release.set()
            await task

        asyncio.run(scenario())
        self.assertEqual(len(self.runtime.get_detail("background")["events"]), 2)


if __name__ == "__main__":
    unittest.main()

from dataclasses import replace
import json
import logging
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker

from monitor import ConsoleLogSink, MonitorConfig, MonitorEvent, MonitorRuntime, monitor, to_console_log


class ConsoleProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = json.loads((Path(__file__).resolve().parents[2] /
                             "console/schema-draft/log.schema.json").read_text())
        cls.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def test_all_event_kinds_validate_with_original_log_time(self):
        payloads = []
        runtime = MonitorRuntime((ConsoleLogSink(payloads.append),))
        logger = logging.getLogger("console-projection-test")

        @monitor(MonitorConfig(name="payment", monitor_id="payment-monitor",
                               node_ids=("payment",), instance_id="worker-1"), runtime=runtime)
        def work(fail=False):
            record = logging.LogRecord(logger.name, logging.WARNING, __file__, 1, "delayed log", (), None)
            record.created = 0.125
            logger.handle(record)
            if fail:
                raise ValueError("failed")

        work()
        with self.assertRaises(ValueError):
            work(True)
        self.assertEqual({p["attributes"]["kind"] for p in payloads},
                         {"started", "finished", "log", "exception"})
        for payload in payloads:
            self.validator.validate(payload)
            self.assertEqual(payload["monitor_id"], "payment-monitor")
            self.assertEqual(payload["refs"], {"node_ids": ["payment"]})
            self.assertEqual(payload["instance_id"], "worker-1")
        self.assertEqual(payloads[1]["occurred_at"], "1970-01-01T00:00:00.125000Z")
        self.assertEqual(payloads[1]["level"], "WARN")
        self.assertIn("ValueError: failed", payloads[-1]["attributes"]["traceback"])
        self.assertEqual(runtime.sink_errors, 0)

    def test_defaults_and_replay_preserve_identity(self):
        payloads = []
        runtime = MonitorRuntime((ConsoleLogSink(payloads.append),))

        @monitor(runtime=runtime)
        def work():
            pass

        work()
        self.assertEqual(payloads[0]["monitor_id"], f"{work.__module__}.{work.__qualname__}")
        self.assertEqual(payloads[0]["refs"], {"node_ids": []})
        self.assertNotIn("instance_id", payloads[0])
        for raw, payload in zip(runtime.get_detail(work.__qualname__)["events"], payloads):
            self.assertEqual(to_console_log(MonitorEvent(**raw)), payload)
            self.validator.validate(payload)

    def test_levels_and_invalid_configuration(self):
        event = MonitorEvent("id", "2026-09-12T00:00:00Z", "log", "function", "call", None,
                             "INFO", "hello", monitor_id="monitor")
        for level, expected in {"TRACE": "TRACE", "DEBUG": "DEBUG", "INFO": "INFO",
                                "WARNING": "WARN", "WARN": "WARN", "ERROR": "ERROR",
                                "CRITICAL": "FATAL", "FATAL": "FATAL"}.items():
            payload = to_console_log(replace(event, level=level))
            self.assertEqual(payload["level"], expected)
            self.validator.validate(payload)
        with self.assertRaises(ValueError):
            to_console_log(replace(event, level="CUSTOM"))
        with self.assertRaises(ValueError):
            to_console_log(replace(event, monitor_id=None))
        for args in ({"node_ids": ("a", "a")}, {"node_ids": "payment"},
                     {"node_ids": ("",)}, {"monitor_id": " "}, {"instance_id": ""}):
            with self.assertRaises(ValueError):
                MonitorConfig(**args)
        self.assertEqual(MonitorConfig(node_ids=["payment"]).node_ids, ("payment",))


if __name__ == "__main__":
    unittest.main()

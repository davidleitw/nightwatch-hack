"""File-backed, sampled graph history. Owned by the single ASGI event loop."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import tempfile

from pydantic import Field, model_validator

from logs import Identifier, StrictModel

TAIPEI = timezone(timedelta(hours=8))
TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]([01]\d|2[0-3]):[0-5]\d)$"


def parse_timestamp(value: str) -> datetime:
    if not re.fullmatch(TIMESTAMP_PATTERN, value):
        raise ValueError("timestamp must be RFC3339 with an explicit timezone")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def write_json_atomic(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class HistoryConfig(StrictModel):
    directory: Identifier = ".run/snapshots"
    interval_seconds: int = Field(default=5, ge=1)
    retention_seconds: int = Field(default=900, ge=1)

    @model_validator(mode="after")
    def valid_retention(self):
        if self.retention_seconds < self.interval_seconds:
            raise ValueError("history retention must be at least one sampling interval")
        return self


class GraphHistory:
    def __init__(self, directory: Path, config: HistoryConfig):
        self.directory = directory
        self.config = config
        self.index = {}
        # Scan only our snapshot filenames; temporary and unrelated files are ignored.
        for path in sorted(directory.glob("snapshot-*.json")):
            if not re.fullmatch(r"snapshot-\d{20,}\.json", path.name):
                continue
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            seq = snapshot["seq"]
            if (type(seq) is not int or seq < 1 or path.name != self.filename(seq)
                    or snapshot["schema_version"] != "nightwatch.snapshot.v2"):
                raise ValueError(f"Invalid graph history snapshot: {path}")
            at = parse_timestamp(snapshot["at"])
            self.index[seq] = (at.timestamp(), snapshot["at"], path)
        # Remember the highest sequence even when all loaded files have expired.
        self.max_seq = max(self.index, default=0)
        self.prune()

    @staticmethod
    def filename(seq):
        return f"snapshot-{seq:020d}.json"

    def prune(self):
        cutoff = datetime.now(TAIPEI).timestamp() - self.config.retention_seconds
        for seq, (at, _, path) in tuple(self.index.items()):
            if at < cutoff:
                path.unlink(missing_ok=True)
                del self.index[seq]

    def save(self, snapshot):
        self.prune()
        at = parse_timestamp(snapshot["at"]).timestamp()
        cutoff = datetime.now(TAIPEI).timestamp() - self.config.retention_seconds
        # Do not invent fresh observations if live checkpoint updates have stalled.
        if at < cutoff or snapshot["seq"] in self.index:
            return
        path = self.directory / self.filename(snapshot["seq"])
        write_json_atomic(path, snapshot)
        self.index[snapshot["seq"]] = (at, snapshot["at"], path)
        self.max_seq = max(self.max_seq, snapshot["seq"])

    def retained(self):
        cutoff = datetime.now(TAIPEI).timestamp() - self.config.retention_seconds
        return [(seq, item) for seq, item in self.index.items() if item[0] >= cutoff]

    def list_snapshots(self, limit=100, before_seq=None):
        entries = sorted((entry for entry in self.retained()
                          if before_seq is None or entry[0] < before_seq), reverse=True)
        page = entries[:limit]
        return {
            "snapshots": [{"seq": seq, "at": item[1]} for seq, item in page],
            "next_before_seq": page[-1][0] if len(entries) > limit else None,
        }

    def at_or_before(self, requested: datetime):
        candidates = [(item[0], seq, item[2]) for seq, item in self.retained()
                      if item[0] <= requested.timestamp()]
        if not candidates:
            return None
        # If timestamps tie, use the most recently committed sequence.
        path = max(candidates)[2]
        return json.loads(path.read_text(encoding="utf-8"))

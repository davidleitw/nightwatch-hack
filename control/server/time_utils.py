"""UTC timestamps shared by live projections and explicit demos."""

from datetime import datetime, timezone


def timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

import json
import logging
import sys
from datetime import datetime, timezone

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys())


class StructuredFormatter(logging.Formatter):
    """Emits one JSON object per line, with any `extra=` fields inlined.

    The point (see requirement §20 Observability) is that a parser-breaking
    change on the source side should be *obvious* in the logs, not silently
    swallowed — every adapter logs discovered/new/updated/skipped/error
    counts plus request durations through this formatter.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and key != "message":
                try:
                    json.dumps(value)
                    payload[key] = value
                except TypeError:
                    payload[key] = str(value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger("careers_os")
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(StructuredFormatter())
    root.addHandler(handler)
    root.propagate = False

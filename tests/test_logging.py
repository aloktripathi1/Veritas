import io
import json
import logging

from veritas.logging_setup import JsonFormatter, configure_logging


def test_json_formatter_emits_one_json_object_with_extra_fields():
    rec = logging.LogRecord("veritas.x", logging.WARNING, __file__, 1, "hello %s", ("w",), None)
    rec.run_id = "r1"
    out = json.loads(JsonFormatter().format(rec))
    assert out["msg"] == "hello w" and out["level"] == "WARNING" and out["run_id"] == "r1" and out["logger"] == "veritas.x"


def test_configure_logging_is_idempotent_and_writes_json():
    buf = io.StringIO()
    configure_logging("INFO", buf)
    configure_logging("INFO", buf)
    logging.getLogger("veritas.t").info("once", extra={"k": 1})
    lines = buf.getvalue().strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["k"] == 1
    logging.getLogger("veritas").handlers.clear()
    logging.getLogger("veritas").propagate = True

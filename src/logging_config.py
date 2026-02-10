import logging
import sys
import os
import json
import contextvars
import colorama
import traceback

colorama.init()

trace_id_var = contextvars.ContextVar("trace_id", default="")

COLORS = {
    'DEBUG': '\033[36m',
    'INFO': '\033[32m',
    'WARNING': '\033[33m',
    'ERROR': '\033[31m',
    'CRITICAL': '\033[35m',
}
RESET = '\033[0m'

trace_id_var = contextvars.ContextVar("trace_id", default="")

class ColoredFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record):
        levelname = record.levelname
        if levelname in COLORS:
            colored_levelname = COLORS[levelname] + levelname + RESET
            record.levelname = colored_levelname
        return super().format(record)

class JSONFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record),
            "service": self.service_name,
            "pid": record.process,
            "level": record.levelname,
            "message": record.getMessage(),
            "trace_id": trace_id_var.get(),
            "filename": record.filename,
            "lineno": record.lineno,
            "funcName": record.funcName,
        }

        if record.exc_info:
            log_entry["exception"] = "".join(
                traceback.format_exception(*record.exc_info)
            )

        return json.dumps(log_entry, ensure_ascii=False)

def setup_logger(
    service_name: str,
    log_level: str = None,
    json_format: bool = True
):
    log_level = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()
    logger = logging.getLogger()
    logger.setLevel(log_level)

    logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    if json_format:
        formatter = JSONFormatter(service_name)
    else:
        formatter = ColoredFormatter(
            f"%(asctime)s - [{service_name}] - PID=%(process)d - "
            f"%(levelname)s - %(filename)s:%(lineno)d - %(message)s"
        )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

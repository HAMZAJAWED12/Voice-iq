# app/utils/logger.py
import logging

# httpx logs "HTTP Request: <METHOD> <full URL>" at INFO for every response
# (httpcore adds DEBUG traces). Source-client URLs carry API keys (appid,
# apikey) and transcript-derived claim subjects, so these loggers stay at
# WARNING whatever the root level. A logging config applied after this
# module is imported can override that; keep them out of any such config.
_QUIET_HTTP_LOGGERS = ("httpx", "httpcore")


def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    for name in _QUIET_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


setup_logging()
import logging as _logging

logger = _logging.getLogger("voiceiq")

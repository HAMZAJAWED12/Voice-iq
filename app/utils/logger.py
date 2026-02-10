# app/utils/logger.py
import logging
def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
setup_logging()
import logging as _logging
logger = _logging.getLogger("voiceiq")
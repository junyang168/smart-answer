import logging
import logging.config
from logging.handlers import TimedRotatingFileHandler
import os

log_dir = os.environ.get("LOG_DIR", ".")
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
log_file_path = os.path.join(log_dir, "gen-ai.log")

timeRotatingLogHandler = TimedRotatingFileHandler(log_file_path, when="midnight", backupCount=30)
timeRotatingLogHandler.suffix = "%Y%m%d"

logger = logging
logger.basicConfig( format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
                        , level=os.environ.get("LOGLEVEL", "INFO")
                        ,     handlers=[
                                timeRotatingLogHandler,
                                logging.StreamHandler()
                            ]

                        )

import logging
import logging.config
from logging.handlers import TimedRotatingFileHandler
import os

timeRotatingLogHandler = TimedRotatingFileHandler("gen-ai.log", when="midnight", backupCount=30)
timeRotatingLogHandler.suffix = "%Y%m%d"

logger = logging
logger.basicConfig( format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
                        , level=os.environ.get("LOGLEVEL", "INFO")
                        ,     handlers=[
                                timeRotatingLogHandler,
                                logging.StreamHandler()
                            ]

                        )

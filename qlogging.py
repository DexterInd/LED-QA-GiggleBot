from logging import handlers
from threading import Thread, Event
from queue import Empty
from time import sleep
import logging.config
import os
import sys
import yaml

def PUBLogger(queue, level = logging.DEBUG):
    '''
    Returns a logger to use for logging stuff. 

    Key params:
    queue -- A queue to push records to. This in turn gets processed by a listener and redirected to a handler.
    level -- Set the logging level. Check formatters list down below to see all 5 levels.
    '''
    formatters = {
        logging.DEBUG: logging.Formatter("[%(levelname)s] %(message)s"),
        logging.INFO: logging.Formatter("[%(levelname)s] %(message)s"),
        logging.WARN: logging.Formatter("[%(levelname)s] %(message)s"),
        logging.ERROR: logging.Formatter("[%(levelname)s] %(message)s"),
        logging.CRITICAL: logging.Formatter("[%(levelname)s] %(message)s")
    }

    logger = logging.getLogger()
    logger.setLevel(level)

    if not logger.handlers:
        handler = handlers.QueueHandler(queue)
        handler.formatters = formatters
        logger.addHandler(handler)

    return logger

class SUBLogger():
    '''
    Create a log subscriber which listens for incoming log records. Records can get pushed to files or to stdout.
    '''
    def __init__(self, queue, default_path='logging.yaml'):
        '''
        Init params:
        queue -- The queue to extract records from.
        default_path -- Default path where the logging configuration file resides.

        If the file is not present or another exception occurs, the failed attribute gets set to True.
        '''

        try:
            with open(default_path, 'rt') as f:
                config = yaml.safe_load(f.read())
                logging.config.dictConfig(config)
            self.failed = False
        except (IOError, Exception):
            self.failed = True

        logger = logging.getLogger()
        hdlrs = logger.handlers
        self._listener = handlers.QueueListener(queue, *hdlrs, respect_handler_level = True)

    def start(self):
        '''
        Start processing incoming records.
        '''
        self._listener.start()

    def stop(self):
        '''
        Stops processing records. Waits until the thread finishes, thus it's similar to Thread's join method.
        '''
        self._listener.stop()
import os
import logging
from logging.handlers import RotatingFileHandler

def setup_logger(app):
    """Configures structured rotating file logging for the Flask app."""
    # Create logs directory if it doesn't exist
    if not os.path.exists('logs'):
        os.mkdir('logs')

    # Set up basic format
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s in %(module)s.%(funcName)s (line %(lineno)d): %(message)s'
    )

    # Set up rotating file handler (max 10MB per file, keep 5 backups)
    file_handler = RotatingFileHandler(
        'logs/aanyaas.log', maxBytes=10485760, backupCount=5
    )
    file_handler.setFormatter(formatter)
    
    # Set logging level based on debug mode
    root_logger = logging.getLogger()
    if app.debug:
        file_handler.setLevel(logging.DEBUG)
        root_logger.setLevel(logging.DEBUG)
    else:
        file_handler.setLevel(logging.INFO)
        root_logger.setLevel(logging.INFO)

    # Add handler to root logger so logging.info() works everywhere
    root_logger.addHandler(file_handler)
    
    # Also attach to app.logger specifically just in case
    app.logger.addHandler(file_handler)
    
    # Hook into Werkzeug to also capture HTTP errors in our file
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.WARNING)
    log.addHandler(file_handler)
    
    logging.info('Aanyaas Application Started / Logger Initialized')

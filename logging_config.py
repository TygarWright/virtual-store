import logging
import sys
from pythonjsonlogger import jsonlogger

def setup_logging(app):
    """Configure structured JSON logging for the application."""
    # Remove default handlers
    for handler in app.logger.handlers[:]:
        app.logger.removeHandler(handler)
    
    # Create a JSON formatter
    formatter = jsonlogger.JsonFormatter(
        '%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s %(method)s %(path)s %(status_code)s'
    )
    
    # Create a stream handler for stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    
    # Set the logger level
    app.logger.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    
    # Also set the root logger
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    
    # Prevent duplicate logs
    app.logger.propagate = False

def get_request_id():
    """Generate or retrieve a request ID for correlation."""
    from flask import request, g
    if hasattr(g, 'request_id'):
        return g.request_id
    # Generate a new request ID if not present
    import uuid
    request_id = str(uuid.uuid4())
    g.request_id = request_id
    return request_id

class RequestIDMiddleware:
    """Middleware to add request ID to the request context."""
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        from flask import request, g
        # Generate a request ID
        request_id = environ.get('HTTP_X_REQUEST_ID', str(uuid.uuid4()))
        g.request_id = request_id
        return self.app(environ, start_response)

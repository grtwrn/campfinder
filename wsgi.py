"""Production entry point (gunicorn wsgi:app)."""
from web.app import app  # noqa: F401

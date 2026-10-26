"""Vercel Python ASGI entrypoint."""
from imap_connector.http_server import app

__all__ = ["app"]

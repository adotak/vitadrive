"""Vercel entrypoint (FastAPI zero-config): Vercel routes every request to ``app``."""

from vitadrive.api import create_app

app = create_app()

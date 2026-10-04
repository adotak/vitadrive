"""Vercel entrypoint: Vercel's Python runtime serves the ASGI ``app`` exported here."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vitadrive.api import create_app  # noqa: E402

app = create_app()

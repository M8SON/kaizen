"""python -m panel: run the control panel (kaizen-panel.service)."""

from dotenv import load_dotenv

from panel.app import REPO, serve

load_dotenv(REPO / ".env")
serve()

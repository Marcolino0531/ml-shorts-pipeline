"""Dashboard Streamlit para acompanhar o pipeline (`mlshorts dashboard`)."""

from pathlib import Path

from mlshorts.dashboard.data import Artifact, DashboardData

APP_PATH = Path(__file__).with_name("app.py")

__all__ = ["APP_PATH", "Artifact", "DashboardData"]

"""Reads settings from the .env file. Nothing secret is ever hardcoded here."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID", "")
PLAID_SECRET = os.getenv("PLAID_SECRET", "")
PLAID_ENV = os.getenv("PLAID_ENV", "sandbox")

DATABASE_PATH = PROJECT_ROOT / os.getenv("DATABASE_PATH", "finance.db")


def plaid_is_configured() -> bool:
    """True once both Plaid keys are filled in. Never returns the values themselves."""
    return bool(PLAID_CLIENT_ID and PLAID_SECRET)

"""Shared test setup.

Authentication is enforced by default in the application; the historical unit tests
exercise business logic and were written before identity existed. They run with the
guard disabled and a synthetic admin identity, which is the same switch a local
developer uses. ``tests/test_auth.py`` turns it back on and tests the real path.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

os.environ.setdefault("FACTORY_STORE", "memory")
os.environ["FACTORY_AUTH"] = "off"

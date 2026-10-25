"""Puts the repo root on sys.path so `runtime.*` and `contracts.*` import under a bare `pytest`."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

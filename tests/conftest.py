"""Test configuration: put src/ on the import path.

The project runs its modules as scripts (`python src/server.py`), so there is no
installed package — src/ is added here the same way the script dir would be, so
tests can `from state import CoralState` exactly as the server does.
"""
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

"""Pytest configuration: add the bundled lymow_api to sys.path for tests."""
import sys
from pathlib import Path

# After the git mv, lymow_api lives inside custom_components/lymow/.
# Add that directory to sys.path so tests can still do `from lymow_api import ...`
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "lymow"))

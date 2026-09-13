"""Installed resources and writable application state have separate roots."""

import os
from pathlib import Path


def state_home() -> Path:
    root = Path(os.environ.get("VYASA_HOME", str(Path.home() / ".vyasa"))).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def fleet_root() -> Path:
    configured = os.environ.get("VYASA_FLEET_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).parent / "data"

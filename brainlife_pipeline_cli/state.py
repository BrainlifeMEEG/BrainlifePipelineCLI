"""Flat-JSON checkpoint file: {step_key: {"task_id": ..., "outputs": {...}}}.

Callers choose step_key's naming scheme (e.g. "subject:run:app") -- this
module only persists whatever dict it's given.
"""
import json
from pathlib import Path


def load_state(path):
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(path, state):
    Path(path).write_text(json.dumps(state, indent=2, sort_keys=True))

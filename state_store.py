from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from config import SETTINGS


class StateStore:
    def __init__(self):
        self.path = Path(SETTINGS.state_file)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def save(self, data: Dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, indent=2))

"""Профили сценариев: пороги детекторов и веса значимости из profiles/<name>.yaml."""

import os
import yaml

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from typing import Dict

PROFILES_DIR = Path(os.getenv("AGENT_PROFILES_DIR", Path(__file__).resolve().parents[2] / "profiles"))


@dataclass
class Profile:
    """Профиль сценария; detectors — параметры по типам детекторов, как в YAML."""
    name:          str
    title:         str
    description:   str = ""
    merge_gap_s:   float = 2.0
    significance:  Dict[str, Any] = field(default_factory=dict)
    detectors:     Dict[str, Dict[str, Any]] = field(default_factory=dict)
    prompt_notes:  str = ""

    def detector(self, name: str) -> Dict[str, Any]:
        return self.detectors.get(name) or {}

    def level(self, significance: float) -> str:
        """Уровень значимости: high, medium или low."""
        levels = self.significance.get("levels") or {}
        if significance >= levels.get("high", 0.6):
            return "high"
        if significance >= levels.get("medium", 0.35):
            return "medium"
        return "low"

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "title": self.title, "description": self.description,
                "merge_gap_s": self.merge_gap_s, "significance": self.significance, "detectors": self.detectors}


def load_profile(name_or_path: str = "interview") -> Profile:
    """
    Профиль по имени (файл в каталоге профилей) или по пути к YAML.

    Args:
        name_or_path (str, optional): Имя профиля или путь. Defaults to "interview".

    Raises:
        FileNotFoundError: Профиля нет.

    Returns:
        Profile: Профиль.
    """
    path = Path(name_or_path)
    if path.suffix not in (".yaml", ".yml"):
        path = PROFILES_DIR / f"{name_or_path}.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    known = {k: data[k] for k in ("name", "title", "description", "merge_gap_s", "significance", "detectors",
                                  "prompt_notes") if k in data}
    known.setdefault("name", path.stem)
    known.setdefault("title", known["name"])
    return Profile(**known)

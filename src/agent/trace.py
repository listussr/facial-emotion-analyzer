"""Журнал разбора trace.jsonl: промпты, вызовы ручек с ответами, ответы модели, проверки — всё, что видела модель."""

import json
import time

from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional


class Trace:
    """События разбора в памяти; write() сохраняет их построчно в JSON."""

    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self._t0 = time.monotonic()

    def add(self, kind: str, moment: Optional[str] = None, **data: Any) -> None:
        self.events.append({"t": round(time.monotonic() - self._t0, 3), "kind": kind,
                            **({"moment": moment} if moment else {}), **data})

    def usage(self) -> Dict[str, int]:
        """Сумма токенов по ответам модели."""
        total: Dict[str, int] = {}
        for e in self.events:
            for k, v in (e.get("usage") or {}).items():
                if isinstance(v, int):
                    total[k] = total.get(k, 0) + v
        return total

    def write(self, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for e in self.events:
                f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")

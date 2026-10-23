"""
Управление конфигами сессий в топике session-config.

Запуск (из корня проекта):
    python scripts/session_config_cli.py init-topic
    python scripts/session_config_cli.py set cam_01 --vision tracker=deepsort clip_frames=16
    python scripts/session_config_cli.py set cam_01 --audio vad_threshold=0.6 --fusion method=weighted_logits
    python scripts/session_config_cli.py unset cam_01 --vision model
    python scripts/session_config_cli.py get cam_01
    python scripts/session_config_cli.py list
    python scripts/session_config_cli.py delete cam_01
"""

import argparse
import json
import sys
import time

from dataclasses import replace
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.session_config import DEFAULT_TOPIC  # noqa: E402
from src.session_config import SessionConfig  # noqa: E402
from src.session_config import SessionConfigPublisher  # noqa: E402
from src.session_config import SessionConfigStore  # noqa: E402
from src.session_config import TopicSettings  # noqa: E402
from src.session_config import check_topic  # noqa: E402
from src.session_config import ensure_topic  # noqa: E402
from src.session_config import next_revision  # noqa: E402

SECTIONS = ("vision", "audio", "fusion")


def build_parser() -> argparse.ArgumentParser:
    """Подкоманды init-topic / set / unset / get / list / delete и общий --kafka."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kafka", default="localhost:9092", help="bootstrap.servers")
    ap.add_argument("--topic", default=DEFAULT_TOPIC)
    ap.add_argument("--timeout", type=float, default=10.0)
    sub = ap.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-topic", help="создать топик с настройками compaction")
    p_init.add_argument("--replication", type=int, default=1, help="фактор репликации (3 в кластере)")

    p_set = sub.add_parser("set", help="задать поля секций")
    p_set.add_argument("camera_id")
    for s in SECTIONS:
        p_set.add_argument(f"--{s}", nargs="+", metavar="KEY=VALUE", default=None)

    p_unset = sub.add_parser("unset", help="вернуть поля к значениям по умолчанию")
    p_unset.add_argument("camera_id")
    for s in SECTIONS:
        p_unset.add_argument(f"--{s}", nargs="+", metavar="FIELD", default=None)

    for name, help_ in (("get", "показать конфиг"), ("delete", "удалить конфиг (tombstone)")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("camera_id")

    sub.add_parser("list", help="все конфиги")
    return ap


def _convert(value: str) -> Any:
    low = value.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def parse_assignments(items: List[str]) -> Dict[str, Any]:
    """
    ['tracker=deepsort', 'window_ms=1500', 'identification=false'] → словарь
    с приведением типов (int, float, bool, null). Нет '=' → ValueError.
    """
    out: Dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"expected KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        out[key]   = _convert(value)
    return out


def load_current(bootstrap: str, camera_id: str, timeout: float,
                 topic: str = DEFAULT_TOPIC) -> Optional[SessionConfig]:
    """Прочитать текущий конфиг сессии через временный SessionConfigStore."""
    store = SessionConfigStore(bootstrap, topic=topic)
    try:
        store.start(ready_timeout=timeout)
        return store.get(camera_id)
    finally:
        store.stop()


def _print_config(cfg: SessionConfig) -> None:
    data               = cfg.to_dict()
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cfg.updated_at))
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _modify(args: argparse.Namespace, unset: bool) -> int:
    current = load_current(args.kafka, args.camera_id, args.timeout, args.topic)
    cfg     = current or SessionConfig(camera_id=args.camera_id, revision=0)
    changed = False
    for name in SECTIONS:
        items = getattr(args, name)
        if not items:
            continue
        changes = {f: None for f in items} if unset else parse_assignments(items)
        section = cfg.section(name) or SessionConfig._sections[name]()
        section = section.updated(**changes)
        cfg     = cfg.with_section(name, section if section.to_dict() else None, revision=cfg.revision)
        changed = True
    if not changed:
        print("nothing to change: pass --vision/--audio/--fusion", file=sys.stderr)
        return 2

    cfg = replace(cfg, revision=next_revision(current), updated_at=time.time())
    with SessionConfigPublisher(args.kafka, topic=args.topic) as pub:
        pub.publish(cfg, timeout=args.timeout)
    _print_config(cfg)
    return 0


def cmd_init_topic(args: argparse.Namespace) -> int:
    """ensure_topic + вывод расхождений настроек, если топик уже был."""
    settings = TopicSettings(name=args.topic, replication_factor=args.replication)
    created  = ensure_topic(args.kafka, settings, timeout=args.timeout)
    if created:
        print(f"topic {args.topic} created")
        return 0
    diffs = check_topic(args.kafka, settings, timeout=args.timeout)
    print(f"topic {args.topic} already exists" + ("" if not diffs else ", differences:"))
    for d in diffs:
        print(f"  {d}")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    """Текущий конфиг (или пустой) → наложить поля из --vision/--audio/--fusion → новая ревизия → publish."""
    return _modify(args, unset=False)


def cmd_unset(args: argparse.Namespace) -> int:
    """Сбросить указанные поля секции в None (вернуть к значениям по умолчанию) → publish."""
    return _modify(args, unset=True)


def cmd_get(args: argparse.Namespace) -> int:
    """Вывести конфиг сессии в читаемом виде; нет конфига → код 1."""
    cfg = load_current(args.kafka, args.camera_id, args.timeout, args.topic)
    if cfg is None:
        print(f"no config for {args.camera_id}", file=sys.stderr)
        return 1
    _print_config(cfg)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """Таблица: camera_id, revision, updated_at, заданные секции."""
    store = SessionConfigStore(args.kafka, topic=args.topic)
    try:
        store.start(ready_timeout=args.timeout)
        configs = store.snapshot()
    finally:
        store.stop()
    if not configs:
        print("no configs")
        return 0
    print(f"{'camera_id':<24} {'revision':>20}  {'updated_at':<19}  sections")
    for cam, cfg in sorted(configs.items()):
        sections = ",".join(s for s in SECTIONS if cfg.section(s) is not None) or "-"
        ts       = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cfg.updated_at))
        print(f"{cam:<24} {cfg.revision:>20}  {ts:<19}  {sections}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    """Отправить tombstone для сессии."""
    with SessionConfigPublisher(args.kafka, topic=args.topic) as pub:
        pub.delete(args.camera_id, timeout=args.timeout)
    print(f"deleted config for {args.camera_id}")
    return 0


COMMANDS = {
    "init-topic": cmd_init_topic, "set": cmd_set, "unset": cmd_unset,
    "get": cmd_get, "list": cmd_list, "delete": cmd_delete,
}


def main(argv: Optional[List[str]] = None) -> int:
    """Разбор аргументов и вызов подкоманды; код возврата — код процесса."""
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (ValueError, KeyError, TypeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI конфигов сессий без Kafka."""

import sys
import pytest

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import session_config_cli as cli  # noqa: E402


def test_parse_assignments_types():
    """'a=1' → int, 'b=0.5' → float, 'c=true'/'false' → bool, 'd=null' → None, 'e=text' → str."""
    parsed = cli.parse_assignments([
        "a=1", "b=0.5", "c=true", "C=False", "d=null", "e=resnet-50", "f=", "g=a=b",
    ])
    assert parsed == {
        "a": 1, "b": 0.5, "c": True, "C": False, "d": None,
        "e": "resnet-50", "f": "", "g": "a=b",
    }
    assert isinstance(parsed["a"], int) and not isinstance(parsed["a"], bool)


def test_parse_assignments_requires_equals():
    """Элемент без '=' → ValueError."""
    with pytest.raises(ValueError):
        cli.parse_assignments(["tracker"])


def test_parser_subcommands():
    """set / unset / get / list / delete / init-topic разбираются; без подкоманды — ошибка."""
    p = cli.build_parser()

    a = p.parse_args(["set", "cam_01", "--vision", "tracker=deepsort", "model=resnet-50",
                      "--audio", "hop_ms=500"])
    assert a.command == "set" and a.camera_id == "cam_01"
    assert a.vision == ["tracker=deepsort", "model=resnet-50"]
    assert a.audio == ["hop_ms=500"] and a.fusion is None

    a = p.parse_args(["unset", "cam_01", "--vision", "model"])
    assert a.command == "unset" and a.vision == ["model"]

    for argv, command in ((["get", "c"], "get"), (["delete", "c"], "delete"),
                          (["list"], "list"), (["init-topic"], "init-topic")):
        assert p.parse_args(argv).command == command

    a = p.parse_args(["--kafka", "host:1", "list"])
    assert a.kafka == "host:1"

    with pytest.raises(SystemExit):
        p.parse_args([])

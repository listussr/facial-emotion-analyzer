"""
TopicSettings без Kafka.
"""

from src.session_config.topic import DEFAULT_TOPIC
from src.session_config.topic import TopicSettings


def test_default_topic_configs_are_compacted():
    configs = TopicSettings().to_topic_configs()
    assert configs["cleanup.policy"] == "compact"
    assert configs["segment.ms"] == "3600000"
    assert configs["delete.retention.ms"] == "86400000"
    assert configs["min.cleanable.dirty.ratio"] == "0.1"
    assert all(isinstance(v, str) for v in configs.values())


def test_extra_configs_are_merged():
    configs = TopicSettings(extra_configs={"retention.ms": "-1"}).to_topic_configs()
    assert configs["retention.ms"] == "-1"
    assert configs["cleanup.policy"] == "compact"


def test_defaults():
    s = TopicSettings()
    assert s.name == DEFAULT_TOPIC == "session-config"
    assert s.partitions == 1 and s.replication_factor == 1

# kafka_io.py

from confluent_kafka import Consumer, Producer
import json
import logging


class KafkaIO:
    def __init__(self, bootstrap_servers: str, input_topic: str, output_topic: str, group_id: str):
        self.input_topic = input_topic
        self.output_topic = output_topic

        self.consumer = Consumer({
            'bootstrap.servers': bootstrap_servers,
            'group.id': group_id,
            'auto.offset.reset': 'latest',
            'fetch.message.max.bytes': 10 * 1024 * 1024,
            'enable.auto.commit': True,
            'auto.commit.interval.ms': 1000,
        })
        self.consumer.subscribe([input_topic])

        self.producer = Producer({
            'bootstrap.servers': bootstrap_servers,
            'compression.type': 'lz4',
            'enable.idempotence': True
        })

        self.logger = logging.getLogger("KafkaIO")

    def consume(self, timeout: float = 1.0):
        msg = self.consumer.poll(timeout)
        if msg is None:
            return None
        if msg.error():
            self.logger.error(f"Kafka error: {msg.error()}")
            return None
        return msg.value()
    
    def produce(self, result: dict):
        try:
            self.producer.produce(
                self.output_topic,
                key=result["camera_id"].encode('utf-8'),
                value=json.dumps(result, ensure_ascii=False).encode('utf-8')
            )
            self.producer.poll(0)
        except BufferError:
            self.logger.warning("Producer queue full — dropping result")
        except Exception as e:
            self.logger.error(f"Failed to produce message: {e}")

    def close(self, flush_timeout: float = 3.0) -> None:
        """
        Корректное закрытие Kafka-клиентов. Без этого librdkafka держит
        фоновые потоки и процесс (особенно после Ctrl+C на Windows) висит,
        пока ОС не отстрелит зомби.
        """
        try:
            remaining = self.producer.flush(flush_timeout)
            if remaining:
                self.logger.warning(f"{remaining} kafka messages were not delivered")
        except Exception:
            self.logger.exception("Producer flush failed")
        try:
            self.consumer.close()
        except Exception:
            self.logger.exception("Consumer close failed")
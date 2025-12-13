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
"""Apache Kafka tabanlı mesajlaşma backend'i.

Yüksek hacimli, kalıcı mesajlar için kullanılır (audit log, market data
arşivi, trade event'leri). Kafka topic'lere replikasyon ve uzun retention
ile yazılır.

Not: Bu modül iskelet seviyesindedir. Tam implementasyon için TODO'lara
bakın.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from src.core.logging import get_logger
from src.core.messaging.base import MessageBus

logger = get_logger(__name__)


class KafkaBus(MessageBus):
    """Kafka tabanlı message bus."""

    def __init__(
        self,
        bootstrap_servers: str = "kafka:9092",
        client_id: str = "macts",
        consumer_group: str = "macts-agents",
    ) -> None:
        """KafkaBus başlat.

        Args:
            bootstrap_servers: Virgülle ayrılmış broker listesi.
            client_id: Kafka client ID.
            consumer_group: Varsayılan consumer group.
        """
        self._bootstrap_servers = bootstrap_servers
        self._client_id = client_id
        self._default_group = consumer_group
        self._producer: AIOKafkaProducer | None = None
        self._consumers: dict[str, AIOKafkaConsumer] = {}

    async def connect(self) -> None:
        """Producer'ı başlat."""
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            client_id=self._client_id,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            acks="all",
            enable_idempotence=True,
        )
        await self._producer.start()
        logger.info(
            "kafka_producer_started",
            bootstrap_servers=self._bootstrap_servers,
            client_id=self._client_id,
        )

    async def disconnect(self) -> None:
        """Producer ve tüm consumer'ları kapat."""
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

        for consumer in self._consumers.values():
            await consumer.stop()
        self._consumers.clear()

        logger.info("kafka_disconnected")

    async def publish(
        self,
        channel: str,
        message: dict[str, Any],
        *,
        key: str | None = None,
    ) -> None:
        """Topic'e mesaj gönder.

        Args:
            channel: Topic adı.
            message: Mesaj.
            key: Partition key (aynı key'li mesajlar aynı partition'a gider).
        """
        if self._producer is None:
            raise RuntimeError("KafkaBus connect() çağrılmamış")

        await self._producer.send_and_wait(channel, value=message, key=key)

    async def subscribe(
        self,
        channel: str,
        *,
        group: str | None = None,
        consumer: str | None = None,  # noqa: ARG002
        from_beginning: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        """Topic'i tüket.

        TODO: Implementasyon detayları:
        - AIOKafkaConsumer instance'ı oluştur ve cache'le
        - auto_offset_reset = "earliest" if from_beginning else "latest"
        - Mesajları async for ile tüket ve parse edip yield et
        - Bağlantı hatalarında exponential backoff reconnect
        - Graceful shutdown desteği (cancellation)
        """
        if self._producer is None:
            raise RuntimeError("KafkaBus connect() çağrılmamış")

        group_id = group or self._default_group
        consumer_key = f"{channel}:{group_id}"

        if consumer_key not in self._consumers:
            kc = AIOKafkaConsumer(
                channel,
                bootstrap_servers=self._bootstrap_servers,
                group_id=group_id,
                client_id=self._client_id,
                auto_offset_reset="earliest" if from_beginning else "latest",
                enable_auto_commit=False,
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            )
            await kc.start()
            self._consumers[consumer_key] = kc

        consumer_inst = self._consumers[consumer_key]

        try:
            async for msg in consumer_inst:
                payload = msg.value if isinstance(msg.value, dict) else {}
                payload["_kafka_offset"] = msg.offset
                payload["_kafka_partition"] = msg.partition
                yield payload
                await consumer_inst.commit()
        except Exception as e:
            logger.exception("kafka_subscribe_error", channel=channel, error=str(e))
            raise

    async def acknowledge(
        self,
        channel: str,  # noqa: ARG002
        message_id: str,  # noqa: ARG002
        *,
        group: str | None = None,  # noqa: ARG002
    ) -> None:
        """Kafka'da ack auto-commit veya commit() ile yapılır; subscribe içinde
        her mesaj sonrası commit ediliyor. Bu metod no-op."""

    async def health_check(self) -> bool:
        """Kafka producer sağlıklı mı?"""
        return self._producer is not None

"""Message bus için soyut interface.

Tüm mesajlaşma backend'leri (Redis Streams, Kafka) bu interface'i
implement eder. Bu sayede agent'lar belirli bir backend'e bağımlı olmaz.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from typing import Any

MessageHandler = Callable[[dict[str, Any]], Any]


class MessageBus(ABC):
    """Async message bus için soyut sınıf."""

    @abstractmethod
    async def connect(self) -> None:
        """Backend'e bağlan."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Bağlantıyı kapat."""

    @abstractmethod
    async def publish(
        self,
        channel: str,
        message: dict[str, Any],
        *,
        key: str | None = None,
    ) -> None:
        """Bir kanala mesaj yayınla.

        Args:
            channel: Kanal/topic/stream adı.
            message: Yayınlanacak mesaj (JSON-serializable dict).
            key: Partition key (Kafka için anlamlı, Redis için yoksayılır).
        """

    @abstractmethod
    async def subscribe(
        self,
        channel: str,
        *,
        group: str | None = None,
        consumer: str | None = None,
        from_beginning: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        """Bir kanala abone ol ve mesajları stream et.

        Args:
            channel: Kanal/topic/stream adı.
            group: Consumer group adı (load balancing için).
            consumer: Consumer instance adı.
            from_beginning: True ise mevcut tüm mesajlardan başla.

        Yields:
            Sırasıyla gelen mesajlar.
        """
        # ABC abstract method olduğu için yield asla çalışmaz; tip kontrolü için
        if False:  # pragma: no cover
            yield {}

    @abstractmethod
    async def acknowledge(self, channel: str, message_id: str, *, group: str | None = None) -> None:
        """Bir mesajı işlendi olarak işaretle (sadece grup tüketicilerinde anlamlı)."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Bağlantının sağlıklı olup olmadığını kontrol et."""

    async def __aenter__(self) -> MessageBus:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()

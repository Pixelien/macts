"""Tüm agent'lar için ortak temel sınıf.

BaseAgent şunları sağlar:
- Standart yaşam döngüsü (initialize → start → run → stop)
- Mesaj bus entegrasyonu (Redis Streams + Kafka)
- Health check ve heartbeat mekanizması
- Graceful shutdown (SIGTERM/SIGINT yakalama)
- Structured logging context
- Prometheus metrik endpoint'i
- Hata izolasyonu (bir agent çökerse diğerlerini etkilemez)
- Circuit breaker entegrasyonu

Tüm 13 MACTS agent'ı bu sınıftan türemelidir ve `_run()` metodunu
implement etmelidir.
"""

from __future__ import annotations

import asyncio
import signal
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from src.core.config import AppConfig, load_config
from src.core.logging import bind_request_context, get_logger
from src.core.messaging import KafkaBus, MessageBus, RedisStreamsBus
from src.models import AgentStatus, HealthCheckResult


# =============================================================================
# Prometheus Metrikleri (tüm agent'lar için ortak)
# =============================================================================

AGENT_UP = Gauge(
    "macts_agent_up",
    "Agent çalışıyor mu (1=evet, 0=hayır)",
    ["agent_name"],
)

AGENT_HEARTBEAT_TIMESTAMP = Gauge(
    "macts_agent_heartbeat_timestamp_seconds",
    "Son heartbeat unix timestamp",
    ["agent_name"],
)

AGENT_MESSAGES_PROCESSED = Counter(
    "macts_agent_messages_processed_total",
    "İşlenen mesaj sayısı",
    ["agent_name", "channel", "status"],
)

AGENT_MESSAGE_PROCESSING_DURATION = Histogram(
    "macts_agent_message_processing_seconds",
    "Mesaj işleme süresi",
    ["agent_name", "channel"],
)

AGENT_ERRORS = Counter(
    "macts_agent_errors_total",
    "Agent hata sayısı",
    ["agent_name", "error_type"],
)


# =============================================================================
# BaseAgent
# =============================================================================

class BaseAgent(ABC):
    """Tüm MACTS agent'ları için soyut temel sınıf.

    Alt sınıflar `agent_name` class attribute'unu ve `_run()` metodunu
    override etmelidir. İsteğe bağlı olarak `_initialize()`, `_shutdown()`,
    `_on_message()` ve `_health_check()` da override edilebilir.
    """

    #: Agent'ın benzersiz adı (alt sınıf override etmeli).
    agent_name: str = "base"

    #: Heartbeat aralığı (saniye).
    heartbeat_interval: float = 5.0

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        redis_bus: MessageBus | None = None,
        kafka_bus: MessageBus | None = None,
    ) -> None:
        """Agent'ı başlat.

        Args:
            config: AppConfig instance (None ise otomatik yüklenir).
            redis_bus: Redis Streams bus (None ise oluşturulur).
            kafka_bus: Kafka bus (None ise oluşturulur).
        """
        self.config = config or load_config()
        self.logger = get_logger(self.agent_name).bind(agent=self.agent_name)

        self._redis_bus = redis_bus
        self._kafka_bus = kafka_bus
        self._owns_redis = redis_bus is None
        self._owns_kafka = kafka_bus is None

        self._status: AgentStatus = AgentStatus.STOPPED
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self._start_time: float | None = None
        self._last_heartbeat: datetime | None = None
        self._last_error: str | None = None

        # Bind agent context'ini tüm loglara
        bind_request_context(agent=self.agent_name)

    # =========================================================================
    # Public Lifecycle API
    # =========================================================================

    async def start(self) -> None:
        """Agent'ı başlat — tüm kaynakları setup et ve _run() döngüsüne gir."""
        if self._status != AgentStatus.STOPPED:
            self.logger.warning("agent_already_running", current_status=self._status.value)
            return

        self.logger.info("agent_starting", mode=self.config.system.mode)
        self._status = AgentStatus.INITIALIZING
        self._start_time = time.time()

        try:
            await self._setup_messaging()
            await self._initialize()
            self._setup_signal_handlers()

            # Prometheus metrik HTTP server'ını başlat (port 8000, /metrics endpoint)
            try:
                start_http_server(8000)
                self.logger.info("prometheus_metrics_server_started", port=8000)
            except OSError as e:
                # Aynı portta server zaten çalışıyorsa görmezden gel
                self.logger.warning("prometheus_metrics_server_skip", error=str(e))
            AGENT_UP.labels(agent_name=self.agent_name).set(1)
            self._status = AgentStatus.RUNNING

            # Heartbeat task'ını arka planda başlat
            self._tasks.append(asyncio.create_task(self._heartbeat_loop()))

            # Ana iş döngüsünü başlat
            self._tasks.append(asyncio.create_task(self._run_with_error_handling()))

            self.logger.info("agent_started")
            await self._stop_event.wait()
        except Exception as e:
            self._status = AgentStatus.ERROR
            self._last_error = str(e)
            AGENT_ERRORS.labels(agent_name=self.agent_name, error_type=type(e).__name__).inc()
            self.logger.exception("agent_start_failed", error=str(e))
            raise
        finally:
            await self._cleanup()

    async def stop(self) -> None:
        """Agent'ı graceful olarak durdur."""
        if self._status == AgentStatus.STOPPED:
            return

        self.logger.info("agent_stopping")
        self._status = AgentStatus.STOPPING
        self._stop_event.set()

    async def health_check(self) -> HealthCheckResult:
        """Agent'ın sağlık durumunu döndür."""
        custom_metrics = await self._health_check()

        return HealthCheckResult(
            agent_name=self.agent_name,
            status=self._status,
            last_heartbeat=self._last_heartbeat or datetime.utcnow(),
            metrics={
                "uptime_seconds": time.time() - self._start_time if self._start_time else 0,
                **custom_metrics,
            },
            error_message=self._last_error,
            source_agent=self.agent_name,
        )

    # =========================================================================
    # Hooks (alt sınıflar override eder)
    # =========================================================================

    async def _initialize(self) -> None:
        """Agent başlamadan önce kaynakları hazırla.

        Alt sınıflar veritabanı bağlantıları, model yükleme, vs. burada
        yapmalıdır. Hata fırlatırsa agent başlatılmaz.
        """
        return None

    @abstractmethod
    async def _run(self) -> None:
        """Agent'ın ana iş döngüsü.

        Bu metod _stop_event set edilene kadar çalışmalıdır. Alt sınıflar
        kendi mantıklarını implement eder. Genelde:

            while not self._stop_event.is_set():
                async for msg in self._redis_bus.subscribe(...):
                    await self._on_message(msg)
        """

    async def _shutdown(self) -> None:
        """Kaynakları temizle. Alt sınıflar override edebilir."""
        return None

    async def _on_message(self, message: dict[str, Any]) -> None:
        """Tek bir mesajı işle. İstenirse alt sınıflar override eder.

        Args:
            message: Bus'tan gelen mesaj.
        """
        # Varsayılan: log ve geç
        self.logger.debug("message_received", message_keys=list(message.keys()))

    async def _health_check(self) -> dict[str, float]:
        """Custom sağlık metrikleri. Alt sınıflar override edebilir.

        Returns:
            Metrik adı -> değer dict'i.
        """
        return {}

    # =========================================================================
    # Internal: Messaging Setup
    # =========================================================================

    async def _setup_messaging(self) -> None:
        """Mesaj bus bağlantılarını kur."""
        import os

        if self._redis_bus is None:
            self._redis_bus = RedisStreamsBus(
                host=os.environ.get("REDIS_HOST", "redis"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                password=os.environ.get("REDIS_PASSWORD") or None,
                db=int(os.environ.get("REDIS_DB", "0")),
                max_stream_length=self.config.messaging.redis_streams.get(
                    "max_length", 100_000
                ),
                block_ms=self.config.messaging.redis_streams.get(
                    "consumer_block_ms", 100
                ),
            )

        if self._kafka_bus is None:
            self._kafka_bus = KafkaBus(
                bootstrap_servers=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"),
                client_id=f"macts-{self.agent_name}",
                consumer_group=os.environ.get("KAFKA_CONSUMER_GROUP", "macts-agents"),
            )

        if self._owns_redis:
            await self._redis_bus.connect()
        if self._owns_kafka:
            await self._kafka_bus.connect()

    @property
    def redis(self) -> MessageBus:
        """Redis Streams bus'a erişim."""
        if self._redis_bus is None:
            raise RuntimeError("Mesaj bus henüz başlatılmadı")
        return self._redis_bus

    @property
    def kafka(self) -> MessageBus:
        """Kafka bus'a erişim."""
        if self._kafka_bus is None:
            raise RuntimeError("Kafka bus henüz başlatılmadı")
        return self._kafka_bus

    # =========================================================================
    # Internal: Signal Handling & Cleanup
    # =========================================================================

    def _setup_signal_handlers(self) -> None:
        """SIGTERM ve SIGINT için graceful shutdown handler'ı kur."""
        loop = asyncio.get_running_loop()

        def _handler(sig: signal.Signals) -> None:
            self.logger.info("shutdown_signal_received", signal=sig.name)
            self._stop_event.set()

        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, _handler, sig)
        except NotImplementedError:
            # Windows'ta signal handler eklenemez, sessizce geç
            pass

    async def _cleanup(self) -> None:
        """Tüm kaynakları temizle."""
        AGENT_UP.labels(agent_name=self.agent_name).set(0)

        # Tüm task'ları iptal et
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # Task'ların bitmesini bekle
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        # Alt sınıfın shutdown hook'unu çağır
        try:
            await self._shutdown()
        except Exception as e:
            self.logger.exception("agent_shutdown_hook_failed", error=str(e))

        # Bus bağlantılarını kapat (eğer biz oluşturduysak)
        if self._owns_redis and self._redis_bus is not None:
            await self._redis_bus.disconnect()
        if self._owns_kafka and self._kafka_bus is not None:
            await self._kafka_bus.disconnect()

        self._status = AgentStatus.STOPPED
        self.logger.info("agent_stopped")

    # =========================================================================
    # Internal: Background Loops
    # =========================================================================

    async def _heartbeat_loop(self) -> None:
        """Periyodik heartbeat publish et."""
        while not self._stop_event.is_set():
            try:
                self._last_heartbeat = datetime.utcnow()
                AGENT_HEARTBEAT_TIMESTAMP.labels(
                    agent_name=self.agent_name
                ).set(self._last_heartbeat.timestamp())

                # Monitoring agent'ına heartbeat gönder
                if self._redis_bus is not None:
                    await self._redis_bus.publish(
                        "stream:heartbeats",
                        {
                            "agent_name": self.agent_name,
                            "status": self._status.value,
                            "timestamp": self._last_heartbeat.isoformat(),
                        },
                    )
            except Exception as e:
                self.logger.exception("heartbeat_failed", error=str(e))
                AGENT_ERRORS.labels(
                    agent_name=self.agent_name,
                    error_type="heartbeat",
                ).inc()

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.heartbeat_interval,
                )
            except asyncio.TimeoutError:
                continue

    async def _run_with_error_handling(self) -> None:
        """_run() metodunu hata yakalama ile sarmalla."""
        try:
            await self._run()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._status = AgentStatus.ERROR
            self._last_error = str(e)
            AGENT_ERRORS.labels(
                agent_name=self.agent_name,
                error_type=type(e).__name__,
            ).inc()
            self.logger.exception("agent_run_loop_crashed", error=str(e))
            self._stop_event.set()
            raise


# =============================================================================
# CLI Entrypoint Helper
# =============================================================================

async def run_agent(agent_class: type[BaseAgent]) -> None:
    """Bir agent'ı CLI'dan çalıştırmak için yardımcı.

    Kullanım (her agent'ın main.py'sinde):

        from src.agents.base import run_agent
        from src.agents.market_scanner.agent import MarketScannerAgent

        if __name__ == "__main__":
            asyncio.run(run_agent(MarketScannerAgent))
    """
    agent = agent_class()
    await agent.start()

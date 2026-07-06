"""AI Analyst için kota-dostu zamanlayıcı.

20 sembolün analiz isteklerini AI_ANALYST_INTERVAL penceresine eşit yayarak
(staggered) anlık burst'ü engeller. NVIDIA NIM ücretsiz katmanı ~40 RPM,
token bucket tavanımız 30 RPM (bkz. docs/AI_ANALYST_MODEL_SELECTION.md §5);
bu zamanlayıcı taban yükün dakikada ~1-3 istekte kalmasını garanti eder.

Saf (pure) modüldür: Redis/ağ bağımlılığı yok, unit test edilebilir.
Offset'ler crc32 ile deterministiktir — restart sonrası aynı sembol aynı
faza düşer, sürü (thundering herd) oluşmaz.
"""

from __future__ import annotations

import zlib


class StaggeredScheduler:
    """Sembol başına sabit aralıklı, faz-kaydırmalı zamanlayıcı.

    Kullanım:
        sched = StaggeredScheduler(interval_seconds=900)
        sched.register("BTCUSDT", now=time.time())
        ...
        for symbol in sched.due_symbols(now=time.time()):
            analiz_et(symbol)
            sched.mark_ran(symbol, now=time.time())
    """

    def __init__(self, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds pozitif olmalı")
        self.interval = float(interval_seconds)
        self._next_due: dict[str, float] = {}

    # -------------------------------------------------------------------
    # Kayıt yönetimi (universe değişimlerinde çağrılır)
    # -------------------------------------------------------------------

    def register(self, symbol: str, now: float) -> None:
        """Sembolü zamanlayıcıya ekle; ilk çalışma faz offset'iyle planlanır."""
        if symbol in self._next_due:
            return
        offset = self._phase_offset(symbol)
        self._next_due[symbol] = now + offset

    def unregister(self, symbol: str) -> None:
        """Sembolü çıkar (universe'ten düştüğünde)."""
        self._next_due.pop(symbol, None)

    def sync_universe(self, symbols: set[str], now: float) -> tuple[set[str], set[str]]:
        """Kayıtlı seti verilen universe ile eşitle.

        Returns:
            (eklenenler, çıkarılanlar)
        """
        current = set(self._next_due)
        added = symbols - current
        removed = current - symbols
        for s in added:
            self.register(s, now)
        for s in removed:
            self.unregister(s)
        return added, removed

    # -------------------------------------------------------------------
    # Zamanlama
    # -------------------------------------------------------------------

    def due_symbols(self, now: float) -> list[str]:
        """Şu an analiz sırası gelmiş sembolleri döndür (deterministik sıra)."""
        return sorted(s for s, t in self._next_due.items() if now >= t)

    def mark_ran(self, symbol: str, now: float) -> None:
        """Analiz yapıldı; bir sonraki çalışmayı planla."""
        if symbol in self._next_due:
            self._next_due[symbol] = now + self.interval

    def next_due_at(self, symbol: str) -> float | None:
        """Sembolün bir sonraki planlı zamanı (kayıtlı değilse None)."""
        return self._next_due.get(symbol)

    @property
    def tracked_count(self) -> int:
        return len(self._next_due)

    # -------------------------------------------------------------------
    # İç yardımcılar
    # -------------------------------------------------------------------

    def _phase_offset(self, symbol: str) -> float:
        """Sembole deterministik faz offset'i ata (0 <= offset < interval).

        crc32 kullanılır çünkü Python'un yerleşik hash()'i process başına
        rastgele tuzlanır (PYTHONHASHSEED) — restart'ta faz kayardı.
        """
        h = zlib.crc32(symbol.encode("utf-8"))
        return (h % int(self.interval * 1000)) / 1000.0

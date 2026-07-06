#!/usr/bin/env python3
"""NIM Model Probe — Aşama 0 canlı model doğrulama aracı.

VPS'te çalıştırılır (MACTS repo kökünden):

    export NVIDIA_API_KEY=nvapi-...
    python scripts/nim_model_probe.py            # tüm adaylar
    python scripts/nim_model_probe.py --models nvidia/nemotron-3-super-120b

Her aday model için ölçer:
  1. Erişilebilirlik (404 eleği — katalog varlığı != çalışan endpoint)
  2. JSON şema uyumu (pydantic doğrulama, N tekrar üzerinden parse başarı oranı)
  3. Gecikme (p50/p95 kaba tahmin)
  4. Kota davranışı (429/402 gövdeleri kaydedilir)

Çıktılar probe_results/ altına JSON olarak yazılır; özet tablo stdout'a basılır.
Sonuçlar docs/AI_ANALYST_MODEL_SELECTION.md §8'e işlenmelidir.

Bağımlılık: httpx, pydantic (ikisi de MACTS'ta zaten mevcut).
Kota notu: script varsayılanla model başına 5 istek atar (4 model × 5 = 20 istek,
dakikalık 40 RPM limitinin yarısı). İstekler arası 3s bekleme vardır.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

BASE_URL = "https://integrate.api.nvidia.com/v1"

DEFAULT_MODELS = [
    "nvidia/nemotron-3-super-120b",
    "zhipuai/glm-5.1",
    "deepseek-ai/deepseek-v4-pro",
    "qwen/qwen3.5-397b",
]

# Hedef çıktı şeması — Aşama 3'teki üretim şemasıyla birebir aynı tutulmalı.
class TradingAnalysis(BaseModel):
    symbol: str
    direction: Literal["long", "short", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    risk_flags: list[str]
    time_horizon: Literal["1h", "4h", "1d"]


# Gerçekçi örnek feature snapshot (BTCUSDT, 14 indikatör + son 5 mum özeti).
# Not: Prod'da bu veri feature stream'inden gelecek; probe için temsili sabit veri.
SAMPLE_FEATURES = {
    "symbol": "BTCUSDT",
    "timeframe": "1m",
    "timestamp": "2026-07-05T10:00:00Z",
    "indicators": {
        "rsi_14": 38.2,
        "macd": -142.5, "macd_signal": -98.3, "macd_hist": -44.2,
        "bb_upper": 109450.0, "bb_middle": 108200.0, "bb_lower": 106950.0,
        "ema_9": 107410.0, "ema_21": 107890.0, "ema_50": 108310.0,
        "sma_20": 108150.0, "sma_50": 108420.0,
        "atr_14": 385.0,
        "close": 107250.0,
    },
    "recent_candles_ohlcv": [
        [107900, 107950, 107600, 107650, 812.4],
        [107650, 107700, 107300, 107380, 1104.9],
        [107380, 107520, 107210, 107490, 953.1],
        [107490, 107510, 107100, 107180, 1322.7],
        [107180, 107340, 107050, 107250, 887.2],
    ],
}

SYSTEM_PROMPT = (
    "You are a quantitative crypto analyst. You receive technical indicator data "
    "and recent candles for one symbol. Respond with ONLY a JSON object matching "
    "this schema, no prose, no markdown fences:\n"
    '{"symbol": str, "direction": "long"|"short"|"neutral", '
    '"confidence": float 0-1, "reasoning": str (max 60 words), '
    '"risk_flags": [str], "time_horizon": "1h"|"4h"|"1d"}'
)


def extract_json(text: str) -> dict[str, Any]:
    """Thinking/markdown gürültüsüne toleranslı JSON çıkarıcı."""
    text = text.strip()
    # <think>...</think> bloklarını at
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    # markdown fence temizliği
    text = text.replace("```json", "```")
    if "```" in text:
        parts = [p for p in text.split("```") if "{" in p]
        if parts:
            text = parts[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("JSON bulunamadı")
    return json.loads(text[start : end + 1])


def probe_model(client: httpx.Client, model: str, n: int, sleep_s: float) -> dict:
    result = {
        "model": model,
        "reachable": None,
        "attempts": 0,
        "parse_ok": 0,
        "latencies_s": [],
        "errors": [],
        "samples": [],
    }
    for i in range(n):
        payload = {
            "model": model,
            "max_tokens": 4096,  # thinking modelleri için zorunlu taban
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(SAMPLE_FEATURES)},
            ],
        }
        t0 = time.monotonic()
        try:
            r = client.post("/chat/completions", json=payload, timeout=120)
        except httpx.HTTPError as e:
            result["errors"].append(f"attempt {i}: network {type(e).__name__}: {e}")
            result["attempts"] += 1
            time.sleep(sleep_s)
            continue
        latency = time.monotonic() - t0
        result["attempts"] += 1

        if r.status_code == 404:
            result["reachable"] = False
            result["errors"].append(f"404 — endpoint yok (katalog eleği): {r.text[:200]}")
            break  # bu model için devam etmenin anlamı yok
        if r.status_code in (402, 429):
            result["errors"].append(
                f"attempt {i}: HTTP {r.status_code} (kota) gövde: {r.text[:300]}"
            )
            time.sleep(max(sleep_s, 10))
            continue
        if r.status_code != 200:
            result["errors"].append(f"attempt {i}: HTTP {r.status_code}: {r.text[:200]}")
            time.sleep(sleep_s)
            continue

        result["reachable"] = True
        result["latencies_s"].append(round(latency, 2))
        try:
            content = r.json()["choices"][0]["message"]["content"]
            parsed = TradingAnalysis.model_validate(extract_json(content))
            result["parse_ok"] += 1
            result["samples"].append(parsed.model_dump())
        except (KeyError, ValueError, ValidationError, json.JSONDecodeError) as e:
            result["errors"].append(f"attempt {i}: parse fail: {e}")
            result["samples"].append({"raw": content[:500] if "content" in dir() else "?"})
        time.sleep(sleep_s)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    ap.add_argument("--n", type=int, default=5, help="model başına istek sayısı")
    ap.add_argument("--sleep", type=float, default=3.0, help="istekler arası bekleme (s)")
    args = ap.parse_args()

    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        print("HATA: NVIDIA_API_KEY env değişkeni yok. (.env'den export edin, commit etmeyin)")
        return 1

    outdir = Path("probe_results")
    outdir.mkdir(exist_ok=True)
    client = httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )

    all_results = []
    for model in args.models:
        print(f"\n=== {model} ===")
        res = probe_model(client, model, args.n, args.sleep)
        all_results.append(res)
        lats = res["latencies_s"]
        p50 = statistics.median(lats) if lats else None
        p95 = (sorted(lats)[max(0, int(len(lats) * 0.95) - 1)] if lats else None)
        ok_rate = res["parse_ok"] / res["attempts"] * 100 if res["attempts"] else 0
        print(f"  erişim={res['reachable']}  parse={res['parse_ok']}/{res['attempts']} "
              f"(%{ok_rate:.0f})  p50={p50}s  p95≈{p95}s  hata={len(res['errors'])}")
        for e in res["errors"][:3]:
            print(f"    ! {e}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outfile = outdir / f"probe_{ts}.json"
    outfile.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\nHam sonuçlar: {outfile}")
    print("Sonuçları docs/AI_ANALYST_MODEL_SELECTION.md §8 tablosuna işleyin.")
    print("Kabul kriteri: erişilebilir + parse ≥ %95 + p95 < 60s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

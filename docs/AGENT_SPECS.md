# MACTS — Agent Spesifikasyonları

> ⚠️ **VISION SPEC** — Bu doküman projenin **hedef durumunu** tanımlar, mevcut canlı sistemi değil.
> Mevcut durum için [STATUS.md](../STATUS.md), yol haritası için [ROADMAP.md](ROADMAP.md).
> Bu spec'in tamamı henüz uygulanmamıştır; bazı bölümler Faz 3+ tamamlandıkça hayata geçecektir.

---


> 13 agent'ın her birinin sorumluluk, input, output, dependency ve key metrik'leri.

## 1. Market Scanner Agent

**Amaç**: Trade universe'ünü dinamik olarak yönetmek.

| Field | Value |
|---|---|
| Container | `agent-market-scanner` |
| Heartbeat | 10 saniye |
| Critical | Hayır (yeniden başlatılabilir) |

**Inputs**: Yok (saf yayıncı)

**Outputs**:
- `stream:universe.update` (Redis)
- `stream:universe.snapshot` (Redis)

**Bağımlılıklar**: Binance REST API

**Key Metrics**:
- `macts_universe_size` — universe'deki coin sayısı
- `macts_universe_changes_per_hour` — eklenme/çıkarılma sayısı

---

## 2. Data Collection Agent

**Amaç**: Real-time market data toplamak.

| Field | Value |
|---|---|
| Container | `agent-data-collection` |
| Heartbeat | 5 saniye |
| Critical | EVET |

**Inputs**: `stream:universe.update`

**Outputs**:
- `stream:ticks.{symbol}.kline.{interval}` (Redis)
- `stream:ticks.{symbol}.trade` (Redis)
- `stream:ticks.{symbol}.depth` (Redis)
- `stream:ticks.{symbol}.markprice` (Redis)
- `macts.market.data` (Kafka)
- InfluxDB writes

**Bağımlılıklar**: Binance WebSocket + REST, Redis, Kafka, InfluxDB

**Key Metrics**:
- `macts_ws_connections_active`
- `macts_ws_reconnects_total`
- `macts_ws_messages_per_second`
- `macts_data_lag_seconds` (server time - last received)

---

## 3. Feature Engineering Agent

**Amaç**: Streaming teknik indikatör ve mikroyapı feature'ları üretmek.

| Field | Value |
|---|---|
| Container | `agent-feature-engineering` |
| Heartbeat | 5 saniye |
| Critical | EVET |

**Inputs**:
- `stream:ticks.{symbol}.kline.*`
- `stream:ticks.{symbol}.trade`
- `stream:ticks.{symbol}.depth`

**Outputs**: `stream:features.{symbol}` (Redis)

**Key Metrics**:
- `macts_features_computed_per_second`
- `macts_feature_computation_latency_p99_ms`

---

## 4. Per-Coin Learning Agent

**Amaç**: Her coin için bağımsız TFT + PPO model eğitmek ve inference yapmak.

| Field | Value |
|---|---|
| Container | `agent-per-coin-learning` |
| Heartbeat | 30 saniye |
| Critical | EVET |
| Memory | 8 GB+ |

**Inputs**:
- `stream:features.{symbol}`
- `stream:model.deployment` (model registry'den)

**Outputs**: `stream:predictions.{symbol}` (Redis)

**Bağımlılıklar**: PyTorch, MLflow, MinIO

**Key Metrics**:
- `macts_model_inference_latency_ms`
- `macts_model_predictions_per_second`
- `macts_model_validation_loss{symbol}`

---

## 5. Risk Management Agent

**Amaç**: Sinyalleri risk kuralları açısından değerlendirmek.

| Field | Value |
|---|---|
| Container | `agent-risk-management` |
| Heartbeat | 5 saniye |
| Critical | EVET |

**Inputs**:
- `stream:signals.raw`
- `stream:portfolio.snapshot`
- `stream:features.*` (volatilite/korelasyon)

**Outputs**: `stream:risk.assessment`

**Key Metrics**:
- `macts_signals_approved_total`
- `macts_signals_rejected_total{reason}`
- `macts_active_cooldowns`
- `macts_var_95_pct`

---

## 6. Signal Generation Agent

**Amaç**: ML, teknik ve mikroyapı sinyallerini ensemble ile birleştirmek.

| Field | Value |
|---|---|
| Container | `agent-signal-generation` |
| Heartbeat | 5 saniye |
| Critical | EVET |

**Inputs**:
- `stream:predictions.{symbol}`
- `stream:features.{symbol}`
- `stream:risk.assessment`

**Outputs**:
- `stream:signals.raw` (risk için)
- `stream:signals.approved` (execution için)

**Key Metrics**:
- `macts_signals_generated_total`
- `macts_signal_avg_confidence`

---

## 7. Execution Agent

**Amaç**: Onaylı sinyalleri Binance'e gerçek emirlere çevirmek.

| Field | Value |
|---|---|
| Container | `agent-execution` |
| Heartbeat | 3 saniye |
| Critical | EVET (live'da) |

**Inputs**:
- `stream:signals.approved`
- `stream:execution.commands` (CB'den)

**Outputs**:
- `stream:orders.events`
- `stream:trades.executed`

**Bağımlılıklar**: Binance REST API + User Data Stream

**Key Metrics**:
- `macts_orders_placed_total`
- `macts_order_latency_ms`
- `macts_avg_slippage_bps`
- `macts_failed_orders_total`

---

## 8. Simulation Agent

**Amaç**: Paper trading — gerçek WebSocket akışı + sahte fill.

| Field | Value |
|---|---|
| Container | `agent-simulation` |
| Heartbeat | 5 saniye |
| Mode | Sadece `paper` ve `live` (A/B için) |

**Inputs**:
- `stream:signals.approved`
- `stream:ticks.{symbol}.trade`

**Outputs**:
- `stream:trades.executed` (simulated=true ile işaretli)
- `stream:simulation.metrics`
- `stream:simulation.promotion_ready`

**Key Metrics**:
- `macts_paper_sharpe_ratio`
- `macts_paper_max_drawdown_pct`
- `macts_paper_win_rate`
- `macts_paper_profit_factor`

---

## 9. Portfolio Manager Agent

**Amaç**: Pozisyon, marjin, PnL takibi.

| Field | Value |
|---|---|
| Container | `agent-portfolio-manager` |
| Heartbeat | 5 saniye |
| Critical | EVET |

**Inputs**:
- `stream:trades.executed`
- `stream:orders.events`
- Binance User Data Stream

**Outputs**: `stream:portfolio.snapshot`

**Key Metrics**:
- `macts_total_balance_usdt`
- `macts_unrealized_pnl_usdt`
- `macts_realized_pnl_today`
- `macts_open_positions_count`
- `macts_drawdown_daily_pct`

---

## 10. Monitoring & Logging Agent

**Amaç**: Health-check, anomali tespiti, bildirim.

| Field | Value |
|---|---|
| Container | `agent-monitoring` |
| Heartbeat | 5 saniye |
| Critical | EVET |
| Port | 8000 (Prometheus /metrics) |

**Inputs**: Tüm `stream:*`

**Outputs**:
- `stream:alerts`
- Telegram, Email
- PostgreSQL audit_log
- Prometheus /metrics endpoint

**Key Metrics**:
- `macts_stale_agents_count`
- `macts_alerts_sent_total{severity}`
- `macts_anomaly_detections_total`

---

## 11. Backtesting Agent

**Amaç**: Walk-forward backtest ve hyperparameter optimization.

| Field | Value |
|---|---|
| Container | `agent-backtesting` |
| Heartbeat | 60 saniye |
| Mode | On-demand (cron tetiklenir) |

**Inputs**: `stream:backtest.requests`

**Outputs**:
- `stream:backtest.results`
- `stream:model.candidates`
- MLflow experiments

**Key Metrics**:
- `macts_backtests_completed_total`
- `macts_backtest_duration_seconds`
- `macts_optuna_best_score`

---

## 12. Model Registry Agent

**Amaç**: MLflow ile model lifecycle yönetimi.

| Field | Value |
|---|---|
| Container | `agent-model-registry` |
| Heartbeat | 30 saniye |

**Inputs**:
- `stream:backtest.results`
- `stream:simulation.metrics`

**Outputs**:
- `stream:model.deployment`
- `stream:model.registry.events`

**Key Metrics**:
- `macts_models_in_production`
- `macts_canary_progress_pct`
- `macts_model_rollbacks_total`

---

## 13. Circuit Breaker Agent

**Amaç**: Felaket senaryolarında kill-switch.

| Field | Value |
|---|---|
| Container | `agent-circuit-breaker` |
| Heartbeat | 1 saniye (en agresif) |
| Critical | EN YÜKSEK |
| Restart Policy | always (asla durmamalı) |

**Inputs**:
- `stream:ticks.{symbol}.trade` (flash crash)
- `stream:portfolio.snapshot` (daily loss)
- `stream:risk.assessment` (correlation spike)

**Outputs**:
- `stream:circuit_breaker.events`
- `stream:execution.commands` (close_all)

**Trigger Tipleri**:
- `flash_crash` — 5sn'de %3+ hareket
- `exchange_outage` — 10 ardışık başarısız REST çağrısı
- `abnormal_spread` — bid-ask spread > 50 bps
- `daily_loss_breach` — günlük zarar limiti aşıldı
- `correlation_spike` — portföy korelasyonu > 0.95
- `manual` — operatör tarafından

**Key Metrics**:
- `macts_circuit_breaker_state` (0=closed, 1=open)
- `macts_circuit_breaker_triggers_total{type}`
- `macts_circuit_breaker_time_open_seconds`

---

## Agent Bağımlılık Matrisi

```
                MS  DC  FE  PCL RM  SG  EX  SIM PM  MON BT  MR  CB
Market Scanner  -   -   -   -   -   -   -   -   -   -   -   -   -
Data Collection ●   -   -   -   -   -   -   -   -   -   -   -   -
Feature Eng         ●   -   -   -   -   -   -   -   -   -   -   -
Per-Coin Learn          ●   -   -   -   -   -   -   -   -   ●   -
Risk Mgmt                       -   -   -   -   ●   -   -   -   -
Signal Gen              ●           ●   -   -   -   -   -   -   -
Execution                           -   ●   -   -   -   -   -   ●
Simulation                              ●   -   -   -   -   -   ●
Portfolio                                       ●   -   -   -   -
Monitoring      ●   ●   ●   ●   ●   ●   ●   ●   ●   -   -   -   ●
Backtesting                                                 -   -   -
Model Registry                                              ●   -   -
Circuit Breaker     ●                           ●           -   -   -
```

(● = upstream'i dinler)

"""Per-Coin Learning Agent — Faz 2 Adım 3 (Tüm universe için batch training).

İlk versiyon hedefi:
- Market Scanner'dan universe al (20 sembol)
- Her sembol için ayrı model eğit (HistGradientBoostingClassifier)
- Her birini ayrı MLflow run olarak kaydet
- Sonunda leaderboard yayınla (en iyi accuracy'den en kötüye)

Gelecekte (Faz 2 Adım 4):
- Saatlik retrain loop
- Canlı tahmin yayını (stream:predictions.{symbol})
- Model Registry'e en iyi modelleri promote etme
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from influxdb_client import InfluxDBClient
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score

import mlflow
import mlflow.sklearn

from src.agents.base import BaseAgent, run_agent

# Config
LOOKBACK_HOURS = 24
MIN_TRAIN_SAMPLES = 100
INITIAL_WAIT_SECONDS = 30  # Universe yayını için bekle
INTER_TRAIN_DELAY = 1.0  # Sembol arası nefes (MLflow/MinIO yormamak için)

# Stream
STREAM_UNIVERSE_SNAPSHOT = "stream:universe.snapshot"

# InfluxDB
INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "gazifintech")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "macts_market_data")

# MLflow
MLFLOW_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000")
MLFLOW_EXPERIMENT = "per_coin_learning_universe_v1"

# Feature names
FEATURE_COLS = [
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_upper",
    "bb_middle",
    "bb_lower",
    "ema_9",
    "ema_21",
    "ema_50",
    "sma_20",
    "sma_50",
    "atr_14",
]


class PerCoinLearningAgent(BaseAgent):
    """Universe için batch training, MLflow'a 20 run."""

    agent_name = "per_coin_learning"
    heartbeat_interval = 30.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._batch_done = False
        self._results: list[dict[str, Any]] = []
        self._universe: list[str] = []
        # Live inference için modelleri RAM'de tut
        self._models: dict[str, Any] = {}
        self._predictions_published = 0
        self._predict_errors = 0
        # InfluxDB için HTTP session (predictions yazma)
        self._influx_session = None  # _initialize'da set edilecek

    async def _initialize(self) -> None:
        self.logger.info(
            "per_coin_learning_initializing",
            lookback_hours=LOOKBACK_HOURS,
            min_train_samples=MIN_TRAIN_SAMPLES,
            mlflow_uri=MLFLOW_URI,
            experiment=MLFLOW_EXPERIMENT,
        )
        mlflow.set_tracking_uri(MLFLOW_URI)
        mlflow.set_experiment(MLFLOW_EXPERIMENT)

        # InfluxDB HTTP session (predictions yazma için)
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=10)
        headers = {
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "text/plain; charset=utf-8",
            "Accept": "application/json",
        }
        self._influx_session = aiohttp.ClientSession(
            timeout=timeout, headers=headers
        )

    async def _run(self) -> None:
        self.logger.info("per_coin_learning_loop_started")

        # 1. Universe'in yayınlanması için bekle
        self.logger.info(
            "waiting_for_universe", seconds=INITIAL_WAIT_SECONDS
        )
        await asyncio.sleep(INITIAL_WAIT_SECONDS)

        # 2. Universe'i al
        try:
            self._universe = await self._fetch_universe()
        except Exception as e:
            self.logger.exception("universe_fetch_failed", error=str(e))
            self._universe = []

        if not self._universe:
            self.logger.warning("no_universe_available_aborting_training")
        else:
            self.logger.info(
                "universe_obtained",
                size=len(self._universe),
                symbols=self._universe,
            )

            # 3. Her sembol için ayrı eğitim
            try:
                await self._train_all_symbols()
            except Exception as e:
                self.logger.exception("batch_training_failed", error=str(e))

            # 4. Leaderboard
            self._log_leaderboard()

        self._batch_done = True

        # 5. Live inference loop — eğitilmiş modellerle her dakika tahmin
        if self._models:
            self.logger.info(
                "starting_predict_loop",
                n_models=len(self._models),
            )
            await self._predict_loop()
        else:
            self.logger.warning("no_models_for_inference_idling")
            # Idle bekle
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.heartbeat_interval
                    )
                except asyncio.TimeoutError:
                    continue

    async def _fetch_universe(self) -> list[str]:
        """Market Scanner'ın son yayınladığı universe'i Redis'ten oku."""
        if self._redis_bus is None or self._redis_bus._client is None:
            return []

        # XREVRANGE ile son entry'yi al
        try:
            entries = await self._redis_bus._client.xrevrange(
                STREAM_UNIVERSE_SNAPSHOT, max="+", min="-", count=1
            )
        except Exception as e:
            self.logger.warning("universe_redis_read_failed", error=str(e))
            return []

        if not entries:
            return []

        _stream_id, fields = entries[0]
        # Symbols field'ı string olarak JSON encoded
        symbols_raw = fields.get("symbols") or fields.get(b"symbols")
        if isinstance(symbols_raw, bytes):
            symbols_raw = symbols_raw.decode()

        if isinstance(symbols_raw, str):
            import json
            try:
                return list(json.loads(symbols_raw))
            except json.JSONDecodeError:
                return []
        elif isinstance(symbols_raw, list):
            return list(symbols_raw)
        return []

    async def _train_all_symbols(self) -> None:
        """Tüm universe için sırayla eğitim."""
        self.logger.info(
            "batch_training_starting", symbols=self._universe
        )

        for i, symbol in enumerate(self._universe, start=1):
            self.logger.info(
                "training_symbol",
                symbol=symbol,
                progress=f"{i}/{len(self._universe)}",
            )
            try:
                result = await self._train_one(symbol)
                if result is not None:
                    # Modeli ayrı dict'te sakla (live inference için)
                    self._models[symbol] = {
                        "model": result["model"],
                        "feature_cols": result["feature_cols"],
                    }
                    # Result'tan modeli çıkar (leaderboard ve log'larda görünmesin)
                    result_for_log = {k: v for k, v in result.items() if k not in ("model", "feature_cols")}
                    self._results.append(result_for_log)
            except Exception as e:
                self.logger.warning(
                    "training_one_failed", symbol=symbol, error=str(e)
                )

            # MLflow/MinIO'ya nefes verelim
            await asyncio.sleep(INTER_TRAIN_DELAY)

        self.logger.info(
            "batch_training_complete",
            trained=len(self._results),
            total=len(self._universe),
        )

    async def _train_one(self, symbol: str) -> dict[str, Any] | None:
        """Tek bir sembol için eğitim. Return: result dict veya None."""
        # 1. Veri çek
        df = await asyncio.to_thread(self._fetch_features_from_influx, symbol)

        if df is None or len(df) < MIN_TRAIN_SAMPLES:
            self.logger.warning(
                "insufficient_data",
                symbol=symbol,
                rows=len(df) if df is not None else 0,
            )
            return None

        # 2. Target
        df = df.sort_index()
        df["future_close"] = df["close"].shift(-1)
        df["target"] = (df["future_close"] > df["close"]).astype(int)
        df = df.dropna()

        if len(df) < MIN_TRAIN_SAMPLES:
            return None

        # 3. Feature/target
        available_features = [c for c in FEATURE_COLS if c in df.columns]
        if len(available_features) < 5:
            return None

        X = df[available_features].values
        y = df["target"].values

        # 4. Train/test split (zaman serisi: shuffle=False)
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        # Target tek sınıf mı kontrol et
        if len(np.unique(y_train)) < 2 or len(np.unique(y_test)) < 2:
            self.logger.warning(
                "single_class_in_split",
                symbol=symbol,
                train_classes=int(len(np.unique(y_train))),
                test_classes=int(len(np.unique(y_test))),
            )
            return None

        # 5. Model
        model_params = {
            "max_iter": 100,
            "max_depth": 5,
            "learning_rate": 0.05,
            "min_samples_leaf": 20,
            "random_state": 42,
        }
        model = HistGradientBoostingClassifier(**model_params)
        await asyncio.to_thread(model.fit, X_train, y_train)

        # 6. Metrik
        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]

        acc = float(accuracy_score(y_test, y_pred))
        f1 = float(f1_score(y_test, y_pred, zero_division=0))
        try:
            auc = float(roc_auc_score(y_test, y_proba))
        except ValueError:
            auc = 0.5

        # 7. MLflow'a kaydet
        try:
            run_id = await asyncio.to_thread(
                self._log_to_mlflow,
                symbol,
                model,
                model_params,
                available_features,
                acc,
                f1,
                auc,
                len(X_train),
                len(X_test),
            )
        except Exception as e:
            self.logger.warning(
                "mlflow_log_failed", symbol=symbol, error=str(e)
            )
            run_id = None

        result = {
            "symbol": symbol,
            "accuracy": acc,
            "f1": f1,
            "auc": auc,
            "n_train": len(X_train),
            "n_test": len(X_test),
            "n_features": len(available_features),
            "mlflow_run_id": run_id,
            "model": model,  # Live inference için RAM'de tut
            "feature_cols": available_features,  # Tahmin sırasında aynı sırayı korumak için
        }

        self.logger.info(
            "symbol_training_complete",
            symbol=symbol,
            accuracy=round(acc, 4),
            f1=round(f1, 4),
            auc=round(auc, 4),
            train_samples=len(X_train),
            test_samples=len(X_test),
        )

        return result

    def _fetch_features_from_influx(self, symbol: str) -> pd.DataFrame | None:
        """InfluxDB'den bir sembol için feature'ları çek."""
        try:
            client = InfluxDBClient(
                url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG
            )
            query_api = client.query_api()

            flux = f"""
            from(bucket: "{INFLUX_BUCKET}")
              |> range(start: -{LOOKBACK_HOURS}h)
              |> filter(fn: (r) => r._measurement == "features" and r.symbol == "{symbol}")
              |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
              |> sort(columns: ["_time"])
            """

            tables = query_api.query(flux)
            records = []
            for table in tables:
                for record in table.records:
                    row = {"_time": record.get_time()}
                    for key, val in record.values.items():
                        if key.startswith("_") or key in (
                            "result",
                            "table",
                            "symbol",
                        ):
                            continue
                        row[key] = val
                    records.append(row)

            client.close()

            if not records:
                return None

            df = pd.DataFrame(records).set_index("_time")
            return df
        except Exception as e:
            self.logger.warning(
                "influx_fetch_failed", symbol=symbol, error=str(e)
            )
            return None

    def _log_to_mlflow(
        self,
        symbol: str,
        model: Any,
        params: dict[str, Any],
        features: list[str],
        acc: float,
        f1: float,
        auc: float,
        n_train: int,
        n_test: int,
    ) -> str:
        """Modeli, parametreleri ve metrikleri MLflow'a kaydet. Return: run_id."""
        run_name = f"{symbol}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        with mlflow.start_run(run_name=run_name) as run:
            mlflow.log_param("symbol", symbol)
            mlflow.log_param("lookback_hours", LOOKBACK_HOURS)
            mlflow.log_param("n_features", len(features))
            mlflow.log_param("features", ",".join(features))
            mlflow.log_param("model_type", "HistGradientBoostingClassifier")
            for k, v in params.items():
                mlflow.log_param(k, v)

            mlflow.log_metric("accuracy", acc)
            mlflow.log_metric("f1", f1)
            mlflow.log_metric("auc", auc)
            mlflow.log_metric("n_train_samples", n_train)
            mlflow.log_metric("n_test_samples", n_test)

            mlflow.set_tag("symbol", symbol)
            mlflow.set_tag("phase", "universe_batch_v1")

            mlflow.sklearn.log_model(model, name="model")

            return run.info.run_id

    def _log_leaderboard(self) -> None:
        """Sonuçları accuracy'ye göre sırala ve logla."""
        if not self._results:
            self.logger.warning("no_results_for_leaderboard")
            return

        sorted_results = sorted(
            self._results, key=lambda r: r["accuracy"], reverse=True
        )

        self.logger.info("=" * 60)
        self.logger.info("LEADERBOARD: Per-Symbol Accuracy")
        self.logger.info("=" * 60)
        for i, r in enumerate(sorted_results, start=1):
            self.logger.info(
                "leaderboard_entry",
                rank=i,
                symbol=r["symbol"],
                accuracy=round(r["accuracy"], 4),
                auc=round(r["auc"], 4),
                f1=round(r["f1"], 4),
                samples=r["n_train"] + r["n_test"],
            )
        self.logger.info("=" * 60)

        # Özet metrikler
        accs = [r["accuracy"] for r in sorted_results]
        self.logger.info(
            "leaderboard_summary",
            n_models=len(sorted_results),
            best_symbol=sorted_results[0]["symbol"],
            best_accuracy=round(sorted_results[0]["accuracy"], 4),
            worst_symbol=sorted_results[-1]["symbol"],
            worst_accuracy=round(sorted_results[-1]["accuracy"], 4),
            median_accuracy=round(float(np.median(accs)), 4),
            mean_accuracy=round(float(np.mean(accs)), 4),
        )


    async def _predict_loop(self) -> None:
        """Her dakika her sembol için canlı tahmin yayınla."""
        # Dakikanın 10. saniyesinde tahmin yapacağız (feature'ın InfluxDB'ye yazılmasını bekle)
        import time
        
        while not self._stop_event.is_set():
            # Sonraki dakikanın 10. saniyesine kadar bekle
            now = time.time()
            seconds_into_minute = now % 60
            sleep_seconds = (60 - seconds_into_minute) + 10
            if sleep_seconds > 70:
                sleep_seconds -= 60
            
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=sleep_seconds
                )
                break  # stop event geldi
            except asyncio.TimeoutError:
                pass  # zaman aşımı = vakti geldi, devam
            
            # Her sembol için tahmin
            tasks = [
                self._predict_one(symbol)
                for symbol in self._models.keys()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            n_ok = sum(1 for r in results if r is True)
            n_err = sum(1 for r in results if isinstance(r, Exception) or r is False)
            
            self.logger.info(
                "predict_cycle_done",
                n_ok=n_ok,
                n_err=n_err,
                total_published=self._predictions_published,
            )
    
    async def _predict_one(self, symbol: str) -> bool:
        """Bir sembol için son feature'ı InfluxDB'den oku, tahmin yap, yayınla."""
        model_info = self._models.get(symbol)
        if model_info is None:
            return False
        
        # 1. InfluxDB'den son feature satırını çek
        try:
            df = await asyncio.to_thread(
                self._fetch_latest_features, symbol
            )
        except Exception as e:
            self.logger.warning("predict_fetch_failed", symbol=symbol, error=str(e))
            self._predict_errors += 1
            return False
        
        if df is None or len(df) == 0:
            return False
        
        # 2. Feature'ları çıkar (eğitimle aynı sırada)
        feature_cols = model_info["feature_cols"]
        try:
            X = df[feature_cols].values[-1:]  # Son satır
        except KeyError as e:
            self.logger.warning("predict_feature_missing", symbol=symbol, error=str(e))
            return False
        
        # 3. NaN check
        if np.isnan(X).any():
            return False
        
        # 4. Tahmin
        model = model_info["model"]
        try:
            prob_up = float(model.predict_proba(X)[0, 1])
            prediction = int(model.predict(X)[0])
        except Exception as e:
            self.logger.warning("predict_inference_failed", symbol=symbol, error=str(e))
            self._predict_errors += 1
            return False
        
        # Confidence: 0.5'ten ne kadar uzaksa o kadar emin
        confidence = abs(prob_up - 0.5) * 2  # 0-1 arası
        
        feature_time = df.index[-1]
        feature_time_ms = int(feature_time.timestamp() * 1000) if hasattr(feature_time, 'timestamp') else 0
        
        payload = {
            "symbol": symbol,
            "prob_up": prob_up,
            "prediction": prediction,
            "confidence": confidence,
            "feature_time": feature_time_ms,
            "predicted_at": datetime.utcnow().isoformat(),
        }
        
        # 5. Redis stream yayını
        if self._redis_bus is not None:
            await self._redis_bus.publish(f"stream:predictions.{symbol.lower()}", payload)
        
        # 6. InfluxDB'ye yaz
        await self._write_prediction_to_influx(symbol, payload, feature_time_ms)
        
        self._predictions_published += 1
        return True
    
    def _fetch_latest_features(self, symbol: str) -> pd.DataFrame | None:
        """InfluxDB'den bir sembolün son feature'larını çek (son 5 dakika yeterli)."""
        try:
            client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            query_api = client.query_api()
            
            flux = f"""
            from(bucket: "{INFLUX_BUCKET}")
              |> range(start: -5m)
              |> filter(fn: (r) => r._measurement == "features" and r.symbol == "{symbol}")
              |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
              |> sort(columns: ["_time"])
            """
            
            tables = query_api.query(flux)
            records = []
            for table in tables:
                for record in table.records:
                    row = {"_time": record.get_time()}
                    for key, val in record.values.items():
                        if key.startswith("_") or key in ("result", "table", "symbol"):
                            continue
                        row[key] = val
                    records.append(row)
            
            client.close()
            
            if not records:
                return None
            
            return pd.DataFrame(records).set_index("_time")
        except Exception:
            return None
    
    async def _write_prediction_to_influx(
        self,
        symbol: str,
        payload: dict,
        feature_time_ms: int,
    ) -> None:
        """Predictions'ı InfluxDB'ye yaz."""
        if self._influx_session is None or not INFLUX_TOKEN:
            return
        
        # Line protocol
        line = (
            f"predictions,symbol={symbol} "
            f"prob_up={payload['prob_up']},"
            f"prediction={payload['prediction']}i,"
            f"confidence={payload['confidence']} "
            f"{feature_time_ms * 1_000_000}"  # ns
        )
        
        url = f"{INFLUX_URL}/api/v2/write"
        params = {"org": INFLUX_ORG, "bucket": INFLUX_BUCKET, "precision": "ns"}
        try:
            async with self._influx_session.post(
                url, params=params, data=line.encode("utf-8")
            ) as resp:
                if resp.status != 204:
                    self._predict_errors += 1
        except Exception:
            self._predict_errors += 1
    
    async def _shutdown(self) -> None:
        self.logger.info("per_coin_learning_shutting_down")
        if self._influx_session is not None:
            await self._influx_session.close()

    async def _health_check(self) -> dict[str, float]:
        return {
            "batch_done": float(self._batch_done),
            "n_models_trained": float(len(self._results)),
            "n_models_in_memory": float(len(self._models)),
            "n_universe": float(len(self._universe)),
            "predictions_published": float(self._predictions_published),
            "predict_errors": float(self._predict_errors),
        }


if __name__ == "__main__":
    asyncio.run(run_agent(PerCoinLearningAgent))

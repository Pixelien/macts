# Grid Bot Backtest Yolculuğu — 19 Mayıs 2026

## Strateji
ML-Filtered Grid Bot (Faz B)
- 9 günlük veri, 5 sembol (BTC/ETH/BNB/SOL/XRP)
- Ranging market detection ile filter
- Trending tespit edildiyse liquidation

## Sonuç Karşılaştırması

| Versiyon | PnL | Max DD | Açıklama |
|---|---|---|---|
| v1 ham grid | -%4.42 | %5.78 | ML filter yok, ham parametreler |
| v1.5 tuned | -%1.48 | %2.18 | Parametre tuning |
| v2 ML filter | -%0.27 | %0.51 | Regime detection eklendi |
| v3 liquidation | -%0.03 | %0.07 | Trending'de inventory sat |
| **v4 loose filter** | **+%0.02** | **%0.04** | **Ranging threshold 0.40** |

## Karar
- Backtest tuning bitti — daha fazla devam etmek overfitting riski
- Paper trade'e geç → testnet deploy
- 1 hafta gözlem sonrası live deploy kararı

## Sonraki Adım
- Live grid bot agent yazımı
- Testnet deploy
- 1 hafta paper trade

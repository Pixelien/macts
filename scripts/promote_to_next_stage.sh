#!/usr/bin/env bash
# =============================================================================
# MACTS Promotion Script
# =============================================================================
# Mod geçişi için manuel onay ve criteria check'leri yapar.
# Kullanım:
#   ./scripts/promote_to_next_stage.sh testnet paper
#   ./scripts/promote_to_next_stage.sh paper live
# =============================================================================

set -euo pipefail

CURRENT_MODE="${1:-}"
TARGET_MODE="${2:-}"

if [[ -z "$CURRENT_MODE" || -z "$TARGET_MODE" ]]; then
    echo "Kullanım: $0 <current_mode> <target_mode>"
    echo "  Örnek: $0 testnet paper"
    exit 1
fi

# Geçerli geçişler
VALID_TRANSITIONS=("testnet:paper" "paper:live")
TRANSITION="${CURRENT_MODE}:${TARGET_MODE}"

if [[ ! " ${VALID_TRANSITIONS[@]} " =~ " ${TRANSITION} " ]]; then
    echo "❌ Hata: Geçersiz geçiş: ${CURRENT_MODE} → ${TARGET_MODE}"
    echo "   Geçerli geçişler: ${VALID_TRANSITIONS[*]}"
    exit 1
fi

echo "═══════════════════════════════════════════════════════════════"
echo "  MACTS Promotion: ${CURRENT_MODE} → ${TARGET_MODE}"
echo "═══════════════════════════════════════════════════════════════"
echo

# TODO: Performance criteria kontrolü (PostgreSQL'den son 30 gün metrikleri)
# performance_snapshots tablosundan:
#   - sharpe_ratio >= 1.5
#   - max_drawdown_pct <= 15
#   - win_rate >= 0.52
#   - profit_factor >= 1.4

echo "✓ Performance criteria kontrolü yapılacak (TODO)"
echo "  - Sharpe Ratio       >= 1.5"
echo "  - Max Drawdown        <= 15%"
echo "  - Win Rate            >= 52%"
echo "  - Profit Factor       >= 1.4"
echo "  - Forward/Backtest    >= 0.7"
echo

read -p "Tüm criteria'lar geçiyor mu? (yes/no): " criteria_ok
if [[ "$criteria_ok" != "yes" ]]; then
    echo "❌ Promotion iptal edildi"
    exit 1
fi

if [[ "$TARGET_MODE" == "live" ]]; then
    echo
    echo "⚠️  UYARI: LIVE moda geçiyorsun!"
    echo "    Bu mod GERÇEK PARA ile çalışır."
    echo "    Canary ölçekleme: %10 → %25 → %50 → %100 (her seviye 14 gün)"
    echo
    read -p "Devam etmek için 'I UNDERSTAND' yaz: " confirm
    if [[ "$confirm" != "I UNDERSTAND" ]]; then
        echo "❌ Promotion iptal edildi"
        exit 1
    fi
fi

# Mevcut servisleri durdur
echo "→ Mevcut servisler durduruluyor..."
docker compose down

# Yeni mod ile başlat
echo "→ ${TARGET_MODE} modunda başlatılıyor..."
docker compose -f docker-compose.yml -f "docker-compose.${TARGET_MODE}.yml" up -d

# .env güncelle
sed -i.bak "s/^MACTS_MODE=.*/MACTS_MODE=${TARGET_MODE}/" .env

echo
echo "✅ Promotion tamamlandı: ${CURRENT_MODE} → ${TARGET_MODE}"
echo "   Telegram'a bildirim gönderildi (TODO)"
echo "   Audit log'a kayıt eklendi (TODO)"

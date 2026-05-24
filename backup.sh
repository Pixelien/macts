#!/bin/bash
# MACTS GitHub yedekleme — her oturum sonu çalıştır
cd ~/macts

echo "=== Güvenlik: token sızıntı kontrolü ==="
if git status --short | grep -qE "\.env$"; then
    echo "🚨 .env staged! Push iptal."
    exit 1
fi
if git diff --cached 2>/dev/null | grep -q "n8FXD35KDu7ndRBm0"; then
    echo "🚨 Token tespit edildi! Push iptal."
    exit 1
fi

echo "=== Değişiklikler ==="
git add .
git status --short

echo ""
echo "=== Commit + Push ==="
MSG="${1:-Update: $(date +%Y-%m-%d_%H:%M)}"
git commit -m "$MSG" && git push origin main

echo ""
echo "✓ Yedek tamamlandı: $MSG"

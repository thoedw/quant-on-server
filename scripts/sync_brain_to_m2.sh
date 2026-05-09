#!/bin/zsh
# ═══════════════════════════════════════════════════════════════
# sync_brain_to_m2.sh
# Đồng bộ Não bộ Antigravity từ Mac Mini M4 → Mac Mini M2
# Chạy: ./scripts/sync_brain_to_m2.sh
# ═══════════════════════════════════════════════════════════════

# ── Config ────────────────────────────────────────────────────
M2_USER="tuanho"                          # ← username trên M2
M2_HOST="192.168.1.14"                    # Mac Mini M2
M2_BRAIN_DIR="~/.gemini/antigravity"      # Đích trên M2

SRC="$HOME/.gemini/antigravity/"
LOG="/tmp/sync_brain_m2.log"

# ── Màu sắc terminal ──────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'

echo ""
echo "${BLUE}╔══════════════════════════════════════════════╗${NC}"
echo "${BLUE}║   🧠 Antigravity Brain Sync  M4 → M2        ║${NC}"
echo "${BLUE}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo "  Source : $SRC"
echo "  Target : $M2_USER@$M2_HOST:$M2_BRAIN_DIR"
echo "  Time   : $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# ── Kiểm tra SSH kết nối ──────────────────────────────────────
echo "${YELLOW}[1/4] Kiểm tra kết nối SSH đến M2...${NC}"
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$M2_USER@$M2_HOST" "echo ok" &>/dev/null; then
    echo "${RED}❌ Không thể kết nối SSH đến $M2_HOST${NC}"
    echo "   → Kiểm tra: M2 đang bật? SSH đang bật? IP đúng chưa?"
    echo "   → Trên M2: System Settings → General → Sharing → Remote Login = ON"
    exit 1
fi
echo "${GREEN}✅ SSH OK${NC}"
echo ""

# ── Tính kích thước trước khi sync ───────────────────────────
echo "${YELLOW}[2/4] Tính kích thước dữ liệu...${NC}"
SRC_SIZE=$(du -sh "$SRC" --exclude="browser_recordings" 2>/dev/null | cut -f1)
echo "  Dữ liệu cần sync (bỏ qua browser_recordings): ~${SRC_SIZE}"
echo ""

# ── Rsync não bộ M4 → M2 ─────────────────────────────────────
echo "${YELLOW}[3/4] Đang sync...${NC}"

rsync -avz --progress \
    --exclude="browser_recordings/" \
    --exclude=".tempmediaStorage/" \
    --exclude=".system_generated/steps/" \
    --exclude="*.pyc" \
    --exclude="__pycache__/" \
    --delete \
    "$SRC" \
    "$M2_USER@$M2_HOST:$M2_BRAIN_DIR/" \
    2>&1 | tee "$LOG"

RSYNC_EXIT=$?

# ── Kết quả ───────────────────────────────────────────────────
echo ""
echo "${YELLOW}[4/4] Kết quả${NC}"
if [ $RSYNC_EXIT -eq 0 ]; then
    SENT=$(grep "sent" "$LOG" | tail -1)
    echo "${GREEN}✅ Sync thành công!${NC}"
    echo "  $SENT"
    echo ""
    echo "  📋 Đã sync:"
    echo "    ✓ brain/         (conversation logs + artifacts)"
    echo "    ✓ conversations/ (raw data)"
    echo "    ✓ knowledge/     (Knowledge Items)"
    echo "    ✓ skills/        (workflow skills)"
    echo "    ✓ implicit/      (workspace context)"
    echo "    ✗ browser_recordings/ (BỎ QUA — quá lớn ~13GB)"
else
    echo "${RED}❌ Sync thất bại (exit code: $RSYNC_EXIT)${NC}"
    echo "  Xem log: $LOG"
    exit 1
fi

echo ""
echo "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "${GREEN}  M2 đã có não bộ mới nhất từ M4!${NC}"
echo "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

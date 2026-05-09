#!/usr/bin/env python3
"""
ubck_q1_pdf_downloader.py
=========================
Tải toàn bộ BCTC Q1/2026 từ cổng UBCKNN (112+ báo cáo).

Selectors đã xác nhận từ DOM inspection:
  - Từ ngày input : #pt9\\:id1\\:\\:content
  - Đến ngày input: #pt9\\:id2\\:\\:content
  - Dropdown loại : a#pt9\\:smc2\\:\\:drop
  - Search button : a.xfp
  - Result table  : table với id chứa "t1"
  - Row link      : a.xgn  (click detail)
  - Download icon : a.xgp  (trực tiếp trên row!)
  - Next page btn : a[id$="nb_nx"]

Output: ./data/bctc_q1_2026/<SYMBOL>_<TYPE>_Q1_2026_<HASH>.pdf
"""

import os
import re
import sys
import time
import hashlib
import logging
import argparse
from pathlib import Path

# ── Cấu hình ─────────────────────────────────────────────────────────────
UBCK_URL    = "https://congbothongtin.ssc.gov.vn/faces/NewsSearch"
DATE_FROM   = "01/04/2026"
DATE_TO     = "30/04/2026"
STORAGE_DIR = Path("./data/bctc_q1_2026")
LOG_FILE    = "ubck_q1_downloader.log"

# Loại báo cáo cần tải (phần text trong dropdown)
REPORT_TYPES = [
    "Báo cáo tài chính Hợp nhất - Quý",
    "Báo cáo tài chính Riêng - Quý",
    "Báo cáo tài chính Tổng hợp - Quý",
    "Báo cáo tài chính Mẹ - Quý",
]

# ── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("ubck_q1")


def sha256_short(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:10]


def safe_name(text: str) -> str:
    return re.sub(r"[^\w]", "_", text).strip("_")


def get_downloaded() -> set:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return {f.stem for f in STORAGE_DIR.glob("*.pdf")}


def run(symbols_filter: list | None = None, dry_run: bool = False):
    from playwright.sync_api import sync_playwright

    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    downloaded_keys = get_downloaded()
    n_ok = n_skip = n_fail = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            accept_downloads=True,
        )
        page = ctx.new_page()
        page.set_default_timeout(90_000)

        try:
            # ══ 1. Mở trang tìm kiếm ═══════════════════════════════════
            logger.info(f"📡 Mở UBCKNN...")
            page.goto(UBCK_URL, wait_until="networkidle", timeout=120_000)
            page.wait_for_timeout(2000)

            # ══ 2. Điền ngày ════════════════════════════════════════════
            logger.info(f"🗓  Đặt filter: {DATE_FROM} → {DATE_TO}")
            # Dùng selector css escape cho ADF ids có dấu ':'
            page.locator("id=pt9:id1::content").fill(DATE_FROM)
            page.wait_for_timeout(300)
            page.locator("id=pt9:id2::content").fill(DATE_TO)
            page.wait_for_timeout(300)

            # ══ 3. Chọn tất cả loại Báo cáo Quý ════════════════════════
            logger.info("📋 Mở dropdown Tên báo cáo...")
            try:
                page.locator("id=pt9:smc2::drop").click()
                page.wait_for_timeout(1500)
                # Chọn tất cả option chứa "Quý"
                quy_items = page.locator("li.x1z0d").filter(has_text="Quý")
                count = quy_items.count()
                logger.info(f"   Tìm thấy {count} loại Báo cáo Quý")
                for i in range(count):
                    text = quy_items.nth(i).inner_text().strip()
                    quy_items.nth(i).click()
                    logger.info(f"   ✓ Chọn: {text}")
                    page.wait_for_timeout(200)
                # Click ra ngoài để đóng dropdown
                page.locator("id=pt9:id1::content").click()
                page.wait_for_timeout(500)
            except Exception as e:
                logger.warning(f"   ⚠️  Không set được dropdown (dùng mặc định): {e}")

            # ══ 4. Bấm Tìm kiếm ═════════════════════════════════════════
            logger.info("🔍 Tìm kiếm...")
            page.locator("a.xfp").first.click()
            page.wait_for_load_state("networkidle", timeout=60_000)
            page.wait_for_timeout(3000)

            # ══ 5. Loop qua từng trang ══════════════════════════════════
            page_num = 1
            total_rows_seen = 0

            while True:
                logger.info(f"\n{'═'*60}")
                logger.info(f"📄 TRANG {page_num}")
                logger.info(f"{'═'*60}")

                # ── Lấy tất cả download icon a.xgp trong table kết quả
                # Cũng lấy data từ td tương ứng để biết symbol và type
                table = page.locator("table").filter(has=page.locator("a.xgn")).first
                rows = table.locator("tr").filter(has=page.locator("a.xgn")).all()

                if not rows:
                    logger.warning("Không tìm thấy row nào! Dừng paging.")
                    break

                logger.info(f"   {len(rows)} báo cáo trong trang này")
                total_rows_seen += len(rows)

                for idx, row in enumerate(rows):
                    try:
                        cells = row.locator("td").all()
                        cell_texts = [c.inner_text().strip() for c in cells]

                        # Phân tích cột (xác nhận từ debug): 0=STT, 1=Sàn, 2=(trống), 3=Loại BC, 4=Tên công ty, 5=Kỳ, 6=Ngày
                        exchange    = cell_texts[1] if len(cell_texts) > 1 else ""
                        report_type = cell_texts[3] if len(cell_texts) > 3 else ""
                        company     = cell_texts[4] if len(cell_texts) > 4 else ""
                        period      = cell_texts[5] if len(cell_texts) > 5 else ""
                        pub_date    = cell_texts[6] if len(cell_texts) > 6 else ""

                        # Symbol KHÔNG có trên list page — tạm dùng safe company name
                        company_safe = safe_name(company[:30]) if company else f"unk_{idx}"

                        # Key để check duplicate
                        rtype_safe = "HopNhat" if "Hợp nhất" in report_type else \
                                     "TongHop"  if "Tổng hợp" in report_type else \
                                     "Me"       if "Mẹ" in report_type else "Rieng"
                        file_key = f"{company_safe}_{rtype_safe}_Q1_2026"

                        # Skip nếu đã có
                        if any(dk.startswith(file_key.split("_Q1")[0]) for dk in downloaded_keys):
                            logger.info(f"   [{idx+1}] ⏭  {company[:30]} ({rtype_safe}) — đã tải")
                            n_skip += 1
                            continue

                        logger.info(f"   [{idx+1}] {exchange:5s} | {rtype_safe:8s} | {company[:35]:35s} | {pub_date}")

                        if dry_run:
                            logger.info(f"         [DRY-RUN] Sẽ tải: {file_key}")
                            continue

                        # ── Download trực tiếp: click a.xgp trên row ───
                        dl_icon = row.locator("a.xgp").first
                        if dl_icon.count() == 0:
                            logger.warning(f"   [{idx+1}] ⚠️  Không có icon download")
                            n_fail += 1
                            continue

                        tmp_path = STORAGE_DIR / f"tmp_{os.getpid()}_{page_num}_{idx}.pdf"
                        try:
                            with page.expect_download(timeout=60_000) as dl_info:
                                dl_icon.click()
                            dl = dl_info.value
                            dl.save_as(tmp_path)

                            h = sha256_short(tmp_path)
                            final_name = f"{file_key}_{h}.pdf"
                            final_path = STORAGE_DIR / final_name
                            tmp_path.rename(final_path)
                            downloaded_keys.add(final_name.removesuffix(".pdf"))

                            size_kb = final_path.stat().st_size // 1024
                            logger.info(f"         ✅ {final_name} ({size_kb} KB)")
                            n_ok += 1

                        except Exception as de:
                            logger.warning(f"         ❌ Download lỗi: {de}")
                            if tmp_path.exists():
                                tmp_path.unlink()
                            n_fail += 1

                        time.sleep(1.0)

                    except Exception as e:
                        logger.error(f"   [{idx+1}] ❌ Lỗi row: {e}")
                        n_fail += 1

                # ── Chuyển trang tiếp theo ───────────────────────────────
                next_btn = page.locator("[id$='nb_nx']").first
                if next_btn.count() > 0 and next_btn.is_enabled():
                    is_disabled = next_btn.get_attribute("class") or ""
                    if "x177" in is_disabled or "disabled" in is_disabled:
                        logger.info("🏁 Đã hết trang!")
                        break
                    logger.info(f"⏭  Chuyển trang {page_num} → {page_num+1}...")
                    next_btn.click()
                    page.wait_for_load_state("networkidle", timeout=30_000)
                    page.wait_for_timeout(3000)
                    page_num += 1
                else:
                    logger.info("🏁 Không có trang tiếp theo!")
                    break

        except Exception as e:
            logger.error(f"❌ Lỗi nghiêm trọng: {e}", exc_info=True)
        finally:
            browser.close()

    # ══ Báo cáo cuối ══════════════════════════════════════════════════════
    logger.info("\n" + "═"*60)
    logger.info("📊 KẾT QUẢ TẢI BCTC Q1/2026 TỪ UBCKNN")
    logger.info("═"*60)
    logger.info(f"   ✅ Tải thành công  : {n_ok:4d} file")
    logger.info(f"   ⏭  Đã có, bỏ qua  : {n_skip:4d} file")
    logger.info(f"   ❌ Thất bại        : {n_fail:4d} file")
    logger.info(f"   📁 Lưu tại         : {STORAGE_DIR.resolve()}")
    total_size = sum(f.stat().st_size for f in STORAGE_DIR.glob("*.pdf"))
    logger.info(f"   💾 Tổng dung lượng : {total_size/1024/1024:.1f} MB")
    logger.info("═"*60)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Tải BCTC Q1/2026 từ UBCKNN")
    ap.add_argument("--symbols", type=str, default=None,
                    help="Giới hạn theo mã VD: TCB,HPG (Bỏ qua = toàn thị trường)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Chỉ list, không tải thật")
    args = ap.parse_args()

    sf = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None

    logger.info("🚀 UBCK Q1/2026 PDF Downloader — START")
    logger.info(f"   Mã          : {sf or 'TOÀN THỊ TRƯỜNG (~112 báo cáo)'}")
    logger.info(f"   Giai đoạn   : {DATE_FROM} → {DATE_TO}")
    logger.info(f"   Output      : {STORAGE_DIR}/")
    logger.info(f"   Dry-run     : {args.dry_run}")

    run(symbols_filter=sf, dry_run=args.dry_run)

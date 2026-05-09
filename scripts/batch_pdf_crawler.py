import os
import sys
import logging
import argparse
import time
import requests
from bs4 import BeautifulSoup

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from securities_master.extractors.pdf_crawler import PdfCrawler
from securities_master.database import DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("pdf_crawler.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("batch_pdf_crawler")

def main():
    parser = argparse.ArgumentParser(description="Chạy luồng Cào Báo cáo thường niên (PDF) siêu tốc từ cổng UBCKNN")
    parser.add_argument("--symbols", type=str, help="Danh sách mã cổ phiếu, cách nhau bằng dấu phẩy.")
    args = parser.parse_args()

    # Load Database để check logs
    db_path = os.getenv("SMD_DB_PATH", "./data/securities_master.db")
    db = DatabaseManager(db_path)
    crawler = PdfCrawler(storage_path="./data/pdf_vault")

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        logger.info("Chưa có danh sách mã. Đang lọc Top 500 cổ phiếu có khối lượng giao dịch đột biến/cao nhất...")
        # Lấy Top 500 mã theo Volume (Thanh khoản ưu tiên)
        try:
            import sqlite3
            import pandas as pd
            conn = sqlite3.connect(db_path, detect_types=0)
            query = """
                SELECT symbol, SUM(volume) as total_vol
                FROM stock_prices
                GROUP BY symbol
                ORDER BY total_vol DESC
                LIMIT 500
            """
            df = pd.read_sql_query(query, conn)
            symbols = df['symbol'].tolist()
            conn.close()
            if not symbols:
                raise ValueError("Không tìm thấy dữ liệu stock_prices.")
        except Exception as e:
            logger.warning(f"Lỗi khi query DB: {e}. Fallback lấy List vnstock.")
            from vnstock import Vnstock
            stock = Vnstock().stock(symbol='FPT', source='VCI')
            symbols = stock.listing.all_symbols()['symbol'].tolist()[:500]
    
    logger.info(f"🚀 Khởi động Cỗ máy Cào PDF SSC cho {len(symbols)} mã tiêu điểm.")
    
    success_count = 0
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # Bật browser phục vụ quét liên tục
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
            
        for idx, symbol in enumerate(symbols):
            # Khởi động lại browser sau mỗi 50 mã để tránh tắt nghẽn RAM do Oracle ADF
            if idx > 0 and idx % 50 == 0:
                logger.info(f"🔄 Restarting Browser sau {idx} mã để chống Memory Leak...")
                page.close()
                browser.close()
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()

            logger.info(f"[{idx+1}/{len(symbols)}] Đang thâm nhập Cổng UBCKNN gom BCTC cho mã: {symbol}")
            
            try:
                href = None
                try:
                    page.goto("https://congbothongtin.ssc.gov.vn/faces/NewsSearch", wait_until="networkidle", timeout=60000)
                    page.wait_for_timeout(2000)
                    
                    # 1. Tìm ID của input "Mã chứng khoán" bằng cách quét label
                    input_id = page.evaluate('''() => {
                        let labels = Array.from(document.querySelectorAll('label'));
                        let targetLabel = labels.find(l => l.innerText.includes("Mã chứng khoán") || l.innerText.includes("Stock code"));
                        return targetLabel ? targetLabel.getAttribute("for") : null;
                    }''')
                    
                    if not input_id:
                        logger.warning(f"[{symbol}] ⚠️ Không tìm thấy ô nhập Mã Chứng Khoán.")
                        db.log_etl_run(symbol, 'pdf_crawler', 'failed', 0, "No Ticker Input")
                        continue
                        
                    # 2. Điền mã
                    page.fill(f'[id="{input_id}"]', symbol)
                    page.wait_for_timeout(500)
                    
                    # 3. Click nút Tìm Kiếm (.xfp)
                    search_btn = page.locator('a.xfp').first
                    if search_btn.count() == 0:
                        search_btn = page.locator('a[role="button"]', has_text="Search").first
                    
                    if search_btn.count() > 0:
                        search_btn.click()
                        page.wait_for_timeout(5000)
                    
                    # 4. Tìm link BCTC trong kết quả
                    bctc_link = page.locator('a.xgn', has_text="Báo cáo tài chính").first
                    if bctc_link.count() > 0:
                        bctc_link.click()
                        page.wait_for_timeout(3000)
                    else:
                        logger.warning(f"[{symbol}] ⚠️ Không có BCTC nào trong kết quả tìm kiếm SSC.")
                        db.log_etl_run(symbol, 'pdf_crawler', 'failed', 0, "Không có dòng BCTC")
                        continue

                    # 5. Nhấp tải xuống và chờ file
                    links = page.locator('a.xgn', has_text="Tải_xuống").first
                    if links.count() == 0:
                        links = page.locator('a[id$=":cxl1"]').first
                        
                    if links.count() > 0:
                        logger.info(f"[{symbol}] 🎯 Tìm thấy nút tải xuống, bắt đầu Intercept File...")
                        with page.expect_download(timeout=30000) as download_info:
                            links.click()
                        
                        download = download_info.value
                        
                        # Xử lý File Hash nội bộ
                        import uuid
                        import hashlib
                        temp_path = os.path.join(crawler.storage_path, f"tmp_{uuid.uuid4()}.pdf")
                        download.save_as(temp_path)
                        
                        # Hash file
                        sha256_hash = hashlib.sha256()
                        with open(temp_path, "rb") as f:
                            for byte_block in iter(lambda: f.read(4096), b""):
                                sha256_hash.update(byte_block)
                        final_hash = sha256_hash.hexdigest()
                        
                        final_file_path = os.path.join(crawler.storage_path, f"{symbol.upper()}_{final_hash}.pdf")
                        os.rename(temp_path, final_file_path)
                        
                        logger.info(f"[{symbol}] ✅ Tải thành công PDF bằng luồng Browser: {final_hash}")
                        db.log_etl_run(symbol, 'pdf_crawler', 'success', 1, f"Downloaded {final_hash}")
                        success_count += 1
                        
                    else:
                        logger.warning(f"[{symbol}] ⚠️ Click BCTC thành công nhưng không thấy File đính kèm.")
                        db.log_etl_run(symbol, 'pdf_crawler', 'failed', 0, "BCTC không có nút tải")

                except Exception as e:
                    logger.debug(f"[{symbol}] ❌ Lỗi khi thao tác trang UBCKNN bằng Playwright: {e}")
                    db.log_etl_run(symbol, 'pdf_crawler', 'failed', 0, str(e))
                    
            except Exception as e:
                logger.error(f"❌ Lỗi hệ lỗi mã {symbol}: {e}")
                db.log_etl_run(symbol, 'pdf_crawler', 'failed', 0, str(e))
            
        browser.close()
        
    logger.info(f"🚀 XONG CHIẾN DỊCH TẢI PDF: Thành công {success_count}/{len(symbols)} mã.")

if __name__ == "__main__":
    main()

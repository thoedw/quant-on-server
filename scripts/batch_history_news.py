#!/usr/bin/env python3
import os
import sys
import argparse
import logging
from vnstock import Vnstock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from securities_master.news_pipeline import NewsPipeline
from securities_master.extractors.cafef_history_extractor import CafeFHistoryExtractor

logger = logging.getLogger(__name__)

class HeavyweightHistoryPipeline(NewsPipeline):
    def __init__(self, db_path: str, delay_seconds: int = 1, years: int = 10, headless: bool = True):
        # Khởi tạo Pipeline nhưng inject Playwright Extractor thay vì vnstock
        super().__init__(db_path=db_path, delay_seconds=delay_seconds)
        self.extractor = CafeFHistoryExtractor(years=years, headless=headless)

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    parser = argparse.ArgumentParser(description="Batch tải lịch sử CĂNG CỰC - 10 năm Tin tức Doanh Nghiệp (Playwright)")
    parser.add_argument("--symbols", type=str, help="Danh sách mã cổ phiếu (VD: FPT,VIC,VNM).")
    parser.add_argument("--years", type=int, default=10, help="Số năm lịch sử cần cào lùi lại.")
    parser.add_argument("--delay", type=int, default=1, help="Độ trễ bằng giây. Mặc định 1s.")
    parser.add_argument("--force", action="store_true", help="Bắt buộc cào lại toàn bộ, bỏ qua kiểm tra Smart Resume.")
    parser.add_argument("--gui", action="store_true", help="Mở giao diện trình duyệt trực quan thay vì chạy ẩn (Headless). Cấm dùng trên Linux Server.")
    
    args = parser.parse_args()
    db_path = os.getenv("SMD_DB_PATH", "./data/securities_master.db")
    
    if args.symbols:
        symbols_list = [s.strip() for s in args.symbols.split(",")]
    else:
        logger.info("Đang nạp tọa độ cho Toàn bộ Thị Trường từ vnstock (VN30 ưu tiên)...")
        stock = Vnstock().stock(symbol='FPT', source='VCI')
        # Tối ưu: Nếu cần lấy toàn thị trường, ưu tiên VN30 trước
        symbols_list = stock.listing.all_symbols()['symbol'].tolist()
        logger.info(f"Đã lên đạn cho {len(symbols_list)} mã niêm yết.")
    
    # Kích hoạt chế độ Headless = True nếu không truyền --gui
    is_headless = not args.gui
    pipeline = HeavyweightHistoryPipeline(db_path=db_path, delay_seconds=args.delay, years=args.years, headless=is_headless)
    
    # Overwrite hàm run để log source riêng ('cafef_history') thay vì ('vietcap_news')
    # Chúng ta kế thừa logic của NewsPipeline nhưng cần sửa lại logging string
    successful_symbols = set()
    if not args.force:
        successful_symbols = pipeline.db.get_successful_symbols_today('cafef_history')
        logger.info(f"Smart Resume ON. Đã bỏ qua {len(successful_symbols)} mã đã cào lịch sử thành công hôm nay.")

    for symbol in symbols_list:
        if not args.force and symbol in successful_symbols:
            logger.info(f"[SKIPPED] {symbol} đã cào hoàn tất lịch sử, rẽ hướng sang lô cốt khác.")
            continue

        try:
            security_id = pipeline._get_security_id(symbol)
            logger.info(f"🚜 Tiến hành đổ bộ bờ biển: {symbol} - Đào bới {args.years} Năm lịch sử.")
            
            news_data = pipeline.extractor.fetch_history_metadata(symbol)
            if news_data:
                inserted = pipeline._save_news(security_id, news_data)
                logger.info(f"[THÀNH CÔNG] {symbol}: 🗃️ Đút két được {inserted} tin bài siêu cổ.")
                pipeline.db.log_etl_run(symbol, "cafef_history", "success", inserted, "History extracted via Playwright")
            else:
                logger.info(f"[EMPTY] {symbol} Vườn không nhà trống. Không tìm thấy lịch sử!")
                pipeline.db.log_etl_run(symbol, "cafef_history", "success", 0, "No historical news found")
                
        except Exception as e:
            logger.error(f"Error extracting history for {symbol}: {e}")
            pipeline.db.log_etl_run(symbol, "cafef_history", "failed", 0, str(e))
            
        # Thêm chu trình Clear/Garbage collection để tránh nổ RAM linux nếu vòng for dài 1500 lần
        # Code Playwright đã tạo và đóng Browser cho CẦU MỖI mã, nên khá an toàn.

if __name__ == "__main__":
    main()

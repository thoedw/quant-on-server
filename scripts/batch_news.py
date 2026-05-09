#!/usr/bin/env python3
import os
import sys
import argparse
import logging
from vnstock import Vnstock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from securities_master.news_pipeline import NewsPipeline

logger = logging.getLogger(__name__)

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    parser = argparse.ArgumentParser(description="Batch tải lịch sử Tin tức Doanh Nghiệp")
    parser.add_argument("--symbols", type=str, help="Danh sách mã cổ phiếu (VD: FPT,VIC,VNM). Nếu bỏ qua, script sẽ quét toàn bộ mã niêm yết.")
    parser.add_argument("--delay", type=int, default=2, help="Độ trễ bằng giây. Mặc định 2s để tránh bị block IP.")
    parser.add_argument("--force", action="store_true", help="Bắt buộc cào lại toàn bộ, bỏ qua kiểm tra Smart Resume (đã cào trong ngày).")
    
    args = parser.parse_args()
    db_path = os.getenv("SMD_DB_PATH", "./data/securities_master.db")
    
    if args.symbols:
        symbols_list = [s.strip() for s in args.symbols.split(",")]
    else:
        logger.info("Chưa cấp danh sách --symbols, Đang tự động kéo danh sách toàn bộ mã chứng khoán (1.544 mã) từ vnstock...")
        stock = Vnstock().stock(symbol='FPT', source='VCI')
        symbols_list = stock.listing.all_symbols()['symbol'].tolist()
        logger.info(f"Đã tìm thấy {len(symbols_list)} mã niêm yết.")
    
    pipeline = NewsPipeline(db_path=db_path, delay_seconds=args.delay)
    
    resume_today = not args.force
    pipeline.run(symbols_list, resume_today=resume_today, incremental=True)
    
if __name__ == "__main__":
    main()

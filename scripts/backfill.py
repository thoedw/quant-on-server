import os
import sys
import argparse
import logging
from datetime import datetime, timedelta

# Ensure the root project directory is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from securities_master.pipeline import ETLPipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

import time
from vnstock import Vnstock

def backfill_history(years: int, symbols: str, interval: str):
    db_path = os.getenv("SMD_DB_PATH", "./data/securities_master.db")
    
    if symbols.lower() == "all":
        try:
            df_listing = Vnstock().stock(symbol="VN30F1M", source="VCI").listing.all_symbols()
            symbols_list = df_listing['symbol'].tolist()
            logger.info("Chế độ ALL: Đã tải được danh sách %d mã chứng khoán.", len(symbols_list))
        except Exception as e:
            logger.error("Lỗi khi tải danh sách All symbols: %e. Dùng danh sách mặc định.", e)
            symbols_list = ["VNINDEX", "FPT", "VCB", "HPG", "VNM"]
    else:
        symbols_list = [s.strip() for s in symbols.split(",")]
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365 * years)
    
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')
    
    logger.info(f"Bắt đầu tải dữ liệu lịch sử cho {len(symbols_list)} mã.")
    logger.info(f"Khoảng thời gian: Từ {start_str} đến {end_str} | Khung thời gian: {interval}")
    
    pipeline = ETLPipeline(db_path=db_path)
    
    # Process one by one to add small sleep (rate limit protection)
    interval_list = [iv.strip() for iv in interval.split(',')]
    for i, symbol in enumerate(symbols_list):
        logger.info(f"Đang xử lý [{i+1}/{len(symbols_list)}]: {symbol}")
        try:
            pipeline.run(symbols=[symbol], start_date=start_str, end_date=end_str, intervals=interval_list)
        except Exception as e:
            logger.error(f"Failed to backfill {symbol}: {e}")
        # Be nice to the API provider
        time.sleep(1)
        
    logger.info("Hoàn tất quá trình tải lịch sử.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tải dữ liệu chứng khoán lịch sử (Backfill)")
    parser.add_argument("--years", type=int, default=5, help="Số năm lịch sử cần lấy (Mặc định: 5 năm)")
    parser.add_argument("--symbols", type=str, default="VNINDEX,FPT,VCB,HPG,VNM", help="Danh sách mã cổ phiếu (phân tách bằng phẩy)")
    parser.add_argument("--interval", type=str, default="1D", help="Khung thời gian (Ví dụ: 1D, 1W, 15m)")
    
    args = parser.parse_args()
    backfill_history(args.years, args.symbols, args.interval)

import os
import sys
import logging
import argparse
from datetime import datetime
from vnstock import Vnstock

# Thêm root dự án vào path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from securities_master.pipeline import ETLPipeline

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("prices_backfill.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Chạy đè toàn bộ, bỏ qua tính năng Smart Resume")
    args = parser.parse_args()

    logger.info("Đang trích xuất danh sách 1.544 mã chứng khoán (HOSE, HNX, UPCOM)...")
    stock = Vnstock().stock(symbol='FPT', source='VCI')
    all_symbols = stock.listing.all_symbols()['symbol'].tolist()
    
    logger.info(f"Đã lấy xong {len(all_symbols)} mã. Khởi động Cỗ máy xúc DNSE LightSpeed.")
    
    db_path = os.getenv("SMD_DB_PATH", "./data/securities_master.db")
    pipeline = ETLPipeline(db_path=db_path)
    
    # Chiến lược cào giá Lịch sử:
    # Do cào cho 1.500 mã, chúng ta sẽ ưu tiên gom Nến ngày (1D) và Nến tuần (1W) suốt 10 năm qua. 
    # (Nến phút sẽ được backfill sau nếu bạn cần thiết kế đánh Scalping/Day Trading)
    start_date = "2014-01-01" 
    end_date = datetime.now().strftime("%Y-%m-%d")
    
    logger.info(f"Mệnh lệnh: Hút giá từ {start_date} tới {end_date} (10 năm).")
    
    # Execute 
    pipeline.run(
        symbols=all_symbols,
        start_date=start_date,
        end_date=end_date,
        intervals=['1m', '5m', '15m', '30m', '1H', '1D', '1W'], # Kéo toàn bộ 7 khung thời gian
        resume_today=not args.force
    )
    
    logger.info("QUÁ TRÌNH HÚT GIÁ LỊCH SỬ DNSE HOÀN TẤT!")

if __name__ == "__main__":
    main()

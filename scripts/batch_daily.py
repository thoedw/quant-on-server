import os
import sys
import logging
from datetime import datetime, timedelta
from vnstock import Vnstock

# Thêm root dự án vào path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from securities_master.pipeline import ETLPipeline

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("daily_prices.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def main():
    logger.info("Đang lấy danh sách mã chứng khoán để chạy cào Giá gia tăng (Increment daily)...")
    stock = Vnstock().stock(symbol='FPT', source='VCI')
    all_symbols = stock.listing.all_symbols()['symbol'].tolist()
    
    db_path = os.getenv("SMD_DB_PATH", "./data/securities_master.db")
    pipeline = ETLPipeline(db_path=db_path)
    
    # Cào đắp thêm 2 ngày gần nhất (Overwrite an toàn)
    start_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")
    
    logger.info(f"Tiến hành đắp dữ liệu tự động từ {start_date} tới {end_date}.")
    
    pipeline.run(
        symbols=all_symbols,
        start_date=start_date,
        end_date=end_date,
        intervals=['1m', '5m', '15m', '30m', '1H', '1D', '1W'],
        resume_today=True
    )
    
    logger.info("HOÀN TẤT TỰ ĐỘNG CẬP NHẬT GIÁ CUỐI NGÀY!")

if __name__ == "__main__":
    main()

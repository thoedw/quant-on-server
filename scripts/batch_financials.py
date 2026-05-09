import os
import sys
import logging
import argparse
from vnstock import Vnstock

# Thêm root dự án vào path để import được các module bên trong
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from securities_master.financial_pipeline import FinancialPipeline

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("financial_backfill.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Chạy đè toàn bộ, bỏ qua tính năng Smart Resume")
    args = parser.parse_args()

    logger.info("Đang lấy danh sách mã toàn bộ thị trường...")
    stock = Vnstock().stock(symbol='FPT', source='VCI')
    df_symbols = stock.listing.all_symbols()
    all_symbols = df_symbols['symbol'].tolist()
    
    logger.info(f"Đã tìm thấy {len(all_symbols)} mã chứng khoán. Chuẩn bị chạy Cào Dữ liệu (Delay 5 giây / mã).")
    
    db_path = os.getenv("SMD_DB_PATH", "./data/securities_master.db")
    pipeline = FinancialPipeline(db_path=db_path, delay_seconds=5)
    
    # Bắt đầu chạy tuần tự
    pipeline.run(all_symbols, resume_today=not args.force, incremental=True)
    
    logger.info("ĐÃ HOÀN THẤT TOÀN BỘ QUÁ TRÌNH CÀO BÁO CÁO TÀI CHÍNH.")

if __name__ == "__main__":
    main()

import os
import sys
import logging
import argparse
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from securities_master.ekg_pipeline import EKGPipeline

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("ekg_batch.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Chạy luồng EKG Pipeline đồng bộ Node & Edge Đồ thị")
    parser.add_argument("--symbols", type=str, help="Danh sách mã cổ phiếu, cách nhau bằng dấu phẩy. Nếu không truyền sẽ lấy toàn TTCK.")
    parser.add_argument("--force", action="store_true", help="Chạy đè toàn bộ, bỏ qua tính năng Smart Resume")
    args = parser.parse_args()

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        logger.info("Chưa có danh sách mã. Đang truy vấn CSDL để cấu trúc thứ tự ưu tiên EKG theo Thanh khoản (Liquidity) lớn nhất...")
        db_path = os.getenv("SMD_DB_PATH", "./data/securities_master.db")
        from securities_master.database import DatabaseManager
        db = DatabaseManager(db_path)
        with db.get_connection() as conn:
            cursor = conn.cursor()
            # Lấy danh sách mã sắp xếp theo Volume trung bình lớn nhất (Đại diện thanh khoản)
            cursor.execute("""
                SELECT s.symbol 
                FROM securities s
                JOIN stock_prices p ON s.security_id = p.security_id
                WHERE p.interval = '1D'
                GROUP BY s.symbol
                ORDER BY MAX(p.volume) DESC;
            """)
            rows = cursor.fetchall()
            if rows:
                symbols = [r[0] for r in rows]
            else:
                logger.warning("Không có dữ liệu giá. Lùi về chế độ vét cạn Linear từ VNStock...")
                from vnstock import Vnstock
                stock = Vnstock().stock(symbol='FPT', source='VCI')
                symbols = stock.listing.all_symbols()['symbol'].tolist()
    
    logger.info(f"Khởi động cỗ máy EKG phân tích vĩ mô cho {len(symbols)} mã.")
    
    db_path = os.getenv("SMD_DB_PATH", "./data/securities_master.db")
    pipeline = EKGPipeline(db_path=db_path)
    
    successful_symbols = set()
    if not args.force:
        successful_symbols = pipeline.db.get_successful_symbols_today('ekg_pipeline')
        logger.info(f"Smart Resume: Đã bỏ qua {len(successful_symbols)} mã đã lên Đồ thị thành công trong ngày hôm nay.")
    
    success_count = 0
    for idx, symbol in enumerate(symbols):
        if not args.force and symbol in successful_symbols:
            continue

        logger.info(f"[{idx+1}/{len(symbols)}] Đang thâm nhập Hạch tâm dữ liệu: {symbol}")
        
        status = pipeline.process_symbol(symbol)
        
        if status:
            success_count += 1
            logger.info(f"[{symbol}] Khai phá Đồ thị thành công!")
        else:
            logger.warning(f"[{symbol}] Gặp trở ngại! Bỏ qua và chuyển mã tiếp theo.")
            
        # Rate Limiting bảo vệ Quota Gemini 1.5 Pro
        if (idx+1) < len(symbols):
            logger.info("Nghỉ giải lao 30 giây để tránh Rate Limit API của Google...")
            time.sleep(30)
            
    logger.info(f"🚀 XONG CHIẾN DỊCH: Thành công {success_count}/{len(symbols)} mã.")

if __name__ == "__main__":
    main()

import os
import argparse
import logging
from securities_master.database import DatabaseManager
from securities_master.auditors.financial_auditor import FinancialAuditor

logger = logging.getLogger(__name__)

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    
    parser = argparse.ArgumentParser(description="Run Rule-based Financial Ratio Auditor")
    parser.add_argument("--symbols", type=str, help="Comma-separated list of symbols (e.g. FPT,HPG)")
    args = parser.parse_args()
    
    db_path = os.getenv("SMD_DB_PATH", "./data/securities_master.db")
    db = DatabaseManager(db_path)
    # create table if not exists (already done via initialized schema)
    db.initialize_schema()
    
    auditor = FinancialAuditor(db_path)
    
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        # Lấy toàn bộ symbol đã có trong DB
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT symbol FROM securities")
        symbols = [row['symbol'] for row in cur.fetchall()]
        
    for symbol in symbols:
        sec_id = db.upsert_security(symbol)
        logger.info(f"Đang chạy Audit Kế toán Quy tắc cứng cho: {symbol}")
        try:
            auditor.run_year_audit(sec_id)
        except Exception as e:
            logger.error(f"Lỗi kiểm toán {symbol}: {e}")
            
    logger.info("Hoàn tất quy trình kiểm toán. Bạn có thể sử dụng Antigravity AI để phân tích các mã có status UNEXPLAINED_ANOMALY.")

if __name__ == "__main__":
    main()

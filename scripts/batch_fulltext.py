import os
import sys
import argparse
import logging
import time
import random

# Thêm root dự án vào path để import được các module bên trong
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from securities_master.database import DatabaseManager
from securities_master.extractors.fulltext_extractor import GenericFulltextExtractor

class FulltextPipeline:
    def __init__(self, db_path: str, delay_seconds: int = 5):
        self.db = DatabaseManager(db_path)
        self.extractor = GenericFulltextExtractor()
        self.delay_seconds = delay_seconds
        
        # Cấu hình logging
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        # Đảm bảo in ra console
        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
            self.logger.addHandler(handler)

    def run(self, limit: int = 50):
        # self.db.initialize_schema() # Bảo vệ dữ liệu, không reset schema tự động
        
        conn = self.db.get_connection()
        cur = conn.cursor()
        
        # Lấy các bài báo chưa có Full-text
        cur.execute("""
            SELECT id, symbol, title, source_link 
            FROM news_sentiment n
            JOIN securities s ON n.security_id = s.security_id
            WHERE (n.content IS NULL OR n.content = '')
            AND n.source_link IS NOT NULL AND n.source_link != ''
            ORDER BY n.published_at DESC
            LIMIT ?
        """, (limit,))
        
        rows = cur.fetchall()
        if not rows:
            self.logger.info("🎉 THÀNH CÔNG: KHÔNG CÒN BÀI BÁO NÀO THIẾU FULL-TEXT TRONG CSDL!")
            return

        self.logger.info(f"Đã bắt mạch được {len(rows)} bài báo trống ruột. Khởi động Máy Đắp Thịt...")
        
        success_count = 0
        for row in rows:
            news_id_db = row['id']
            url = row['source_link']
            title = row['title']
            symbol = row['symbol']
            
            self.logger.info(f"[{symbol}] Bóc tách: {title[:50]}... ({url})")
            
            fulltext = self.extractor.fetch_fulltext(url)
            
            if fulltext:
                cur.execute("UPDATE news_sentiment SET content = ? WHERE id = ?", (fulltext, news_id_db))
                conn.commit()
                success_count += 1
                self.logger.info("   -> Đã bơm thịt thành công! 🥩")
            else:
                self.logger.info("   -> Thất bại: Không chộp được text hoặc link hỏng 💀")
                
            if self.delay_seconds > 0:
                time.sleep(self.delay_seconds + random.uniform(0, 1))

        self.logger.info(f"ĐÃ HOÀN TẤT. Bơm thành công {success_count}/{len(rows)} bài báo.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Đắp Thịt (Full-text) cho các bài báo rỗng")
    parser.add_argument("--limit", type=int, default=100, help="Số lượng bài cần lấy mỗi mẻ (tránh bị ban IP)")
    parser.add_argument("--delay", type=int, default=3, help="Độ trễ giữa 2 lần cào trang Web (giây)")
    args = parser.parse_args()
    
    db_path = os.getenv("SMD_DB_PATH", "./data/securities_master.db")
    pipeline = FulltextPipeline(db_path, delay_seconds=args.delay)
    pipeline.run(limit=args.limit)

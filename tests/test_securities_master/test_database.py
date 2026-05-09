import os
import sqlite3
import pytest
from securities_master.database import DatabaseManager

@pytest.fixture
def memory_db():
    # Use an in-memory SQLite database for testing
    db = DatabaseManager(":memory:")
    db.initialize_schema()
    yield db
    db.close()

def test_database_initialization(memory_db):
    """Test that the database initializes correctly with all required tables."""
    conn = memory_db.get_connection()
    cursor = conn.cursor()
    
    # Check if tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cursor.fetchall()}
    
    assert "securities" in tables
    assert "stock_prices" in tables
    assert "etl_run_log" in tables
    assert "financial_reports" in tables

def test_pragmas(memory_db):
    """Test that WAL mode and normal synchronous are set."""
    conn = memory_db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA journal_mode;")
    journal_mode = cursor.fetchone()[0].lower()
    # In-memory DBs might default to memory for journal_mode, but let's check what it returns
    assert journal_mode in ["memory", "wal"]
    
    cursor.execute("PRAGMA synchronous;")
    sync_mode = cursor.fetchone()[0]
    # normal is 1
    assert sync_mode in [1, "1", "NORMAL"]

def test_ekg_schema_initialization(memory_db):
    """TEST TDD: Đảm bảo Schema mới hỗ trợ Đồ thị Doanh nghiệp Vĩ mô (EKG) được tạo thành công."""
    conn = memory_db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cursor.fetchall()}
    
    # Kỹ thuật TDD: Các bảng chuẩn DW và Graph mới
    assert "dim_entities" in tables, "Bảng dim_entities (Chuẩn hóa tên) chưa được tạo"
    assert "fact_relationship_network" in tables, "Bảng fact_relationship_network (Mạng lưới sở hữu/chuỗi cung ứng) chưa được tạo"
    assert "document_registry" in tables, "Bảng document_registry (Quản lý File hash chống lặp) chưa được tạo"
    
def test_ekg_scd_type2_columns(memory_db):
    """TEST TDD: Kiểm tra tính tuân thủ SCD Type 2 trên bảng đồ thị"""
    conn = memory_db.get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("PRAGMA table_info(fact_relationship_network);")
        columns = {row['name'] for row in cursor.fetchall()}
        
        assert "valid_from" in columns
        assert "valid_to" in columns
        assert "is_current" in columns
    except Exception as e:
        pytest.fail(f"Bảng fact_relationship_network chưa sẵn sàng: {e}")

def test_upsert_security_scd2(memory_db):
    """TEST TDD: Kiểm tra logic Upsert Security có kích hoạt lịch sử SCD Type 2"""
    conn = memory_db.get_connection()
    cursor = conn.cursor()

    # 1. Tự động sinh ID và bản ghi lịch sử đầu tiên
    sec_id = memory_db.upsert_security("FPT", "HOSE", "Công ty FPT")
    assert sec_id > 0
    
    cursor.execute("SELECT * FROM securities WHERE security_id = ?", (sec_id,))
    sec_row = cursor.fetchone()
    assert sec_row['symbol'] == "FPT"
    assert sec_row['exchange'] == "HOSE"

    cursor.execute("SELECT * FROM security_history WHERE security_id = ? AND is_current = 1", (sec_id,))
    history_rows = cursor.fetchall()
    assert len(history_rows) == 1
    assert history_rows[0]['exchange'] == "HOSE"

    # 2. Đổi sàn giao dịch HNX -> SCD-2 tự chia dòng
    same_sec_id = memory_db.upsert_security("FPT", "HNX", "Công ty FPT")
    assert same_sec_id == sec_id  # ID vật lý phải GIỮ NGUYÊN!
    
    # Kiểm tra Master đã update
    cursor.execute("SELECT * FROM securities WHERE security_id = ?", (sec_id,))
    updated_sec = cursor.fetchone()
    assert updated_sec['exchange'] == "HNX"

    # Kiểm tra History (Dòng cũ is_current=0, dòng mới is_current=1)
    cursor.execute("SELECT exchange, is_current FROM security_history WHERE security_id = ? ORDER BY history_id", (sec_id,))
    all_histories = cursor.fetchall()
    assert len(all_histories) == 2
    assert all_histories[0]['exchange'] == "HOSE" and all_histories[0]['is_current'] == 0
    assert all_histories[1]['exchange'] == "HNX" and all_histories[1]['is_current'] == 1

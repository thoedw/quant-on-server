import pytest
from datetime import date, datetime
from securities_master.models import PriceRecord, Security
from securities_master.database import DatabaseManager
from securities_master.loaders.sqlite_loader import SQLiteLoader

@pytest.fixture
def db():
    db = DatabaseManager(":memory:")
    db.initialize_schema()
    
    # Insert a dummy security to test against
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO securities (symbol, exchange, asset_type) VALUES (?, ?, ?)",
        ("FPT", "HOSE", "EQUITY")
    )
    conn.commit()
    
    yield db
    db.close()

def test_sqlite_loader_upsert(db):
    loader = SQLiteLoader(db)
    
    # 1. Initial Load
    records = [
        PriceRecord(
            security_id=1,
            interval='1D',
            trade_time=datetime(2023, 10, 1),
            open=10.0,
            high=11.0,
            low=9.0,
            close=10.5,
            volume=1000
        )
    ]
    
    loader.load_prices(records)
    
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM stock_prices WHERE security_id=1")
    rows = cursor.fetchall()
    
    assert len(rows) == 1
    assert rows[0]['close'] == 10.5
    assert rows[0]['volume'] == 1000
    
    # 2. Upsert / Update existing record
    updated_records = [
        PriceRecord(
            security_id=1,
            interval='1D',
            trade_time=datetime(2023, 10, 1),
            open=10.0,
            high=11.0,
            low=9.0,
            close=11.5, # Changed close price
            volume=2000 # Changed volume
        )
    ]
    
    loader.load_prices(updated_records)
    
    cursor.execute("SELECT * FROM stock_prices WHERE security_id=1")
    rows = cursor.fetchall()
    
    assert len(rows) == 1 # Still 1 row
    assert rows[0]['close'] == 11.5
    assert rows[0]['volume'] == 2000

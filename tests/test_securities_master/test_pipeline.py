import pytest
from unittest.mock import MagicMock
from securities_master.pipeline import ETLPipeline
from securities_master.database import DatabaseManager

@pytest.fixture
def db():
    db = DatabaseManager(":memory:")
    db.initialize_schema()
    conn = db.get_connection()
    conn.execute("INSERT INTO securities (security_id, symbol, exchange) VALUES (1, 'FPT', 'HOSE')")
    conn.commit()
    yield db
    db.close()

def test_pipeline_run_success(db):
    extractor = MagicMock()
    # Mock extractor returns a DataFrame
    import pandas as pd
    extractor.fetch_ohlcv.return_value = pd.DataFrame({
        'time': ['2023-10-01'],
        'open': [10.5],
        'high': [11.0],
        'low': [10.0],
        'close': [10.8],
        'volume': [1000]
    })
    
    pipeline = ETLPipeline(db_path=":memory:", extractor=extractor)
    # inject the memory db correctly into all components
    pipeline.db = db
    pipeline.loader.db = db
    
    pipeline.run(["FPT"], start_date="2023-10-01", end_date="2023-10-01")
    
    # Verify Data loaded
    conn = db.get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM stock_prices WHERE security_id=1")
    assert len(c.fetchall()) == 1
    
    # Verify Log
    c.execute("SELECT * FROM etl_run_log WHERE symbol='FPT'")
    logs = c.fetchall()
    assert len(logs) == 1
    assert logs[0]['status'] == 'success'
    assert logs[0]['rows_inserted'] == 1

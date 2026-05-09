from datetime import date
import pytest
from pydantic import ValidationError
from securities_master.models import Security, PriceRecord, ETLRunRecord

def test_security_validation():
    # Valid security
    sec = Security(symbol="VNM", exchange="HOSE", name="Vinamilk")
    assert sec.symbol == "VNM"
    assert sec.asset_type == "EQUITY"  # default
    
    # Missing required field
    with pytest.raises(ValidationError):
        Security(symbol="VNM")

def test_price_record_validation():
    from datetime import datetime
    record = PriceRecord(
        security_id=1,
        interval='1D',
        trade_time=datetime(2023, 10, 1),
        open=10.5,
        close=11.0,
        volume=1000
    )
    assert record.security_id == 1
    assert record.interval == '1D'
    assert record.trade_time == datetime(2023, 10, 1)
    assert record.high is None

    # Volume can be None (or 0)
    price_no_vol = PriceRecord(
        security_id=2,
        interval='1W',
        trade_time=datetime(2023, 10, 2),
        close=5.0
    )
    assert price_no_vol.volume is None

def test_etl_run_record_validation():
    r = ETLRunRecord(
        symbol="FPT",
        source="vnstock",
        status="success",
        rows_inserted=100
    )
    assert r.status == "success"

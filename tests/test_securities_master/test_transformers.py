import pytest
import pandas as pd
from datetime import datetime
from securities_master.transformers.ohlcv_transformer import OHLCVTransformer
from securities_master.models import PriceRecord

def test_transform_valid_ohlcv():
    df = pd.DataFrame({
        'time': ['2023-10-01', '2023-10-02'],
        'open': [10.5, 11.0],
        'high': [11.0, 12.0],
        'low': [10.0, 10.5],
        'close': [10.8, 11.5],
        'volume': [1000, 2000]
    })
    
    records = OHLCVTransformer.transform(df, security_id=1, interval='1D')
    
    assert len(records) == 2
    assert isinstance(records[0], PriceRecord)
    assert records[0].security_id == 1
    assert records[0].interval == '1D'
    assert records[0].trade_time.isoformat() == '2023-10-01T00:00:00'
    assert records[0].close == 10.8

def test_transform_drops_invalid():
    # Record with NaN close should be dropped
    df = pd.DataFrame({
        'time': ['2023-10-01', '2023-10-02'],
        'open': [10.5, 11.0],
        'high': [11.0, 12.0],
        'low': [10.0, 10.5],
        'close': [10.8, None], # invalid
        'volume': [1000, 0] # 0 volume might be okay or not, let's keep it but handle NaN close
    })
    
    records = OHLCVTransformer.transform(df, security_id=1, interval='1D')
    
    # Second should be dropped due to NaN close
    assert len(records) == 1
    assert records[0].trade_time.isoformat() == '2023-10-01T00:00:00'

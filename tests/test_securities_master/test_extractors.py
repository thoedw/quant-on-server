import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from securities_master.extractors.vnstock_extractor import VnstockExtractor, ExtractionError

@pytest.fixture
def extractor():
    return VnstockExtractor()

@patch('securities_master.extractors.vnstock_extractor.Vnstock')
def test_fetch_listing(mock_vnstock_class, extractor):
    mock_vnstock_instance = MagicMock()
    mock_vnstock_class.return_value = mock_vnstock_instance
    mock_stock = MagicMock()
    mock_vnstock_instance.stock.return_value = mock_stock
    
    mock_df = pd.DataFrame({
        'ticker': ['FPT', 'VNM'],
        'organ_name': ['FPT Corp', 'Vinamilk'],
        'exchange': ['HOSE', 'HOSE']
    })
    mock_stock.listing.all.return_value = mock_df

    df = extractor.fetch_listing()
    assert len(df) == 2
    assert "FPT" in df['ticker'].values
    mock_stock.listing.all.assert_called_once()

@patch('securities_master.extractors.vnstock_extractor.Vnstock')
def test_fetch_ohlcv(mock_vnstock_class, extractor):
    mock_vnstock_instance = MagicMock()
    mock_vnstock_class.return_value = mock_vnstock_instance
    mock_stock = MagicMock()
    mock_vnstock_instance.stock.return_value = mock_stock
    
    mock_df = pd.DataFrame({
        'time': ['2023-10-01', '2023-10-02'],
        'open': [10, 11],
        'high': [12, 12],
        'low': [9, 10],
        'close': [11, 11.5],
        'volume': [1000, 2000]
    })
    mock_stock.quote.history.return_value = mock_df

    df = extractor.fetch_ohlcv("FPT", "2023-10-01", "2023-10-02", interval='1D')
    assert len(df) == 2
    # Ensure correct symbol is used
    mock_vnstock_instance.stock.assert_called_with(symbol="FPT", source="VCI")
    mock_stock.quote.history.assert_called_once_with(start="2023-10-01", end="2023-10-02", interval='1D')

def test_extraction_error(extractor):
    with patch('securities_master.extractors.vnstock_extractor.Vnstock') as mock_vnstock_class:
        mock_vnstock_class.side_effect = Exception("API Down")
        
        with pytest.raises(ExtractionError, match="API Down"):
            extractor.fetch_listing()

# ==========================================
# TEST TDD CHO EKG GRAPH API CRAWLERS
# ==========================================

@patch('securities_master.extractors.vnstock_extractor.Vnstock')
def test_fetch_company_profile(mock_vnstock_class, extractor):
    mock_vnstock_instance = MagicMock()
    mock_vnstock_class.return_value = mock_vnstock_instance
    mock_stock = MagicMock()
    mock_vnstock_instance.stock.return_value = mock_stock
    
    # Mock data từ Overview
    mock_df = pd.DataFrame([{
        'company_profile': 'Cong ty XYZ',
        'icb_name2': 'Cong nghe TTTT',
        'icb_name3': 'Phan mem',
        'issue_share': 1000000,
        'charter_capital': 10000000000
    }])
    mock_stock.company.overview.return_value = mock_df

    profile = extractor.fetch_company_profile("FPT")
    assert profile is not None
    assert profile['issue_share'] == 1000000
    assert profile['icb_name3'] == 'Phan mem'
    mock_stock.company.overview.assert_called_once()

@patch('securities_master.extractors.vnstock_extractor.Vnstock')
def test_fetch_shareholders(mock_vnstock_class, extractor):
    mock_vnstock_instance = MagicMock()
    mock_vnstock_class.return_value = mock_vnstock_instance
    mock_stock = MagicMock()
    mock_vnstock_instance.stock.return_value = mock_stock
    
    mock_df = pd.DataFrame([{
        'share_holder': 'Tập đoàn SCIC',
        'quantity': 500000,
        'share_own_percent': 0.05
    }])
    mock_stock.company.shareholders.return_value = mock_df

    df = extractor.fetch_shareholders("FPT")
    assert len(df) == 1
    assert "SCIC" in df.iloc[0]['share_holder']
    mock_stock.company.shareholders.assert_called_once()

@patch('securities_master.extractors.vnstock_extractor.Vnstock')
def test_fetch_officers(mock_vnstock_class, extractor):
    mock_vnstock_instance = MagicMock()
    mock_vnstock_class.return_value = mock_vnstock_instance
    mock_stock = MagicMock()
    mock_vnstock_instance.stock.return_value = mock_stock
    
    mock_df = pd.DataFrame([{
        'officer_name': 'Trương Gia Bình',
        'officer_position': 'Chủ tịch HĐQT'
    }])
    mock_stock.company.officers.return_value = mock_df

    df = extractor.fetch_officers("FPT")
    assert not df.empty
    mock_stock.company.officers.assert_called_once()

@patch('securities_master.extractors.vnstock_extractor.Vnstock')
def test_fetch_subsidiaries(mock_vnstock_class, extractor):
    mock_vnstock_instance = MagicMock()
    mock_vnstock_class.return_value = mock_vnstock_instance
    mock_stock = MagicMock()
    mock_vnstock_instance.stock.return_value = mock_stock
    
    mock_df = pd.DataFrame([{
        'organ_name': 'Công ty Phần mềm FPT',
        'ownership_percent': 1.0,
        'type': 'Công ty con'
    }])
    mock_stock.company.subsidiaries.return_value = mock_df

    df = extractor.fetch_subsidiaries("FPT")
    assert len(df) == 1
    mock_stock.company.subsidiaries.assert_called_once()

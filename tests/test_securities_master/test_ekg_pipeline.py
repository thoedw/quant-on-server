import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
from securities_master.database import DatabaseManager, SCHEMA

# Chúng ta giả định sẽ có module ekg_pipeline với class EKGPipeline
from securities_master.ekg_pipeline import EKGPipeline

@pytest.fixture
def memory_db():
    db = DatabaseManager(":memory:")
    # Initialize schema
    with db.get_connection() as conn:
        conn.executescript(SCHEMA)
    return db

@patch('securities_master.ekg_pipeline.VnstockExtractor')
@patch('securities_master.ekg_pipeline.PdfCrawler')
@patch('securities_master.ekg_pipeline.GeminiGraphParser')
@patch('securities_master.ekg_pipeline.genai')
def test_ekg_pipeline_full_flow(mock_genai, mock_gemini_class, mock_pdf_class, mock_vnstock_class, memory_db):
    """TEST TDD: Giả lập việc chạy luồng trọn vẹn cho 1 mã Cổ phiếu"""
    
    # Mock extractors
    mock_vnstock = MagicMock()
    mock_vnstock_class.return_value = mock_vnstock
    mock_vnstock.fetch_shareholders.return_value = pd.DataFrame([
        {'share_holder': 'CTCP Sân Sau', 'quantity': 100, 'share_own_percent': 0.51}
    ])
    mock_vnstock.fetch_officers.return_value = pd.DataFrame([
        {'officer_name': 'Ông Trùm', 'officer_position': 'Chủ tịch'}
    ])
    mock_vnstock.fetch_subsidiaries.return_value = pd.DataFrame() # Trống
    
    # Mock PDF Crawler
    mock_pdf = MagicMock()
    mock_pdf_class.return_value = mock_pdf
    mock_pdf.fetch_metadata.return_value = [{"DocID": "111", "Url": "http://fake.pdf"}]
    mock_pdf.download_file.return_value = {
        "success": True, 
        "file_path": "/tmp/fake.pdf", 
        "file_hash": "hash123",
        "file_size": 1000
    }
    
    # Mock Gemini Parser
    mock_gemini = MagicMock()
    mock_gemini_class.return_value = mock_gemini
    mock_gemini.parse_document.return_value = [
        {"source": "Ông Trùm", "target": "VIC", "relation_type": "OFFICER_AT", "ownership_pct": None},
        {"source": "VIC", "target": "Thép Điện", "relation_type": "PRODUCES_PRODUCT", "ownership_pct": None}
    ]
    
    # Khởi tạo Pipeline
    pipeline = EKGPipeline(db_path=":memory:")
    # Thay thế db của pipeline bằng bộ nhớ ram
    pipeline.db = memory_db
    
    # Run
    status = pipeline.process_symbol("VIC")
    
    assert status == True
    
    with memory_db.get_connection() as conn:
        cursor = conn.cursor()
        # Kiểm tra dim_entities
        cursor.execute("SELECT entity_name, entity_type FROM dim_entities")
        entities = cursor.fetchall()
        entity_names = [e[0] for e in entities]
        assert "VIC" in entity_names
        assert "CTCP SÂN SAU" in entity_names
        assert "ÔNG TRÙM" in entity_names
        assert "THÉP ĐIỆN" in entity_names # Có từ Gemini
        
        # Kiểm tra fact_relationship_network
        cursor.execute("SELECT relation_type FROM fact_relationship_network")
        relations = [r[0] for r in cursor.fetchall()]
        assert len(relations) >= 3 # OWNS_SHARES, OFFICER_AT, PRODUCES_PRODUCT
        assert "OWNS_SHARES" in relations
        assert "PRODUCES_PRODUCT" in relations

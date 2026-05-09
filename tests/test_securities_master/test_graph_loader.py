import pytest
import sqlite3
import tempfile
from unittest.mock import MagicMock
from securities_master.database import DatabaseManager
from securities_master.loaders.graph_loader import GraphLoader

@pytest.fixture
def db_manager():
    # Sử dụng in-memory database hoặc file tạm thời
    temp_db = tempfile.NamedTemporaryFile(delete=False)
    db = DatabaseManager(temp_db.name)
    db.initialize_schema()
    yield db
    db.close()
    import os
    os.unlink(temp_db.name)

def test_load_graph_data(db_manager):
    """TEST TDD: Tải mảng JSON Graph từ Gemini vào SQLite (dim_entities và fact_relationship_network)"""
    loader = GraphLoader(db_manager)
    
    # Giả lập dữ liệu json mock trả về từ Gemini
    mock_json_graph = [
        {"source": "Ông Phạm XYZ", "target": "HPG", "relation_type": "OFFICER_AT", "ownership_pct": 0, "role": "Chủ tịch"},
        {"source": "HPG", "target": "Thép HRC", "relation_type": "PRODUCES_PRODUCT", "ownership_pct": None}
    ]
    
    # Đăng ký mầm mống cổ phiếu gốc (HPG) để foreign key hoặc entity tồn tại
    db_manager.upsert_security("HPG", "HOSE", "Tập đoàn Hòa Phát")
    
    # Act
    stats = loader.load_graph(symbol="HPG", graph_data=mock_json_graph)
    
    # Assert
    assert stats['entities_inserted'] >= 3  # Ông Phạm XYZ, HPG, Thép HRC
    assert stats['relationships_inserted'] == 2
    
    conn = db_manager.get_connection()
    cur = conn.cursor()
    
    # Kiểm tra dim_entities
    cur.execute("SELECT entity_name FROM dim_entities")
    entities = [row['entity_name'] for row in cur.fetchall()]
    assert "Ông Phạm XYZ" in entities
    assert "HPG" in entities
    assert "Thép HRC" in entities
    
    # Kiểm tra fact_relationship_network
    cur.execute("SELECT * FROM fact_relationship_network")
    relations = cur.fetchall()
    assert len(relations) == 2
    
    # Verify the specific relations
    assert any(r['relation_type'] == 'OFFICER_AT' for r in relations)
    assert any(r['relation_type'] == 'PRODUCES_PRODUCT' for r in relations)

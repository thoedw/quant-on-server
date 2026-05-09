import json
import pytest
from unittest.mock import patch, MagicMock
from securities_master.transformers.gemini_parser import GeminiGraphParser, ParsingError

@pytest.fixture
def parser():
    # Giả lập môi trường API Key
    with patch.dict('os.environ', {'GEMINI_API_KEY': 'fake_key'}):
        return GeminiGraphParser()

@patch('securities_master.transformers.gemini_parser.genai')
def test_parse_pdf_to_graph_json(mock_genai, parser):
    """TEST TDD: Đảm bảo Gemini Parser có thể gọi API và parse kết quả JSON hợp lệ"""
    mock_model = MagicMock()
    mock_response = MagicMock()

    # Chuỗi dữ liệu hỗn độn LLM thường sinh ra (Có Dấu Markdown)
    mock_json_str = """
    Dạ vâng, tôi đã phân tích tài liệu và tìm thấy các mối quan hệ sau:
    ```json
    [
        {"source": "Công ty TNHH Sân Sau", "target": "VIC", "relation_type": "OWNS_SHARES", "ownership_pct": 5.5},
        {"source": "VIC", "target": "Xe Hơi Điện", "relation_type": "PRODUCES_PRODUCT", "ownership_pct": null}
    ]
    ```
    Hi vọng thông tin này hữu ích.
    """
    mock_response.text = mock_json_str
    mock_model.generate_content.return_value = mock_response
    
    # Ép mock configure
    parser.model = mock_model

    # Gọi hàm parse
    edges = parser.parse_document("dummy_file.pdf", symbol="VIC")
    
    assert len(edges) == 2
    assert edges[0]['source'] == "Công ty TNHH Sân Sau"
    assert edges[0]['relation_type'] == "OWNS_SHARES"
    assert edges[1]['target'] == "Xe Hơi Điện"

@patch('securities_master.transformers.gemini_parser.genai')
def test_parse_invalid_json(mock_genai, parser):
    """TEST TDD: Trả về ExtractionError nếu LLM nói linh tinh không ra JSON"""
    mock_model = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Tài liệu này không có thông tin sở hữu nào cả."
    mock_model.generate_content.return_value = mock_response
    parser.model = mock_model

    with pytest.raises(ParsingError):
        parser.parse_document("dummy_file.pdf", symbol="VIC")

import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock

# Kỳ vọng sẽ có class PdfCrawler trong module mới
from securities_master.extractors.pdf_crawler import PdfCrawler

@pytest.fixture
def crawler():
    return PdfCrawler(storage_path=tempfile.gettempdir())

@patch('requests.Session.get')
def test_fetch_documents_metadata(mock_get, crawler):
    """TEST TDD: Giả lập việc kéo danh sách file PDF (không cần quét JS) từ API"""
    mock_response = MagicMock()
    mock_response.status_code = 200
    # Giả lập JSON trả về từ API SSC/CafeF (IDOR hoặc API thô)
    mock_response.json.return_value = [
        {"DocID": "123", "Title": "BCTC Q1 2023", "Url": "http://fake.url/bctc_123.pdf"}
    ]
    mock_get.return_value = mock_response

    with patch.object(crawler, '_random_sleep'):
        docs = crawler.fetch_metadata("FPT", doc_type="BCTC")
        
    assert len(docs) == 1
    assert docs[0]["DocID"] == "123"
    mock_get.assert_called_once()

@patch('requests.Session.get')
def test_download_and_hash_document(mock_get, crawler):
    """TEST TDD: Giả lập việc tải file PDF, lưu trữ và băm mã SHA-256"""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b'Fake PDF Content For Hashing 12345'
    mock_get.return_value = mock_response

    with patch.object(crawler, '_random_sleep'):
        result = crawler.download_file("http://fake.url/bctc_123.pdf")
    
    assert result['success'] is True
    assert 'file_path' in result
    assert os.path.exists(result['file_path'])
    
    # SHA-256 của b'Fake PDF Content For Hashing 12345' phải cố định
    assert 'file_hash' in result
    assert len(result['file_hash']) == 64  # SHA-256 length
    
    # Clean up
    os.remove(result['file_path'])

def test_anti_blocking_sleep(crawler):
    """TEST TDD: Đảm bảo có cơ chế Random Sleep chống block IP"""
    with patch('securities_master.extractors.pdf_crawler.time.sleep') as mock_sleep:
        crawler._random_sleep(min_sec=3, max_sec=5)
        mock_sleep.assert_called_once()
        args, kwargs = mock_sleep.call_args
        sleep_time = args[0]
        assert 3 <= sleep_time <= 5

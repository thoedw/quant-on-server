import os
import pytest
from unittest import mock
from mcp import types

def test_run_colab_code_mock():
    # Bài test (Green): Cung cấp mock environment variable để hàm execute_code trả về response định sẵn
    from colab_mcp.server import execute_code
    
    with mock.patch.dict(os.environ, {"COLAB_PROXY_URL": "mock"}):
        code_to_run = "print('Hello Google Colab')"
        result = execute_code(code_to_run)
        
        assert result == "Hello Google Colab\n"

def test_run_colab_code_missing_env():
    from colab_mcp.server import execute_code
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="COLAB_PROXY_URL is not set"):
            execute_code("print('Test')")


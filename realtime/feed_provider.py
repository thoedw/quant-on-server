from abc import ABC, abstractmethod
from typing import Callable, Awaitable, List

class FeedProvider(ABC):
    """
    Interface cơ sở cho các nguồn cung cấp dữ liệu Tick thời gian thực (Feed Providers).
    Ví dụ: DNSEProvider, MASVNProvider.
    """
    
    name: str = "Unknown"
    is_connected: bool = False

    def __init__(self):
        # Callback được TickRouter tiêm vào sau khi khởi tạo.
        # Signature: on_tick(symbol, price, volume, timestamp, source, side=None)
        #   side: 'BUY' | 'SELL' | 'NEUTRAL' | None
        #   - MASVN truyền side thật từ field mb (độ chính xác ~95%)
        #   - DNSE không có side → truyền None → TickRouter tự classify bằng Tick Rule
        self.on_tick: Callable = None

    @abstractmethod
    async def connect(self) -> bool:
        """Thực hiện kết nối tới WebSocket/MQTT của vendor. Trả về True nếu thành công."""
        pass

    @abstractmethod
    async def subscribe(self, symbols: List[str]):
        """Đăng ký nhận luồng dữ liệu tick cho danh sách mã chứng khoán."""
        pass

    @abstractmethod
    async def disconnect(self):
        """Ngắt kết nối an toàn."""
        pass

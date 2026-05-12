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
        # Callback cho put-through snapshot — chỉ DNSEProvider implement.
        # Signature: on_putthrough(symbol: str, data: dict)
        #   data keys: pt_vol, avg_pt_price, pt_val_tỷ, pt_count, latest_pt_price
        self.on_putthrough: Callable = None
        # Callback cho giao dịch nước ngoài — chỉ DNSEProvider implement.
        # Signature: on_foreign_tick(symbol: str, board: str, data: dict)
        #   board: 'G1' (khớp lệnh) | 'G4' (thỏa thuận)
        #   data keys: buy_vol, buy_val, sell_vol, sell_val, net_vol
        self.on_foreign_tick: Callable = None

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

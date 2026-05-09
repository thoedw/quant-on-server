Notes

đây là mục tiêu xây dựng dữ liệu giá của mình intraday và interday:
1. mình kéo dữ liệu giá OHLCV 10 năm với cả 7 timeframe cho 7 nến 1m, 5m, 15m, 1h, daily, weekly... 
2. mỗi ngày mình chạy intraday engine để lấy dữ liệu intraday (1m, 5m, 15m, 1h..) và buy vol, sell vol, delta, nếu mình không chạy ngày nào thì mình sẽ bị mất buy vol, sell vol, delta ngày hôm đó
3. cuối mỗi ngày giao dịch, mình chạy eod để lấy dữ liệu giá OHLCV với cả 7 timeframe cho 7 nến 1m, 5m, 15m, 1h, daily, của ngày hôm đó
4. Mình cần một tiến trình data quality check và  data gap filled để quét dữ liệu giá ở DB (cả 7 timeframe cho 7 nến 1m, 5m, 15m, 1h, daily, weekly), nếu bị gap thì thực hiện gap filled từ dữ liệu eod của DNSE, tiến trình này mình phải chạy trước khi mình chạy bất kỳ tiến trình quant interday nào.
em hãy đánh giá lại toàn bộ kiến trúc hiện tại và lên kế hoạch hoàn thiện dữ liêu giá phục vụ quant

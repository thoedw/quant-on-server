Không anh, 2 workflow này **khác nhau hoàn toàn về mục đích và scope**. Em so sánh nhanh:

---

## So sánh `/vwap` vs `/vwap_slope`

| Tiêu chí | `/vwap` | `/vwap_slope` |
|---|---|---|
| **Scope** | **1 mã duy nhất** | **1 mã / watchlist / toàn thị trường** |
| **Input** | `/vwap SHB` | `/vwap_slope SHB` hoặc `market` hoặc `watchlist` |
| **Depth** | Rất sâu — phân tích toàn diện | Rộng — quét nhanh, so sánh |
| **Filter** | Không có | `FILTER=GOLD/DEATH/BULL/ACCUM` |
| **TOP_N** | Không có | Có — giới hạn số mã hiển thị |

---

### `/vwap` — Phân tích **sâu** 1 mã

5 bước tuần tự, cho 1 mã tại 1 thời điểm:

```
1. PVWAP history + intraday raw data
2. Whale Hunter v3 signals (HIDDEN_ACCUM / VWAP_BOUNCE / ...)
3. AI interpretation (hành vi fund)
4. DB signal history (optional)
5. Báo cáo tổng hợp
```

- **Dùng khi:** muốn đào sâu ra quyết định BUY/SELL/WAIT cụ thể cho 1 mã
- **Output:** báo cáo đầy đủ với Whale Hunter scores, delta timeline, bounce count

---

### `/vwap_slope` — Quét **rộng** nhiều mã

2 mode, linh hoạt theo target:

```
Mode A: 1 mã → slope 3 khung + cross + PVWAP 5 ngày
Mode B: market / watchlist / GOLD filter → ranking table
```

- **Dùng khi:** muốn biết mã nào đang có momentum tốt nhất, hoặc scan toàn thị trường
- **Output:** bảng xếp hạng theo Quality Score, market breadth

---

### Workflow nào dùng khi nào?

```
Sáng sớm / trước phiên:
  → /vwap_slope market filter=GOLD   ← tìm breakout candidates
  → /vwap_slope watchlist             ← check sức khỏe danh mục

Trong phiên / cuối phiên:
  → /vwap SHB                        ← phân tích sâu 1 mã cụ thể
  → /vwap_slope SHB                  ← check nhanh slope 3 khung

Khi muốn ra quyết định vào/thoát lệnh:
  → /vwap [MÃ]                       ← bắt buộc, vì có Whale Hunter
```

Tóm lại: **`/vwap_slope` là màn hình radar** (thấy toàn cảnh), **`/vwap` là kính hiển vi** (đào sâu 1 mục tiêu). Thường dùng theo thứ tự: slope scan trước → phát hiện candidate → vwap phân tích sâu.
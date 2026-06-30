# K线: 撤销提前平仓后残留价格线未彻底清除

## 状态
Open (待修复)

## 现象
分批模式下，提前平仓后再撤销，K线上恢复的止盈线中有一根的视觉位置停留在提前平仓价格（而非其标签显示的止盈价格）。标签文本显示正确，但线画在了错误位置。

## 复现场景
1. 分批开仓 3 笔，止盈分别为 1573.63 / 1573.89 / 1574.04
2. 点击提前平仓 → 止盈线消失，黄色提前平线出现在 1573.52
3. 撤销提前平仓 → 3 条绿色止盈线重绘，标签正确，但批次3的线视觉上在 1573.52 而非 1573.63

## 后端确认（已排除）
- `_batch_early_close()` / `_cancel_batch_early_close()` 备份与恢复逻辑正确
- `_build_state()` 返回的 `tp_orders[].tp_price` 数据正确
- 标签显示正确说明 `createPriceLine({ price: 1573.63, title: '...' })` 参数无误

## 疑似根因
`src/web/static/index.html` → `clearChartMarkers()` 中第一行使用了 Lightweight Charts 内部属性：

```javascript
try { candleSeries.removePriceLine(candleSeries._priceLines); } catch (e) {}
```

`_priceLines` 是 Lightweight Charts 内部实现，行为不可靠。这可能导致提前平仓线未被正确移除，残留在图上。

## 建议修复方向
移除 `_priceLines` hack，只依赖 `chart._markerLines` 追踪的引用来清理所有标记线。

## 相关文件
- `src/web/static/index.html:1399-1404` — clearChartMarkers()
- `src/web/server.py:568-576` — _build_state() early_close_price 字段
- `src/trading/live.py:3882-3984` — _batch_early_close / _cancel_batch_early_close

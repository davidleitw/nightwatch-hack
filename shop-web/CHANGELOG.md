# Changelog

## Unreleased

- 新增 `traffic` 服務，定期透過 gateway 執行商品、購物車與 checkout 請求，讓服務關聯圖持續有真實流量。
- 新增 `TRAFFIC_INTERVAL_SECONDS`、`TRAFFIC_CHECKOUT_PROBABILITY` 與 `TRAFFIC_REQUEST_TIMEOUT_SECONDS` 設定。
- 產生器預設 request timeout 為 12 秒，能完整觀察 checkout 故障的 10 秒等待。

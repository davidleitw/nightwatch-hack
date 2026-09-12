# Backend 變更紀錄

## [Unreleased] — 2026-09-12

### Gateway 與服務拆分

- `app.main:app` 改為無資料庫的 public gateway：保留商品、cart、order 與 demo fault 的既有公開 path、schema 與 status，分別轉送至 catalog、cart、order service。
- 新增 `app.order:app` 與持久 checkout operation/attempt 狀態；order 以自己的 SQLite 保存訂單與復原狀態，支援 `Idempotency-Key`、cart prepare/complete/abort、catalog current-price lookup 與週期 reconcile。
- 新增 `app.common` 共用 Pydantic schema、SQLite/legacy read-only migration helper 與 request fingerprint；`app.http_client` 使用標準庫 HTTP，將業務與 control 請求分開 executor。

### Checkout recovery

- cart checkout 在 order DB 先記錄 `preparing`，跨服務 reserve cart、查價後將 order 與 `committed` 狀態放入同一筆 order transaction；cart complete 失敗會保留 `committed`，由相同 key retry 或背景 reconcile 完成。
- pre-commit failure 會嘗試 idempotent cart abort；startup reconcile 會清理未完成 operation。`checkout_exception` 在真實寫入後、commit 前 raise，`database_write_lock` 鎖定 order DB，`checkout_delay` 保持非阻塞等待。
- demo fault cards 沒有自動 lease 或 expiry；啟用後由 DELETE 手動解除，`lease_seconds`、`expires_at`、`remaining_seconds` 維持 `null`，process shutdown 仍會釋放 lock worker。

### Logging

- gateway、catalog、cart 與 order service 都使用現有 `shop` logger、UTC stdout/UTF-8 file 輸出與 `/logs/app.log` 每日 UTC 輪替；request log 不含 query/body，未預期錯誤含 traceback 後重新 raise。
- `LOG_LEVEL` 預設 `INFO`、`LOG_DIR` 預設 `/logs`；`app.log.YYYY-MM-DD` 保留七份，無 request 時由下一筆 log 觸發輪替。

### 驗證狀態

- 接手後已完成 production build、既有 10 tests（23.926 秒）、真 HTTP 故障與服務停止／恢復、commit 後 complete 失敗的同 key 重試與 restart reconcile、legacy 原始內容比對。完整結果見 `../docs/CHANGELOG.md` 的「接手修正與最終驗證」。
- 前端已使用 cart API；`localStorage` 保存 cart ID，舊版內容成功匯入後才移除。

### 接手驗證修正

- catalog 中斷時 cart GET 回傳具明確錯誤訊息的 503，保留購物車內容。
- 尚未 prepare 的 abort 保存取消紀錄，避免背景恢復反覆 404，也防止遲到的 prepare 再次保留購物車。
- catalog/cart 加上啟停與 HTTP application logging，沿用現有 stdout、UTC 每日輪替與私有 `/logs/app.log`。

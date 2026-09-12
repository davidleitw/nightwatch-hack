## 做了什麼

- 新增 `control/server/detector.py`，在現有每約 5 秒的 graph refresh 後確認異常，再透過既有 investigation manager 自動建立 session。
- 修改 `control/server/investigation_store.py`：在 SQLite 保存每個來源的 `latched` 旗標、固定 request_id、觸發節點、三次量測與當時 graph。先保存旗標再開案；完成、失敗、重啟均不解除，避免同一異常重複觸發。
- 修改 `control/server/investigation_api.py`：共用單一 running session 限制；忙碌時保存偵測，空閒後重新確認；agent 開場收到觸發原因與保存的 graph。未接通的模型仍留下 failed session。
- 更新 `control/README.md`、`control/server/README.md`、`control/INVESTIGATION-SPEC.md`，說明自動觸發與 session 生命週期。只變更 control，沒有新增第三方依賴或公開 API 欄位。
- 假設：同一節點連續三次新快照為 warning／failing 才觸發；要求 seq 與 at 前進、快照不超過 15 秒（允許未來 5 秒時鐘差）、logstore.ok=true。資料失效會中斷累計。
- 假設：觸發節點連續三次為 ok、全圖沒有其他 warning／failing 且沒有 running session 才解除旗標；unknown 或觸發節點消失不算恢復。旗標生效期間其他節點異常也不另開案。
- 假設：帶 query 的 graph 預覽／歷史來源及 NIGHTWATCH_MOCK_DATA=1 不自動觸發；偵測資料保留於既有 investigation DB。
- 依使用者指示，改動移至 `/private/tmp/nightwatch-auto-investigation`，分支 `feat/guardroom-auto-investigation`；只建立草稿 MR，不合併。

## 依據什麼驗證的

無。依使用者明確指示，本次不執行驗證。

## 沒有驗證的

- 未執行 build、測試、語法檢查、服務啟動、HTTP／SSE、真實模型呼叫或端對端排查。
- 去重、SQLite 建表與重啟恢復、忙碌後開案、恢復後重新觸發均未實跑；以上為程式實作與設定說明，不是驗證結果。

## 契約疑問

無。本次依使用者授權採用目前 Monitor graph 的 warning／failing 語意與持久旗標；不宣稱符合舊 shopper 基線／探活偵測流程。

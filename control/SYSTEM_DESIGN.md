# Agent loop：調查、演練修復與觀測

本文件說明 `control` 的實作。店面介面依 2026-09-12 本機服務公開的 OpenAPI；`nightwatch_agent/demo-faults.schema.json` 是其中 `DemoFaultsResponse` 與引用型別展開後的副本。沒有新增套件、修改店面或共用契約。

```text
手動 POST /api/investigations 或現有 detector 連續三次異常
  → InvestigationManager 保存 session
  → GraphAPI 準備固定 tools／system instructions
  → PydanticAI 呼叫模型
  → 工具取得 observation 或執行限定解除操作
  → 保存 tool.started／observation.recorded／tool.failed
  → 模型依結果選下一個工具
  → submit_report → 保存 report／context／usage／export
```

## 修復邊界

未設定 `NIGHTWATCH_SHOP_URL` 時，保留既有唯讀調查。設定例如 `http://127.0.0.1:8005` 才授權 agent 解除該本機演練服務的故障，並同時提供以下三個工具。只能使用本機 HTTP origin；不接受模型指定 URL、shell、任意方法或路徑。帶 query 的 graph 預覽／歷史模式不提供修復工具。CLI 與 HTTP manager 使用同一個 GraphAPI。

| 工具 | 實際 API | 結果與限制 |
| --- | --- | --- |
| `get_demo_faults` | `GET /api/demo-faults` | 保留 cards、active、lease_seconds、delay_seconds；記錄該 session 看到的 active。這是演練控制資訊，不是獨立根因推理。 |
| `deactivate_demo_fault` | `GET` → `DELETE` → `GET /api/demo-faults` | tool 參數 fault_id、started_at 必須與先前觀測相同；DELETE 本身無 body。送出前再查相同身份與租期，每 session 最多一次 DELETE。 |
| `check_shop_health` | `GET /api/health` | 回傳真實健康 API 結果，只能證明這次健康查詢結果，不能證明結帳成功。 |

故障注入 `POST /api/demo-faults` 留給操作人或驗證腳本，沒有提供給 agent。模型先取得 graph，再儘早查故障，避免把 60 秒租期全花在歷史調查。解除後要求模型讀 fault state、health、fresh graph，再提交附引用的報告。

解除結果保留 before、response、after，另外記錄 delete_sent、fault_control_cleared、recovery_verified=false。active=null 只證明 API 當下無作用中故障。模型報告沿用既有 schema，透過 findings／limitations 描述操作，`report_ready` 仍只是有效調查報告，不是 recovered。健康檢查與圖上的 retained errors 都不能代替成功結帳；驗證腳本在模型完成後獨立重送同一購物車結帳。

工具沿用 20 calls／900 秒／400,000 tokens 總預算、10 秒工具上限及順序執行。Shop HTTP 每個請求 2 秒、最大 16 KiB、不跟隨轉址；解除的最多三次 HTTP 請求不自動重試。API 拒絕、無效 JSON、schema 錯誤、過期或身份變更都成為可見 tool.failed，不生成成功 evidence。DELETE 一旦嘗試，即使回應丟失也不能在該 session 重送，應重新讀取狀態並報告不確定性。

修復說明由 System Prompt 要求沿用調查原本的 root cause 與證據，交代「原因如何造成症狀 → 為什麼此處置能改善 → 預期恢復的業務行為 → 實際驗證到哪裡」。對使用者不以工具名稱、API、HTTP 方法或參數流水帳解釋修復；執行細節仍保留於事件與 evidence，錯誤與限制以白話呈現。根因尚未確認時，明說是假設或症狀處置，不能從故障卡倒推已確認根因，也不能杜撰未觀測到的修復機制。例如已有證據支持寫入鎖阻塞結帳時，可解釋「寫入鎖使結帳無法保存訂單，解除阻塞後預期能恢復寫入」，再依後續證據說明是否真的成功。

## API 尚缺的保證

DELETE 沒有 instance ID、revision 或原子條件。GET 與 DELETE 之間，另一個操作人可能替換故障，租期也可能到期。因此目前只適用單一操作人獨占的本機演練，不能宣稱原子解除、跨 session exactly-once 或「空狀態一定由 agent 造成」。正式 actuator 應由店面提供條件式解除契約；本次沒有擅自增加 API 欄位。發生程序終止時也可能只留下 tool.started，操作結果需重新查核。

假設：本次所指環境錯誤 API 是目前公開的 `/api/demo-faults`；system design 放在本目錄，遵守只修改 `control/` 的範圍。真模型驗證是此次明確要求的外部模型連線，業務工具僅連本機。

## Prompt cache

保留既有 `nightwatch-python-agent-v1` cache key。system instructions 與工具 schema 在每件調查建立時固定，時間、故障狀態、剩餘預算與觀測只在 opening／後續工具訊息中傳入；每次模型回合保留原對話。修復能力開關會改變固定前綴，因此唯讀與修復 run 不保證共用整段 cache。既有預算耗盡時移除工具的行為保留，最後一輪可能改變前綴。

context／export／CLI 的 usage 新增 `cache_hit_rate` 與 `model_requests`。整體比率為所有 `cache_read_tokens / input_tokens`，不是各輪比率平均；零 input 回 null。逐輪資料來自 SDK ModelResponse.usage，保留 input、cached、output 與比率，不用推估值填補。這是 token 命中比率，不是請求命中率，也不是修復成功率。新增修復說明後的冷啟動與之後回合必須分別看實測，不能承諾固定門檻。

OpenAI 官方 [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) 說明固定前綴與快取；實際結果以服務回傳 usage 為準。本機 PydanticAI 2.43 的 `cache_read_tokens` 與 `input_tokens` 是此處統計來源。

## 實際觸發方式

使用現有 server virtualenv，不新增依賴；由呼叫環境提供模型金鑰。設定 `PYTHONPATH=control:control/server` 後執行：

```sh
control/server/.venv/bin/python control/acceptance/demo_repair.py \
  --shop-url http://127.0.0.1:8005 \
  --graph-url http://127.0.0.1:8004/api/graph \
  --output /absolute/path/to/control/.codex/repair-smoke
```

腳本啟動隔離的 HTTP investigation service，使用真實 graph、店面與模型。對 checkout_exception、database_write_lock 各建立專用購物車，注入故障、確認結帳 5xx，再 POST 開案；要求歸檔有 agent 解除證據及尚有效租期，最後相同購物車結帳必須回 201。保存完整 export 與逐輪 cache usage。腳本會產生兩筆真實本機 demo 訂單，清除專用購物車與仍由此次注入的故障，停止驗證 server。此腳本驗證手動觸發，不代表店面 checkout 異常已被既有 monitor 拓樸完整捕捉或 detector 已驗證。


## 2026-09-12 本機實跑

真模型 `gpt-6-astra`、真實店面 8005、graph 8004、隔離 investigation service 8017，未用替身。兩次手動 POST 都保存完整 export 與有效模型報告。模型缺乏結帳 monitor 的獨立證據，因此報告 outcome=unresolved；下列結帳結果由驗證腳本在模型完成後直接確認，沒有偽裝成模型已知證據。

| 案例 | 故障中結帳 | agent 解除前剩餘租期 | 解除後同車結帳 | 模型 requests | cached / input | 比率 |
| --- | --- | --- | --- | --- | --- | --- |
| checkout_exception | 500 | 49.950 秒 | 201 | 13 | 68,272 / 79,020 | 86.40% |
| database_write_lock | 500 | 40.388 秒 | 201 | 7 | 38,558 / 50,508 | 76.34% |
| 原版 master 唯讀 loop 對照 | 不注入 | 不適用 | 不適用 | 16 | 173,535 / 190,729 | 90.99% |

修復兩輪合併為 82.48%。第一輪第一個請求 cache=0，第二輪第一個請求 88.17%，顯示同設定的前綴仍有重用。原版與修復調查的回合數、工具輸出量和時間不同，以上是實際樣本，不能當成受控效能回歸結論，也不能宣稱命中率與原版相同。新增逐輪數據可追查某輪是否讀入大量新觀測。

執行中的實際程式完成兩個修復案例；另以既有快取中的 hatchling 離線建出 wheel，確認包含 actuator 與 JSON schema，從解開的 wheel 實際執行 graph／fault／health 讀取並確認錯誤身份在 DELETE 前被拒絕。完整模型修復是從 worktree 原始碼啟動；沒有再從 wheel 重跑整個模型案例。既有 23 項測試通過。未驗證自動 detector、checkout_delay 的完整模型流程、多人並發解除及長時間 cache 穩定性。

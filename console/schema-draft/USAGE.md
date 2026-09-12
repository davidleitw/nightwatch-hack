# Agent token 與 Prompt cache 命中率

假設：使用者提到的 prompt rate 指 Prompt cache 命中率（%）。不代表模型輸入處理速率（tokens/s），也不表示回答正確率。

## 資料與 schema

沿用 `contracts/API.md` §6 的 `GET /api/state` → `incident.usage`，以及 `GET /api/incidents/{id}` 的 `usage`。`usage.schema.json` 將現有欄位補上型別約束；不增加端點、SSE 事件或傳輸欄位。正式契約保持不變。

| 欄位 | 型別 | 意義 |
| --- | --- | --- |
| `input_tokens` | 非負整數 | 包含快取的全部輸入 token |
| `cached_tokens` | 非負整數 | 輸入中命中 prompt cache 的 token，必須 ≤ `input_tokens` |
| `output_tokens` | 非負整數 | 後端回報的輸出 token |
| `calls` | 非負整數 | 後端回報的模型呼叫次數 |
| `elapsed_secs` | 非負數值 | 後端回報的累計耗時（秒） |

完整 usage 需要以上五欄；尚未回報時省略 usage，前端也容忍 null。部分欄位缺漏時 UI 只顯示已有數值，缺值顯示「—」，不代表已通過完整 schema。負數、非數值、不安全整數及快取超出輸入會在用量區顯示具體錯誤；合法的其他數字仍可讀。

這是事故累計快照，不是增量。重新拉取時取代舊值，重新整理後仍以完整事故投影恢復。假設：同一事故內重新調查或重試仍累計，不把工具個數當成模型呼叫次數。

`observation.recorded.payload.usage.round` 沿用 `API.md` §6 的三個 token 欄位與共用 `$defs/tokens`。因契約沒有明確說明 round 的統計範圍，前端不將它加總成事故總量，也不把多個工具事件算成多次模型呼叫。

## UI 計算

- 總 token = `input_tokens + output_tokens`；快取已在 input 內，不能再加一次。
- Prompt cache 命中率 = `cached_tokens / input_tokens × 100%`，畫面保留最多一位小數。不平均每次呼叫的百分比。
- `input_tokens = 0` 時命中率顯示「—」；input > 0 且 cached = 0 時顯示 `0%`。
- 缺 input 或 output 時總量顯示「—」；缺 input 或 cached 時命中率顯示「—」。
- 不從整段事故時間或 `elapsed_secs` 推算 prompt tokens/s；那些時間可能包含模型生成、工具、等待，無法代表純輸入處理時間。
- 兩個主要數字下方顯示輸入、其中快取輸入、輸出；橫條只表達 cache 比例，不暗示品質、預算或費用。
- 保留既有 SSE 事故事件觸發重拉 `/api/state` 的流程。數字表示最後收到的累計投影，不能視為請求尚在進行時的即時 token 計數。調查事件及報告頁籤共用此用量區。

文件計算示例取自 `API.md` §6：input 15,700、cached 9,300、output 325，總量 **16,025**，命中率 **59.2%**。只是契約示例，不會注入網頁或假冒 live 資料。

## 待確認的契約

1. `incident.usage` 是否在每次模型回應完成時更新，並透過既有事故事件通知前端？`AGENT.md` §1.5 只描述交報告後更新，若調查期間不更新，前端無法顯示進度中的累計。
2. 舊錄影 `usage.round` 使用 `cached_input_tokens` 且附 `calls`，正式 `API.md` 使用 `cached_tokens`。是否由契約擁有者統一錄影與正式格式，並明確定義 round 與事故累計、重試的範圍？此版不私自改名或加總；舊錄影缺 `incident.usage` 時顯示未回報。

本次不修改 `contracts/` 或後端。JSON Schema 網址僅是標準識別；建置與頁面不連線下載 validator。

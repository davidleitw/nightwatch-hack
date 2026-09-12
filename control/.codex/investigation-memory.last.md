## 做了什麼

- 在獨立 worktree 實作，僅修改 `control/`。`memory.py` 定義案例結構；`investigation_store.py` 與結案同時保存案例並補建舊資料；`investigation_api.py` 注入最近五次摘要並提供 `get_investigation_memory`。
- `loop.py` 加入歷史比對指引與工具上限；`report.py` 拒絕用歷史查詢證據支持當次結論；`README.md` 記錄資料結構及行為。本機驗證摘要存於 worktree 的 `control/.codex/investigation-memory-smoke.json`（依既有 ignore 規則不提交）。
- 案例包含症狀、查詢步驟、候選根因、支持證據、反證、不確定性與下一步。模型須用當次觀測確認舊根因條件，不能只憑症狀相似判定復發。
- 假設：本次對話即任務規格；依使用者確認，每次啟動注入最近五次結案摘要。失敗、中斷及證據不足也保留原狀態；沿用既有 SQLite 與 investigation ID，不增加模型呼叫或套件。範圍為持久化 HTTP 後端，獨立 CLI 不接案例庫。

## 依據什麼驗證的

- 執行 `python -m unittest discover -s control/tests -v`：既有 23 個測試全部通過；其中含模型替身。
- 執行 `python -m compileall -q control/nightwatch_agent control/server` 與 `git diff --check`：均通過。
- 以既有 Python 環境、真模型 `gpt-6-astra`、本機 `8004` 真實 graph 與獨立 SQLite，在 `18004` 啟動無 reload 的後端；執行 `python3 control/.data/memory-smoke/invoke.py baseline` 及 `with-memory`。兩次 POST 均 202，均完成結構化報告與歸檔，所有報告、context、snapshots、events、export 查詢可讀，結案後沒有 active investigation。
- 改動前約 62.22 秒，13 次查詢、1 次歷史快照 404，輸入 token 29,081；新版約 54.21 秒，15 次查詢、零工具錯誤，輸入 token 52,486。兩次均 completed/unresolved，原因是觀測不足，並非 loop 執行失敗；單次比較不能保證效能，歷史查詢確實增加輸入 token。
- 實讀第一則 user message，確認 `recent_investigations` 位於最上方；真模型實際呼叫案例工具，讀到前案 13 個步驟與候選根因，最終明確說明相似條件不能確認同一根因。當次報告未引用歷史查詢的 evidence ID。
- 停服後重新開啟 SQLite，確認舊案例補建、新案例保存、最新在前、查詢 ID 可讀、未知 ID 明確報錯、案例不遞迴複製歷史查詢。兩個本次啟動的服務程序均已停止；curl 18004 回連線失敗。
- 執行 `git status --porcelain`，本 worktree 改動均在 `control/`。

## 沒有驗證的

- 沒有注入真實事故，未驗證根因診斷準確率、修復、恢復、自動偵測觸發與瀏覽器操作；實際端到端只驗手動開案。
- 真模型只注入一筆前案，未端到端跑滿五筆、超過五筆淘汰、超長案例截斷、失敗／中斷結案；23 個既有測試含替身，不等於上述場景的真模型驗證。
- `uv build --offline ... control` 未完成：隔離快取缺少既有建置依賴 hatchling，未安裝新套件。Python 編譯與實際後端啟動已驗證，wheel 打包未驗證。

## 契約疑問

無

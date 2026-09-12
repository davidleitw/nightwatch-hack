# 封存目錄

這裡保留 2026-09-12 整理前的舊黑客松任務與模擬服務。檔案原文保留，方便追查與還原。
封存內容不代表現有實作，也不是目前的啟動入口。

假設：依本次 repo 資料夾整理需求，將尚無實作的舊 shop、console 規劃，以及已由 Python
loop 取代的 control Go 任務，視為歷史資料；只供這些任務使用的 stubs 一併封存。
這 34 個檔案均已確認自初始化 commit `e12599a` 起沒有內容或權限變更，且與本機工作區一致。

| 原路徑 | 封存位置 | 依據 |
| --- | --- | --- |
| `shop/` | [legacy-hackathon/shop/](legacy-hackathon/shop/) | 只有 BRIEF、任務文件與佔位檔；現有購物網站位於 `shop-web/` |
| `console/` | [legacy-hackathon/console/](legacy-hackathon/console/) | 只有 BRIEF、任務文件與佔位檔；BRIEF 本身已註明舊 demo 使用另一個前端 |
| `stubs/` | [legacy-hackathon/stubs/](legacy-hackathon/stubs/) | 舊任務使用的模擬服務與 dev server；現有 shop-web 與 Python loop 的執行入口沒有引用 |
| `control/BRIEF.md` | [legacy-hackathon/control/BRIEF.md](legacy-hackathon/control/BRIEF.md) | Go 服務規劃；現行 control README 已明示改用 Python |
| `control/tasks/` | [legacy-hackathon/control/tasks/](legacy-hackathon/control/tasks/) | 與上述 Go 服務規劃配套的九份編號任務 |
| 根目錄 `README.md` 的舊內容 | [legacy-hackathon/README.md](legacy-hackathon/README.md) | 保留原三部份開工與提交流程；根目錄改列現有程式入口 |

`shop-web/`、control 的 Python 程式、`gate/`、`contracts/` 與 agent skills 保留原位。
`contracts/` 的 schema 與事故錄影仍供 Python loop 使用。[MR #3](https://github.com/davidleitw/nightwatch-hack/pull/3)
已將購物網站文件整理到 `shop-web/docs/`；本次以合併後的主線為基準，保留該結果與新後端骨架。

## 查閱與還原

歷史文件中的相對路徑、指令、驗證紀錄與技術選擇保持原樣；其中的驗證紀錄是原作者的紀錄，
不代表本次重新驗證。尤其 stubs 仍按原位置尋找 `../contracts/` 或 `../console/dist/`，
不能直接將封存目錄當成可執行專案。

若要恢復舊流程，先確認表中原路徑沒有新的同名檔案，再將需要的檔案或目錄搬回原路徑。
完整恢復編號任務需搬回 `shop/`、`console/`、`stubs/`、`control/BRIEF.md` 與 `control/tasks/`；
`contracts/` 仍在 repo 根目錄，無須複製。需要舊操作說明時查閱歷史 README 即可。

封存沒有補齊舊任務尚未實作的程式、前端 build 產物或外部服務；還原路徑後仍須依任務內容準備。

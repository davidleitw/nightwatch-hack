# 給隊友的 MR 實測 prompt

把 `<PART>` 換成自己負責的 `shop`、`control` 或 `console`，貼進該部份的 Codex session。
這是讓真實 PR 進入監控的測試，不需要修改產品功能。

```text
這次任務是 NightWatch MR 監控的端到端測試。我的部份是 <PART>。
請讀 AGENTS.md；只改我的目錄，不動其他既有改動、根目錄或 contracts/。

第一輪：
1. 在我的目錄新增 harness-smoke.md，寫明這次測試的目的、實際操作時間與「第一輪：確認 MR 被監控收進來」。
2. 讀回檔案確認內容，記錄真正執行的命令與結果；不要把沒有執行的檢查寫成通過。
3. 準備一筆獨立 draft PR，分支 <PART>/00-harness-smoke，標題「<PART> 00：MR 監控實測」，base 為 master。
4. PR 內文第一行為：
   <!-- nightwatch part=<PART> nn=00 check=not_run secs=0 files=1 -->
   接著依序寫：
   ## 第 1 輪
   ## 做了什麼
   ## 依據什麼驗證的
   ## 沒有驗證的
   ## 契約疑問
   四節寫真實內容。契約沒有問題就寫「無」。這次只新增文件，不聲稱產品測試通過。
5. 遵守 AGENTS.md 的 git 限制：你完成檔案與 PR body，提供讓我執行的建立分支、只提交該檔案、push 及 gh pr create --draft 指令。不要執行 task.sh submit，它不會建立 draft PR；不要把其他改動一起帶入。
6. 我執行後，把實際 PR URL 與 head SHA 交給 leader。不要合併。

等 leader 說「第一輪已看到」後，再做第二輪：
- 在同一檔案補上「第二輪：確認新 commit 與回報會更新」。
- PR 內文保留第一輪，追加「## 第 2 輪」和相同四個標題，內容只描述這次真的做的事。
- 提供讓我更新同一分支及同一 PR 的命令；不要另開第二筆 PR。
```

Leader 在 `http://127.0.0.1:8787` 確認：新 draft PR 出現；點進去能看到改動摘要、檔案與 patch、四節回報；第二輪更新後 head SHA 與摘要有變，完整回報仍保留兩輪。
預設在上一輪完成後每 30 秒重掃，頁面每 5 秒刷新；API 有延遲時以最後成功同步時間判斷。
這份 prompt 驗證的是 PR 擷取和展示。draft PR 會被原本的 gate 跳過，因此不會產生合併、退回或實測事件；這些流程要另外授權並用真實任務驗證。

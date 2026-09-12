# 開工前讀這一頁

這是黑客松的共用 repo。一個部份一個目錄一個主人,三個人完全平行做,不用互相等。

## 你的第一件事

```bash
bash setup.sh <你的部份>        # shop / control / console,只跑一次
```

它會檢查工具、GitHub 權限、設好 git、裝 pre-push hook。最後一行綠就可以開工,紅的整段貼給 leader。

然後讀 `<你的部份>/BRIEF.md` —— 那是你這塊要做什麼、為什麼、跟別人怎麼接。

## 每一段任務,四個指令

git 指令不用自己打,`task.sh` 全包了。

```bash
bash task.sh start 01 <你的部份>   # 拉新 master、開分支 <部份>/01-<短名>
bash run-task.sh <你的部份> 01     # 讓 codex 做事
bash task.sh submit                # 檢查 → commit → 合 master → push → 開 PR
bash task.sh done                  # leader 合併之後:回 master、刪分支
```

`submit` 失敗就 `bash task.sh fix` —— 它把失敗原因餵回 codex,修完再 `submit`。同一條 PR 會更新,標「第 N 輪」。

分支名和 commit 訊息都從任務檔 `<部份>/tasks/<NN>-<短名>.md` 的檔名與第一行來,不用手打。忘記指令就打 `bash task.sh`。

## 三條紅線

**只改自己目錄底下的檔案。** 碰到別人的目錄或根目錄的檔案,整條 PR 退回,連內容都不看 —— 包含你這輪做對的部分。需要別的目錄配合,寫進回報,不要自己動手。

**`contracts/` 是唯讀的。** 鄰居的介面只看契約,不要讀鄰居的程式碼。契約裡的名字(欄位、端點、指標、錯誤碼)一字不改地照抄。

**契約有問題不要自己繞。** 寫進回報的 `## 契約疑問`,leader 會回答。自己改契約或自己改名字,後面三個人會對不起來。

## 回報格式

codex 的最後一則訊息就是回報,會自動貼進 PR 內文。四節,標題一字不改:

```
## 做了什麼
## 依據什麼驗證的
## 沒有驗證的
## 契約疑問
```

**沒有實際跑過的事,不要寫進「依據什麼驗證的」。** 這場沒有共用的驗收指令(不假設每台機器環境一樣),所以 leader 合併時看的就是這四節。用 stub、mock 擋過去的,算「沒有驗證的」。

`## 契約疑問` 只要不是「無」,PR 就會停下來等 leader 回答 —— 所以只放真的疑問。

## 出事的時候

| 狀況 | 怎麼辦 |
|---|---|
| `setup.sh` 紅 | 整段貼給 leader |
| `submit` 說合不上 master | `bash task.sh fix`,codex 會解衝突 |
| PR 被退回 | 看 leader 的留言,`bash task.sh fix` 再 `submit` |
| PR 卡著沒動 | 多半是契約疑問在等 leader;問一聲 |
| 不小心改到別人的目錄 | `submit` 會自動還原,內容備份在 `<部份>/.codex/outside/` |

## 檔案在哪

| 路徑 | 是什麼 |
|---|---|
| `<部份>/BRIEF.md` | 你這塊是什麼(先讀這個) |
| `<部份>/tasks/` | 一段一個任務檔 |
| `contracts/` | 共用契約,唯讀 |
| `AGENTS.md` | codex 自動讀的規則,你不用讀 |
| `TASK-TEMPLATE.md` | 任務檔怎麼寫(leader 用) |

# Graph API 一鍵重啟

需先安裝 uv，系統需提供 curl、lsof。從 repo 根目錄執行：

```sh
./guardroom/restart.sh
```

Script 可從任意工作目錄執行，會安裝鎖定的依賴、停止本 script 啟動的舊程序，
並在背景啟動 FastAPI、等待 endpoint 就緒。預設綁定 `127.0.0.1:8001`。
自訂 port：`PORT=8002 ./guardroom/restart.sh`。每個 port 各自管理程序；
換 port 會啟動另一個 instance。PID 與 log 位於 `guardroom/.run/`。
遇到其他程式佔用 port 時會報錯，不會終止它。

| GET URL | 回傳 |
| --- | --- |
| `/api/graph?state=normal` | 所有節點正常 |
| `/api/graph?state=problem` | payment failing / origin；checkout、frontend warning / suspect；相關連線錯誤率與延遲升高 |
| `/api/graph` | 預設 normal |

```sh
curl 'http://127.0.0.1:8001/api/graph?state=normal'
curl 'http://127.0.0.1:8001/api/graph?state=problem'
```

`state` 僅選擇當次回應，不修改全域狀態；其他值回傳 HTTP 422。
兩種狀態均為 dummy 資料，拓撲與 schema 相同。API 文件：`http://127.0.0.1:8001/docs`。

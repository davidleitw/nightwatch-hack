# Function monitor

收集函式執行生命週期、執行期間的 Python logging output，以及未處理 exception。
僅使用 Python 3.11+ 標準函式庫。既有 `@monitor(name="...")` 用法相容。

```python
import logging
from monitor import MonitorConfig, get_detail, monitor

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # 由應用程式決定哪些 log 會被產生

@monitor(MonitorConfig(name="payment", description="處理付款"))
def payment():
    logger.info("開始付款")
    try:
        return charge()
    except Exception:
        logger.warning("付款失敗，交由呼叫端處理")
        raise

# payment 執行後（即使失敗），可取得狀態與近期事件：
detail = get_detail("payment")
```

亦支援 `@monitor`、`@monitor()`、`@monitor(config=MonitorConfig(...))`。
預設 config：name 使用函式 qualname、description 為空、level 為 INFO、capture_logs 為 True。
`level` 只篩選收集的 logger output；生命週期與 exception 仍會記錄。

| 模組 | 責任 |
| --- | --- |
| `models.py` | config、state、event 與 EventSink 輸出介面 |
| `runtime.py` | invocation 關聯、生命週期、狀態及有上限的近期事件 |
| `adapters.py` | logging handler 輸入、JSONL 檔案輸出 |
| `__init__.py` | decorator 與預設依賴組裝、對外 API |

每次呼叫產生 invocation_id，巢狀呼叫使用 parent_invocation_id，log 歸屬最內層
監測函式。ContextVar 隔離 async task；`asyncio.to_thread` 可傳遞 context，
自行建立的 thread／process 不會自動傳遞。函式回傳後才發出的背景 log 不再歸屬該次呼叫。
一般函式與 coroutine 皆支援；generator／async generator 會明確拒絕。

ERROR log 不會把函式狀態改為 error；未處理 exception（含取消）才會記為 error，
保留 traceback 並原樣拋出。`logger.exception` 的已處理例外則保留在 log event 中。
如同一次例外先 logging 再拋出，會有 log 與 exception 兩筆不同事件。
不自動收集參數、回傳值、locals、任意 logging extra 或 print；log message 與 traceback
仍會保留應用程式寫入的文字。

Decorator 會將 handler 安裝到 root logger，不調整全域 level，也不替換既有 handlers。
logger 本身過濾掉的訊息無法收集；`propagate=False` 的 logger 請顯式呼叫
`install_logging(logger)`。必須在函式執行的 context 內捕捉；請在 QueueHandler 的
來源 logger 安裝 handler，不能等 QueueListener 的其他 thread 才關聯。

`get_states()` 回傳各節點最新一次狀態更新；並行執行時不表示所有呼叫的彙總健康。
`get_detail(name)` 含 config、state、active_invocations、events、event_capacity、sink_errors；
不存在的 name 拋出 KeyError。預設在記憶體保留全 runtime 最近 1000 筆事件，
超過會淘汰舊資料；重啟不恢復。節點註冊資料保留至程序結束，應使用固定名稱。

預設 JSONL 路徑沿用 `control/tmp/monitor.jsonl`，時間改為 UTC RFC3339；
每筆事件包含 event_id、node、invocation_id、kind、level、message 等欄位。
歷史檔案的舊紀錄不會改寫，消費端須容許舊欄位。檔案不自動輪替；多程序部署應各用
自己的 sink／檔案，不能依賴這個 thread lock 協調其他程序。

可注入 `MonitorRuntime(sinks=(custom_sink,), max_events=1000)` 到 decorator 的 `runtime=`。
custom_sink 只需實作 `emit(event)`，runtime 不依賴檔案格式；輸出失敗會增加 sink_errors，
不取代應用程式的結果或例外。`configure_file_sink(path)` 為預設 runtime 增加輸出位置，
重複設定同一路徑不重複輸出。

```sh
cd control
uv run --no-project --python 3.13 python -m unittest discover -s tests -p 'test_monitor.py' -v
```

目前提供程序內 API，尚未將 detail 接入 HTTP、SSE 或 graph 健康判定。

## Console log projection

`ConsoleLogSink` 將原始事件投影為 `console/schema-draft/log.schema.json` 格式，
透過注入的同步 callback 交給下一層。預設 JSONL sink 仍保留原始格式。

```python
from monitor import ConsoleLogSink, MonitorConfig, MonitorRuntime, monitor

payloads = []  # 示範；正式 consumer 可換成 queue.put_nowait 等同步輸出
runtime = MonitorRuntime(sinks=(ConsoleLogSink(payloads.append),))

@monitor(MonitorConfig(
    name="payment",
    monitor_id="payment-monitor",
    node_ids=("payment",),
), runtime=runtime)
def payment():
    logger.info("開始付款")
```

`monitor_id` 未設定時使用函式的 `module.qualname`，同一函式跨呼叫保持一致；
需跨重構維持 ID 時請明確設定。`node_ids` 預設空 tuple，不推測 graph 節點；
可傳 list，會轉成不可變 tuple，拒絕空白或重複 ID。`instance_id` 選填，未提供不輸出。

四種事件都可投影：`schema_version=nightwatch.log.v1`，`timestamp → occurred_at`，
`WARNING → WARN`、`CRITICAL → FATAL`，額外執行資訊放入 `attributes`。
logging 事件使用 `LogRecord.created` 的 UTC 時間；生命週期使用事件產生時間。
重送可用 `to_console_log(event)`，保留原 event_id 與時間；此 adapter 不負責去重。
未知自訂 log level 明確拒絕投影，透過 runtime 使用時會計入 sink_errors。

原始事件新增 monitor_id、node_ids、instance_id，原有欄位保留。
此處只建立 payload，尚未實作 SSE 傳輸、重播或 HTTP 接線。

包含 console schema 驗證的測試：

```sh
cd control
uv run --no-project --python 3.13 --with 'jsonschema>=4.23,<5' python -m unittest discover -s tests -p 'test_monitor*.py' -v
```

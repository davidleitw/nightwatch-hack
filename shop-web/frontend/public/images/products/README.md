# 商品圖片映射

這六張商品圖依 seed 商品的穩定名稱映射；前端以商品名稱選圖，避免使用可與自訂商品重疊的數字 ID。未匹配的自訂商品仍使用 API 回傳的 `icon` 作為 fallback。

| 商品名稱 | 圖片檔案 |
| --- | --- |
| 晨光陶瓷杯 | `morning-ceramic-mug.png` |
| 日常帆布托特包 | `daily-canvas-tote.png` |
| 木質香氛蠟燭 | `wood-scented-candle.png` |
| 靈感方格筆記本 | `grid-notebook.png` |
| 輕旅保溫水瓶 | `travel-water-bottle.png` |
| 桌上綠意盆栽 | `desk-houseplant.png` |

圖片以內建 image generation 工具逐張生成，沒有使用外部圖片來源；本環境無法指定或宣稱特定的 `GPT image 2.5` 模型版本。

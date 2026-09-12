# Nightwatch

一個能自主監控服務、判斷異常並採取處置的生態系統。

我們將「服務」定義為由多個節點（Node）組成的網購服務。每個節點皆由獨立的 Monitor 監控，產生的日誌與狀態會集中至 Guard Room；Watcher 則讀取 Guard Room 的資訊，判斷系統狀況並控制各個節點。

```text
Nodes → Monitors → Guard Room → Watcher → Nodes
                         ↓
                      Frontend
```

## 專案方向

### 1. Example Service (Shop)

作為監控與處置對象的範例購物網站，由多個節點組成。

### 2. Backend

- **Monitor**：獨立監控各個節點，蒐集日誌與狀態。
- **Guard Room**：彙整所有 Monitor 傳入的資訊，提供完整的服務視圖。
- **Watcher**：根據 Guard Room 的資訊自主判斷，並控制各個節點 from Example Service。

### 3. Frontend

- 接收 Guard Room 提供的 Graph Schema，依時間戳呈現服務狀態與節點關係。
- 呈現 Watcher 的目前狀態與行動。

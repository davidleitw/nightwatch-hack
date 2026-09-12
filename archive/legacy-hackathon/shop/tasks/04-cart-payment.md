# 店面 04:購物車與付款,付款有主備兩個供應商(第二張故障卡的機制)

## 為什麼要有這段
第二張卡是「主要付款供應商變慢變爛」,修復方式是切到備援而不是回滾。服務圖上的兩個外部節點與兩條邊,只有這裡的 client span 能產生。

## 目標
- cart 是進程內的購物車服務:同一商品重複加會累加、可以清空;旋鈕 `error_rate` 大於 0 時按比例直接失敗並寫契約規定的 WARN log。
- payment 依旋鈕 `provider.active` 選主要或備援供應商扣款。供應商在進程內模擬,但每次扣款要開一個 client span,名字與 `peer.service` 照契約,讓 collector 算出 payment 對兩個外部節點的邊。
- 主要供應商的延遲與失敗率由兩顆旋鈕控制;失敗回 402、寫契約規定的 WARN log、span 標錯。備援固定 120 ms、不失敗。
- 旋鈕改了下一筆就生效;只推 `provider.active` 不會洗掉其他旋鈕(合併語意在 payment 上也成立)。

## 契約在哪
- `../contracts/SHOP-INTERNAL.md` §2(cart、payment 那幾列)、§3(卡 B 那行)、§6(cart、payment 端點,建議值)
- `../contracts/METRICS.md` §2.1(`provider.charge` client span)、§2.3(collector 算出來的邊)、§2.4(關鍵 log)

## 怎麼算做完
- 兩個服務各自起來,加購物車、累加、清空都對;一筆扣款成功約 80 ms。
- 把主要供應商失敗率推到 1、延遲推到 800 ms:扣款回 402、約 800 ms、stdout 有 WARN;再只推 `provider.active=secondary`:扣款回 200、約 120 ms。
- cart 的 `error_rate` 推到 1:每個請求都 500、stdout 有 WARN。

## 測試
只寫契約邊界與主線。一定要有:失敗率 1 回 402;切備援後成功;一次扣款產生一個帶正確 `peer.service` 的 client span。

## 不做什麼
結帳、店面網頁、合成顧客、出貨。

## 接線點
compose-min 加上 cart 與 payment,對 payment 打幾筆扣款:Jaeger 要看到 `provider.charge` client span,Prometheus 的服務圖指標要出現 payment 對兩個供應商的邊(`METRICS.md` §2.3)。卡 B 的顧客端症狀要等 05 與 07。

# RerollSuperMoment

建議先自己按一次 把遊戲預設的部分先開啟 再使用指令執行即可

MLB 9 局職棒 勁旅對決 26（MLB 9 Innings Rivals）的刷初始帳號腳本。

在 LDPlayer 安卓模擬器上自動重複「建隊 → 換卡 → 組合 → 抽卡 → 判定 → 重置」，
抽到指定卡片就停下來並寄信通知。支援同時操作兩台模擬器。

全部走 ADB，**不佔用實體滑鼠鍵盤、不需要模擬器視窗在前景**，所以跑的時候可以正常用電腦。

## 需要什麼

- Windows
- Python 3.12+ 與 `pip install opencv-python numpy mss pydirectinput pygetwindow pytesseract`
- [LDPlayer](https://www.ldplayer.tw/)（預設路徑 `C:\LDPlayer\LDPlayer14`），解析度 **1600x900 / DPI 240**
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)（判定卡片名字用）

解析度必須一致：`templates/` 裡的 43 個模板都是在 1600x900 / 240dpi 上裁的，
換解析度會讓每一步都對不上。程式在開跑前會驗，對不上直接擋下來。

## 設定

流程全部寫在 `reroll_config.json`，改 JSON 就好，不用動程式。
檔案裡的 `_說明` 區塊有每種步驟與每個選項的用法。

想抓哪些卡片改 `hit_targets`：

```json
"hit_targets": [
  { "keywords": ["urakam", "Murakami"], "ovr": 76, "note": "M. Murakami" }
]
```

`keywords` 用姓氏中段（避開字首字尾，OCR 最容易在那裡出錯）。

### 信箱通知

密碼**只**放環境變數，不進設定檔——設定檔會連同模板一起被備份或分享出去：

```powershell
[Environment]::SetEnvironmentVariable("REROLL_SMTP_PASS", "你的應用程式密碼", "User")
```

Gmail 要用[應用程式密碼](https://myaccount.google.com/apppasswords)，不是登入密碼。
設定檔裡改 `notify_mail` 的收發信地址。

## 怎麼跑

單台：

```powershell
python reroll.py run
```

兩台（先用 `ldconsole copy --name LDPlayer-2 --from 0` 複製第二個實例，
再把 serial 填進設定檔的 `instances`）：

```powershell
python watchdog.py --instance A --instance B
```

隨時按 **F12** 中斷（兩台一起停）。

### 裁模板 / 除錯

```powershell
python reroll.py shot                  # 截圖，拿去裁模板
python reroll.py test 陣容.png          # 測某個模板比對得到嗎
python reroll.py ocr                   # 測 OCR 讀不讀得到
python reroll.py mail                  # 寄測試信
```

## 卡住的時候會怎樣

不是每次都順利，所以失敗處理是分層的：

| 狀況 | 處理 |
|---|---|
| 遊戲閃退 | 自動重開，再走一次遊戲內重置回到起點 |
| 流程卡住 | 先自己救 2 次（關掉 App 重開，不刪資料）|
| 自救失敗 | 寄信通知並附上**自救前**的現場截圖，然後停著等 |
| 你處理完 | 把畫面弄回「創立球隊」，它會自己偵測到並繼續，不用重下指令 |
| log 停滯超過 5 分鐘 | 看門狗殺掉重開 |

重置一律用**遊戲內的重置功能**，不用 `pm clear` 清除 App 資料——
清資料要重走條款／訪客登入，每輪多花四分半。

## 檔案

| 檔案 | 用途 |
|---|---|
| `reroll.py` | 主程式。擷取、模板比對、OCR、ADB 輸入、流程執行 |
| `watchdog.py` | 看門狗。管一到多個實例，偵測卡住並重開 |
| `reroll_config.json` | 所有流程、座標、命中條件、實例設定 |
| `templates/` | 43 個畫面模板 |

`shots/`、`hits/`、`*.log` 是執行期產物，沒有進版控。

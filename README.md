# RerollSuperMoment

建議先自己按一次 把遊戲預設的部分先開啟 再使用指令執行即可

MLB 9 局職棒 勁旅對決 26（MLB 9 Innings Rivals）的刷初始帳號腳本。

在 LDPlayer 安卓模擬器上自動重複「建隊 → 換卡 → 組合 → 抽卡 → 判定 → 重置」，
抽到指定卡片就停下來並寄信通知。支援同時操作兩台模擬器。

全部走 ADB，**不佔用實體滑鼠鍵盤、不需要模擬器視窗在前景**，所以跑的時候可以正常用電腦。

## 需要什麼

- Windows
- Python 3.12+ 與 `pip install opencv-python numpy mss pydirectinput pygetwindow pytesseract`
- [LDPlayer](https://www.ldplayer.tw/)（預設路徑 `C:\LDPlayer\LDPlayer14`）
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)（判定卡片名字用）
- **遊戲本體**：MLB 9 局職棒 勁旅對決 26，裝在模擬器裡（見下面的事前準備）

## 事前準備

`templates/` 裡的 43 個模板是「照著特定畫面裁出來的圖」，靠像素比對定位按鈕。
所以模擬器的畫面必須跟當初裁模板時一模一樣，下面每一項都會影響比對結果。

**1. 模擬器設定成 1600x900 / DPI 240**

```powershell
& "C:\LDPlayer\LDPlayer14\ldconsole.exe" modify --index 0 --resolution 1600,900,240
```

`--resolution` 的順序是 `寬,高,dpi`，而且要在模擬器**關機時**改（開著改要重開才生效）。
也可以在模擬器的設定介面裡改，結果一樣。

改完務必驗一下，這是唯一能確定沒設錯的方法：

```powershell
& "C:\LDPlayer\LDPlayer14\adb.exe" -s emulator-5554 shell wm size      # 要回報 1600x900
& "C:\LDPlayer\LDPlayer14\adb.exe" -s emulator-5554 shell wm density   # 要回報 240
```

（遊戲是直向的，所以 `screencap` 抓出來會是 900x1600——那是旋轉後的結果，正常。）

解析度或 DPI 不對，43 個模板會一起失效——每一步都對不上，看起來像整個流程壞掉，
而不像「設定錯了」。程式開跑前會驗（設定檔的 `adb.device_size`），對不上直接擋下來
不讓你白跑一整晚。

**2. 模擬器語言設成繁體中文**

模板上的字都是繁體中文（「陣容」、「組合」、「重置遊戲」…）。
語言不對的話，凡是含文字的模板全部比不到。
在模擬器裡：設定 → 語言與輸入 → 選「中文（繁體）」。

**3. 安裝遊戲並登入到「創立球隊」畫面**

從模擬器內的 Google Play 搜尋「MLB 9 局職棒 勁旅對決」安裝
（套件名 `com.com2us.futuremlb.android.google.global.normal`）。

裝好後**手動**開一次，把首次啟動流程走完：同意使用條款 → 選「使用訪客登入」→
一路到出現「創立球隊」的畫面。

第一次要手動，是因為首次啟動流程要走好幾分鐘，而腳本等「創立球隊」只等 30 秒。
之後每一輪都由腳本自己重置回這個畫面，不用再管。

腳本的起點與終點都是「創立球隊」畫面——開跑前請確認停在那裡。

**4.（雙開才需要）複製第二個實例**

```powershell
& "C:\LDPlayer\LDPlayer14\ldconsole.exe" quit --index 0
& "C:\LDPlayer\LDPlayer14\ldconsole.exe" copy --name LDPlayer-2 --from 0
& "C:\LDPlayer\LDPlayer14\ldconsole.exe" modify --index 1 --imei auto --androidid auto --mac auto --imsi auto --simserial auto
```

一定要用 `copy` 而不是 `add`：`copy` 會把**已經裝好的遊戲和解析度設定一起複製過去**，
`add` 出來的是空的模擬器，還得重裝遊戲、重設解析度、再手動登入一次。
`modify` 那行是把裝置識別碼打散，避免遊戲把兩台當成同一台裝置。

複製完啟動兩台，用 `adb devices` 確認 serial（通常是 `emulator-5554` 和 `emulator-5556`），
填進設定檔的 `instances`。

### 版本相依

模板是在**遊戲 4.04.00** 上裁的。遊戲改版動到 UI 的話，對應的模板要重裁：

```powershell
python reroll.py shot                  # 截一張現在的畫面
python reroll.py test 陣容.png          # 看某個模板還對不對（分數低於 0.85 就要重裁）
```

重裁一定要從 `reroll.py shot` 截出來的圖上裁，不能自己另外截圖——
經過的縮放路徑不一樣，比對不會過。

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

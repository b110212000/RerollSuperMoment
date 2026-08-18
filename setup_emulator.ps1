<#
    模擬器設定：把 LDPlayer 準備成刷初始腳本能用的狀態。

    做的事（只改需要改的，重複執行安全）：
      1. 解析度 1600x900 / DPI 240 —— 模板全靠這個，錯了 43 個一起失效
      2. 語言 zh-TW —— 模板上的字是繁中，語言不對含文字的全部比不到
      3. CPU 3 核 / RAM 4096MB —— 兩台加起來留一半資源給你用電腦
      4. 沒有第二台就用 copy 複製（連遊戲和解析度一起帶過去）並打散裝置識別碼
      5. 開機、等 ADB 上線、逐項驗證

    不做的事（做不到，會在最後列出來）：
      - 安裝遊戲：要登入 Google Play，得你自己來
      - 首次啟動到「創立球隊」：要好幾分鐘，腳本只等 30 秒，第一次得手動

    用法：
      .\setup_emulator.ps1           互動，會先問過再動
      .\setup_emulator.ps1 -Yes      不問，直接做
      .\setup_emulator.ps1 -Single   只設定第一台，不複製第二台
      .\setup_emulator.ps1 -Force    有 run 在跑也照做（會打斷它）
#>
[CmdletBinding()]
param(
    [string]$LdDir  = 'C:\LDPlayer\LDPlayer14',
    [string]$Clone  = 'LDPlayer-2',
    [int]$Cpu       = 3,
    [int]$Memory    = 4096,
    [string]$Locale = 'zh-TW',
    [string]$Package  = 'com.com2us.futuremlb.android.google.global.normal',
    [string]$Activity = 'com.com2us.futuremlb.android.google.global.normal/com.com2us.futuremlb.MainActivity',
    [switch]$Single,
    [switch]$Yes,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$WantW = 1600
$WantH = 900
$WantDpi = 240

function Say  ($m) { Write-Host $m }
function Head ($m) { Write-Host ''; Write-Host "=== $m ===" -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Die  ($m) { Write-Host "  [X]  $m" -ForegroundColor Red; exit 1 }

$Ld  = Join-Path $LdDir 'ldconsole.exe'
$Adb = Join-Path $LdDir 'adb.exe'
if (-not (Test-Path $Ld))  { Die "找不到 $Ld ，用 -LdDir 指定 LDPlayer 安裝路徑" }
if (-not (Test-Path $Adb)) { Die "找不到 $Adb" }

function Get-Instances {
    # list2 每行: index,名稱,頂層視窗,綁定視窗,是否開機,PID,VBoxPID,寬,高,DPI
    $out = & $Ld list2
    $r = @()
    foreach ($line in $out) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $f = $line.Split(',')
        if ($f.Count -lt 10) { continue }
        $idx = [int]$f[0]
        $r += [pscustomobject]@{
            Index   = $idx
            Name    = $f[1]
            Running = ($f[4] -eq '1')
            Width   = [int]$f[7]
            Height  = [int]$f[8]
            Dpi     = [int]$f[9]
            Serial  = 'emulator-' + (5554 + 2 * $idx)
        }
    }
    return $r
}

function Confirm-Step ($msg) {
    if ($Yes) { return $true }
    Write-Host ''
    Write-Host $msg -ForegroundColor Yellow
    $a = Read-Host '  繼續？(y/N)'
    if ($a -eq 'y') { return $true }
    if ($a -eq 'Y') { return $true }
    return $false
}

Head '現況'
$inst = Get-Instances
if ($inst.Count -eq 0) { Die 'ldconsole list2 沒有回傳任何實例，LDPlayer 裝好了嗎？' }
foreach ($i in $inst) {
    $state = '已關機'
    if ($i.Running) { $state = '執行中' }
    Say ('  index ' + $i.Index + '  ' + $i.Name.PadRight(14) + ' ' + $i.Width + 'x' + $i.Height + ' dpi=' + $i.Dpi + '  ' + $state + '  ' + $i.Serial)
}

# 有腳本在跑就別亂關模擬器：半路關掉會讓帳號停在流程中間，
# 下次啟動得先自救才能繼續。
$busy = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and ($_.CommandLine -like '*reroll.py*' -or $_.CommandLine -like '*watchdog.py*') }
if ($null -ne $busy) {
    # 這裡刻意不受 -Yes 影響。-Yes 的意思是「例行確認不用問我」，
    # 不該連「有人正在跑，關掉會打斷他」這種安全檢查一起跳過。
    # 要無視得另外給 -Force，因為那真的會打斷一個進行中的 run。
    Warn 'reroll.py / watchdog.py 還在跑：'
    foreach ($b in $busy) { Say ('       pid ' + $b.ProcessId) }
    Say  ''
    Say  '  這個腳本會關掉模擬器。半路關掉會讓帳號停在流程中間，'
    Say  '  那一輪會作廢，下次啟動得先自救才能繼續。'
    Say  ''
    Say  '  先按 F12 停掉再執行；真的要打斷它就加 -Force。'
    if (-not $Force) { Die '沒有 -Force，不動它' }
    Warn '有 -Force，繼續（會打斷進行中的 run）'
}

Head '需要調整的項目'
$plan = @()
$first = $inst | Where-Object { $_.Index -eq 0 } | Select-Object -First 1
if ($null -eq $first) { Die '找不到 index 0 的實例' }

if ($first.Width -ne $WantW -or $first.Height -ne $WantH -or $first.Dpi -ne $WantDpi) {
    $plan += '第一台解析度 ' + $first.Width + 'x' + $first.Height + ' dpi=' + $first.Dpi + ' 改成 ' + $WantW + 'x' + $WantH + ' dpi=' + $WantDpi
} else {
    Ok ('第一台解析度已是 ' + $WantW + 'x' + $WantH + ' dpi=' + $WantDpi)
}

$cloneInst = $inst | Where-Object { $_.Name -eq $Clone } | Select-Object -First 1
if (-not $Single) {
    if ($null -eq $cloneInst) {
        $plan += '複製第二台「' + $Clone + '」（連遊戲和解析度一起帶過去）'
    } else {
        Ok ('第二台「' + $Clone + '」已存在（index ' + $cloneInst.Index + '）')
    }
}
$plan += '設成 CPU ' + $Cpu + ' 核 / RAM ' + $Memory + ' MB、語言 ' + $Locale

foreach ($p in $plan) { Say ('  - ' + $p) }
if (-not (Confirm-Step '接下來會關掉所有模擬器再套用設定。')) { Die '已取消' }

Head '關閉所有模擬器（copy 和改解析度都需要關機）'
& $Ld quitall | Out-Null
for ($i = 0; $i -lt 40; $i++) {
    if ((Get-Instances | Where-Object { $_.Running }).Count -eq 0) { break }
    Start-Sleep -Seconds 1
}
$still = (Get-Instances | Where-Object { $_.Running }).Count
if ($still -gt 0) { Die ('還有 ' + $still + ' 台沒關掉，手動關掉再重試') }
Ok '都關了'

if ($null -eq $cloneInst -and -not $Single) {
    Head ('複製第二台「' + $Clone + '」')
    Say  '  用 copy 而不是 add：copy 會把已安裝的遊戲和解析度一起帶過去，'
    Say  '  add 出來是空的，還得重裝遊戲、重設解析度、再手動登入一次。'
    & $Ld copy --name $Clone --from 0 | Out-Null
    $cloneInst = Get-Instances | Where-Object { $_.Name -eq $Clone } | Select-Object -First 1
    if ($null -eq $cloneInst) { Die 'copy 之後找不到新實例' }
    Ok ('已建立 index ' + $cloneInst.Index)
    Say '  打散裝置識別碼，避免遊戲把兩台當成同一台'
    & $Ld modify --index $cloneInst.Index --imei auto --androidid auto --mac auto --imsi auto --simserial auto | Out-Null
    Ok '已打散'
}

$targets = @(0)
if ($null -ne $cloneInst) { $targets += $cloneInst.Index }

Head '套用解析度 / CPU / RAM / 語言'
$res = $WantW.ToString() + ',' + $WantH.ToString() + ',' + $WantDpi.ToString()
foreach ($idx in $targets) {
    & $Ld modify --index $idx --resolution $res --cpu $Cpu --memory $Memory | Out-Null
    & $Ld setprop --index $idx --key persist.sys.locale --value $Locale | Out-Null
    Ok ('index ' + $idx + ' 已套用')
}

Head '啟動並等 ADB 上線'
foreach ($idx in $targets) {
    & $Ld launch --index $idx | Out-Null
    Say ('  已下 launch --index ' + $idx)
    Start-Sleep -Seconds 8
}
$want = $targets.Count
for ($i = 0; $i -lt 60; $i++) {
    if ((& $Adb devices | Select-String -Pattern 'device$').Count -ge $want) { break }
    Start-Sleep -Seconds 3
}
$n = (& $Adb devices | Select-String -Pattern 'device$').Count
if ($n -lt $want) {
    Warn ('只有 ' + $n + ' 台上線（預期 ' + $want + '），下面的驗證可能不完整')
} else {
    Ok ($n.ToString() + ' 台都上線了')
}

Head '逐項驗證'
$bad = 0
foreach ($idx in $targets) {
    $s = 'emulator-' + (5554 + 2 * $idx)
    Say ('  --- index ' + $idx + '  ' + $s + ' ---')

    $size = (& $Adb -s $s shell wm size) -join ''
    $dens = (& $Adb -s $s shell wm density) -join ''
    $loc  = ((& $Adb -s $s shell getprop persist.sys.locale) -join '').Trim()

    if ($size -match ($WantW.ToString() + 'x' + $WantH.ToString())) {
        Ok ('解析度 ' + $size.Trim())
    } else {
        Warn ('解析度不對：' + $size.Trim() + '（要 ' + $WantW + 'x' + $WantH + '）')
        $bad++
    }

    if ($dens -match $WantDpi.ToString()) {
        Ok ('DPI ' + $dens.Trim())
    } else {
        Warn ('DPI 不對：' + $dens.Trim() + '（要 ' + $WantDpi + '）')
        $bad++
    }

    if ($loc -eq $Locale) {
        Ok ('語言 ' + $loc)
    } else {
        Warn ('語言是 ' + $loc + '，要 ' + $Locale + '。模板上的字是繁中，語言不對含文字的模板全部比不到')
        $bad++
    }

    $pkgs = & $Adb -s $s shell pm list packages
    if ($pkgs -match 'futuremlb') {
        Ok '遊戲已安裝'
    } else {
        Warn '遊戲沒安裝，這台要自己從 Google Play 裝'
        $bad++
    }
}

Head '開啟遊戲'
foreach ($idx in $targets) {
    $s2 = 'emulator-' + (5554 + 2 * $idx)
    $pk = & $Adb -s $s2 shell pm list packages
    if ($pk -match 'futuremlb') {
        & $Adb -s $s2 shell am start -n $Activity | Out-Null
        Ok ($s2 + ' 已下 am start，遊戲正在載入')
    } else {
        Warn ($s2 + ' 沒裝遊戲，跳過')
    }
}
Say ''
Say '  遊戲載入要一點時間。載完之後確認每一台都停在「創立球隊」畫面：'
Say '    python reroll.py --instance A test 創立球隊.png'
Say '  分數 0.85 以上就是到位了。'

Head '結果'
if ($bad -eq 0) { Ok '模擬器這邊都設好了' } else { Warn ('有 ' + $bad + ' 項要處理，見上面') }

Say ''
Say '還需要你自己做的（腳本做不到）：'
Say ''
Say '  1. 遊戲沒裝的話，在模擬器裡開 Google Play 搜「MLB 9 局職棒 勁旅對決」安裝'
Say ''
Say '  2. 每一台都手動開一次遊戲，把首次啟動流程走完：'
Say '     同意使用條款 → 選「使用訪客登入」→ 一路到出現「創立球隊」畫面'
Say '     第一次一定要手動：這段要好幾分鐘，而腳本等「創立球隊」只等 30 秒。'
Say ''
Say '  3. 要收中獎通知的話設環境變數（Gmail 用應用程式密碼，不是登入密碼）：'
Say '     [Environment]::SetEnvironmentVariable("REROLL_SMTP_PASS","你的應用程式密碼","User")'
Say ''
Say '  兩台都停在「創立球隊」之後就可以開跑：'
Say '     python watchdog.py --instance A --instance B'
Say ''

# 這支不要直接執行，請雙擊「管理員模式.bat」
# 它會在提權後的視窗裡跑這段，把環境準備好

Set-Location 'D:\tryClaudeSteam'

# 把 Python 塞進這個視窗的 PATH，省得每次打完整路徑
$pyDir = "$env:LOCALAPPDATA\Programs\Python\Python312"
$env:Path = "$pyDir;$pyDir\Scripts;$env:Path"

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host ""
if ($isAdmin) {
    Write-Host "  ✓ 已取得管理員權限，現在點得動 MLB RIVALS 了" -ForegroundColor Green
} else {
    Write-Host "  ✗ 沒有管理員權限，點擊還是會被 Windows 丟掉" -ForegroundColor Red
    Write-Host "    請關掉這個視窗，改用雙擊「管理員模式.bat」" -ForegroundColor Red
}

Write-Host ""
Write-Host "  工作目錄：$(Get-Location)"
Write-Host "  Python  ：$((Get-Command python -ErrorAction SilentlyContinue).Source)"
Write-Host ""
Write-Host "  可以用的指令：" -ForegroundColor Cyan
Write-Host "    python reroll.py shot              截圖，拿去裁模板"
Write-Host "    python reroll.py test 陣容.png     測模板比對得到嗎"
Write-Host "    python reroll.py ocr               測 OCR 讀不讀得到"
Write-Host "    python reroll.py run --max 3       先跑三輪試水溫"
Write-Host "    python reroll.py run               正式跑"
Write-Host ""
Write-Host "  跑起來之後隨時按 F12 或 Ctrl+C 中斷。" -ForegroundColor Yellow
Write-Host ""

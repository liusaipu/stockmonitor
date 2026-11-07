[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ps1 = Join-Path $root 'watch_quotes.ps1'

# 1. 检查 UTF-8 BOM
$bytes = [System.IO.File]::ReadAllBytes($ps1)
$hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
if (-not $hasBom) {
    Write-Host "[FAIL] watch_quotes.ps1 缺少 UTF-8 BOM，运行时会按系统默认编码解析导致中文引号错误" -ForegroundColor Red
    exit 1
}
Write-Host "[PASS] watch_quotes.ps1 已包含 UTF-8 BOM" -ForegroundColor Green

# 2. 运行一次 watch.cmd，检查是否能正常退出且无解析错误
$cmd = Join-Path $root 'watch.cmd'
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = 'cmd.exe'
$psi.Arguments = "/c `"$cmd`" --once"
$psi.WorkingDirectory = $root
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.EnvironmentVariables['WATCH_NO_PAUSE'] = '1'
$proc = [System.Diagnostics.Process]::Start($psi)
$stdout = $proc.StandardOutput.ReadToEnd()
$stderr = $proc.StandardError.ReadToEnd()
$proc.WaitForExit()

$combined = $stdout + $stderr
if ($proc.ExitCode -ne 0) {
    Write-Host "[FAIL] watch.cmd --once 退出码非零: $($proc.ExitCode)" -ForegroundColor Red
    Write-Host $combined
    exit 1
}
$parserErrors = @('ParserError', 'UnexpectedToken', '缺少结束符', '不允许使用与号')
$found = $parserErrors | Where-Object { $combined -like "*$_*" }
if ($found) {
    Write-Host "[FAIL] watch.cmd --once 输出中检测到解析错误: $found" -ForegroundColor Red
    Write-Host $combined
    exit 1
}
Write-Host "[PASS] watch.cmd --once 运行正常，无解析错误" -ForegroundColor Green

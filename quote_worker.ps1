param(
    [string[]]$Secids,
    [string]$Fields,
    [int]$Timeout = 5
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Secids = @($Secids | ForEach-Object { $_ -split ',' } | Where-Object { $_ })

function Convert-Secid($Secid) {
    $parts = $Secid -split '\.', 2
    $market = if ($parts.Count -eq 2) { $parts[0] } else { '0' }
    $code = if ($parts.Count -eq 2) { $parts[1] } else { $Secid }
    if ($market -eq '1') {
        $prefix = if ($code -match '^000(001|688)$') { 's_sh' } else { 'sh' }
    } elseif ($market -eq '0') {
        if ($code -match '^899\d{3}$') { $prefix = 's_bj' }
        elseif ($code -match '^(399|930)\d{3}$') { $prefix = 's_sz' }
        elseif ($code -match '^(4|8|92)\d{4,5}$') { $prefix = 'bj' }
        else { $prefix = 'sz' }
    } else {
        $prefix = 'bj'
    }
    $prefix + $code
}

function Parse-Sina([string]$Text) {
    $rows = @()
    foreach ($line in ($Text -split "`r?`n")) {
        if ($line -notmatch 'var hq_str_([a-z_]+(\d+))="([^"]*)";') { continue }
        $sinaCode = $matches[1]
        if ([string]::IsNullOrWhiteSpace($matches[3])) { continue }
        $parts = $matches[3] -split ','
        if ($parts.Count -lt 6) { continue }
        $row = [ordered]@{
            f12 = $matches[2]
            f13 = if ($sinaCode -match 'sh') { '1' } else { '0' }
            f14 = $parts[0]
            f8 = '-'
            f10 = '-'
        }
        if ($sinaCode.StartsWith('s_')) {
            $row.f2 = $parts[1]
            $row.f4 = $parts[2]
            $row.f3 = $parts[3]
            $row.f5 = $parts[4]
            $row.f6 = $parts[5]
            $row.f18 = [double]$parts[1] - [double]$parts[2]
        } else {
            $row.f2 = $parts[3]
            $row.f18 = $parts[2]
            $row.f17 = $parts[1]
            $row.f15 = $parts[4]
            $row.f16 = $parts[5]
            $row.f5 = if ($parts[8] -match '^\d+$') { [math]::Floor([double]$parts[8] / 100) } else { '-' }
            $row.f6 = $parts[9]
            $row.f4 = [double]$parts[3] - [double]$parts[2]
            $row.f3 = if ([double]$parts[2] -ne 0) { ($row.f4 / [double]$parts[2]) * 100 } else { 0 }
            $row.f86 = [DateTimeOffset]::new([datetime]::ParseExact("$($parts[30]) $($parts[31])", 'yyyy-MM-dd HH:mm:ss', $null)).ToUnixTimeSeconds()
        }
        $rows += [PSCustomObject]$row
    }
    ,$rows
}

$rows = @()
$errorText = $null
if ($Secids.Count -gt 0) {
    $url = 'https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&fields=' + $Fields + '&secids=' + ($Secids -join ',')
    for ($attempt = 0; $attempt -lt 2 -and $rows.Count -eq 0; $attempt++) {
        try {
            $data = Invoke-RestMethod -Uri $url -TimeoutSec $Timeout -UseBasicParsing -DisableKeepAlive -Headers @{ 'User-Agent' = 'Mozilla/5.0'; 'Referer' = 'https://quote.eastmoney.com/' }
            if ($data.data.diff) { $rows = @($data.data.diff) }
        } catch {
            $errorText = $_.Exception.Message
        }
    }
    if ($rows.Count -eq 0) {
        try {
            $codes = @($Secids | ForEach-Object { Convert-Secid $_ })
            $response = Invoke-WebRequest -Uri ('https://hq.sinajs.cn/list=' + ($codes -join ',')) -TimeoutSec 15 -UseBasicParsing -DisableKeepAlive -Headers @{ 'Referer' = 'https://finance.sina.com.cn/'; 'User-Agent' = 'Mozilla/5.0' }
            $text = [Text.Encoding]::GetEncoding('gbk').GetString($response.RawContentStream.ToArray())
            $rows = @(Parse-Sina $text)
        } catch {
            $errorText = $_.Exception.Message
        }
    }
}

if ($rows.Count -eq 0 -and -not $errorText) { $errorText = 'empty quote response' }
[PSCustomObject]@{
    Rows = $rows
    Error = $errorText
    CompletedAt = [DateTime]::Now
}

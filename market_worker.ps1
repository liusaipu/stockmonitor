param([string[]]$Secids)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Secids = @($Secids | ForEach-Object { $_ -split ',' } | Where-Object { $_ })
$result = @{}
$today = Get-Date
$beg = $today.AddDays(-25).ToString('yyyyMMdd')
$end = $today.AddDays(1).ToString('yyyyMMdd')
foreach ($secid in $Secids) {
    try {
        $url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=$secid&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt=101&fqt=0&beg=$beg&end=$end"
        $data = Invoke-RestMethod -Uri $url -TimeoutSec 15 -UseBasicParsing -DisableKeepAlive -Headers @{ 'User-Agent' = 'Mozilla/5.0'; 'Referer' = 'https://quote.eastmoney.com/' }
        $items = @()
        foreach ($line in @($data.data.klines)) {
            $parts = $line -split ','
            if ($parts.Count -ge 7) { $items += ,@($parts[0], [double]$parts[6]) }
        }
        $result[$secid] = $items
    } catch {
        $result[$secid] = @()
    }
}
$result

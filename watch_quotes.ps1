[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$OutputEncoding = [System.Text.Encoding]::UTF8

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol

# 提升并发连接数、关闭 Expect100Continue，减少连接被服务端意外关闭的概率

[Net.ServicePointManager]::DefaultConnectionLimit = 100

[Net.ServicePointManager]::Expect100Continue = $false

$ErrorActionPreference = "Stop"

# 关掉 Invoke-WebRequest / Invoke-RestMethod 的进度条。
# 该进度条由 PowerShell 宿主直接画在控制台上（反色蓝条），不经过本脚本的写屏函数；
# 每次取数都会画一次再擦一次，于是出现“蓝底一闪”，还可能把表格顶动。
# 实测（.frame_log.txt）：两次闪烁时刻脚本一次屏幕都没写，时间点正好落在分块取数上。
$ProgressPreference = 'SilentlyContinue'

# 字段名常量（东财 push2 / 新浪通用）

$F_PRICE      = 'f2'   # 最新价

$F_PCT_CHG    = 'f3'   # 涨跌幅(%)

$F_CHG        = 'f4'   # 涨跌额

$F_VOL        = 'f5'   # 成交量(手)

$F_AMT        = 'f6'   # 成交额(元)

$F_TURNOVER   = 'f8'   # 换手率(%)

$F_VOL_RATIO  = 'f10'  # 量比

$F_CODE       = 'f12'  # 代码

$F_MARKET     = 'f13'  # 市场 (0=深, 1=沪)

$F_NAME       = 'f14'  # 名称

$F_HIGH       = 'f15'  # 最高价

$F_LOW        = 'f16'  # 最低价

$F_OPEN       = 'f17'  # 今开

$F_PREV_CLOSE = 'f18'  # 昨收

# ---------- 字段集合（按模式精简请求字段）----------

$FULL_FIELDS    = 'f12,f13,f14,f2,f3,f4,f5,f6,f8,f10,f15,f16,f17,f18,f20,f21,f30'

$STEALTH_FIELDS = 'f2,f3,f4,f6,f8,f10,f12,f13,f14,f20,f21,f30'  # f6 留给指数计算三市成交额

# ---------- 表格宽度常量 ----------

$STEALTH_TABLE_WIDTH = 64

$NORMAL_TABLE_WIDTH = 114

# ---------- 基础工具 ----------

function Fetch-Json($url, $timeout = 15, $retries = 1) {

    $headers = @{

        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

        'Referer' = 'https://quote.eastmoney.com/'

    }

    $lastErr = $null

    for ($i = 0; $i -lt $retries; $i++) {

        try {

            return Invoke-RestMethod -Uri $url -TimeoutSec $timeout -UseBasicParsing -Headers $headers -DisableKeepAlive

        } catch {

            $lastErr = $_

            if ($i -lt $retries - 1) {

                Start-Sleep -Milliseconds (200 * ($i + 1))

            }

        }

    }

    throw $lastErr

}

function Convert-SecidToSinaCode($secid) {

    $market = '0'; $code = $secid

    if ($secid -match '^(\d+)\.(.+)$') {

        $market = $matches[1]

        $code = $matches[2]

    }

    $prefix = ''
    if ($market -eq '1') {
        if ($code -match '^000(001|688)$') { $prefix = 's_sh' } else { $prefix = 'sh' }
    } elseif ($market -eq '0') {
        if ($code -match '^899\d{3}$') { $prefix = 's_bj' }
        elseif ($code -match '^(399|930)\d{3}$') { $prefix = 's_sz' }
        elseif ($code -match '^(4|8|92)\d{4,5}$') { $prefix = 'bj' }
        else { $prefix = 'sz' }
    } else {
        $prefix = 'bj'
    }

    return "$prefix$code"

}

function Parse-SinaQuoteResponse($text) {

    $rows = @()

    foreach ($line in ($text -split "`r?`n")) {

        if ($line -notmatch 'var hq_str_([a-z_]+(\d+))="([^"]*)";') { continue }

        $sinaCode = $matches[1]

        $content = $matches[3]

        if ([string]::IsNullOrWhiteSpace($content)) { continue }

        $parts = $content -split ','

        $code = $matches[2]

        $marketNum = switch -Regex ($sinaCode) {

            '^s?_?sh' { '1' }

            '^s?_?sz' { '0' }

            '^s?_?bj' { '0' }

            default { '1' }

        }

        $row = [ordered]@{

            f12 = $code

            f13 = $marketNum

            f14 = $parts[0]

            f8 = '-'

            f10 = '-'

        }

        if ($sinaCode -match '^s_') {

            if ($parts.Count -lt 4) { continue }

            $row.f2 = $parts[1]

            $row.f4 = $parts[2]

            $row.f3 = $parts[3]

            $row.f5 = if ($parts.Count -gt 4) { $parts[4] } else { '-' }

            $row.f6 = if ($parts.Count -gt 5) { $parts[5] } else { '-' }

            $row.f15 = '-'

            $row.f16 = '-'

            $row.f17 = '-'

            $row.f18 = '-'

            try {

                $p = [double]$parts[1]

                $c = [double]$parts[2]

                $row.f18 = [math]::Round($p - $c, 4)

            } catch {}

        } else {

            if ($parts.Count -lt 6) { continue }

            $row.f2 = $parts[3]

            $row.f18 = $parts[2]

            $row.f17 = $parts[1]

            $row.f15 = $parts[4]

            $row.f16 = $parts[5]

            $row.f5 = if ($parts.Count -gt 8 -and $parts[8] -match '^\d+$') { [math]::Floor([double]$parts[8] / 100) } else { '-' }

            $row.f6 = if ($parts.Count -gt 9) { $parts[9] } else { '-' }

            try {

                $p = [double]$parts[3]

                $pc = [double]$parts[2]

                $row.f4 = [math]::Round($p - $pc, 3)

                $row.f3 = if ($pc -ne 0) { [math]::Round(($p - $pc) / $pc * 100, 2) } else { 0 }

            } catch {

                $row.f4 = '-'

                $row.f3 = '-'

            }

        }

        $rows += [PSCustomObject]$row

    }

    return $rows

}

function Fetch-Quotes-Sina($secids) {

    if ($secids.Count -eq 0) { return @() }

    $sinaCodes = $secids | ForEach-Object { Convert-SecidToSinaCode $_ }

    $list = $sinaCodes -join ','

    $url = "https://hq.sinajs.cn/list=$list"

    $headers = @{ Referer = 'https://finance.sina.com.cn/' }

    $lastErr = $null

    for ($retry = 0; $retry -lt 2; $retry++) {

        try {

            Write-FrameLog "fetch sina quotes n=$($secids.Count)"
            $resp = Invoke-WebRequest -Uri $url -Headers $headers -UseBasicParsing -TimeoutSec 15 -DisableKeepAlive

            $bytes = $resp.RawContentStream.ToArray()

            # 使用 gbk 解码（gb2312 的超集，更安全兼容）

            $text = [System.Text.Encoding]::GetEncoding('gbk').GetString($bytes)

            return Parse-SinaQuoteResponse $text

        } catch {

            $lastErr = $_

            if ($retry -lt 1) {

                Start-Sleep -Milliseconds (300 * ($retry + 1))

            }

        }

    }

    throw $lastErr

}

function Format-Number($v, $digits = 2, $divisor = 1) {

    # 注意：PowerShell 中 $null -eq '' 会因类型强制而为 true，所以先做字符串判断

    if ($v -eq $null) { return '-' }

    $sv = "$v"

    if ($sv -eq '' -or $sv -eq '-') { return '-' }

    try {

        $f = [double]$v / $divisor

        if ($digits -eq 0) {

            return "{0:N0}" -f $f

        }

        return "{0:N$digits}" -f $f

    } catch {

        return "$v"

    }

}

function Format-Yi($v) {

    try { return "{0:F2}" -f ([double]$v / 1e8) } catch { return '-' }

}


function Format-Cap($v) {

    try {

        $f = [double]$v

        if ($f -ge 1e12) {

            return ("{0:F3}" -f ($f / 1e12)), '万亿'

        }

        return ("{0:F2}" -f ($f / 1e8)), '亿'

    } catch {

        return '-', '亿'

    }

}


function Format-LatestVol($v) {

    try { return "{0:N0}" -f ([math]::Abs([double]$v)) } catch { return '-' }

}


function Format-AvgPrice($row, $amt = $null, $vol = $null) {

    try {

        if ($amt -eq $null -or "$amt" -eq '' -or "$amt" -eq '-') { $amt = $row.f6 }

        if ($vol -eq $null -or "$vol" -eq '' -or "$vol" -eq '-') { $vol = $row.f5 }

        $amt = [double]$amt

        $vol = [double]$vol

        if ($vol -le 0) { return '-' }

        $digits = if (Test-IsETF $row) { 3 } else { 2 }

        return "{0:N$digits}" -f ($amt / ($vol * 100))

    } catch { return '-' }

}


function Format-PricePctPair($price, $pct, $totalWidth = 16, $minGap = 3) {

    $priceStr = "$price"

    $pctStr = "$pct"

    $available = $totalWidth - (Measure-DisplayWidth $priceStr) - (Measure-DisplayWidth $pctStr)

    $gap = if ($available -ge $minGap) { $minGap } else { [Math]::Max(1, $available) }

    return $priceStr + (' ' * $gap) + $pctStr

}


function Test-IsETF($row) {

    return "$($row.f14)".ToUpper().Contains('ETF')

}

function Measure-DisplayWidth($s) {

    $str = "$s"

    $w = 0

    foreach ($c in $str.ToCharArray()) {

        if ([int]$c -gt 127) { $w += 2 } else { $w += 1 }

    }

    return $w

}

function Pad-Right($s, $width) {

    $s = Truncate-ToWidth $s $width

    $pad = $width - (Measure-DisplayWidth $s)

    if ($pad -lt 0) { $pad = 0 }

    return $s + (' ' * $pad)

}

function Pad-Left($s, $width) {

    $s = Truncate-ToWidth $s $width

    $pad = $width - (Measure-DisplayWidth $s)

    if ($pad -lt 0) { $pad = 0 }

    return (' ' * $pad) + $s

}

function Truncate-Name($s, $maxWidth = 8) {

    $str = "$s"

    $w = 0

    $result = ""

    foreach ($c in $str.ToCharArray()) {

        $cw = if ([int]$c -gt 127) { 2 } else { 1 }

        if ($w + $cw -le $maxWidth) {

            $w += $cw

            $result += $c

        } else {

            break

        }

    }

    return $result

}

function Truncate-ToWidth($s, $maxWidth) {

    $str = "$s"

    $w = 0

    $result = ""

    foreach ($c in $str.ToCharArray()) {

        $cw = if ([int]$c -gt 127) { 2 } else { 1 }

        if ($w + $cw -le $maxWidth) {

            $w += $cw

            $result += $c

        } else {

            break

        }

    }

    return $result

}

# ---------- 缓存：name -> secid ----------

$script:secidCachePath = Join-Path $PSScriptRoot '.secids_cache.json'

$script:secidCache = @{}

function Write-JsonFile($path, $data) {
    # 统一缓存格式：UTF-8 无 BOM、临时文件原子替换，供 macOS/Linux Python 版共用。
    try {
        $json = $data | ConvertTo-Json -Depth 8
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        $tmp = "$path.tmp"
        [System.IO.File]::WriteAllText($tmp, $json, $utf8)
        if (Test-Path -LiteralPath $path) {
            [System.IO.File]::Replace($tmp, $path, $null)
        } else {
            [System.IO.File]::Move($tmp, $path)
        }
    } catch {
        # Replace 在个别文件系统上可能失败，退回到 Move-Item
        try { Move-Item -LiteralPath $tmp -Destination $path -Force } catch {}
    }
}

function Load-SecidCache {

    if (Test-Path $script:secidCachePath) {

        try {

            $json = Get-Content $script:secidCachePath -Encoding UTF8 | ConvertFrom-Json

            foreach ($p in $json.PSObject.Properties) {

                $script:secidCache[$p.Name] = $p.Value

            }

        } catch {

            # 缓存损坏时忽略，重新搜索

        }

    }

}

function Save-SecidCache {

    try {

        Write-JsonFile $script:secidCachePath $script:secidCache

    } catch {

        # 保存失败不影响主流程

    }

}

# ---------- 缓存：secid -> half-year pct ----------

$script:hyCachePath = Join-Path $PSScriptRoot '.hy_pct_cache.json'

$script:hyCache = @{}              # secid -> @{ pct; date }

$script:hyUpdateJob = $null        # background update job

$script:hyStartInterval = 0        # 半年涨跌开始刷新的间隔（秒）

$script:nextHyStartTime = $null    # 下次开始半年涨跌刷新的时间

function Load-HyCache {

    if (Test-Path $script:hyCachePath) {

        try {

            $json = Get-Content $script:hyCachePath -Encoding UTF8 | ConvertFrom-Json

            foreach ($p in $json.PSObject.Properties) {

                $script:hyCache[$p.Name] = $p.Value

            }

        } catch {

            # 缓存损坏时忽略，重新获取

        }

    }

}

function Save-HyCache {

    try {

        Write-JsonFile $script:hyCachePath $script:hyCache

    } catch {

        # 保存失败不影响主流程

    }

}

# ---------- 缓存：三市成交额 ----------

$script:marketTotalCachePath = Join-Path $PSScriptRoot '.market_total_cache.json'

$script:marketTotalCache = @{}    # secid -> date -> amount

function Load-MarketTotalCache {

    if (Test-Path $script:marketTotalCachePath) {

        try {

            $json = Get-Content $script:marketTotalCachePath -Encoding UTF8 | ConvertFrom-Json

            $script:marketTotalCache = @{}

            foreach ($p in $json.PSObject.Properties) {

                $script:marketTotalCache[$p.Name] = @{}

                foreach ($dp in $p.Value.PSObject.Properties) {

                    $script:marketTotalCache[$p.Name][$dp.Name] = [double]$dp.Value

                }

            }

        } catch {

            # 缓存损坏时忽略，重新获取

            $script:marketTotalCache = @{}

        }

    }

}

function Save-MarketTotalCache {

    try {

        Write-JsonFile $script:marketTotalCachePath $script:marketTotalCache

    } catch {

        # 保存失败不影响主流程

    }

}

# ---------- 缓存：日均线 ----------

$script:maCachePath = Join-Path $PSScriptRoot '.ma_cache.json'

$script:maCache = @{}   # secid -> @{ date; ma10; ma20; ma30; ma250 }

function Load-MaCache {

    if (Test-Path $script:maCachePath) {

        try {

            $json = Get-Content $script:maCachePath -Encoding UTF8 | ConvertFrom-Json

            foreach ($p in $json.PSObject.Properties) {

                $script:maCache[$p.Name] = $p.Value

            }

        } catch {

            # 缓存损坏时忽略，重新计算

        }

    }

}

function Save-MaCache {

    try {

        Write-JsonFile $script:maCachePath $script:maCache

    } catch {

        # 保存失败不影响主流程

    }

}

$script:depthCachePath = Join-Path $PSScriptRoot '.depth_cache.json'

$script:depthCache = @{}

$script:depthUpdateJob = $null

$script:depthJobSecid = $null

$script:depthPending = $null

function Load-DepthCache {

    if (Test-Path $script:depthCachePath) {

        try {

            $json = Get-Content $script:depthCachePath -Encoding UTF8 | ConvertFrom-Json

            foreach ($p in $json.PSObject.Properties) {

                $script:depthCache[$p.Name] = $p.Value

            }

        } catch {

            # 缓存损坏时忽略，重新获取

        }

    }

    Clear-StaleDepthCache

}

function Save-DepthCache {

    try {

        Write-JsonFile $script:depthCachePath $script:depthCache

    } catch {

        # 保存失败不影响主流程

    }

}

# 只保留当天缓存，避免磁盘无限增长

function Clear-StaleDepthCache {

    $today = (Get-Date).ToString('yyyy-MM-dd')

    $stale = @($script:depthCache.Keys | Where-Object { $script:depthCache[$_].date -ne $today })

    foreach ($k in $stale) { $script:depthCache.Remove($k) }

}

function Fetch-DailyKlines($secid, $limit = 300) {

    # 获取日线收盘价序列（f53=收盘），用于计算日均线

    try {

        $url = (

            "https://push2his.eastmoney.com/api/qt/stock/kline/get?" +

            "secid=$secid&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&" +

            "fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&" +

            "klt=101&fqt=0&end=20500101&lmt=$limit"

        )

        $data = Fetch-Json $url 15

        $klines = $data.data.klines

        if ($klines -eq $null -or $klines.Count -eq 0) { return @() }

        $closes = @()

        foreach ($k in $klines) {

            $parts = $k.Split(',')

            if ($parts.Count -ge 3) {

                $closes += [double]$parts[2]

            }

        }

        return $closes

    } catch {

        return @()

    }

}

function Get-DailyMAs($secid) {

    # 只读当天缓存，不触发网络；缺失时启动后台补齐并返回 $null（对应区块暂不显示）

    $today = (Get-Date).ToString('yyyy-MM-dd')

    $cached = $script:maCache[$secid]

    if ($cached -ne $null -and $cached.date -eq $today) {

        if ($cached.ma10 -ne $null -or $cached.ma20 -ne $null -or $cached.ma30 -ne $null -or
            $cached.ma120 -ne $null -or $cached.ma250 -ne $null) {

            return $cached

        }

        return $null

    }

    Start-MaUpdate @($secid)

    return $null

}

$script:maUpdateJob = $null

$script:maUpdateJobScript = {

    param($secids)

    $secids = @($secids | ForEach-Object { $_ -split ',' } | Where-Object { $_ })

    # 后台串行计算日均线，网络与计算都在子进程，不阻塞界面

    $headers = @{

        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

        'Referer'    = 'https://quote.eastmoney.com/'

    }

    $result = @{}

    foreach ($secid in $secids) {

        try {

            $url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?" +

                "secid=$secid&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&" +

                "fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&" +

                "klt=101&fqt=0&end=20500101&lmt=300")


            $resp = Invoke-RestMethod -Uri $url -TimeoutSec 15 -UseBasicParsing -Headers $headers

            $klines = $resp.data.klines

            if ($klines -eq $null -or $klines.Count -lt 10) { continue }

            $closes = @()

            foreach ($k in $klines) {

                $parts = $k.Split(',')

                if ($parts.Count -ge 3) { $closes += [double]$parts[2] }

            }

            if ($closes.Count -lt 10) { continue }

            $entry = @{ date = (Get-Date).ToString('yyyy-MM-dd') }

            foreach ($period in @(10, 20, 30, 120, 250)) {

                if ($closes.Count -ge $period) {

                    $sum = 0.0

                    for ($i = $closes.Count - $period; $i -lt $closes.Count; $i++) { $sum += $closes[$i] }

                    $entry["ma$period"] = [math]::Round($sum / $period, 2)

                }

            }

            $result[$secid] = $entry

        } catch {}

    }

    return $result

}

function Start-MaUpdate($secids) {

    if ($script:maUpdateJob -ne $null -and $script:maUpdateJob.State -eq 'Running') { return }

    $today = (Get-Date).ToString('yyyy-MM-dd')

    $toUpdate = @()

    foreach ($secid in $secids) {

        $cached = $script:maCache[$secid]

        if ($cached -eq $null -or $cached.date -ne $today) { $toUpdate += $secid }

    }

    if ($toUpdate.Count -eq 0) { return }

    $script:maUpdateJob = Start-Job -ScriptBlock $script:maUpdateJobScript -ArgumentList (,$toUpdate)

}

function Complete-MaUpdate {

    if ($script:maUpdateJob -eq $null) { return $false }

    if ($script:maUpdateJob.State -in @('Completed', 'Failed')) {

        $updated = $false

        if ($script:maUpdateJob.State -eq 'Completed') {

            $result = Receive-Job $script:maUpdateJob

            if ($result -ne $null) {

                foreach ($secid in $result.Keys) {

                    $script:maCache[$secid] = $result[$secid]

                }

                Save-MaCache

                $updated = $true

            }

        }

        Remove-Job $script:maUpdateJob -Force -ErrorAction SilentlyContinue

        $script:maUpdateJob = $null

        return $updated

    }

    return $false

}

# ---------- 展示层缓存（按 secid 存储，避免刷新间隙空白） ----------

$script:stockQuoteCache = @{}   # secid -> row

$script:indexQuoteCache = @{}   # secid -> row

function Is-MissingValue($v) {

    return $v -eq $null -or "$v" -eq '' -or "$v" -eq '-'

}

function Merge-Row($oldRow, $newRow) {

    if ($oldRow -eq $null) { return $newRow }

    $merged = $oldRow | Select-Object *

    foreach ($prop in $newRow.PSObject.Properties) {

        if (-not (Is-MissingValue $prop.Value)) {

            $merged | Add-Member -MemberType NoteProperty -Name $prop.Name -Value $prop.Value -Force

        }

    }

    return $merged

}

function Update-Caches($rows) {

    $indexSet = @{}

    foreach ($s in $script:indexSecids) { $indexSet[$s] = $true }

    foreach ($r in $rows) {

        $secid = "$($r.f13).$($r.f12)"

        $script:quoteSuccessTimes[$secid] = Get-Date

        if ($indexSet.ContainsKey($secid)) {

            $script:indexQuoteCache[$secid] = Merge-Row $script:indexQuoteCache[$secid] $r

        } else {

            $script:stockQuoteCache[$secid] = Merge-Row $script:stockQuoteCache[$secid] $r

        }

    }

}

function Get-CachedRows($cache, $secids) {

    # 按 watchlist.json 业务 secid 顺序返回，避免 Hashtable 打乱顺序

    $rows = @()

    foreach ($secid in $secids) {

        if ($cache.ContainsKey($secid)) {

            $rows += $cache[$secid]

        }

    }

    return $rows

}

function Test-QuoteStale($row) {

    $secid = "$($row.f13).$($row.f12)"

    $success = $script:quoteSuccessTimes[$secid]

    if ($success -eq $null) { return $true }

    $limit = [math]::Max(90, $interval * 3)

    return ((Get-Date) - [DateTime]$success).TotalSeconds -gt $limit

}

function Apply-HalfYearPct($rows) {

    foreach ($r in $rows) {

        $secid = "$($r.f13).$($r.f12)"

        $cached = $script:hyCache[$secid]

        if ($cached -ne $null) {

            $r | Add-Member -MemberType NoteProperty -Name 'hyPct' -Value $cached.pct -Force

        } else {

            $r | Add-Member -MemberType NoteProperty -Name 'hyPct' -Value $null -Force

        }

    }

}

# ---------- 网络接口 ----------

function Search-SecIds($names) {

    $resolved = @{}

    $missing = @()

    foreach ($name in $names) {

        if ($script:secidCache.ContainsKey($name)) {

            $resolved[$name] = $script:secidCache[$name]

        } else {

            $missing += $name

        }

    }

    foreach ($name in $missing) {

        try {

            $encoded = [Uri]::EscapeDataString($name)

            $data = Fetch-Json "https://searchapi.eastmoney.com/api/suggest/get?input=$encoded&type=14&count=5" 10

            $item = @($data.QuotationCodeTable.Data | Where-Object { $_.Name -eq $name } | Select-Object -First 1)

            if ($item.Count -gt 0) {

                $resolved[$name] = $item[0].QuoteID

                $script:secidCache[$name] = $item[0].QuoteID

            } else {

                Write-FrameLog "security not matched exactly: $name"

            }

        } catch {

            Write-FrameLog "security search failed: $name : $_"

        }

    }

    if ($missing.Count -gt 0) { Save-SecidCache }

    $secids = @()

    foreach ($name in $names) {

        if ($resolved.ContainsKey($name)) { $secids += $resolved[$name] }

    }

    return $secids

}

function Fetch-AllQuotes($secids, $stealth, $quiet = $false, $timeout = 15) {

    if ($secids.Count -eq 0) { return @() }

    # 1) 主数据源：东方财富

    try {

        $fields = if ($stealth) { $STEALTH_FIELDS } else { $FULL_FIELDS }

        $secidStr = $secids -join ','

        $url = "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&fields=$fields&secids=$secidStr"

        Write-FrameLog "fetch eastmoney quotes n=$($secids.Count)"

        $data = Fetch-Json $url $timeout 2

        if ($data.data.diff.Count -gt 0) {

            return $data.data.diff

        }

    } catch {

        if (-not $quiet) {

            Write-Host "[warn] EastMoney quote fetch failed: $_" -ForegroundColor DarkGray

        }

    }

    # 2) 兜底数据源：新浪财经

    try {

        $rows = Fetch-Quotes-Sina $secids

        if ($rows.Count -gt 0) {

            if (-not $quiet) {

                Write-Host "[info] switched to Sina fallback ($($rows.Count)/$($secids.Count) symbols)" -ForegroundColor DarkGray

            }

            return $rows

        }

    } catch {

        if (-not $quiet) {

            Write-Host "[warn] Sina quote fetch failed: $_" -ForegroundColor DarkGray

        }

    }

    throw "所有行情数据源均不可用（东财、新浪）"

}

function Parse-SinaDepthResponse($text, $sinaCode, $name = $null) {

    # 解析新浪盘口返回内容

    # 新浪盘口字段顺序（以 sh688981 为例）：

    # 名称,今开,昨收,最新,最高,最低,竞买价,竞卖价,成交量(股),成交额,

    # 买一到买五的价/量,卖一到卖五的价/量,日期,时间,状态

    if ($text -notmatch "var hq_str_$sinaCode=`"([^`"]*)`";") { return $null }

    $parts = $matches[1] -split ','

    if ($parts.Count -lt 33) { return $null }

    function _num($idx) {

        $v = $parts[$idx]

        if ($v -eq $null -or $v -eq '' -or $v -eq '-') { return $null }

        try { return [double]$v } catch { return $null }

    }

    function _vol($idx) {

        # 新浪返回的委托量是股数，转成手数（1手=100股）

        $v = _num $idx

        if ($v -eq $null) { return $null }

        return [int]($v / 100)

    }

    $price = _num 3

    $prev  = _num 2

    $change = if ($price -ne $null -and $prev -ne $null) { [math]::Round($price - $prev, 4) } else { $null }

    $pct = if ($change -ne $null -and $prev -ne $null -and $prev -ne 0) { [math]::Round($change / $prev * 100, 2) } else { $null }

    $volShares = _num 8

    $volume = if ($volShares -ne $null) { [math]::Floor($volShares / 100) } else { $null }

    return [PSCustomObject]@{

        name       = if ([string]::IsNullOrWhiteSpace($name)) { $parts[0] } else { $name }

        code       = ($sinaCode -replace '^[a-z_]+', '')

        price      = $price

        prev_close = $prev

        open       = _num 1

        high       = _num 4

        low        = _num 5

        volume     = $volume

        amount     = _num 9

        turnover   = $null

        change     = $change

        pct_chg    = $pct

        asks       = @(

            , @((_num 21), (_vol 20))   # 卖1

            , @((_num 23), (_vol 22))   # 卖2

            , @((_num 25), (_vol 24))   # 卖3

            , @((_num 27), (_vol 26))   # 卖4

            , @((_num 29), (_vol 28))   # 卖5

        )

        bids       = @(

            , @((_num 11), (_vol 10))   # 买1

            , @((_num 13), (_vol 12))   # 买2

            , @((_num 15), (_vol 14))   # 买3

            , @((_num 17), (_vol 16))   # 买4

            , @((_num 19), (_vol 18))   # 买5

        )

    }

}

function Fetch-DepthData($secid, $name = $null) {

    # 新浪提供完整五档 + 基础行情；东财 details 提供逐笔成交

    $sinaCode = Convert-SecidToSinaCode $secid

    $sinaUrl = "https://hq.sinajs.cn/list=$sinaCode"

    $sinaHeaders = @{

        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

        'Referer'    = 'https://finance.sina.com.cn'

    }

    try {

        $sinaText = Invoke-RestMethod -Uri $sinaUrl -TimeoutSec 10 -UseBasicParsing -Headers $sinaHeaders -DisableKeepAlive

    } catch {

        # 新浪五档失败会导致盘口无法显示，这里保留错误提示但限制刷屏

        Write-Host "[warn] sina depth fetch failed: 盘口数据源暂不可用" -ForegroundColor DarkGray

        return $null

    }

    $d = Parse-SinaDepthResponse $sinaText $sinaCode $name

    if ($d -eq $null) {

        Write-Host "[warn] sina depth parse failed" -ForegroundColor DarkGray

        return $null

    }

    # 东财逐笔成交（details/get 在 PS 5.1 下可正常访问）

    try {

        $dfUrl = "https://push2.eastmoney.com/api/qt/stock/details/get?secid=$secid&fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54"

        $dfHeaders = @{

            'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

            'Referer'    = 'https://quote.eastmoney.com/'

            'Accept'     = 'application/json, text/javascript, */*; q=0.01'

            'Accept-Language' = 'zh-CN,zh;q=0.9,en;q=0.8'

        }

        $df = $null
        $dfErr = $null
        for ($retry = 0; $retry -lt 3; $retry++) {
            try {
                $df = Invoke-RestMethod -Uri $dfUrl -TimeoutSec 10 -UseBasicParsing -Headers $dfHeaders -DisableKeepAlive
                break
            } catch {
                $dfErr = $_
                # 指数退避：300ms / 600ms
                if ($retry -lt 2) {
                    Start-Sleep -Milliseconds (300 * ($retry + 1))
                }
            }
        }

        if ($df -and $df.data -and $df.data.details -and $df.data.details.Count -gt 0) {

            $last = $df.data.details[-1] -split ','

            if ($last.Count -ge 3) {

                $d | Add-Member -NotePropertyName 'last_trade_time'   -NotePropertyValue $last[0]       -Force

                $d | Add-Member -NotePropertyName 'last_trade_price'  -NotePropertyValue ([double]$last[1]) -Force

                $d | Add-Member -NotePropertyName 'last_trade_volume' -NotePropertyValue ([int]$last[2])    -Force

            }

        }

    } catch {

        # details/get 仅用于补充最新逐笔成交，失败不影响盘口主体（新浪五档）。

        # 为避免该偶发网络警告反复刷屏，这里静默忽略；如需排查可在此加日志。

    }

    return $d

}

$script:depthJobScript = {

    param($secid, $name)

    # 后台取盘口：新浪五档 + 东财逐笔成交（独立进程运行，不阻塞界面）

    function Convert-SecidToSinaCode($secid) {

        $market = '0'; $code = $secid

        if ($secid -match '^(\d+)\.(.+)$') {

            $market = $matches[1]

            $code = $matches[2]

        }

        $prefix = ''
        if ($market -eq '1') {
            if ($code -match '^000(001|688)$') { $prefix = 's_sh' } else { $prefix = 'sh' }
        } elseif ($market -eq '0') {
            if ($code -match '^899\d{3}$') { $prefix = 's_bj' }
            elseif ($code -match '^(399|930)\d{3}$') { $prefix = 's_sz' }
            elseif ($code -match '^(4|8|92)\d{4,5}$') { $prefix = 'bj' }
            else { $prefix = 'sz' }
        } else {
            $prefix = 'bj'
        }

        return "$prefix$code"

    }

    function Parse-SinaDepthResponse($text, $sinaCode, $name = $null) {

        if ($text -notmatch "var hq_str_$sinaCode=`"([^`"]*)`";") { return $null }

        $parts = $matches[1] -split ','

        if ($parts.Count -lt 33) { return $null }

        function _num($idx) {

            $v = $parts[$idx]

            if ($v -eq $null -or $v -eq '' -or $v -eq '-') { return $null }

            try { return [double]$v } catch { return $null }

        }

        function _vol($idx) {

            $v = _num $idx

            if ($v -eq $null) { return $null }

            return [int]($v / 100)

        }

        $price = _num 3

        $prev  = _num 2

        $change = if ($price -ne $null -and $prev -ne $null) { [math]::Round($price - $prev, 4) } else { $null }

        $pct = if ($change -ne $null -and $prev -ne $null -and $prev -ne 0) { [math]::Round($change / $prev * 100, 2) } else { $null }

        $volShares = _num 8

        $volume = if ($volShares -ne $null) { [math]::Floor($volShares / 100) } else { $null }

        return [PSCustomObject]@{

            name       = if ([string]::IsNullOrWhiteSpace($name)) { $parts[0] } else { $name }

            code       = ($sinaCode -replace '^[a-z_]+', '')

            price      = $price

            prev_close = $prev

            open       = _num 1

            high       = _num 4

            low        = _num 5

            volume     = $volume

            amount     = _num 9

            turnover   = $null

            change     = $change

            pct_chg    = $pct

            asks       = @(

                , @((_num 21), (_vol 20))

                , @((_num 23), (_vol 22))

                , @((_num 25), (_vol 24))

                , @((_num 27), (_vol 26))

                , @((_num 29), (_vol 28))

            )

            bids       = @(

                , @((_num 11), (_vol 10))

                , @((_num 13), (_vol 12))

                , @((_num 15), (_vol 14))

                , @((_num 17), (_vol 16))

                , @((_num 19), (_vol 18))

            )

        }

    }

    $sinaCode = Convert-SecidToSinaCode $secid

    $sinaUrl = "https://hq.sinajs.cn/list=$sinaCode"

    $sinaHeaders = @{

        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

        'Referer'    = 'https://finance.sina.com.cn'

    }

    try {

        $sinaText = Invoke-RestMethod -Uri $sinaUrl -TimeoutSec 10 -UseBasicParsing -Headers $sinaHeaders

    } catch {

        return $null

    }

    $d = Parse-SinaDepthResponse $sinaText $sinaCode $name

    if ($d -eq $null) { return $null }

    # 东财逐笔成交（补充最新一笔，失败不影响主体）

    try {

        $dfUrl = "https://push2.eastmoney.com/api/qt/stock/details/get?secid=$secid&fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54"

        $dfHeaders = @{

            'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

            'Referer'    = 'https://quote.eastmoney.com/'

            'Accept'     = 'application/json, text/javascript, */*; q=0.01'

        }

        $df = $null

        for ($retry = 0; $retry -lt 3; $retry++) {

            try {

                $df = Invoke-RestMethod -Uri $dfUrl -TimeoutSec 10 -UseBasicParsing -Headers $dfHeaders

                break

            } catch {

                if ($retry -lt 2) { Start-Sleep -Milliseconds (300 * ($retry + 1)) }

            }

        }

        if ($df -and $df.data -and $df.data.details -and $df.data.details.Count -gt 0) {

            $last = $df.data.details[-1] -split ','

            if ($last.Count -ge 3) {

                $d | Add-Member -NotePropertyName 'last_trade_time'   -NotePropertyValue $last[0]       -Force

                $d | Add-Member -NotePropertyName 'last_trade_price'  -NotePropertyValue ([double]$last[1]) -Force

                $d | Add-Member -NotePropertyName 'last_trade_volume' -NotePropertyValue ([int]$last[2])    -Force

            }

        }

    } catch {}

    $d | Add-Member -NotePropertyName 'date' -NotePropertyValue (Get-Date).ToString('yyyy-MM-dd') -Force

    $d | Add-Member -NotePropertyName 'fetched_at' -NotePropertyValue (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') -Force

    return [PSCustomObject]@{ secid = $secid; depth = $d }

}

function Start-DepthUpdate($secid, $name) {

    if ([string]::IsNullOrEmpty($secid)) { return $false }

    if ($script:depthUpdateJob -ne $null) {

        if ($script:depthJobSecid -ne $secid) {

            $script:depthPending = @{ secid = $secid; name = $name }

        }

        return $false

    }

    $script:depthPending = $null

    $script:depthJobSecid = $secid

    $script:depthUpdateJob = Start-Job -ScriptBlock $script:depthJobScript -ArgumentList $secid, $name

    return $true

}

function Complete-DepthUpdate {

    if ($script:depthUpdateJob -eq $null) { return $false }

    if ($script:depthUpdateJob.State -notin @('Completed', 'Failed')) { return $false }

    $updated = $false

    if ($script:depthUpdateJob.State -eq 'Completed') {

        $result = Receive-Job $script:depthUpdateJob

        if ($result -ne $null -and $result.depth -ne $null) {

            $script:depthCache[$result.secid] = $result.depth

            Save-DepthCache

            $updated = $true

        }

    }

    Remove-Job $script:depthUpdateJob -Force -ErrorAction SilentlyContinue

    $script:depthUpdateJob = $null

    $script:depthJobSecid = $null

    if ($script:depthPending -ne $null) {

        $pending = $script:depthPending

        $script:depthPending = $null

        [void](Start-DepthUpdate $pending.secid $pending.name)

        $updated = $true

    }

    return $updated

}

function Get-DepthSkeleton($row) {

    # 用列表行情字段构造盘口骨架：标题与左侧立即成型，五档留空等待后台数据

    $pairs = @()

    for ($i = 0; $i -lt 5; $i++) { $pairs += ,@($null, $null) }

    return [PSCustomObject]@{

        name       = $row.f14

        code       = $row.f12

        price      = $row.f2

        prev_close = $row.f18

        open       = $row.f17

        high       = $row.f15

        low        = $row.f16

        volume     = $null

        amount     = $null

        turnover   = $null

        change     = $row.f4

        pct_chg    = $row.f3

        asks       = $pairs

        bids       = $pairs

        last_trade_time   = $null

        last_trade_price  = $null

        last_trade_volume = $null

    }

}

function Get-HalfYearPct($secid) {

    try {

        $url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=$secid&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt=101&fqt=0&end=20500101&lmt=200"

        $data = Fetch-Json $url 15

        $klines = $data.data.klines

        if ($klines -eq $null -or $klines.Count -eq 0) { return $null }

        $latest = $klines[-1].Split(',')

        try {

            $latestDate = [DateTime]::ParseExact($latest[0], 'yyyy-MM-dd', $null)

        } catch {

            Write-WarnLine "[warn] kline date parse failed for $secid : $_"

            return $null

        }

        $targetDate = $latestDate.AddMonths(-6)

        $targetPrice = $null

        foreach ($k in $klines) {

            $parts = $k.Split(',')

            $d = [DateTime]::ParseExact($parts[0], 'yyyy-MM-dd', $null)

            if ($d -ge $targetDate) {

                $targetPrice = [double]$parts[2]

                break

            }

        }

        if ($targetPrice -eq $null -or $targetPrice -eq 0) { return $null }

        $currentPrice = [double]$latest[2]

        return [math]::Round(($currentPrice - $targetPrice) / $targetPrice * 100, 2)

    } catch {

        return $null

    }

}

function Fetch-IndexKlineAmounts($secid, $days = 20) {

    # 获取指数日线成交额历史（f51=日期, f57=成交额）

    try {

        $today = Get-Date

        $beg = $today.AddDays(-($days + 5)).ToString('yyyyMMdd')

        $end = $today.AddDays(1).ToString('yyyyMMdd')

        $url = (

            "https://push2his.eastmoney.com/api/qt/stock/kline/get?" +

            "secid=$secid&fields1=f1,f2,f3,f4,f5,f6&" +

            "fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&" +

            "klt=101&fqt=0&beg=$beg&end=$end"

        )

        $data = Fetch-Json $url 15

        $klines = $data.data.klines

        if ($klines -eq $null -or $klines.Count -eq 0) { return @() }

        $result = @()

        foreach ($k in $klines) {

            $parts = $k.Split(',')

            if ($parts.Count -ge 7) {

                $result += ,@($parts[0], [double]$parts[6])

            }

        }

        return $result

    } catch {

                    Write-WarnLine "[warn] market total kline fetch failed for $secid : $_"

        return @()

    }

}

function Get-TradingElapsedMinutes($timeStr) {

    # 计算从开盘到指定时刻的累计交易分钟数（A股 09:30-11:30, 13:00-15:00，共240分钟）

    $parts = $timeStr.Split(':')

    $h = [int]$parts[0]

    $m = [int]$parts[1]

    $minutes = $h * 60 + $m

    $amStart = 9 * 60 + 30   # 570

    $amEnd = 11 * 60 + 30    # 690

    $pmStart = 13 * 60       # 780

    $pmEnd = 15 * 60         # 900

    if ($minutes -le $amStart) { return 0 }

    if ($minutes -le $amEnd) { return $minutes - $amStart }

    if ($minutes -le $pmStart) { return $amEnd - $amStart }

    if ($minutes -le $pmEnd) { return ($amEnd - $amStart) + ($minutes - $pmStart) }

    return ($amEnd - $amStart) + ($pmEnd - $pmStart)  # 240

}

$script:marketTotalJob = $null

$script:marketNextRetry = [DateTime]::MinValue

$script:marketHistoryRefreshDate = ''

$script:marketHistoryReady = $false

function Start-MarketTotalUpdate($secids) {

    $today = (Get-Date).ToString('yyyy-MM-dd')

    if ($script:marketTotalJob -ne $null -or $script:marketHistoryRefreshDate -eq $today -or
        (Get-Date) -lt $script:marketNextRetry) { return $false }

    $script:marketHistoryRefreshDate = $today

    $worker = Join-Path $PSScriptRoot 'market_worker.ps1'

    $script:marketTotalJob = Start-Job -ScriptBlock {
        param($path, $ids)
        & $path -Secids $ids
    } -ArgumentList $worker, ($secids -join ',')

    return $true

}

function Complete-MarketTotalUpdate {

    if ($script:marketTotalJob -eq $null -or $script:marketTotalJob.State -notin @('Completed', 'Failed')) { return $false }

    $received = $false

    $freshCount = 0

    if ($script:marketTotalJob.State -eq 'Completed') {

        $result = Receive-Job $script:marketTotalJob

        if ($result -ne $null) {

            foreach ($secid in $result.Keys) {

                if ($result[$secid].Count -gt 0) {

                    if ($script:marketTotalCache[$secid] -eq $null) { $script:marketTotalCache[$secid] = @{} }

                    foreach ($item in $result[$secid]) { $script:marketTotalCache[$secid][$item[0]] = $item[1] }

                    $received = $true

                    $freshCount++

                }

            }

        }

    }

    Remove-Job $script:marketTotalJob -Force -ErrorAction SilentlyContinue

    $script:marketTotalJob = $null

    # 今天没刷新成功时，只要缓存里三个市场都有足够历史数据，就继续显示
    $cachedDays = 999999
    foreach ($secid in @('1.000001', '0.399001', '0.899050')) {
        $days = 0
        $entry = $script:marketTotalCache[$secid]
        if ($entry -ne $null) { $days = $entry.Count }
        if ($days -lt $cachedDays) { $cachedDays = $days }
    }
    $script:marketHistoryReady = (($freshCount -eq 3) -or ($cachedDays -ge 6))

    if ($received) { Save-MarketTotalCache }

    $script:marketNextRetry = (Get-Date).AddSeconds($(if ($received) { 0 } else { 60 }))

    if (-not $script:marketHistoryReady) { $script:marketHistoryRefreshDate = '' }

    return $received

}

function Get-MarketTotalAmounts($indexRows) {

    # 计算沪深京三市场今日成交总额及量比

    # 量比 = (今日累计成交额 / 已交易分钟数) / (过去5日日均成交额 / 240)

    $marketSecids = [ordered]@{

        'sh' = '1.000001'

        'sz' = '0.399001'

        'bj' = '0.899050'

    }

    $secidList = @($marketSecids.Values)

    [void](Start-MarketTotalUpdate $secidList)

    if (-not $script:marketHistoryReady) { return $null }

    # 从已有指数行情中提取今日成交额

    $todayAmounts = @{}

    foreach ($r in $indexRows) {

        $secid = "$($r.f13).$($r.f12)"

        if ($secid -in $secidList) {

            try {

                $todayAmounts[$secid] = [double]$r.f6

            } catch {

                $todayAmounts[$secid] = 0

            }

        }

    }

    if ($todayAmounts.Count -ne 3) { return $null }

    foreach ($v in $todayAmounts.Values) {

        if ($v -le 0) { return $null }

    }

    $today = Get-Date

    if ($today.DayOfWeek -in @([DayOfWeek]::Saturday, [DayOfWeek]::Sunday)) { return $null }

    $todayStr = $today.ToString('yyyy-MM-dd')

    $commonDates = $null

    foreach ($secid in $secidList) {

        $cache = $script:marketTotalCache[$secid]

        $dates = @($(if ($cache -ne $null) { $cache.Keys | Where-Object { $_ -lt $todayStr } } else { @() }))

        $commonDates = @($(if ($commonDates -eq $null) { $dates } else { $commonDates | Where-Object { $_ -in $dates } }))

    }

    $commonDates = @($commonDates | Sort-Object -Descending | Select-Object -First 5)

    if ($commonDates.Count -lt 5) {

        [void](Start-MarketTotalUpdate $secidList)

        return $null

    }

    $totalByDay = @()

    foreach ($date in $commonDates) {

        $sum = 0.0

        foreach ($secid in $secidList) { $sum += [double]$script:marketTotalCache[$secid][$date] }

        $totalByDay += $sum

    }

    $avg = ($totalByDay | Measure-Object -Average).Average

    $todayTotal = 0.0

    foreach ($v in $todayAmounts.Values) { $todayTotal += $v }

    $currentTime = (Get-Date).ToString('HH:mm')

    $elapsed = Get-TradingElapsedMinutes $currentTime

    if ($elapsed -le 0) { return $null }

    $totalMinutes = 240

    $ratio = if ($avg -gt 0) { ($todayTotal / $elapsed) / ($avg / $totalMinutes) } else { $null }

    return @{

        total_yi = [math]::Round($todayTotal / 1e8, 2)

        ratio = if ($ratio -ne $null) { [math]::Round($ratio, 2) } else { $null }

    }

}

# ---------- 数据组织 ----------

function Get-Quotes($stealth) {

    $scriptDir = $PSScriptRoot

    $wl = Get-Content -Path (Join-Path $scriptDir 'watchlist.json') -Encoding UTF8 | ConvertFrom-Json

    $stocks = $wl.stocks

    $indices = $wl.indices

    $stockSecids = Search-SecIds $stocks

    $indexSecids = Search-SecIds $indices

    if ($stockSecids.Count -eq 0 -and $indexSecids.Count -eq 0) {

        return $null, $null, "no stocks or indices"

    }

    # 合并单次请求，减少接口调用

    $allSecids = @($stockSecids) + @($indexSecids)

    try {

        $allRows = @(Fetch-AllQuotes $allSecids $stealth)

    } catch {

        return @(), @(), "行情获取失败: $_"

    }

    $indexSet = @{}

    foreach ($s in $indexSecids) { $indexSet[$s] = $true }

    $stockRows = @()

    $indexRows = @()

    foreach ($r in $allRows) {

        $secid = "$($r.f13).$($r.f12)"

        if ($indexSet.ContainsKey($secid)) {

            $indexRows += $r

        } else {

            $stockRows += $r

        }

    }

    return $stockRows, $indexRows, $null

}

# ---------- 分块增量刷新 ----------

$script:stockSecids = @()

$script:indexSecids = @()

$script:chunkSize = 0

$script:chunkIndex = 0

$script:chunkInterval = 0

$script:nextChunkTime = $null

$script:quoteUpdateJob = $null

$script:quoteStatus = ''

$script:quoteRequestedCount = 0

$script:lastQuoteSuccess = $null

$script:quoteSuccessTimes = @{}

function Start-QuoteUpdate($secids, $stealth, $timeout = 5) {
    if ($secids.Count -eq 0 -or $script:quoteUpdateJob -ne $null) { return $false }
    $fields = if ($stealth) { $STEALTH_FIELDS } else { $FULL_FIELDS }
    $worker = Join-Path $PSScriptRoot 'quote_worker.ps1'
    $script:quoteRequestedCount = $secids.Count
    $script:quoteUpdateJob = Start-Job -ScriptBlock {
        param($path, $ids, $fieldSet, $timeoutSec)
        & $path -Secids $ids -Fields $fieldSet -Timeout $timeoutSec
    } -ArgumentList $worker, ($secids -join ','), $fields, $timeout
    $script:quoteStatus = 'refreshing'
    return $true
}

function Complete-QuoteUpdate {
    if ($script:quoteUpdateJob -eq $null) { return $false }
    if ($script:quoteUpdateJob.State -notin @('Completed', 'Failed')) { return $false }
    $updated = $false
    if ($script:quoteUpdateJob.State -eq 'Completed') {
        $result = Receive-Job $script:quoteUpdateJob
        if ($result -ne $null) {
            $rows = @($result.Rows)
            if ($rows.Count -gt 0) {
                Update-Caches $rows
                $script:lastQuoteSuccess = [DateTime]$result.CompletedAt
                $script:quoteStatus = if ($rows.Count -lt $script:quoteRequestedCount) { 'partial data; missing symbols kept cached' } else { '' }
                $updated = $true
            } elseif ($result.Error) {
                $script:quoteStatus = 'refresh failed; showing cached data'
                $updated = $true
            }
        }
    } else {
        $script:quoteStatus = 'refresh failed; showing cached data'
        $updated = $true
    }
    Remove-Job $script:quoteUpdateJob -Force -ErrorAction SilentlyContinue
    $script:quoteUpdateJob = $null
    return $updated
}

function Initialize-Quotes($stealth) {

    $scriptDir = $PSScriptRoot

    $wl = Get-Content -Path (Join-Path $scriptDir 'watchlist.json') -Encoding UTF8 | ConvertFrom-Json

    $stocks = $wl.stocks

    $indices = $wl.indices

    $script:stockSecids = @(Search-SecIds $stocks)

    $script:indexSecids = @(Search-SecIds $indices)

    if ($script:stockSecids.Count -eq 0 -and $script:indexSecids.Count -eq 0) {

        return "no stocks or indices"

    }

    # 首次全量拉取，填满缓存（静默，避免警告信息破坏界面）

    $allSecids = @($script:stockSecids) + @($script:indexSecids)

    try {

        $allRows = @(Fetch-AllQuotes $allSecids $stealth $true)

    } catch {

        return "行情获取失败: $_"

    }

    Update-Caches $allRows

    $script:lastQuoteSuccess = Get-Date

    $script:quoteStatus = ''

    # 清理已不在当前 watchlist 中的旧缓存条目，保留仍有效的历史字段（如 VolR/Turn%）

    $validSecids = @{}

    foreach ($s in $script:stockSecids) { $validSecids[$s] = $true }

    foreach ($s in $script:indexSecids) { $validSecids[$s] = $true }

    $staleStock = $script:stockQuoteCache.Keys | Where-Object { -not $validSecids.ContainsKey($_) }

    $staleIndex = $script:indexQuoteCache.Keys | Where-Object { -not $validSecids.ContainsKey($_) }

    foreach ($k in $staleStock) { $script:stockQuoteCache.Remove($k) }

    foreach ($k in $staleIndex) { $script:indexQuoteCache.Remove($k) }

    # 计算分块参数：希望每块间隔约 3 秒，但至少 1 块且不超过股票总数

    # 保证 totalChunks * chunkInterval == interval，即每次刷新周期内正好把所有股票更新一遍

    $desiredChunkInterval = 3

    $stockCount = $script:stockSecids.Count

    if ($stockCount -le 0) {

        $script:chunkSize = 0

        $script:chunkIndex = 0

        $script:chunkInterval = $interval

    } else {

        $chunkCount = [math]::Floor($interval / $desiredChunkInterval)

        if ($chunkCount -lt 1) { $chunkCount = 1 }

        if ($chunkCount -gt $stockCount) { $chunkCount = $stockCount }

        $script:chunkSize = [math]::Max(1, [math]::Ceiling($stockCount / $chunkCount))

        $script:chunkIndex = 0

        $totalChunks = [math]::Ceiling($stockCount / $script:chunkSize)

        $script:chunkInterval = $interval / $totalChunks

        if ($script:chunkInterval -lt 0.5) { $script:chunkInterval = 0.5 }

    }

    $script:nextChunkTime = (Get-Date).AddSeconds($script:chunkInterval)

    # 初始化半年涨跌刷新计划：间隔为自动刷新间隔的 4 倍

    # 若当天已有缓存则按计划等待；若缓存缺失/过期则立即在首次轮询后后台更新

    # 保证先展示缓存数据，再尽快补齐真实数据

    $script:hyStartInterval = $interval * 4

    $today = (Get-Date).ToString('yyyy-MM-dd')

    $needsHyUpdate = $false

    foreach ($secid in $script:stockSecids) {

        $cached = $script:hyCache[$secid]

        if ($cached -eq $null -or $cached.date -ne $today) {

            $needsHyUpdate = $true

            break

        }

    }

    if ($needsHyUpdate) {

        $script:nextHyStartTime = (Get-Date)

    } else {

        $script:nextHyStartTime = (Get-Date).AddSeconds($script:hyStartInterval)

    }

    # 启动后台错时抓取 PE/PB（只补当天缺失的缓存，不会阻塞界面）

    Start-PepbUpdate $script:stockSecids

    return $null

}

function Update-NextChunk($stealth) {

    if ($script:quoteUpdateJob -ne $null) { return }

    $stockCount = $script:stockSecids.Count

    if ($stockCount -eq 0) {

        [void](Start-QuoteUpdate $script:indexSecids $stealth 5)

        $script:nextChunkTime = (Get-Date).AddSeconds($interval)

        return

    }

    $totalChunks = [math]::Ceiling($stockCount / $script:chunkSize)

    $start = $script:chunkIndex * $script:chunkSize

    $end = [math]::Min($start + $script:chunkSize, $stockCount)

    $chunkSecids = @($script:stockSecids[$start..($end - 1)])

    $allSecids = @($chunkSecids)

    if ($script:chunkIndex -eq 0 -and $script:indexSecids.Count -gt 0) {

        $allSecids += $script:indexSecids

    }

    if (Start-QuoteUpdate $allSecids $stealth 5) {

        $script:chunkIndex = ($script:chunkIndex + 1) % $totalChunks

        $script:nextChunkTime = (Get-Date).AddSeconds($script:chunkInterval)

    }

}

function Update-HalfYearCache($rows) {

    $today = (Get-Date).ToString('yyyy-MM-dd')

    foreach ($r in $rows) {

        $secid = "$($r.f13).$($r.f12)"

        $cached = $script:hyCache[$secid]

        if ($cached -ne $null -and $cached.date -eq $today) { continue }

        $pct = Get-HalfYearPct $secid

        if ($pct -ne $null) {

            $script:hyCache[$secid] = @{ pct = $pct; date = $today }

        }

    }

    Save-HyCache

}

$script:hyUpdateJobScript = {

    param($secids)

    $secids = @($secids | ForEach-Object { $_ -split ',' } | Where-Object { $_ })

    # 并发抓取半年涨跌：每个 secid 一个 runspace，最多 8 并发

    # 使用 here-string 将 worker 逻辑完整传给每个 runspace，避免作用域问题

    $workerSource = @'

param($s)

function Fetch-Json($url, $timeout = 15, $retries = 1) {

    $headers = @{

        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

        'Referer' = 'https://quote.eastmoney.com/'

    }

    $lastErr = $null

    for ($i = 0; $i -lt $retries; $i++) {

        try {

            return Invoke-RestMethod -Uri $url -TimeoutSec $timeout -UseBasicParsing -Headers $headers -DisableKeepAlive

        } catch {

            $lastErr = $_

            if ($i -lt $retries - 1) {

                Start-Sleep -Milliseconds (200 * ($i + 1))

            }

        }

    }

    throw $lastErr

}

function Get-HalfYearPct($secid) {

    try {

        $url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=$secid&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt=101&fqt=0&end=20500101&lmt=200"

        $data = Fetch-Json $url 15

        $klines = $data.data.klines

        if ($klines -eq $null -or $klines.Count -eq 0) { return $null }

        $latest = $klines[-1].Split(',')

        try {

            $latestDate = [DateTime]::ParseExact($latest[0], 'yyyy-MM-dd', $null)

        } catch {

            return $null

        }

        $targetDate = $latestDate.AddMonths(-6)

        $targetPrice = $null

        foreach ($k in $klines) {

            $parts = $k.Split(',')

            $d = [DateTime]::ParseExact($parts[0], 'yyyy-MM-dd', $null)

            if ($d -ge $targetDate) {

                $targetPrice = [double]$parts[2]

                break

            }

        }

        if ($targetPrice -eq $null -or $targetPrice -eq 0) { return $null }

        $currentPrice = [double]$latest[2]

        return [math]::Round(($currentPrice - $targetPrice) / $targetPrice * 100, 2)

    } catch {

        return $null

    }

}

$pct = Get-HalfYearPct $s

if ($pct -ne $null) { @{ secid = $s; pct = $pct } } else { $null }

'@

    $result = @{}

    if ($secids.Count -eq 0) { return $result }

    $maxConcurrency = [Math]::Min(8, $secids.Count)

    $pool = [runspacefactory]::CreateRunspacePool(1, $maxConcurrency)

    $pool.Open()

    $powershells = New-Object System.Collections.ArrayList

    $handles = New-Object System.Collections.ArrayList

    foreach ($secid in $secids) {

        $ps = [powershell]::Create()

        $ps.RunspacePool = $pool

        [void]$ps.AddScript($workerSource).AddArgument($secid)

        [void]$powershells.Add($ps)

        [void]$handles.Add($ps.BeginInvoke())

    }

    for ($i = 0; $i -lt $powershells.Count; $i++) {

        $r = $powershells[$i].EndInvoke($handles[$i])

        if ($r -ne $null -and $r -is [hashtable] -and $r.ContainsKey('pct')) {

            $result[$r.secid] = $r.pct

        }

        $powershells[$i].Dispose()

    }

    $pool.Close()

    $pool.Dispose()

    return $result

}

function Start-HalfYearUpdate($rows) {

    if (-not $fetchHy) { return }

    # 若已有后台任务在运行，则不重复启动，避免覆盖尚未完成的抓取

    if ($script:hyUpdateJob -ne $null -and $script:hyUpdateJob.State -eq 'Running') { return }

    $today = (Get-Date).ToString('yyyy-MM-dd')

    $secidsToUpdate = @()

    foreach ($r in $rows) {

        $secid = "$($r.f13).$($r.f12)"

        $cached = $script:hyCache[$secid]

        if ($cached -eq $null -or $cached.date -ne $today) {

            $secidsToUpdate += $secid

        }

    }

    if ($secidsToUpdate.Count -eq 0) { return }

    $script:hyUpdateJob = Start-Job -ScriptBlock $script:hyUpdateJobScript -ArgumentList (,$secidsToUpdate)

}

function Complete-HalfYearUpdate {

    if ($script:hyUpdateJob -eq $null) { return }

    if ($script:hyUpdateJob.State -in @('Completed', 'Failed')) {

        if ($script:hyUpdateJob.State -eq 'Completed') {

            $result = Receive-Job $script:hyUpdateJob

            $today = (Get-Date).ToString('yyyy-MM-dd')

            if ($result -ne $null) {

                foreach ($secid in $result.Keys) {

                    $script:hyCache[$secid] = @{ pct = $result[$secid]; date = $today }

                }

                Save-HyCache

            }

        }

        Remove-Job $script:hyUpdateJob -Force -ErrorAction SilentlyContinue

        $script:hyUpdateJob = $null

    }

}

# ---------- 缓存：PE / PB ----------

$script:pepbCachePath = Join-Path $PSScriptRoot '.pepb_cache.json'

$script:pepbCache = @{}   # secid -> @{ date; pe; pb }

$script:pepbUpdateJob = $null

function Load-PepbCache {

    if (Test-Path $script:pepbCachePath) {

        try {

            $json = Get-Content $script:pepbCachePath -Encoding UTF8 | ConvertFrom-Json

            foreach ($p in $json.PSObject.Properties) {

                $script:pepbCache[$p.Name] = $p.Value

            }

        } catch {

            # 缓存损坏时忽略，重新计算

        }

    }

}

function Save-PepbCache {

    try {

        Write-JsonFile $script:pepbCachePath $script:pepbCache

    } catch {

        # 保存失败不影响主流程

    }

}

function Fetch-PepbData($secid) {

    # 通过东财 ulist 接口取单只股票 PE/PB（f9=市盈率，f23=市净率）

    try {

        $url = "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&fields=f9,f23&secids=$secid"

        $data = Fetch-Json $url 15

        $row = $data.data.diff | Select-Object -First 1

        if ($row -ne $null -and $row.f9 -ne $null -and $row.f23 -ne $null) {

            return @{

                pe = [math]::Round([double]$row.f9, 2)

                pb = [math]::Round([double]$row.f23, 2)

            }

        }

    } catch {

        Write-Host "[warn] PE/PB fetch failed for $secid : $_" -ForegroundColor DarkGray

    }

    return $null

}

function Get-Pepb($secid) {

    # 只读当天缓存，不触发网络；缺失时触发后台补齐并返回 $null（对应区块暂不显示）

    $today = (Get-Date).ToString('yyyy-MM-dd')

    $cached = $script:pepbCache[$secid]

    if ($cached -ne $null -and $cached.date -eq $today) {

        return @{ pe = $cached.pe; pb = $cached.pb }

    }

    Start-PepbUpdate @($secid)

    return $null

}

$script:pepbUpdateJobScript = {

    param($secids)

    $secids = @($secids | ForEach-Object { $_ -split ',' } | Where-Object { $_ })

    # 后台错时抓取 PE/PB，每只股票间隔 300ms，避免并发请求被限制

    $headers = @{

        'User-Agent' = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

        'Referer'    = 'https://quote.eastmoney.com/'

    }

    $result = @{}

    foreach ($secid in $secids) {

        try {

            $url = "https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&fields=f9,f23&secids=$secid"

            $data = Invoke-RestMethod -Uri $url -TimeoutSec 15 -UseBasicParsing -Headers $headers -DisableKeepAlive

            $row = $data.data.diff | Select-Object -First 1

            if ($row -ne $null -and $row.f9 -ne $null -and $row.f23 -ne $null) {

                $result[$secid] = @{

                    pe = [math]::Round([double]$row.f9, 2)

                    pb = [math]::Round([double]$row.f23, 2)

                }

            }

        } catch {}

        Start-Sleep -Milliseconds 300

    }

    return $result

}

function Start-PepbUpdate($secids) {

    # 仅当天缓存不存在时启动后台错时更新

    if ($script:pepbUpdateJob -ne $null -and $script:pepbUpdateJob.State -eq 'Running') { return }

    if ($secids.Count -eq 0) { return }

    $today = (Get-Date).ToString('yyyy-MM-dd')

    $toUpdate = @()

    foreach ($secid in $secids) {

        $cached = $script:pepbCache[$secid]

        if ($cached -eq $null -or $cached.date -ne $today) {

            $toUpdate += $secid

        }

    }

    if ($toUpdate.Count -eq 0) { return }

    $script:pepbUpdateJob = Start-Job -ScriptBlock $script:pepbUpdateJobScript -ArgumentList (,$toUpdate)

}

function Complete-PepbUpdate {

    if ($script:pepbUpdateJob -eq $null) { return $false }

    if ($script:pepbUpdateJob.State -in @('Completed', 'Failed')) {

        $updated = $false

        if ($script:pepbUpdateJob.State -eq 'Completed') {

            $result = Receive-Job $script:pepbUpdateJob

            $today = (Get-Date).ToString('yyyy-MM-dd')

            if ($result -ne $null) {

                foreach ($secid in $result.Keys) {

                    $script:pepbCache[$secid] = @{ date = $today; pe = $result[$secid].pe; pb = $result[$secid].pb }

                }

                Save-PepbCache

                $updated = $true

            }

        }

        Remove-Job $script:pepbUpdateJob -Force -ErrorAction SilentlyContinue

        $script:pepbUpdateJob = $null

        return $updated

    }

    return $false

}

# ---------- 显示 ----------

function Get-OpenPriceWithArrow($openVal, $prevCloseVal, $digits = 2) {

    $price = Format-Number $openVal $digits

    try {

        $o = [double]$openVal

        $p = [double]$prevCloseVal

        if ($o -gt $p) { return $price, '▲' }

        if ($o -lt $p) { return $price, '▼' }

        return $price, '-'

    } catch {}

    return $price, ' '

}

function Print-StockRowAt($r, $j, $stealth, $hasCursor, [ref]$frameLines) {

    # 在当前光标位置打印单行股票，调用前需先用 SetCursorPosition 定位

    if ($stealth) {

        $cursor = if (Test-QuoteStale $r) { '!' } elseif ($hasCursor) { '>' } else { ' ' }

        $digits = if (Test-IsETF $r) { 3 } else { 2 }

        $price = Format-Number $r.f2 $digits

        $change = Format-Number $r.f4 $digits

        $pct = Format-Number $r.f3

        $turnover = Format-Number $r.f8

        $turnoverStr = if ($turnover -eq '-') { '-' } else { "$turnover%" }

        $vr = Format-Number $r.f10

        $line = $cursor + (Pad-Left $r.f12 7) + ' ' + (Pad-Right (Truncate-Name $r.f14) 8) + ' ' + (Pad-Left "$pct%" 9) + ' ' + (Pad-Left $change 9) + ' ' + (Pad-Left $price 8) + ' ' + (Pad-Left $vr 8) + ' ' + (Pad-Left $turnoverStr 8)

        if ($frameLines -ne $null) {

            $frameLines.Value += $line

        } else {

            Write-Host $line -ForegroundColor Gray

        }

    } else {

        $bg = if ($j % 2 -eq 0) { 'Black' } else { 'DarkGray' }

        $cursor = if (Test-QuoteStale $r) { '!' } elseif ($hasCursor) { '>' } else { ' ' }

        $digits = if (Test-IsETF $r) { 3 } else { 2 }

        $code = $r.f12

        $name = Truncate-Name $r.f14

        $price = Format-Number $r.f2 $digits

        $prevClose = Format-Number $r.f18 $digits

        $openPrice, $openArrow = Get-OpenPriceWithArrow $r.f17 $r.f18 $digits

        $high = Format-Number $r.f15 $digits

        $low = Format-Number $r.f16 $digits

        $change = Format-Number $r.f4 $digits

        $pct = Format-Number $r.f3

        $turnover = Format-Number $r.f8

        $vr = Format-Number $r.f10

        $amount = Format-Number $r.f6 2 100000000

        $hyPct = Format-Number $r.hyPct

        $prefix = $cursor + (Pad-Left $code 7) + ' ' + (Pad-Right $name 8) + ' '

        $pctStr = Pad-Left "$pct%" 8

        $suffix = ' ' + (Pad-Left $change 8) + ' ' + (Pad-Left $price 7) + ' ' + (Pad-Left $prevClose 7) + ' ' + (Pad-Left $openPrice 7) + $openArrow + ' ' + (Pad-Left $high 7) + ' ' + (Pad-Left $low 7) + ' ' + (Pad-Left $vr 7) + ' ' + (Pad-Left $turnover 8) + ' ' + (Pad-Left $amount 9) + ' ' + (Pad-Left "$hyPct%" 10)

        $line = $prefix + $pctStr + $suffix

        if ($script:hasConsole) { $line = Truncate-ToWidth $line ([Console]::WindowWidth - 1) }

        Write-Host $line -ForegroundColor White -BackgroundColor $bg

    }

}

function Print-StockRows($rows, $stealth, $cursorIndex = -1, [ref]$frameLines) {

    # 记录表头所在屏幕行号，用于后续仅重绘游标所在行

    if ($frameLines -ne $null) {

        $script:stockTableHeaderY = $frameLines.Value.Count

    } elseif ($script:hasConsole) {

        try { $script:stockTableHeaderY = [Console]::CursorPosition.Y } catch {}

    }

    if ($stealth) {

        $headerPrefix = ' ' + (Pad-Left 'Code' 7) + ' ' + (Pad-Right 'Name' 8)

        $header = $headerPrefix + ' ' + (Pad-Left 'Chg%' 9) + ' ' + (Pad-Left 'Chg' 9) + ' ' + (Pad-Left 'Last' 8) + ' ' + (Pad-Left 'VolR' 8) + ' ' + (Pad-Left 'Turn%' 8)

        $sep = '-' * $STEALTH_TABLE_WIDTH

        if ($frameLines -ne $null) {

            $frameLines.Value += $header

            $frameLines.Value += $sep

        } else {

            Write-Host $header -ForegroundColor Gray

            Write-Host $sep -ForegroundColor DarkGray

        }

    } else {

        $header = ' ' + (Pad-Left '代码' 7) + ' ' + (Pad-Right '名称' 8) + ' ' + (Pad-Left '涨跌幅' 8) + ' ' + (Pad-Left '涨跌额' 8) + ' ' + (Pad-Left '最新' 7) + ' ' + (Pad-Left '昨收' 7) + ' ' + (Pad-Left '今开' 8) + ' ' + (Pad-Left '最高' 7) + ' ' + (Pad-Left '最低' 7) + ' ' + (Pad-Left '量比' 7) + ' ' + (Pad-Left '换手%' 8) + ' ' + (Pad-Left '成交额' 9) + ' ' + (Pad-Left '半年涨跌' 10)

        if ($script:hasConsole) {

            $width = [Console]::WindowWidth - 1

            Write-Host (Truncate-ToWidth $header $width)

            Write-Host ('-' * [math]::Min($NORMAL_TABLE_WIDTH, $width))

        } else {

            Write-Host $header

            Write-Host ('-' * $NORMAL_TABLE_WIDTH)

        }

    }

    $j = 0

    foreach ($r in $rows) {

        Print-StockRowAt $r $j $stealth ($j -eq $cursorIndex) $frameLines

        $j++

    }

}

function Update-Cursor($oldIndex, $newIndex, $rows, $stealth) {

    # 仅更新行首的光标字符，避免重绘整行带来的闪烁与延迟

    # 使用 [Console]::Write 直接输出，确保立即刷新到屏幕

    if ($script:stockTableHeaderY -eq $null -or -not $rows -or $rows.Count -eq 0) { return }

    $rowsStartY = $script:stockTableHeaderY + 2

    $fg = if ($stealth) { [ConsoleColor]::Gray } else { [ConsoleColor]::White }

    foreach ($idx in @($oldIndex, $newIndex)) {

        if ($idx -lt 0 -or $idx -ge $rows.Count) { continue }

        try {

            [Console]::SetCursorPosition(0, $rowsStartY + $idx)

        } catch { continue }

        $prevFg = [Console]::ForegroundColor

        $prevBg = [Console]::BackgroundColor

        try {

            if (-not $stealth) {

                [Console]::BackgroundColor = if ($idx % 2 -eq 0) { [ConsoleColor]::Black } else { [ConsoleColor]::DarkGray }

            } else {

                [Console]::BackgroundColor = [ConsoleColor]::Black

            }

            [Console]::ForegroundColor = $fg

            $ch = if (Test-QuoteStale $rows[$idx]) { '!' } elseif ($idx -eq $newIndex) { '>' } else { ' ' }

            [Console]::Write($ch)

        } finally {

            [Console]::ForegroundColor = $prevFg

            [Console]::BackgroundColor = $prevBg

        }

    }

}

function Print-IndexRows($rows, $stealth, [ref]$frameLines) {

    if ($rows.Count -eq 0) { return }

    $tableWidth = if ($stealth) { $STEALTH_TABLE_WIDTH } else { $NORMAL_TABLE_WIDTH }

    $sep = '-' * $tableWidth

    if ($frameLines -ne $null) {

        $frameLines.Value += $sep

    } else {

        Write-Host $sep -ForegroundColor DarkGray

    }

    if ($stealth) {

        $j = 0

        foreach ($r in $rows) {

            $name = Truncate-Name "$($r.f14)"

            $price = Format-Number $r.f2

            $change = Format-Number $r.f4

            $pct = Format-Number $r.f3

            $line = (Pad-Right $name 8) + ' ' + (Pad-Left $price 10) + ' ' + (Pad-Left "$change / $pct%" 20)

            if ($frameLines -ne $null) {

                $frameLines.Value += $line

            } else {

                Write-Host $line -ForegroundColor Gray

            }

            $j++

        }

        return

    }

    $perRow = 5

    $sep = ' | '

    $sepW = Measure-DisplayWidth $sep

    $boxes = @()

    $minBoxW = 0

    foreach ($r in $rows) {

        $name = Truncate-Name "$($r.f14)"

        $price = Format-Number $r.f2

        $change = Format-Number $r.f4

        $pct = Format-Number $r.f3

        $right2 = "$change / $pct%"

        $boxes += ,@($name, $price, $right2)

        $minBoxW = [math]::Max($minBoxW, [math]::Max((Measure-DisplayWidth ($name + $price)), (Measure-DisplayWidth $right2)))

    }

    $boxW = [math]::Floor(($tableWidth - ($perRow - 1) * $sepW) / $perRow)

    if ($boxW -lt $minBoxW) { $boxW = $minBoxW }

    for ($i = 0; $i -lt $boxes.Count; $i += $perRow) {

        $line1 = ""

        $line2 = ""

        for ($j = 0; $j -lt $perRow; $j++) {

            if ($i + $j -ge $boxes.Count) { break }

            $box = $boxes[$i + $j]

            $name = $box[0]

            $price = $box[1]

            $right2 = $box[2]

            $nameW = Measure-DisplayWidth $name

            $priceW = Measure-DisplayWidth $price

            $pad1 = $boxW - $nameW - $priceW

            if ($pad1 -lt 0) { $pad1 = 0 }

            $part1 = $name + (' ' * $pad1) + $price

            $right2W = Measure-DisplayWidth $right2

            $pad2 = $boxW - $right2W

            if ($pad2 -lt 0) { $pad2 = 0 }

            $part2 = (' ' * $pad2) + $right2

            if ($j -gt 0) {

                $line1 += $sep

                $line2 += $sep

            }

            $line1 += $part1

            $line2 += $part2

        }

        $width = if ($script:hasConsole) { [math]::Min($tableWidth, [Console]::WindowWidth - 1) } else { $tableWidth }

        Write-Host (Pad-Right $line1 $width)

        Write-Host (Pad-Right $line2 $width)

    }

}

function Get-SortedRows($rows, $sortState) {

    if ($sortState.c -ne 0) {

        $field = $F_PCT_CHG

        $desc = ($sortState.c -eq 1)

    } elseif ($sortState.v -ne 0) {

        $field = $F_VOL_RATIO

        $desc = ($sortState.v -eq 1)

    } else {

        return $rows

    }

    $rows | Sort-Object -Property @(
        @{ Expression = {
            try { [void][double]$_.$field; 0 } catch { 1 }
        } },
        @{ Expression = {
            try { [double]$_.$field } catch { 0.0 }
        }; Descending = $desc }
    )

}

function Get-SortHint($sortState) {

    $parts = @()

    if ($sortState.c -eq 1) { $parts += 'C=涨跌幅↓' }

    elseif ($sortState.c -eq 2) { $parts += 'C=涨跌幅↑' }

    if ($sortState.v -eq 1) { $parts += 'V=量比↓' }

    elseif ($sortState.v -eq 2) { $parts += 'V=量比↑' }

    return $parts -join ' '

}

function Print-MarketTotal($info, $stealth, [ref]$frameLines) {

    # 在指数方块下方打印沪深京三市场成交额与量比

    if ($info -eq $null -or $info.total_yi -eq $null -or $info.ratio -eq $null) { return }

    $tableWidth = if ($stealth) { $STEALTH_TABLE_WIDTH } else { $NORMAL_TABLE_WIDTH }

    $label = "三市总额/量比 "

    $amountStr = "$($info.total_yi)"

    $value = "$amountStr / $($info.ratio)"

    if ($stealth) {

        # 让数值斜杠与 stealth 指数行 "change / pct%" 的斜杠对齐

        # 指数行斜杠位于显示列 33（0-based），倒推前面空格

        $targetSlashPos = 32  # 斜杠前所有字符的显示宽度（0-based）

        $spacesNeeded = $targetSlashPos - (Measure-DisplayWidth $label) - (Measure-DisplayWidth $amountStr)

        if ($spacesNeeded -lt 0) { $spacesNeeded = 0 }

        $line = $label + (' ' * $spacesNeeded) + $value

    } else {

        $line = $label + $value

    }

    $paddedLine = Pad-Right $line $tableWidth

    if ($frameLines -ne $null) {

        $frameLines.Value += $paddedLine

    } else {

        Write-Host $paddedLine

    }

}

# ---------- 屏幕写入 ----------
#
# 重要（实测结论）：
# 中文是全角字符，终端按两列显示。实测三种写法的效果：
#   1) WriteConsoleOutputW 按“单元格”写：汉字后面会多占一列 -> 整行右移、与表头错位；
#   2) WriteConsoleOutputCharacterW 按“字符”写：汉字会被重复画一次 -> 名称变成“瑞瑞联联…”；
#   3) PowerShell 自带的 $host.UI.RawUI.SetBufferContents + NewBufferCellArray：渲染正确。
# 因此这里保持用 3)，并且只重画内容发生变化的行，把刷新量降到最小，减少闪烁。
#
# 需要抓刷新时序时，设置环境变量 WATCH_FRAME_LOG=1 再启动，
# 每次写屏会往 .frame_log.txt 追加一行（默认不写任何文件）。

$script:frameLogPath = Join-Path $PSScriptRoot '.frame_log.txt'

function Write-FrameLog($text) {

    if ($env:WATCH_FRAME_LOG -ne '1') { return }

    try { Add-Content -LiteralPath $script:frameLogPath -Value ("{0} {1}" -f (Get-Date -Format 'HH:mm:ss.fff'), $text) -Encoding UTF8 } catch {}

}

function Write-WarnLine($text) {

    # 提示信息会写到光标所在的屏幕位置，可能把隐蔽模式的表格顶动/滚屏，
    # 所以隐蔽模式下不打印，只在开启 WATCH_FRAME_LOG=1 时记入日志；
    # 详情模式（中文红绿）仍然照常打印。

    if ($stealth) {

        Write-FrameLog $text

    } else {

        Write-Host $text -ForegroundColor DarkGray

    }

}

function Write-FrameLine($y, $line, $fgColor, $bgColor) {

    # 单行写入（回退时也可用）

    try {

        $buffer = $host.UI.RawUI.NewBufferCellArray(@($line), $fgColor, $bgColor)

        $host.UI.RawUI.SetBufferContents(@{X = 0; Y = $y}, $buffer)

        return $true

    } catch {

        return $false

    }

}

function Write-Frame($lines, $fgColor = 'Gray', $bgColor = 'Black') {

    if (-not $script:hasConsole) {

        foreach ($line in $lines) {

            Write-Host $line -ForegroundColor $fgColor

        }

        return

    }

    $windowWidth = [Console]::WindowWidth

    $windowHeight = [Console]::WindowHeight

    # 先把所有行按窗口宽度补齐/截断，保证差异比较基于实际显示内容

    $paddedLines = @()

    foreach ($line in $lines) {

        $pad = $windowWidth - (Measure-DisplayWidth $line)

        if ($pad -gt 0) {

            $line += ' ' * $pad

        } elseif ($pad -lt 0) {

            $line = Truncate-ToWidth $line $windowWidth

        }

        $paddedLines += $line

    }

    # 缓冲区至少要装得下整帧再多留一行给光标，避免写入把窗口顶上去

    try {

        $bufSize = $host.UI.RawUI.BufferSize

        $newWidth = $bufSize.Width

        $newHeight = $bufSize.Height

        $needResize = $false

        if ($bufSize.Width -lt $windowWidth) { $newWidth = $windowWidth; $needResize = $true }

        if ($bufSize.Height -lt ($paddedLines.Count + 1)) { $newHeight = $paddedLines.Count + 1; $needResize = $true }

        if ($needResize) {

            try { $host.UI.RawUI.BufferSize = @{Width = $newWidth; Height = $newHeight} } catch {}

        }

    } catch {}

    # 窗口被滚动过（例如提示信息把内容顶上去）时整屏重画并回到顶部

    $force = $false

    try { if ([Console]::WindowTop -ne 0) { $force = $true } } catch {}

    $lastLines = $script:lastFrameLines

    $blankLine = ' ' * $windowWidth

    if ($force -or ($lastLines -eq $null) -or ($lastLines.Count -ne $paddedLines.Count)) {

        if ($force) {

            try { $host.UI.RawUI.WindowPosition = @{X = 0; Y = 0} } catch {}

        }

        for ($i = 0; $i -lt $paddedLines.Count; $i++) {

            if (-not (Write-FrameLine $i $paddedLines[$i] $fgColor $bgColor)) { break }

        }

        $fill = [math]::Max(0, $windowHeight - $paddedLines.Count)

        for ($i = 0; $i -lt $fill; $i++) {

            [void](Write-FrameLine ($paddedLines.Count + $i) $blankLine $fgColor $bgColor)

        }

        Write-FrameLog ("full rows=$($paddedLines.Count) fill=$fill windowTop=$([Console]::WindowTop)")

    } else {

        $changed = @()

        for ($i = 0; $i -lt $paddedLines.Count; $i++) {

            if ($paddedLines[$i] -ne $lastLines[$i]) { $changed += $i }

        }

        foreach ($i in $changed) {

            [void](Write-FrameLine $i $paddedLines[$i] $fgColor $bgColor)

        }

        if ($changed.Count -gt 0) { Write-FrameLog ("diff rows=[$($changed -join ',')]") }

    }

    $script:lastFrameLines = $paddedLines

    # 光标停到表格下方；整帧比窗口高时停在窗口最后一行，避免把表头顶出视野

    try {

        $parkY = [math]::Min($paddedLines.Count, $windowHeight - 1)

        if ($parkY -lt 0) { $parkY = 0 }

        [Console]::SetCursorPosition(0, $parkY)

    } catch {}

}

# 注意：此函数目前没有任何调用点（保留备用）。
# 它内部仍是旧的 ConsoleOutputHelper 写法（[short] / [ushort] 在 Windows PowerShell 5.1 里
# 无法解析，CHAR_INFO 也缺少 CharSet.Unicode），直接启用会静默失败；
# 将来若要使用，请改用上面的 Write-Frame（同样的按屏幕列构造单元格、整行写入的逻辑）。

function Update-FieldChanges($fieldInfos) {

    if ($fieldInfos -eq $null -or $fieldInfos.Count -eq 0) { return 0 }

    # 初始化底层控制台输出 API

    if ($script:consoleOutputHelperInitialized -eq $null) {

        $script:consoleOutputHelperInitialized = $true

        try {

            Add-Type -TypeDefinition @"

using System;

using System.Runtime.InteropServices;

public class ConsoleOutputHelper {

    public const int STD_OUTPUT_HANDLE = -11;

    [DllImport("kernel32.dll", SetLastError = true)]

    public static extern IntPtr GetStdHandle(int nStdHandle);

    [DllImport("kernel32.dll", SetLastError = true)]

    public static extern bool WriteConsoleOutput(IntPtr hConsoleOutput, CHAR_INFO[] lpBuffer, COORD dwBufferSize, COORD dwBufferCoord, ref SMALL_RECT lpWriteRegion);

    [StructLayout(LayoutKind.Explicit)]

    public struct CHAR_INFO {

        [FieldOffset(0)] public char UnicodeChar;

        [FieldOffset(2)] public ushort Attributes;

    }

    [StructLayout(LayoutKind.Sequential)]

    public struct COORD {

        public short X;

        public short Y;

        public COORD(short x, short y) { X = x; Y = y; }

    }

    [StructLayout(LayoutKind.Sequential)]

    public struct SMALL_RECT {

        public short Left;

        public short Top;

        public short Right;

        public short Bottom;

        public SMALL_RECT(short l, short t, short r, short b) { Left = l; Top = t; Right = r; Bottom = b; }

    }

}

"@ -ErrorAction Stop

            $script:consoleOutputHelperLoaded = $true

        } catch {

            $script:consoleOutputHelperLoaded = $false

        }

    }

    if (-not $script:consoleOutputHelperLoaded) { return 0 }

    if ($script:lastFieldState -eq $null) { $script:lastFieldState = @{} }

    function _fieldColorAttr($colorName) {

        try { return [int][Enum]::Parse([System.ConsoleColor], $colorName) } catch { return 7 }

    }

    $hOut = [ConsoleOutputHelper]::GetStdHandle([ConsoleOutputHelper]::STD_OUTPUT_HANDLE)

    # 清理已经不在当前帧中的旧字段（例如排序后行号改变）

    $currentKeys = New-Object System.Collections.Generic.HashSet[string]

    foreach ($field in $fieldInfos) { [void]$currentKeys.Add("$($field.Row):$($field.Name)") }

    $oldKeys = @($script:lastFieldState.Keys) | Where-Object { -not $currentKeys.Contains($_) }

    foreach ($key in $oldKeys) { $script:lastFieldState.Remove($key) }

    $updatedCount = 0

    foreach ($field in $fieldInfos) {

        $key = "$($field.Row):$($field.Name)"

        $last = $script:lastFieldState[$key]

        if ($last -eq $null -or $last.Text -ne $field.Text -or $last.Fg -ne $field.Fg) {

            $fg = _fieldColorAttr $field.Fg

            $bg = _fieldColorAttr 'Black'

            $attr = [ushort]($fg + ($bg -shl 4))

            $text = Pad-Right $field.Text $field.Width

            $chars = $text.ToCharArray()

            $buf = New-Object ConsoleOutputHelper+CHAR_INFO[] $chars.Length

            for ($i = 0; $i -lt $chars.Length; $i++) {

                $buf[$i].UnicodeChar = $chars[$i]

                $buf[$i].Attributes = $attr

            }

            $bufSize = New-Object ConsoleOutputHelper+COORD($chars.Length, 1)

            $bufCoord = New-Object ConsoleOutputHelper+COORD(0, 0)

            $right = $field.X + $field.Width - 1

            $rect = New-Object ConsoleOutputHelper+SMALL_RECT([short]$field.X, [short]$field.Row, [short]$right, [short]$field.Row)

            [void][ConsoleOutputHelper]::WriteConsoleOutput($hOut, $buf, $bufSize, $bufCoord, [ref]$rect)

            $script:lastFieldState[$key] = $field

            $updatedCount++

        }

    }

    return $updatedCount

}

function Print-Quotes($stockRows, $indexRows, $showIndex, $stealth, $cursorIndex = -1, $buttonBarText = $null) {

    $now = if ($script:lastQuoteSuccess -ne $null) { $script:lastQuoteSuccess.ToString('HH:mm:ss') } else { '--:--:--' }

    $state = if ($script:quoteStatus) { ' | ' + $script:quoteStatus } else { '' }

    if ($stealth) {

        $frameLines = @()

        $frameLines += "System Monitor  |  Data: $now (Refresh: ${interval}s)$state"

        if ($stockRows.Count -gt 0) {

            Print-StockRows $stockRows $stealth $cursorIndex ([ref]$frameLines)

        }

        $rowsToShow = @()

        $rowsToShow = $indexRows | Where-Object { $_.f14 -eq '上证指数' -or $_.f14 -eq '深证成指' }

        if ($rowsToShow.Count -gt 0) {

            Print-IndexRows $rowsToShow $stealth ([ref]$frameLines)

            $marketInfo = Get-MarketTotalAmounts $indexRows

            Print-MarketTotal $marketInfo $stealth ([ref]$frameLines)

        }

        # 把按钮栏也纳入统一缓冲区，避免单独 Write-Host 重绘时闪烁

        if (-not [string]::IsNullOrEmpty($buttonBarText)) {

            $frameLines += ""

            $frameLines += $buttonBarText

        }

        Write-Frame $frameLines 'Gray'

    } else {

        Write-Host "自选股行情 ($($stockRows.Count) 只)    行情时间: $now (${interval} 秒自动刷新)$state"

        if ($stockRows.Count -gt 0) {

            Print-StockRows $stockRows $stealth $cursorIndex

        }

        $rowsToShow = @()

        if ($showIndex) {

            $rowsToShow = $indexRows

        }

        if ($rowsToShow.Count -gt 0) {

            Print-IndexRows $rowsToShow $stealth

            $marketInfo = Get-MarketTotalAmounts $indexRows

            Print-MarketTotal $marketInfo $stealth

        }

    }

}

function Enter-DepthMode($row, $stockRows, $startIndex) {

    $script:depthMode = $true

    $script:depthRow = $row

    $script:depthSecid = "$($row.f13).$($row.f12)"

    $script:depthStockRows = $stockRows

    $script:depthStockIndex = $startIndex

    $script:depthNextRefreshTime = (Get-Date).AddSeconds(10)

    $script:lastDepthVolume = $null

    $script:depthViewDrawn = $false

    $script:depthViewLast = $null

    $script:lastFrameLines = $null

    if (-not $stealth) { Clear-Host }

}

function Exit-DepthMode {

    $script:depthMode = $false

    $script:depthSecid = $null

    $script:depthPending = $null

    $script:depthRow = $null

    $script:depthStockRows = $null

    $script:depthStockIndex = $null

    $script:depthNextRefreshTime = $null

    $script:lastDepthVolume = $null

    $script:depthViewDrawn = $false

    $script:depthViewLast = $null

    $script:lastFrameLines = $null

    if (-not $stealth) { Clear-Host }

}

function Get-SealAmount($depth, $row) {

    # 若最新价触及涨停/跌停，计算并返回封板金额（元）。

    if ($depth.price -eq $null) { return $null, $null }

    $price = [double]$depth.price

    $name = "$($depth.name)"

    $code = "$($depth.code)"

    $isETF = $name -and $name.ToUpper().Contains('ETF')

    if ($isETF) { return $null, $null }

    if ($depth.prev_close -eq $null -or $depth.prev_close -eq 0) { return $null, $null }

    $prevClose = [double]$depth.prev_close

    $limitRatio = 0.10

    if ($name -match 'ST') { $limitRatio = 0.05 }

    elseif ($code -match '^688') { $limitRatio = 0.20 }

    elseif ($code -match '^(300|301)') { $limitRatio = 0.20 }

    elseif ($code -match '^(8|4)') { $limitRatio = 0.30 }

    $digits = 2

    $eps = [math]::Pow(10, -$digits - 1)

    $limitUp = [math]::Round($prevClose * (1 + $limitRatio), $digits)

    $limitDown = [math]::Round($prevClose * (1 - $limitRatio), $digits)

    $bids = $depth.bids

    $asks = $depth.asks

    if ([math]::Abs($price - $limitUp) -lt $eps) {

        if ($bids -and $bids.Count -gt 0 -and $bids[0][0] -ne $null -and $bids[0][1] -ne $null) {

            $sealPrice = [double]$bids[0][0]

            $sealVol = [int]$bids[0][1]

            return '封板', ($sealPrice * $sealVol * 100)

        }

    }

    if ([math]::Abs($price - $limitDown) -lt $eps) {

        if ($asks -and $asks.Count -gt 0 -and $asks[0][0] -ne $null -and $asks[0][1] -ne $null) {

            $sealPrice = [double]$asks[0][0]

            $sealVol = [int]$asks[0][1]

            return '封板', ($sealPrice * $sealVol * 100)

        }

    }

    return $null, $null

}


function Show-DepthView($row, $stealth) {

    $secid = "$($row.f13).$($row.f12)"

    # 优先用缓存（当天磁盘/内存）或骨架立即渲染；网络获取放后台 job，数据回来后增量刷新

    $d = $script:depthCache[$secid]

    if ($d -eq $null) { $d = Get-DepthSkeleton $row }

    Start-DepthUpdate $secid $row.f14

    $name = $d.name

    $code = $d.code

    $digits = if ($name -and $name.ToUpper().Contains('ETF')) { 3 } else { 2 }

    $hasQuote = $false

    try { if ([double]$d.price -ne 0) { $hasQuote = $true } } catch {}

    $normalFg = if ($stealth) { 'Gray' } else { 'White' }

    $redFg = if ($stealth) { 'Gray' } else { 'Red' }

    $greenFg = if ($stealth) { 'Gray' } else { 'Green' }

    $mainSep = '-' * 57

    $rightSep = '-' * 28

    $lines = @()

    # 标题行：代码 名称 当前价格▲▼ 涨跌额 涨跌幅 现手

    if ($hasQuote) {

        $price = Format-Number $d.price $digits

        $change = Format-Number $d.change $digits

        $pct = Format-Number $d.pct_chg

        if ($d.change -gt 0) { $arrow = '▲'; $headerFg = $redFg }

        elseif ($d.change -lt 0) { $arrow = '▼'; $headerFg = $greenFg }

        else { $arrow = '─'; $headerFg = $normalFg }

        $pricePart = $price + ' ' + $arrow

        $priceCol = Pad-Left $pricePart 10

        $changeCol = Pad-Left $change 9

        $pctCol = Pad-Left ($pct + '%') 7

        $volCol = Pad-Left (Format-LatestVol $row.f30) 11

        $header = (Pad-Right $code 6) + ' ' + (Pad-Right $name 8) + ' ' + $priceCol + ' ' + $changeCol + ' ' + $pctCol + ' ' + $volCol

    } else {

        $headerFg = if ($stealth) { 'Gray' } else { 'DarkGray' }

        $header = (Pad-Right $code 6) + ' ' + (Pad-Right $name 8) + '          未开盘'

    }

    $lines += [PSCustomObject]@{ Text = $header; Color = $headerFg }

    if ($hasQuote) {

        $lines += [PSCustomObject]@{ Text = $mainSep; Color = 'DarkGray' }

        # 涨停跌停

        $isETF = $name -and $name.ToUpper().Contains('ETF')

        $limitUp = $null

        $limitDown = $null

        if (-not $isETF -and $d.prev_close -ne $null -and $d.prev_close -ne 0) {

            $limitRatio = 0.10

            if ($name -match 'ST') { $limitRatio = 0.05 }

            elseif ($code -match '^688') { $limitRatio = 0.20 }

            elseif ($code -match '^(300|301)') { $limitRatio = 0.20 }

            elseif ($code -match '^(8|4)') { $limitRatio = 0.30 }

            $limitUp = [math]::Round($d.prev_close * (1 + $limitRatio), 2)

            $limitDown = [math]::Round($d.prev_close * (1 - $limitRatio), 2)

        }

        # 左侧信息

        $leftLabels = @('均价', '昨收', '今开', '最高', '最低', '涨停', '跌停', '换手', '成交', '量比')

        $highPct = if ($d.high -ne $null -and $d.prev_close -ne $null -and $d.prev_close -ne 0) {

            $pct = [math]::Round(($d.high - $d.prev_close) / $d.prev_close * 100, 2)

            $sign = if ($pct -gt 0) { '+' } else { '' }

            "$sign$pct%"

        } else { '-' }

        $lowPct = if ($d.low -ne $null -and $d.prev_close -ne $null -and $d.prev_close -ne 0) {

            $pct = [math]::Round(($d.low - $d.prev_close) / $d.prev_close * 100, 2)

            $sign = if ($pct -gt 0) { '+' } else { '' }

            "$sign$pct%"

        } else { '-' }

        $leftValues = @(

            (Format-AvgPrice $row $d.amount $d.volume),

            (Format-Number $d.prev_close $digits),

            (Format-Number $d.open $digits),

            (Format-PricePctPair (Format-Number $d.high $digits) $highPct),

            (Format-PricePctPair (Format-Number $d.low $digits) $lowPct),

            (Format-Number $limitUp $digits),

            (Format-Number $limitDown $digits),

            ((Format-Number $row.f8) + '%'),

            ((Format-Yi $row.f6) + '亿'),

            (Format-Number $row.f10)

        )

        # 右侧盘口

        $rightLines = @()

        $hasDepth = $false

        for ($i = 4; $i -ge 0; $i--) {

            $p = Format-Number $d.asks[$i][0] $digits

            $v = Format-Number $d.asks[$i][1] 0

            if ($p -ne '-' -and $v -ne '-') { $hasDepth = $true }

            $rightLines += (Pad-Right "卖$($i + 1)" 4) + ' ' + (Pad-Left $p 10) + ' ' + (Pad-Left $v 12)

        }

        $rightLines += $rightSep

        for ($i = 0; $i -lt 5; $i++) {

            $p = Format-Number $d.bids[$i][0] $digits

            $v = Format-Number $d.bids[$i][1] 0

            if ($p -ne '-' -and $v -ne '-') { $hasDepth = $true }

            $rightLines += (Pad-Right "买$($i + 1)" 4) + ' ' + (Pad-Left $p 10) + ' ' + (Pad-Left $v 12)

        }

        if (-not $hasDepth) {

            $lines += [PSCustomObject]@{ Text = '  暂无盘口数据'; Color = 'DarkGray' }

        } else {

            $sealLabel, $sealAmount = Get-SealAmount $d $row

            for ($i = 0; $i -lt $rightLines.Count; $i++) {

                if ($i -lt $leftLabels.Count) {

                    $left = (Pad-Right $leftLabels[$i] 6) + ' ' + (Pad-Right $leftValues[$i] 16)

                } elseif ($sealLabel -and $sealAmount -ne $null -and $i -eq $leftLabels.Count) {

                    $left = (Pad-Right $sealLabel 6) + ' ' + (Pad-Right ((Format-Yi $sealAmount) + '亿') 16)

                } else {

                    $left = ' ' * 23

                }

                $lines += [PSCustomObject]@{ Text = ($left + '    ' + $rightLines[$i]); Color = $normalFg }

            }

        }

        $lines += [PSCustomObject]@{ Text = $mainSep; Color = 'DarkGray' }

        # 日均线（当天首次计算后放缓存，后续直接复用）

        $mas = Get-DailyMAs $secid

        if ($mas -ne $null) {

            $maLabels = @('均线', '10日', '20日', '30日', '120日', '250日')

            $maVals = @((Format-Number $mas.ma10 $digits), (Format-Number $mas.ma20 $digits), (Format-Number $mas.ma30 $digits), (Format-Number $mas.ma120 $digits), (Format-Number $mas.ma250 $digits))

            $colWidth = 0

            for ($i = 0; $i -lt $maLabels.Count; $i++) {

                $w = Measure-DisplayWidth $maLabels[$i]

                if ($i -gt 0) {

                    $w = [math]::Max($w, (Measure-DisplayWidth $maVals[$i - 1]))

                }

                if ($w -gt $colWidth) { $colWidth = $w }

            }

            $maCells = @('') + $maVals

            # 列内右对齐价格，列间距在不超过 53 字符虚线的前提下尽量拉开

            $maxSep = 4

            $sep = $maxSep

            while ($sep -gt 1 -and ($maLabels.Count * $colWidth + ($maLabels.Count - 1) * $sep) -gt 53) {

                $sep--

            }

            $sepStr = ' ' * $sep

            $maHeader = ($maLabels | ForEach-Object { Pad-Right $_ $colWidth }) -join $sepStr

            $maValues = ($maCells | ForEach-Object { Pad-Left $_ $colWidth }) -join $sepStr

            $lines += [PSCustomObject]@{ Text = $maHeader; Color = $normalFg }

            $lines += [PSCustomObject]@{ Text = $maValues; Color = $normalFg }

            $lines += [PSCustomObject]@{ Text = $mainSep; Color = 'DarkGray' }

        }

        # PE / PB / 市值 / 流通（当天首次计算后放缓存，后续直接复用）

        $pepb = Get-Pepb $secid

        $totalCap, $totalCapUnit = Format-Cap $row.f20

        $floatCap, $floatCapUnit = Format-Cap $row.f21

        if ($pepb -ne $null) {

            $peDisplay = if ($pepb.pe -lt 0) { '-' } else { Format-Number $pepb.pe 2 }

            $pepbLine1 = (Pad-Right '市值' 6) + ' ' + (Pad-Left ($totalCap + $totalCapUnit) 14) + '        ' + (Pad-Right 'PE' 6) + ' ' + (Pad-Left $peDisplay 14)

            $lines += [PSCustomObject]@{ Text = $pepbLine1; Color = $normalFg }

            $pepbLine2 = (Pad-Right '流通' 6) + ' ' + (Pad-Left ($floatCap + $floatCapUnit) 14) + '        ' + (Pad-Right 'PB' 6) + ' ' + (Pad-Left (Format-Number $pepb.pb 2) 14)

            $lines += [PSCustomObject]@{ Text = $pepbLine2; Color = $normalFg }

        } else {

            $capLine = (Pad-Right '市值' 6) + ' ' + (Pad-Left ($totalCap + $totalCapUnit) 14) + '        ' + (Pad-Right '流通' 6) + ' ' + (Pad-Left ($floatCap + $floatCapUnit) 14)

            $lines += [PSCustomObject]@{ Text = $capLine; Color = $normalFg }

        }

        $lines += [PSCustomObject]@{ Text = ''; Color = $normalFg }

    } else {

        $lines += [PSCustomObject]@{ Text = '  暂无行情'; Color = 'DarkGray' }

    }

    $depthTime = if ($d.fetched_at) { $d.fetched_at } else { '等待后台刷新' }

    $lines += [PSCustomObject]@{ Text = "盘口数据时间: $depthTime"; Color = 'DarkGray' }

    # 增量渲染：首次全屏绘制，之后只重写变化的行

    try {

        if (-not $script:depthViewDrawn) {

            Clear-Host

            for ($i = 0; $i -lt $lines.Count; $i++) {

                [Console]::SetCursorPosition(0, $i)

                $text = Truncate-ToWidth $lines[$i].Text ([Console]::WindowWidth - 1)

                $pad = [Console]::WindowWidth - 1 - (Measure-DisplayWidth $text)

                if ($pad -gt 0) { $text += ' ' * $pad }

                Write-Host $text -ForegroundColor $lines[$i].Color -NoNewline

            }

            $script:depthViewDrawn = $true

        } else {

            for ($i = 0; $i -lt $lines.Count; $i++) {

                $prev = $script:depthViewLast[$i]

                if ($prev -eq $null -or $lines[$i].Text -ne $prev.Text -or $lines[$i].Color -ne $prev.Color) {

                    [Console]::SetCursorPosition(0, $i)

                    $text = Truncate-ToWidth $lines[$i].Text ([Console]::WindowWidth - 1)

                    $pad = [Console]::WindowWidth - 1 - (Measure-DisplayWidth $text)

                    if ($pad -gt 0) { $text += ' ' * $pad }

                    Write-Host $text -ForegroundColor $lines[$i].Color -NoNewline

                }

            }

            # 清除旧的、不再使用的行（多清一行，防止旧按键提示残留）

            if ($script:depthViewLast.Count -gt $lines.Count) {

                for ($i = $lines.Count; $i -le $script:depthViewLast.Count; $i++) {

                    [Console]::SetCursorPosition(0, $i)

                    [Console]::Write(' ' * [Console]::WindowWidth)

                }

            }

        }

        # 把光标移到最后一行末尾，保证后续按键提示能接在盘口下方

        $lastIdx = $lines.Count - 1

        [Console]::SetCursorPosition((Measure-DisplayWidth $lines[$lastIdx].Text), $lastIdx)

    } catch {

        # 非控制台环境（如输出被重定向）回退到普通输出

        Clear-Host

        foreach ($line in $lines) {

            Write-Host $line.Text -ForegroundColor $line.Color

        }

        $script:depthViewDrawn = $true

    }

    $script:depthViewLast = $lines

    $script:lastDepthVolume = $d.volume

    return $true

}

function Show-FakeScreen {

    Clear-Host

    $t = Get-Date -Format "HH:mm:ss"

    Write-Host "> npm run build" -ForegroundColor Gray

    Write-Host ""

    Write-Host "> project@1.0.0 build D:\project" -ForegroundColor Gray

    Write-Host ""

    Write-Host "> tsc && vite build" -ForegroundColor Gray

    Write-Host ""

    Write-Host "vite v5.0.0 building for production..." -ForegroundColor Gray

    Write-Host "[12:34:56] 128 modules transformed." -ForegroundColor Gray

    Write-Host "dist/index.html                   0.45 kB" -ForegroundColor Gray

    Write-Host "dist/assets/index-a1b2c3d4.js   142.31 kB" -ForegroundColor Gray

    Write-Host "[12:34:56] built in 3.42s" -ForegroundColor Gray

    Write-Host ""

    Write-Host "[$t] Watching for changes..." -ForegroundColor DarkGray

}

function Get-ButtonBarInfo($stealth, $depthMode = $false) {

    # 返回按钮栏的文本、点击区域和颜色，供统一缓冲区或独立输出使用

    $buttons = if ($depthMode) {

        @(

            @{ Key='R'; Stealth='R refresh'; Normal='R 刷新' },

            @{ Key='Down'; Stealth='↑↓ switch'; Normal='↑↓ 切换' },

            @{ Key='Left'; Stealth='← back'; Normal='← 返回' }

        )

    } else {

        @(

            @{ Key='R'; Stealth='R refresh'; Normal='R 刷新' },

            @{ Key='C'; Stealth='C sort%'; Normal='C 涨跌幅排序' },

            @{ Key='V'; Stealth='V sortVR'; Normal='V 量比排序' },

            @{ Key='B'; Stealth='B hide'; Normal='B 伪装' },

            @{ Key='S'; Stealth='S mode'; Normal='S 切换' },

            @{ Key='Esc'; Stealth='Esc exit'; Normal='Esc 退出' }

        )

    }

    $regions = @()

    $textParts = @()

    $x = 0

    $sep = ' '

    $sepW = Measure-DisplayWidth $sep

    $bg = if ($stealth) { $null } else { 'Gray' }

    $fg = if ($stealth) { 'Gray' } else { 'Black' }

    for ($i = 0; $i -lt $buttons.Count; $i++) {

        $b = $buttons[$i]

        $labelText = if ($stealth) { $b.Stealth } else { $b.Normal }

        $label = "[$labelText]"

        $labelW = Measure-DisplayWidth $label

        $regions += [PSCustomObject]@{

            Key = $b.Key

            StartX = $x

            EndX = $x + $labelW - 1

            Y = -1

        }

        $textParts += $label

        $x += $labelW

        if ($i -lt $buttons.Count - 1) {

            $textParts += $sep

            $x += $sepW

        }

    }

    return [PSCustomObject]@{

        Text = $textParts -join ''

        Regions = $regions

        Fg = $fg

        Bg = $bg

    }

}

function Show-ButtonBar($stealth, $depthMode = $false) {

    $barY = -1

    try { $barY = $host.UI.RawUI.CursorPosition.Y } catch {}

    $info = Get-ButtonBarInfo $stealth $depthMode

    if ($info.Bg -eq $null) {

        Write-Host $info.Text -ForegroundColor $info.Fg -NoNewline

    } else {

        Write-Host $info.Text -ForegroundColor $info.Fg -BackgroundColor $info.Bg -NoNewline

    }

    Write-Host ""

    foreach ($r in $info.Regions) { $r.Y = $barY }

    return $info.Regions

}

function Clear-RightSide($bottomY, $contentWidth) {

    # 清除内容右侧残留：控制台窗口比表格宽时，旧内容会留在右边。

    if (-not $script:hasConsole -or $bottomY -le 0) { return }

    try {

        $width = [Console]::WindowWidth

        if ($width -le $contentWidth) { return }

        $blank = ' ' * ($width - $contentWidth)

        $prevBg = [Console]::BackgroundColor

        try {

            [Console]::BackgroundColor = [ConsoleColor]::Black

            for ($y = 0; $y -lt $bottomY; $y++) {

                [Console]::SetCursorPosition($contentWidth, $y)

                [Console]::Write($blank)

            }

        } finally {

            [Console]::BackgroundColor = $prevBg

        }

    } catch {}

}

function Clear-BottomLeftover($newBottomY) {

    # 清除本帧缩短后底部残留的旧行。

    if (-not $script:hasConsole) { return }

    try {

        $oldBottomY = $script:frameBottomY

        if ($oldBottomY -le $newBottomY) { return }

        $width = [Console]::WindowWidth

        $blank = ' ' * $width

        $lines = $oldBottomY - $newBottomY

        $prevBg = [Console]::BackgroundColor

        try {

            [Console]::BackgroundColor = [ConsoleColor]::Black

            [Console]::SetCursorPosition(0, $newBottomY)

            [Console]::Write(($blank + "`r`n") * $lines)

        } finally {

            [Console]::BackgroundColor = $prevBg

        }

    } catch {}

}

# ---------- 控制台输入（捕获鼠标点击）----------

$script:consoleInputInitialized = $false

$script:hInput = [IntPtr]::Zero

$script:originalConsoleMode = $null

function Initialize-ConsoleInput {

    if ($script:consoleInputInitialized) { return $true }

    try {

        Add-Type -TypeDefinition @"

using System;

using System.Runtime.InteropServices;

public class ConsoleInputHelper {

    public const int STD_INPUT_HANDLE = -10;

    public const uint ENABLE_MOUSE_INPUT = 0x0010;

    public const uint ENABLE_EXTENDED_FLAGS = 0x0080;

    public const uint ENABLE_QUICK_EDIT_MODE = 0x0040;

    public const ushort KEY_EVENT = 1;

    public const ushort MOUSE_EVENT = 2;

    public const uint MOUSE_LEFT_BUTTON = 0x0001;

    [DllImport("kernel32.dll", SetLastError = true)]

    public static extern IntPtr GetStdHandle(int nStdHandle);

    [DllImport("kernel32.dll", SetLastError = true)]

    public static extern bool GetConsoleMode(IntPtr hConsoleHandle, out uint lpMode);

    [DllImport("kernel32.dll", SetLastError = true)]

    public static extern bool SetConsoleMode(IntPtr hConsoleHandle, uint dwMode);

    [DllImport("kernel32.dll", SetLastError = true)]

    public static extern bool GetNumberOfConsoleInputEvents(IntPtr hConsoleInput, out uint lpNumberOfEvents);

    [DllImport("kernel32.dll", SetLastError = true)]

    public static extern bool ReadConsoleInput(IntPtr hConsoleInput, [Out] INPUT_RECORD[] lpBuffer, uint nLength, out uint lpNumberOfEventsRead);

    [StructLayout(LayoutKind.Explicit)]

    public struct INPUT_RECORD {

        [FieldOffset(0)] public ushort EventType;

        [FieldOffset(4)] public KEY_EVENT_RECORD KeyEvent;

        [FieldOffset(4)] public MOUSE_EVENT_RECORD MouseEvent;

    }

    [StructLayout(LayoutKind.Explicit)]

    public struct KEY_EVENT_RECORD {

        [FieldOffset(0)] public int bKeyDown;

        [FieldOffset(4)] public ushort wRepeatCount;

        [FieldOffset(6)] public ushort wVirtualKeyCode;

        [FieldOffset(8)] public ushort wVirtualScanCode;

        [FieldOffset(10)] public char UnicodeChar;

        [FieldOffset(12)] public uint dwControlKeyState;

    }

    [StructLayout(LayoutKind.Explicit)]

    public struct MOUSE_EVENT_RECORD {

        [FieldOffset(0)] public COORD dwMousePosition;

        [FieldOffset(4)] public uint dwButtonState;

        [FieldOffset(8)] public uint dwControlKeyState;

        [FieldOffset(12)] public uint dwEventFlags;

    }

    [StructLayout(LayoutKind.Sequential)]

    public struct COORD {

        public short X;

        public short Y;

    }

}

"@

        -ErrorAction Stop

    } catch {

        try { [void][ConsoleInputHelper] } catch { return $false }

    }

    $h = [ConsoleInputHelper]::GetStdHandle([ConsoleInputHelper]::STD_INPUT_HANDLE)

    if ($h -eq [IntPtr]::Zero -or $h -eq [IntPtr]::new(-1)) { return $false }

    $mode = 0

    if (-not [ConsoleInputHelper]::GetConsoleMode($h, [ref]$mode)) { return $false }

    $script:originalConsoleMode = $mode

    $newMode = ($mode -band -bnot [ConsoleInputHelper]::ENABLE_QUICK_EDIT_MODE) -bor [ConsoleInputHelper]::ENABLE_EXTENDED_FLAGS -bor [ConsoleInputHelper]::ENABLE_MOUSE_INPUT

    if (-not [ConsoleInputHelper]::SetConsoleMode($h, $newMode)) { return $false }

    $script:hInput = $h

    $script:consoleInputInitialized = $true

    return $true

}

function Read-ConsoleInputEvent {

    if (-not $script:consoleInputInitialized) { return $null }

    $events = 0

    if (-not [ConsoleInputHelper]::GetNumberOfConsoleInputEvents($script:hInput, [ref]$events)) { return $null }

    if ($events -eq 0) { return $null }

    $records = New-Object ConsoleInputHelper+INPUT_RECORD[] 1

    $read = 0

    if (-not [ConsoleInputHelper]::ReadConsoleInput($script:hInput, $records, 1, [ref]$read)) { return $null }

    if ($read -eq 0) { return $null }

    $rec = $records[0]

    if ($rec.EventType -eq [ConsoleInputHelper]::KEY_EVENT -and $rec.KeyEvent.bKeyDown -ne 0) {

        $vk = [int]$rec.KeyEvent.wVirtualKeyCode

        if (-not [Enum]::IsDefined([ConsoleKey], $vk)) { return $null }

        return [PSCustomObject]@{

            Type = 'Key'

            Key = [ConsoleKey]$vk

        }

    }

    elseif ($rec.EventType -eq [ConsoleInputHelper]::MOUSE_EVENT) {

        $btn = $rec.MouseEvent.dwButtonState

        $flags = $rec.MouseEvent.dwEventFlags

        if (($btn -band [ConsoleInputHelper]::MOUSE_LEFT_BUTTON) -ne 0 -and $flags -eq 0) {

            return [PSCustomObject]@{

                Type = 'Mouse'

                X = [int]$rec.MouseEvent.dwMousePosition.X

                Y = [int]$rec.MouseEvent.dwMousePosition.Y

                Button = 'Left'

            }

        }

    }

    return $null

}

function Restore-ConsoleInput {

    if ($script:consoleInputInitialized -and $script:hInput -ne [IntPtr]::Zero -and $script:originalConsoleMode -ne $null) {

        [void][ConsoleInputHelper]::SetConsoleMode($script:hInput, $script:originalConsoleMode)

    }

}

# ---------- 参数解析 ----------

$script:HelpLines = @(
    '用法: watch [刷新秒数] [--index on|off] [--stealth on|off] [--hy on|off] [--once] [-h]',
    '',
    '参数:',
    '  刷新秒数          自动刷新间隔，默认 15 秒',
    '  --index on|off    是否显示指数方块，默认 off',
    '                    （隐蔽模式固定显示上证指数、深证成指）',
    '  --stealth on|off  隐蔽模式（英文、朴素窗口标题），默认 on',
    '  --hy on|off       获取并显示半年涨跌，默认 off',
    '  --once            只取一次数据后退出',
    '  -h, --help        显示此帮助信息',
    '',
    'Windows 下命令为 watch.cmd；macOS / Linux 下为 ./watch，参数完全相同。',
    '',
    '列表页快捷键:',
    '  Esc        退出程序',
    '  R          立即刷新行情',
    '  C          按涨跌幅排序（高→低，再按一次恢复）',
    '  V          按量比排序（高→低，再按一次恢复）',
    '  S          切换隐蔽模式 / 普通模式',
    '  B / F9     切换 npm 构建日志外观 / 正常行情',
    '  ↑ / ↓      移动光标选择股票',
    '  → / Enter  进入选中股票的盘口详情',
    '',
    '盘口详情页快捷键:',
    '  R          刷新盘口',
    '  ↑ / ↓      切换到上一只 / 下一只自选股盘口',
    '  ← / Esc    返回列表',
    '  Ctrl+C     强制退出'
)

function Show-Help([bool]$ToError = $false) {
    foreach ($line in $script:HelpLines) {
        if ($ToError) { [Console]::Error.WriteLine($line) }
        else { Write-Output $line }
    }
}

function Fail-Usage([string]$Message) {
    [Console]::Error.WriteLine("错误: $Message")
    [Console]::Error.WriteLine("")
    Show-Help $true
    exit 2
}

function ConvertTo-OptBool([string]$Value) {
    switch ($Value.Trim().ToLowerInvariant()) {
        'on'    { return $true }
        'true'  { return $true }
        '1'     { return $true }
        'yes'   { return $true }
        'y'     { return $true }
        'off'   { return $false }
        'false' { return $false }
        '0'     { return $false }
        'no'    { return $false }
        'n'     { return $false }
        default { return $null }
    }
}

if (@($args) -contains '--help' -or @($args) -contains '-h') {
    Show-Help $false
    exit 0
}

$interval = 15
$showIndex = $false
$stealth = $true
$fetchHy = $false
$once = $false

$i = 0
while ($i -lt $args.Count) {
    $arg = [string]$args[$i]

    if ($arg -eq '--once' -or $arg -eq '-once') {
        $once = $true
        $i++
        continue
    }

    if ($arg.StartsWith('-')) {
        $optName = $arg.TrimStart([char]'-')
        $optValue = $null
        $eqIndex = $optName.IndexOf('=')
        if ($eqIndex -ge 0) {
            $optValue = $optName.Substring($eqIndex + 1)
            $optName = $optName.Substring(0, $eqIndex)
        }
        $optName = $optName.ToLowerInvariant()
        if (@('index', 'stealth', 'hy') -notcontains $optName) {
            Fail-Usage "无法识别的参数: $arg"
        }
        if ($null -eq $optValue) {
            $i++
            if ($i -ge $args.Count -or ([string]$args[$i]).StartsWith('-')) {
                Fail-Usage "--$optName 需要 on 或 off"
            }
            $optValue = [string]$args[$i]
        }
        $optBool = ConvertTo-OptBool $optValue
        if ($null -eq $optBool) {
            Fail-Usage "--$optName 的值必须是 on 或 off（收到: $optValue）"
        }
        switch ($optName) {
            'index'   { $showIndex = $optBool }
            'stealth' { $stealth = $optBool }
            'hy'      { $fetchHy = $optBool }
        }
        $i++
        continue
    }

    $parsedInt = 0
    if (-not [int]::TryParse($arg, [ref]$parsedInt)) {
        Fail-Usage "无法识别的参数: $arg"
    }
    if ($parsedInt -le 0) {
        Fail-Usage "刷新秒数必须大于 0（收到: $arg）"
    }
    $interval = $parsedInt
    $i++
}

# ---------- 初始化 ----------

Load-SecidCache

Load-HyCache

Load-MarketTotalCache

Load-MaCache

Load-PepbCache

Load-DepthCache

$hasConsole = $false

try {

    [void][Console]::CursorVisible

    $hasConsole = $true

} catch {

    $hasConsole = $false

}

$script:hasConsole = $hasConsole

if ($hasConsole -and $stealth) {

    try { $host.ui.RawUI.WindowTitle = "System" } catch {}

}

# 上一帧底部行号，用于清除本帧缩短后留下的旧行
$script:frameBottomY = 0

# 上一帧内容，用于 Stealth 模式差异重绘，只改写变化的行
$script:lastFrameLines = $null

$script:lastWindowWidth = if ($hasConsole) { [Console]::WindowWidth } else { 0 }

$script:lastWindowHeight = if ($hasConsole) { [Console]::WindowHeight } else { 0 }

# ---------- 主循环 ----------

if ($once) {

    $stockRows, $indexRows, $err = Get-Quotes $stealth

    if ($err) {

        [Console]::Error.WriteLine($err)

        exit 1

    }

    $script:lastQuoteSuccess = Get-Date

    foreach ($row in @($stockRows) + @($indexRows)) {

        $script:quoteSuccessTimes["$($row.f13).$($row.f12)"] = $script:lastQuoteSuccess

    }

    if ($indexRows.Count -gt 0) {

        [void](Start-MarketTotalUpdate @('1.000001', '0.399001', '0.899050'))

        while ($script:marketTotalJob -ne $null -and $script:marketTotalJob.State -eq 'Running') {

            Start-Sleep -Milliseconds 50

        }

        $null = Complete-MarketTotalUpdate

    }

    Apply-HalfYearPct $stockRows

    Print-Quotes $stockRows $indexRows $showIndex $stealth 0

    if ($fetchHy) {

        Update-HalfYearCache $stockRows

    }

    exit 0

}

$initErr = Initialize-Quotes $stealth

if ($initErr) {

    Write-Host $initErr

    exit 1

}

try {

    if ($hasConsole) { [Console]::CursorVisible = $false }

    $first = $true

    $bossMode = $false

    $sortState = @{ c = 0; v = 0 }  # 0=原样, 1=从高到低

    $cursorIndex = 0                  # 游标默认在首条数据

    $depthMode = $false               # 是否处于盘口详情模式

    $depthSecid = $null

    $depthRow = $null

    $depthNextRefreshTime = $null

    $lastDepthVolume = $null

    :main while ($true) {

        try {

            if ($hasConsole -and ([Console]::WindowWidth -ne $script:lastWindowWidth -or [Console]::WindowHeight -ne $script:lastWindowHeight)) {

                $script:lastWindowWidth = [Console]::WindowWidth

                $script:lastWindowHeight = [Console]::WindowHeight

                $script:lastFrameLines = $null

                $script:depthViewDrawn = $false

                $script:depthViewLast = $null

                Clear-Host

            }

            $null = Complete-QuoteUpdate

            $null = Complete-DepthUpdate

            $null = Complete-MarketTotalUpdate

            if ($first) {

                if (-not $stealth) { Clear-Host }

                $first = $false

            } elseif ($hasConsole -and -not $bossMode -and -not $depthMode) {

                try {

                    [Console]::SetCursorPosition(0, 0)

                } catch {

                    if (-not $stealth) { Clear-Host }

                }

            } elseif (-not $hasConsole) {

                Clear-Host

            }

            if ($bossMode) {

                Show-FakeScreen

            } elseif ($depthMode) {

                if ((Get-Date) -ge $depthNextRefreshTime) {

                    $depthNextRefreshTime = (Get-Date).AddSeconds(10)

                }

                $null = Show-DepthView $depthRow $stealth

            } else {

                $stockRows = @(Get-CachedRows $script:stockQuoteCache $script:stockSecids)

                $indexRows = @(Get-CachedRows $script:indexQuoteCache $script:indexSecids)

                Apply-HalfYearPct $stockRows

                $displayRows = Get-SortedRows $stockRows $sortState

                if ($cursorIndex -lt 0) { $cursorIndex = 0 }

                if ($cursorIndex -ge $displayRows.Count) { $cursorIndex = [math]::Max(0, $displayRows.Count - 1) }

                # stealth 模式下把按钮栏也纳入统一缓冲区，避免单独重绘闪烁

                $buttonInfo = $null

                $buttonRegions = $null

                if ($stealth) {

                    $buttonInfo = Get-ButtonBarInfo $stealth $false

                    $buttonRegions = $buttonInfo.Regions

                }

                Print-Quotes $displayRows $indexRows $showIndex $stealth $cursorIndex $buttonInfo.Text

            }

            if ($fetchHy -and $script:stockQuoteCache.Count -gt 0) {

                # 每轮优先应用已完成的后台结果，让缓存数据尽快生效

                Complete-HalfYearUpdate

                # 实际发起新的半年涨跌请求则按 4 倍自动刷新间隔控制

                if ((Get-Date) -ge $script:nextHyStartTime) {

                    Start-HalfYearUpdate $stockRows

                    $script:nextHyStartTime = (Get-Date).AddSeconds($script:hyStartInterval)

                }

            }

            # 应用后台 PE/PB 结果

            $null = Complete-PepbUpdate

        } catch {

            if ($first) { if (-not $stealth) { Clear-Host }; $first = $false }

            Write-Host "[error] display failed: $_"

        }

        # stealth 列表页已经在 Print-Quotes 里统一输出按钮栏；

        # 这里只处理 depth 视图和非 stealth 模式

        if ($depthMode -and -not $bossMode) {

            Write-Host ""

            Write-Host ""

            $buttonRegions = Show-ButtonBar $stealth $depthMode

        } elseif (-not $bossMode -and -not $stealth) {

            Write-Host ""

            $buttonRegions = Show-ButtonBar $stealth $depthMode

            # 清除本帧右侧和底部残留，避免旧内容/错误信息残留在屏幕上

            try {

                $newBottomY = [Console]::CursorPosition.Y

                Clear-RightSide $newBottomY $NORMAL_TABLE_WIDTH

                Clear-BottomLeftover $newBottomY

                $script:frameBottomY = $newBottomY

            } catch {}

        }

        if ($hasConsole) {

            $slept = 0

            :poll while ($slept -lt $interval * 1000) {

                Start-Sleep -Milliseconds 10

                $slept += 10

                if (Complete-QuoteUpdate) { if (-not $bossMode) { break } }

                if (Complete-DepthUpdate) { if ($depthMode -and -not $bossMode) { break } }

                if (Complete-MarketTotalUpdate) { if (-not $bossMode) { break } }

                # 盘口详情每 10 秒自动刷新

                if ($depthMode -and (Get-Date) -ge $depthNextRefreshTime) {

                    $depthNextRefreshTime = (Get-Date).AddSeconds(10)

                    break

                }

                # 盘口后台数据（五档/均线/PEPB）到达后立即重绘，不等自动刷新周期

                if ($depthMode) {

                    if (Complete-MaUpdate) { break }

                    if (Complete-PepbUpdate) { break }

                }

                # 分块增量刷新：到点只更新缓存，不立即 break 重绘，

                # 避免部分行频繁单独闪烁；屏幕在 interval 到期时统一重绘

                if (-not $bossMode -and -not $depthMode -and (Get-Date) -ge $script:nextChunkTime) {

                    Update-NextChunk $stealth

                }

                $inputEvent = $null

                if ([Console]::KeyAvailable) {

                    $inputEvent = [PSCustomObject]@{ Type = 'Key'; Key = [Console]::ReadKey($true).Key }

                }

                if ($inputEvent -ne $null) {

                    $action = $null

                    if ($inputEvent.Type -eq 'Key') {

                        switch ($inputEvent.Key) {

                            ([ConsoleKey]::Escape) { $action = if ($depthMode) { 'DepthExit' } else { 'Exit' } }

                            ([ConsoleKey]::R) { $action = 'Refresh' }

                            ([ConsoleKey]::B) { $action = 'Hide' }

                            ([ConsoleKey]::F9) { $action = 'Hide' }

                            ([ConsoleKey]::S) { $action = if ($depthMode) { $null } else { 'Mode' } }

                            ([ConsoleKey]::C) { $action = if ($depthMode) { $null } else { 'SortPct' } }

                            ([ConsoleKey]::V) { $action = if ($depthMode) { $null } else { 'SortVR' } }

                            ([ConsoleKey]::UpArrow) { $action = if ($depthMode) { 'DepthPrev' } else { 'CursorUp' } }

                            ([ConsoleKey]::DownArrow) { $action = if ($depthMode) { 'DepthNext' } else { 'CursorDown' } }

                            ([ConsoleKey]::RightArrow) { $action = if ($depthMode) { $null } else { 'DepthEnter' } }

                            ([ConsoleKey]::Enter) { $action = if ($depthMode) { $null } else { 'DepthEnter' } }

                            ([ConsoleKey]::LeftArrow) { $action = if ($depthMode) { 'DepthExit' } else { $null } }

                        }

                    } elseif ($inputEvent.Type -eq 'Mouse' -and $inputEvent.Button -eq 'Left') {

                        $clicked = $null

                        if ($buttonRegions -ne $null) {

                            $clicked = $buttonRegions | Where-Object {

                                $inputEvent.Y -eq $_.Y -and $inputEvent.X -ge $_.StartX -and $inputEvent.X -le $_.EndX

                            } | Select-Object -First 1

                        }

                        if ($clicked -ne $null) {

                            switch ($clicked.Key) {

                                'R' { $action = 'Refresh' }

                                'C' { $action = 'SortPct' }

                                'V' { $action = 'SortVR' }

                                'B' { $action = 'Hide' }

                                'S' { $action = 'Mode' }

                                'Esc' { if (-not $depthMode) { $action = 'Exit' } }

                                'Left' { $action = 'DepthExit' }

                                'Up' { $action = 'DepthPrev' }

                                'Down' { $action = 'DepthNext' }

                            }

                        }

                    }

                    if ($action -ne $null) {

                        switch ($action) {

                            'Exit' { break main }

                            'Refresh' {

                                if ($depthMode) {

                                    $script:depthCache.Remove($script:depthSecid)

                                    if ($script:depthUpdateJob -ne $null) {

                                        Stop-Job $script:depthUpdateJob -ErrorAction SilentlyContinue

                                        Remove-Job $script:depthUpdateJob -Force -ErrorAction SilentlyContinue

                                        $script:depthUpdateJob = $null

                                    }

                                    $script:depthJobSecid = $null

                                    $script:depthPending = $null

                                    Start-DepthUpdate $script:depthSecid $script:depthRow.f14

                                    $depthNextRefreshTime = (Get-Date).AddSeconds(10)


                                    break poll

                                } else {

                                    $allSecids = @($script:stockSecids) + @($script:indexSecids)

                                    if (Start-QuoteUpdate $allSecids $stealth 10) {

                                        $script:nextChunkTime = (Get-Date).AddSeconds($script:chunkInterval)

                                    }

                                    break poll

                                }

                            }

                            'Hide' {

                                $bossMode = -not $bossMode

                                if ($bossMode) {

                                    Show-FakeScreen

                                } else {

                                    $script:lastFrameLines = $null

                                    if (-not $stealth) { Clear-Host }

                                }

                                break poll

                            }

                            'Mode' {

                                $stealth = -not $stealth

                                $showIndex = -not $showIndex

                                if ($hasConsole) {

                                    try {

                                        if ($stealth) { $host.ui.RawUI.WindowTitle = "System" }

                                        else { $host.ui.RawUI.WindowTitle = "行情监控" }

                                    } catch {}

                                }

                                # 模式所需字段由后台全量刷新补齐，保留当前缓存和按键响应

                                $allSecids = @($script:stockSecids) + @($script:indexSecids)

                                [void](Start-QuoteUpdate $allSecids $stealth 10)

                                $script:lastFrameLines = $null

                                if (-not $stealth) { Clear-Host }

                                break poll

                            }

                            'SortPct' {

                                $sortState.c = if ($sortState.c -eq 0) { 1 } else { 0 }

                                $sortState.v = 0

                                if (-not $stealth) { Clear-Host }

                                break poll

                            }

                            'SortVR' {

                                $sortState.v = if ($sortState.v -eq 0) { 1 } else { 0 }

                                $sortState.c = 0

                                if (-not $stealth) { Clear-Host }

                                break poll

                            }

                            'CursorUp' {

                                $oldCursor = $cursorIndex

                                $cursorIndex = if ($displayRows.Count -gt 0) { ($cursorIndex - 1 + $displayRows.Count) % $displayRows.Count } else { 0 }

                                Update-Cursor $oldCursor $cursorIndex $displayRows $stealth

                            }

                            'CursorDown' {

                                $oldCursor = $cursorIndex

                                $cursorIndex = if ($displayRows.Count -gt 0) { ($cursorIndex + 1) % $displayRows.Count } else { 0 }

                                Update-Cursor $oldCursor $cursorIndex $displayRows $stealth

                            }

                            'DepthEnter' {

                                if (-not $depthMode -and $displayRows.Count -gt 0) {

                                    Enter-DepthMode $displayRows[$cursorIndex] $displayRows $cursorIndex

                                }

                                break poll

                            }

                            'DepthExit' {

                                if ($depthMode) { Exit-DepthMode }

                                break poll

                            }

                            'DepthPrev' {

                                if ($depthMode -and $script:depthStockRows -ne $null -and $script:depthStockRows.Count -gt 0) {

                                    $script:depthStockIndex = ($script:depthStockIndex - 1 + $script:depthStockRows.Count) % $script:depthStockRows.Count

                                    $script:depthRow = $script:depthStockRows[$script:depthStockIndex]

                                    $script:depthSecid = "$($script:depthRow.f13).$($script:depthRow.f12)"

                                    $script:depthNextRefreshTime = (Get-Date).AddSeconds(10)

                                    $script:depthViewDrawn = $false

                                    $script:depthViewLast = $null

                                }

                                break poll

                            }

                            'DepthNext' {

                                if ($depthMode -and $script:depthStockRows -ne $null -and $script:depthStockRows.Count -gt 0) {

                                    $script:depthStockIndex = ($script:depthStockIndex + 1) % $script:depthStockRows.Count

                                    $script:depthRow = $script:depthStockRows[$script:depthStockIndex]

                                    $script:depthSecid = "$($script:depthRow.f13).$($script:depthRow.f12)"

                                    $script:depthNextRefreshTime = (Get-Date).AddSeconds(10)

                                    $script:depthViewDrawn = $false

                                    $script:depthViewLast = $null

                                }

                                break poll

                            }

                        }

                    }

                }

            }

        } else {

            # 非控制台环境下仍然分块刷新

            $slept = 0

            while ($slept -lt $interval * 1000) {

                Start-Sleep -Milliseconds 100

                $slept += 100

                if ((Get-Date) -ge $script:nextChunkTime) {

                    Update-NextChunk $stealth

                }

            }

        }

    }

} finally {

    Restore-ConsoleInput

    if ($script:quoteUpdateJob -ne $null) {

        Stop-Job $script:quoteUpdateJob -ErrorAction SilentlyContinue

        Remove-Job $script:quoteUpdateJob -Force -ErrorAction SilentlyContinue

        $script:quoteUpdateJob = $null

    }

    if ($script:marketTotalJob -ne $null) {

        Stop-Job $script:marketTotalJob -ErrorAction SilentlyContinue

        Remove-Job $script:marketTotalJob -Force -ErrorAction SilentlyContinue

        $script:marketTotalJob = $null

    }

    if ($script:hyUpdateJob -ne $null) {

        Stop-Job $script:hyUpdateJob -ErrorAction SilentlyContinue

        Remove-Job $script:hyUpdateJob -Force -ErrorAction SilentlyContinue

        $script:hyUpdateJob = $null

    }

    if ($script:pepbUpdateJob -ne $null) {

        Stop-Job $script:pepbUpdateJob -ErrorAction SilentlyContinue

        Remove-Job $script:pepbUpdateJob -Force -ErrorAction SilentlyContinue

        $script:pepbUpdateJob = $null

    }

    if ($script:depthUpdateJob -ne $null) {

        Stop-Job $script:depthUpdateJob -ErrorAction SilentlyContinue

        Remove-Job $script:depthUpdateJob -Force -ErrorAction SilentlyContinue

        $script:depthUpdateJob = $null

    }

    if ($script:maUpdateJob -ne $null) {

        Stop-Job $script:maUpdateJob -ErrorAction SilentlyContinue

        Remove-Job $script:maUpdateJob -Force -ErrorAction SilentlyContinue

        $script:maUpdateJob = $null

    }

    if ($hasConsole) {

        [Console]::CursorVisible = $true

        try { $host.ui.RawUI.WindowTitle = "Windows PowerShell" } catch {}

    }

    Clear-Host

    Write-Host "已退出"

}

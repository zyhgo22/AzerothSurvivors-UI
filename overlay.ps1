# AZS DpsGain —— 透明悬浮层
# 读 state.json，把每张卡的「预计总 DPS 提升」画在对应卡牌上方。
param(
  [string]$StateFile = (Join-Path $PSScriptRoot "state.json"),
  [double]$FontScale = 1.0
)

# 工具箱发过来的实时控制（显示/隐藏、字号、宽度）。
# 单独一个文件，因为 state.json 由 brain.py 每 0.25 秒整份覆盖，写进去会被冲掉。
$ControlFile = Join-Path $PSScriptRoot "control.json"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName Microsoft.VisualBasic

# 写 pid 与自诊断日志（start 会开新控制台吞掉 stdout，所以必须自己落盘）
$script:LogPath = Join-Path $PSScriptRoot "logs\overlay.log"
function Log($msg) {
  try {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Add-Content -Path $script:LogPath -Value $line -Encoding UTF8
  } catch {}
}
try {
  $logDir = Join-Path $PSScriptRoot "logs"
  if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
  $PID | Out-File -FilePath (Join-Path $logDir "overlay.pid") -Encoding ascii -NoNewline
  Log "=== overlay start (pid $PID, ps $($PSVersionTable.PSVersion), scale $FontScale) ==="
} catch {}

# ---------- 找到游戏窗口矩形 ----------
function Get-GameRect {
  try {
    $proc = Get-Process AzerothSurvivors -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($proc) {
      $cond = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::ProcessIdProperty, $proc.Id)
      $win = [Windows.Automation.AutomationElement]::RootElement.FindFirst([Windows.Automation.TreeScope]::Children, $cond)
      if ($win) {
        $b = $win.Current.BoundingRectangle
        if ($b.Width -gt 200 -and $b.Height -gt 200) {
          return New-Object System.Drawing.Rectangle ([int]$b.X), ([int]$b.Y), ([int]$b.Width), ([int]$b.Height)
        }
      }
    }
  } catch {}
  $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  return New-Object System.Drawing.Rectangle $b.X, $b.Y, $b.Width, $b.Height
}

$rect = Get-GameRect
Log ("game rect = " + $rect.X + "," + $rect.Y + " " + $rect.Width + "x" + $rect.Height)
$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
$form.Bounds = $rect
$form.TopMost = $true
$form.ShowInTaskbar = $false
$form.BackColor = [System.Drawing.Color]::FromArgb(255, 0, 255)
$form.TransparencyKey = [System.Drawing.Color]::FromArgb(255, 0, 255)
$form.Text = "AZS DpsGain"

# ---- 让窗口对鼠标完全"隐形" ----
# 不加这段，这个覆盖全屏的置顶窗口会吃掉所有鼠标事件，游戏里点不到任何东西。
Add-Type -Namespace AZS -Name Win32 -MemberDefinition @'
[DllImport("user32.dll", SetLastError = true)]
public static extern int GetWindowLong(IntPtr hWnd, int nIndex);
[DllImport("user32.dll", SetLastError = true)]
public static extern int SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);
'@
$GWL_EXSTYLE = -20
$WS_EX_TRANSPARENT = 0x00000020
$WS_EX_NOACTIVATE = 0x08000000
$WS_EX_TOOLWINDOW = 0x00000080

function Set-ClickThrough($handle) {
  try {
    $ex = [AZS.Win32]::GetWindowLong($handle, $GWL_EXSTYLE)
    # 注意：不要手动加 WS_EX_LAYERED。TransparencyKey 已经让 WinForms 走
    # 分层窗口，再手动设一次会让整窗变成完全透明（徽标彻底看不见）。
    $ex = $ex -bor $WS_EX_TRANSPARENT -bor $WS_EX_NOACTIVATE -bor $WS_EX_TOOLWINDOW
    # 顺便钉住 TOPMOST(0x8)：改扩展样式时如果把它丢了，徽标会被压在游戏/其他窗口后面，
    # 表现就是"明明有数据但屏幕上什么都没有"。
    $ex = $ex -bor 0x00000008
    [void][AZS.Win32]::SetWindowLong($handle, $GWL_EXSTYLE, $ex)
    Log ("click-through enabled, exstyle = 0x{0:X}" -f $ex)
  } catch {
    Log ("click-through FAILED: " + $_.Exception.Message)
  }
}

$script:fontScale = $FontScale
$script:fontMain = New-Object System.Drawing.Font("Microsoft YaHei UI", [float](17 * $script:fontScale), [System.Drawing.FontStyle]::Bold)
$script:fontSub = New-Object System.Drawing.Font("Microsoft YaHei UI", [float](8 * $script:fontScale), [System.Drawing.FontStyle]::Regular)

# 字号在工具箱里改完要立刻生效，所以字体做成可以重建的
function Set-FontScale([double]$scale) {
  if ($scale -lt 0.5) { $scale = 0.5 }
  if ($scale -gt 3.0) { $scale = 3.0 }
  if ([math]::Abs($scale - $script:fontScale) -lt 0.001) { return }
  $script:fontScale = $scale
  try {
    $script:fontMain = New-Object System.Drawing.Font("Microsoft YaHei UI", [float](17 * $scale), [System.Drawing.FontStyle]::Bold)
    $script:fontSub = New-Object System.Drawing.Font("Microsoft YaHei UI", [float](8 * $scale), [System.Drawing.FontStyle]::Regular)
    foreach ($b in $script:badges) {
      $b.Main.Font = $script:fontMain
      $b.Sub.Font = $script:fontSub
    }
    if ($script:hint) { $script:hint.Font = $script:fontSub }
    Rebuild-PanelFonts
    $script:panelSig = ""   # 逼下一帧按新字号重排
    $script:dpsSig = ""
    Log ("font scale -> " + $scale)
  } catch {
    Log ("font rebuild failed: " + $_.Exception.Message)
  }
}

$script:badges = @()
for ($i = 0; $i -lt 4; $i++) {
  $main = New-Object System.Windows.Forms.Label
  $main.AutoSize = $false
  $main.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
  $main.Font = $script:fontMain
  $main.BackColor = [System.Drawing.Color]::FromArgb(18, 22, 30)
  $main.ForeColor = [System.Drawing.Color]::White
  $main.Visible = $false

  $sub = New-Object System.Windows.Forms.Label
  $sub.AutoSize = $false
  $sub.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
  $sub.Font = $script:fontSub
  $sub.BackColor = [System.Drawing.Color]::FromArgb(18, 22, 30)
  $sub.ForeColor = [System.Drawing.Color]::FromArgb(170, 180, 195)
  $sub.Visible = $false

  $form.Controls.Add($main)
  $form.Controls.Add($sub)
  $script:badges += [pscustomobject]@{ Main = $main; Sub = $sub }
}

$hint = New-Object System.Windows.Forms.Label
$hint.AutoSize = $false
$hint.Font = $script:fontSub
$hint.TextAlign = [System.Drawing.ContentAlignment]::MiddleLeft
$hint.BackColor = [System.Drawing.Color]::FromArgb(18, 22, 30)
$hint.ForeColor = [System.Drawing.Color]::FromArgb(150, 160, 175)
$hint.Visible = $false
$form.Controls.Add($hint)
$script:hint = $hint

# ---------------- 流派面板 ----------------
# 一块常驻在屏幕右侧的小黑板：这条流派要哪些技能（学了没）、要哪些首饰
# （穿了没、几级），底部是「整体 / 对精英」两列 DPS。
# 用 Panel + 自绘，而不是一堆 Label —— 每行要「左边名字、右边数值」两种颜色，
# Label 一个控件只能一个颜色，拼不出一行里两段不同颜色的效果。
$script:panelBuild = $null
$script:panelTotals = @("0", "0")
$script:panelOn = $true
$script:panelSig = ""

$script:panel = New-Object System.Windows.Forms.Panel
$script:panel.BackColor = [System.Drawing.Color]::FromArgb(14, 18, 26)
$script:panel.Visible = $false
$form.Controls.Add($script:panel)
# Panel 默认单缓冲，内容一变就闪一下；开双缓冲后是「先画在内存里再整块贴上去」
try {
  $pi = $script:panel.GetType().GetProperty("DoubleBuffered", [System.Reflection.BindingFlags]"Instance,NonPublic")
  $pi.SetValue($script:panel, $true, $null)
} catch {}

$script:fPolTitle = New-Object System.Drawing.Font("Microsoft YaHei UI", [float](9.5 * $script:fontScale), [System.Drawing.FontStyle]::Bold)
$script:fPolHead = New-Object System.Drawing.Font("Microsoft YaHei UI", [float](7.5 * $script:fontScale), [System.Drawing.FontStyle]::Regular)
$script:fPolRow = New-Object System.Drawing.Font("Microsoft YaHei UI", [float](8.5 * $script:fontScale), [System.Drawing.FontStyle]::Regular)
$script:fPolNum = New-Object System.Drawing.Font("Microsoft YaHei UI", [float](10 * $script:fontScale), [System.Drawing.FontStyle]::Bold)
$script:fPolSmall = New-Object System.Drawing.Font("Microsoft YaHei UI", [float](7.5 * $script:fontScale), [System.Drawing.FontStyle]::Regular)

$script:brGold = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 214, 102))
$script:brText = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(230, 234, 240))
$script:brDim = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(132, 142, 158))
$script:brOk = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(110, 225, 135))
$script:brMiss = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(120, 130, 145))
$script:penLine = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(46, 56, 72)), 1
$script:sfRight = New-Object System.Drawing.StringFormat
$script:sfRight.Alignment = [System.Drawing.StringAlignment]::Far
$script:sfRight.LineAlignment = [System.Drawing.StringAlignment]::Near

function Rebuild-PanelFonts {
  $s = $script:fontScale
  try {
    $script:fPolTitle = New-Object System.Drawing.Font("Microsoft YaHei UI", [float](9.5 * $s), [System.Drawing.FontStyle]::Bold)
    $script:fPolHead = New-Object System.Drawing.Font("Microsoft YaHei UI", [float](7.5 * $s), [System.Drawing.FontStyle]::Regular)
    $script:fPolRow = New-Object System.Drawing.Font("Microsoft YaHei UI", [float](8.5 * $s), [System.Drawing.FontStyle]::Regular)
    $script:fPolNum = New-Object System.Drawing.Font("Microsoft YaHei UI", [float](10 * $s), [System.Drawing.FontStyle]::Bold)
    $script:fPolSmall = New-Object System.Drawing.Font("Microsoft YaHei UI", [float](7.5 * $s), [System.Drawing.FontStyle]::Regular)
  } catch {}
}

function Measure-PanelH($b) {
  $s = $script:fontScale
  $rowH = [int](17 * $s)
  $h = 9
  $h += [int]($rowH + [math]::Round(7 * $s))
  $h += [int](7 * $s)
  $h += [int](15 * $s)
  $h += $rowH * (@($b.skills).Count)
  $h += [int](8 * $s)
  $h += [int](15 * $s)
  $h += $rowH * (@($b.gear).Count)
  $h += [int](9 * $s)
  $h += [int](26 * $s)
  return [int]$h
}

$script:panel.Add_Paint({
    param($sender, $e)
    $b = $script:panelBuild
    if ($null -eq $b) { return }
    $g = $e.Graphics
    try {
      $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
      $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::ClearTypeGridFit
    } catch {}
    $s = $script:fontScale
    $W = $sender.ClientSize.Width
    $pad = [int](11 * $s)
    $rowH = [int](17 * $s)
    $y = [int](9 * $s)
    $rightRect = New-Object System.Drawing.RectangleF 0, 0, ([float]($W - $pad)), ([float]$rowH)

    # ---- 标题：职业·流派 + 完成度
    $g.DrawString([string]$b.title, $script:fPolTitle, $script:brGold, [float]$pad, ([float]($y - 2)))
    $cnt = "{0}/{1}" -f $b.done, $b.total
    # 一个都还没凑齐时别用绿色，免得看着像「已达成」
    $cntCol = if ($b.done -ge $b.total) { $script:brGold } elseif ($b.done -eq 0) { $script:brDim } else { $script:brOk }
    $rightRect.Y = [float]($y - 2)
    $g.DrawString($cnt, $script:fPolRow, $cntCol, $rightRect, $script:sfRight)
    $y += [int]($rowH + [math]::Round(7 * $s))
    $g.DrawLine($script:penLine, [float]$pad, [float]$y, [float]($W - $pad), [float]$y)
    $y += [int](7 * $s)

    # ---- 法术
    $g.DrawString("法术", $script:fPolHead, $script:brDim, [float]$pad, [float]$y)
    $y += [int](15 * $s)
    foreach ($r in @($b.skills)) {
      $ok = ([string]$r.state -ne "missing")
      $dotCol = if ($ok) { $script:brOk } else { $script:brMiss }
      $nameCol = if ($ok) { $script:brText } else { $script:brDim }
      $g.DrawString($(if ($ok) { "●" } else { "○" }), $script:fPolRow, $dotCol, [float]$pad, [float]($y - 1))
      $g.DrawString([string]$r.zh, $script:fPolRow, $nameCol, [float]($pad + 13), [float]($y - 1))
      $rightRect.Y = [float]($y - 1)
      $g.DrawString([string]$r.note, $script:fPolRow, $dotCol, $rightRect, $script:sfRight)
      $y += $rowH
    }

    # ---- 首饰
    $y += [int](8 * $s)
    $g.DrawString("首饰", $script:fPolHead, $script:brDim, [float]$pad, [float]$y)
    $y += [int](15 * $s)
    foreach ($r in @($b.gear)) {
      $ok = ([string]$r.state -ne "missing")
      $dotCol = if ($ok) { $script:brOk } else { $script:brMiss }
      $nameCol = if ($ok) { $script:brText } else { $script:brDim }
      $g.DrawString($(if ($ok) { "●" } else { "○" }), $script:fPolRow, $dotCol, [float]$pad, [float]($y - 1))
      $g.DrawString([string]$r.name, $script:fPolRow, $nameCol, [float]($pad + 13), [float]($y - 1))
      $rightRect.Y = [float]($y - 1)
      $g.DrawString([string]$r.bonus, $script:fPolRow, $dotCol, $rightRect, $script:sfRight)
      $y += $rowH
    }

    # ---- 双列 DPS
    $y += [int](9 * $s)
    $g.DrawLine($script:penLine, [float]$pad, [float]$y, [float]($W - $pad), [float]$y)
    $y += [int](6 * $s)
    $half = [int]($W / 2)
    $g.DrawString("整体", $script:fPolHead, $script:brDim, [float]$pad, [float]($y + 3))
    $g.DrawString([string]$script:panelTotals[0], $script:fPolNum, $script:brText, [float]($pad + 30), [float]($y - 1))
    $g.DrawString("精英", $script:fPolHead, $script:brDim, [float]($half + 6), [float]($y + 3))
    $g.DrawString([string]$script:panelTotals[1], $script:fPolNum, $script:brGold, [float]($half + 36), [float]($y - 1))
})

# ---------------- 伤害统计面板 ----------------
# 照着游戏原生那个「造成伤害」框做的：每个技能一行，伤害 + 占比。
# 区别是这里能同时给出「整体」和「只打大怪」两套数，游戏原生只有一个。
$script:dpsBuild = $null
$script:dpsSig = ""
$script:dpsOn = $true

$script:dps = New-Object System.Windows.Forms.Panel
$script:dps.BackColor = [System.Drawing.Color]::FromArgb(14, 18, 26)
$script:dps.Visible = $false
$form.Controls.Add($script:dps)
try {
  $pi = $script:dps.GetType().GetProperty("DoubleBuffered", [System.Reflection.BindingFlags]"Instance,NonPublic")
  $pi.SetValue($script:dps, $true, $null)
} catch {}

function Fmt-Dmg($v) {
  $n = 0.0
  try { $n = [double]$v } catch { return "0" }
  if ($n -ge 100000000) { return ("{0:N2}亿" -f ($n / 100000000)) }
  if ($n -ge 10000) { return ("{0:N1}万" -f ($n / 10000)) }
  return ([string][int]$n)
}

function Measure-DpsH($rows) {
  $s = $script:fontScale
  $rowH = [int](17 * $s)
  $h = 9 + [int]($rowH + [math]::Round(7 * $s)) + [int](7 * $s)
  $h += $rowH * ([array]$rows).Count
  $h += [int](9 * $s) + [int](24 * $s)
  return [int]$h
}

$script:dps.Add_Paint({
    param($sender, $e)
    $d = $script:dpsBuild
    if ($null -eq $d) { return }
    $g = $e.Graphics
    try {
      $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
      $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::ClearTypeGridFit
    } catch {}
    $s = $script:fontScale
    $W = $sender.ClientSize.Width
    $pad = [int](11 * $s)
    $rowH = [int](17 * $s)
    $y = [int](9 * $s)
    $colDmg = [int]($W * 0.70)
    $colShare = $W - $pad

    $g.DrawString("伤害统计", $script:fPolTitle, $script:brGold, [float]$pad, [float]($y - 2))
    $head = New-Object System.Drawing.RectangleF 0, ([float]($y - 2)), ([float]($W - $pad)), ([float]$rowH)
    $g.DrawString([string]$d.total, $script:fPolNum, $script:brText, $head, $script:sfRight)
    $y += [int]($rowH + [math]::Round(7 * $s))
    $g.DrawLine($script:penLine, [float]$pad, [float]$y, [float]($W - $pad), [float]$y)
    $y += [int](7 * $s)

    $i = 0
    foreach ($r in @($d.rows)) {
      $i++
      # 第一名金色，二三名亮白，往后灰一点，跟游戏原生那一栏的感觉接近
      $col = if ($i -eq 1) { $script:brGold }
             elseif ($i -le 3) { $script:brText }
             else { $script:brDim }
      $g.DrawString([string]$i, $script:fPolSmall, $script:brMiss, [float]$pad, [float]($y - 1))
      $g.DrawString([string]$r.name, $script:fPolRow, $col, [float]($pad + 18), [float]($y - 1))
      $r1 = New-Object System.Drawing.RectangleF 0, ([float]($y - 1)), ([float]$colDmg), ([float]$rowH)
      $g.DrawString([string]$r.dmg, $script:fPolRow, $col, $r1, $script:sfRight)
      $r2 = New-Object System.Drawing.RectangleF 0, ([float]($y + 2)), ([float]$colShare), ([float]$rowH)
      $g.DrawString([string]$r.share, $script:fPolSmall, $script:brDim, $r2, $script:sfRight)
      $y += $rowH
    }

    $y += [int](9 * $s)
    $g.DrawLine($script:penLine, [float]$pad, [float]$y, [float]($W - $pad), [float]$y)
    $y += [int](6 * $s)
    $g.DrawString("精英", $script:fPolHead, $script:brDim, [float]$pad, [float]($y + 3))
    $g.DrawString([string]$d.boss, $script:fPolNum, $script:brGold, [float]($pad + 36), [float]($y - 1))
})

function Update-DpsPanel($state) {
  $rows = @()
  $total = "0"
  $boss = "0"
  if ($null -ne $state) {
    foreach ($r in @($state.meter)) {
      if ($null -eq $r) { continue }
      $rows += [pscustomobject]@{
        name = [string]$r.name
        dmg = (Fmt-Dmg $r.dmg)
        share = ("{0:N0}%" -f ([double]$r.share * 100))
      }
      if ($rows.Count -ge 8) { break }
    }
    $total = [string]$state.total_dps
    $boss = [string]$state.boss_dps
  }
  $on = $script:ctrlVisible -and $script:dpsOn -and $rows.Count -gt 0
  if (-not $on) {
    if ($script:dps.Visible) { $script:dps.Visible = $false }
    return
  }
  $sig = (($rows | ForEach-Object { "$($_.name)$($_.dmg)$($_.share)" }) -join "|") + "#" + $total + "#" + $boss
  if ($sig -ne $script:dpsSig) {
    $script:dpsSig = $sig
    $script:dpsBuild = [pscustomobject]@{ rows = $rows; total = $total; boss = $boss }
    $W = $form.ClientSize.Width
    $H = $form.ClientSize.Height
    $pw = [int](300 * $script:fontScale)
    if ($pw -gt ($W * 0.24)) { $pw = [int]($W * 0.24) }
    $ph = Measure-DpsH $rows
    # 放左边：右边留给流派面板，两个不打架
    $x = [int](16 * $script:fontScale)
    $y = [int](($H - $ph) / 2)
    if ($y -lt 16) { $y = 16 }
    $script:dps.Bounds = New-Object System.Drawing.Rectangle $x, $y, $pw, $ph
    $script:dps.Invalidate()
  }
  if (-not $script:dps.Visible) { $script:dps.Visible = $true }
}

function Update-BuildPanel($state) {
  $b = $null
  if ($null -ne $state) { $b = $state.build }
  # 流派面板只在选卡时冒出来：平时打怪不占屏幕，这一条是用户明确要求的
  $on = ($null -ne $b) -and [bool]$b.title -and $script:ctrlVisible -and $script:panelOn -and [bool]$state.visible
  if (-not $on) {
    if ($script:panel.Visible) { $script:panel.Visible = $false }
    return
  }
  # 内容没变就不重排重画，200ms 一次别白白折腾 GDI
  $sig = @(
    [string]$b.title, [string]$b.done, [string]$b.total,
    (($b.skills | ForEach-Object { "$($_.zh)$($_.state)$($_.note)" }) -join "|"),
    (($b.gear | ForEach-Object { "$($_.name)$($_.state)$($_.bonus)" }) -join "|"),
    [string]$state.total_dps, [string]$state.boss_dps
  ) -join "#"
  if ($sig -ne $script:panelSig) {
    $script:panelSig = $sig
    $script:panelBuild = $b
    $script:panelTotals = @([string]$state.total_dps, [string]$state.boss_dps)
    $W = $form.ClientSize.Width
    $H = $form.ClientSize.Height
    # 宽屏用 320；游戏窗口化到 1280 以下时按比例收，否则会吃掉四分之一屏幕
    $pw = [int](340 * $script:fontScale)
    if ($pw -gt ($W * 0.26)) { $pw = [int]($W * 0.26) }
    if ($pw -lt 236) { $pw = [int]([math]::Min(236, $W * 0.32)) }
    $ph = Measure-PanelH $b
    $x = $W - $pw - [int](16 * $script:fontScale)
    $y = [int](($H - $ph) / 2)
    if ($y -lt 16) { $y = 16 }
    $script:panel.Bounds = New-Object System.Drawing.Rectangle $x, $y, $pw, $ph
    $script:panel.Invalidate()
  }
  if (-not $script:panel.Visible) { $script:panel.Visible = $true }
}


function Get-Color($pct, $best) {
  if ($null -eq $pct) { return [System.Drawing.Color]::FromArgb(200, 200, 200) }
  if ($best) { return [System.Drawing.Color]::FromArgb(255, 214, 102) }
  if ($pct -ge 10) { return [System.Drawing.Color]::FromArgb(120, 255, 140) }
  if ($pct -ge 5) { return [System.Drawing.Color]::FromArgb(180, 240, 130) }
  if ($pct -ge 2) { return [System.Drawing.Color]::FromArgb(240, 230, 130) }
  if ($pct -le 0.05) { return [System.Drawing.Color]::FromArgb(150, 150, 150) }
  return [System.Drawing.Color]::FromArgb(210, 190, 160)
}

$script:wasVisible = $false
$script:lastPicked = $false
$script:ctrlVisible = $true
# ---- 缓存：让 tick 里「内容没变就一个控件都不碰」 ----
$script:lastState = $null      # 上一次解析出来的 state.json
$script:stateStamp = ""        # 文件大小 + 修改时间，用来判断要不要重新解析
$script:badgeKey = @("", "", "", "")   # 四个徽标各自的内容指纹
$script:hintKey = ""
$script:anyShown = $false      # 当前屏幕上到底有没有东西，避免反复设 Visible
$script:activated = $false     # 只在第一次弹出时把焦点还给游戏
$script:badgeWidth = [int](270 * $script:fontScale)
$script:badgeHeight = [int](44 * $script:fontScale)

$timer = New-Object System.Windows.Forms.Timer
# 250ms 而不是 200ms：brain 本身就是每 0.25 秒写一次 state.json，
# 读得比写得还勤，只会白解析同一份数据。
$timer.Interval = 250
$timer.Add_Tick({
  # ---- 工具箱的实时开关：改完立刻生效，不用重启悬浮层 ----
  try {
    if (Test-Path $ControlFile) {
      $craw = [System.IO.File]::ReadAllText($ControlFile, [System.Text.Encoding]::UTF8)
      if ($craw) {
        $ctrl = $craw | ConvertFrom-Json
        $script:ctrlVisible = $true
        if ($null -ne $ctrl.visible) { $script:ctrlVisible = [bool]$ctrl.visible }
        if ($null -ne $ctrl.enabled -and -not [bool]$ctrl.enabled) { $script:ctrlVisible = $false }
        if ($null -ne $ctrl.panel) { $script:panelOn = [bool]$ctrl.panel }
        if ($null -ne $ctrl.dps) { $script:dpsOn = [bool]$ctrl.dps }
        if ($null -ne $ctrl.font_scale) { Set-FontScale ([double]$ctrl.font_scale) }
        if ($null -ne $ctrl.badge_width) {
          $script:badgeWidth = [int]([double]$ctrl.badge_width * $script:fontScale)
        }
        if ($null -ne $ctrl.badge_height) {
          $script:badgeHeight = [int]([double]$ctrl.badge_height * $script:fontScale)
        }
      }
    }
  } catch {}

  # ---- 读 state.json ----
  # 以前每个 tick 都无条件 ReadAllText + ConvertFrom-Json。这个文件带着两张伤害表，
  # 十几 KB 的 JSON 每秒解析四次，是「一按开关就卡一下」的元凶。
  # 现在先看文件变没变（大小 + 修改时间），没变就直接复用上一次的解析结果。
  $state = $script:lastState
  try {
    if (Test-Path $StateFile) {
      $fi = New-Object System.IO.FileInfo $StateFile
      $stamp = "{0}|{1}" -f $fi.Length, $fi.LastWriteTimeUtc.Ticks
      if ($stamp -ne $script:stateStamp) {
        $script:stateStamp = $stamp
        $raw = [System.IO.File]::ReadAllText($StateFile, [System.Text.Encoding]::UTF8)
        if ($raw) { $script:lastState = $raw | ConvertFrom-Json }
        $state = $script:lastState
      }
    }
  } catch {}

  if ($null -eq $state) {
    if ($script:anyShown) {
      foreach ($b in $script:badges) { $b.Main.Visible = $false; $b.Sub.Visible = $false }
      $script:hint.Visible = $false
      $script:anyShown = $false
    }
    Update-BuildPanel $null
    Update-DpsPanel $null
    return
  }

  if ($state.status -eq "closed") {
    # 游戏已退出：只隐藏，不退出。以前这里 Close() 会因读到上一次运行残留的
    # status=closed 而让整个悬浮层自杀。
    if ($script:anyShown) {
      foreach ($b in $script:badges) { $b.Main.Visible = $false; $b.Sub.Visible = $false }
      $script:hint.Visible = $false
      $script:anyShown = $false
    }
    Update-BuildPanel $null
    Update-DpsPanel $null
    $script:wasVisible = $false
    return
  }

  # 两块面板跟着游戏活着就一直更新；流派面板内部还要求「正在选卡」
  Update-BuildPanel $state
  Update-DpsPanel $state

  $show = [bool]$state.visible -and $script:ctrlVisible
  if (-not $show) {
    if ($script:anyShown) {
      foreach ($b in $script:badges) { $b.Main.Visible = $false; $b.Sub.Visible = $false }
      $script:hint.Visible = $false
      $script:anyShown = $false
      # 徽标藏起来之后清掉缓存，下次弹出来时按当前内容重排一次
      for ($k = 0; $k -lt 4; $k++) { $script:badgeKey[$k] = "" }
    }
    $script:wasVisible = $false
    return
  }

  $w = $form.ClientSize.Width
  $h = $form.ClientSize.Height
  $bw = $script:badgeWidth
  $bh = $script:badgeHeight
  $sh = [int](19 * $script:fontScale)

  $slots = @($state.slots)
  for ($i = 0; $i -lt 4; $i++) {
    $b = $script:badges[$i]
    if ($i -lt $slots.Count) {
      $s = $slots[$i]
      $cx = [int]($w * [double]$s.x)
      $cy = [int]($h * [double]$s.y)
      $main = [string]$s.main
      $note = [string]$s.note
      $best = [bool]$s.best
      # 只有内容真的变了才动控件。WinForms 每次 set Bounds/Text 都会触发重排 +
      # 重绘，而这个 form 是覆盖全屏的分层窗口，一次重绘就是整整一屏。
      $key = "{0}|{1}|{2}|{3}|{4}|{5}" -f $cx, $cy, $main, $note, $best, ([string]$s.pct)
      if ($script:badgeKey[$i] -ne $key) {
        $script:badgeKey[$i] = $key
        $b.Main.Bounds = New-Object System.Drawing.Rectangle (($cx - [int]($bw / 2))), $cy, $bw, $bh
        $b.Sub.Bounds = New-Object System.Drawing.Rectangle (($cx - [int]($bw / 2))), ($cy + $bh), $bw, $sh
        $b.Main.Text = $main
        $b.Sub.Text = $note
        $b.Main.ForeColor = Get-Color $s.pct $best
      }
      if (-not $b.Main.Visible) { $b.Main.Visible = $true; $b.Sub.Visible = $true }
    } else {
      if ($b.Main.Visible) { $b.Main.Visible = $false; $b.Sub.Visible = $false }
    }
  }

  # 底部那一行常驻信息：DoT 层数决定持续时间卡到底有没有收益，放这儿方便对照
  $extra = ""
  if ($null -ne $state.stats.dots) { $extra = $extra + "  层数" + [string]$state.stats.dots }
  if ($null -ne $state.run_level) { $extra = $extra + "  Lv" + [string]$state.run_level }
  if ($state.class) { $extra = $extra + "  " + [string]$state.class }
  $hintText = "总DPS " + [string]$state.total_dps + "    伤害x" + [string]$state.stats.dmg + "  冷却x" + [string]$state.stats.cd + "  暴击" + [string]([math]::Round([double]$state.stats.crit * 100)) + "%" + $extra
  if ($script:hintKey -ne $hintText) {
    $script:hintKey = $hintText
    $script:hint.Bounds = New-Object System.Drawing.Rectangle 12, ($h - 42), 580, 22
    $script:hint.Text = $hintText
  }
  $script:hint.Visible = $true
  $script:anyShown = $true

  if (-not $script:wasVisible) {
    $script:wasVisible = $true
    # 把键盘焦点还给游戏，否则 1/2/3/4 选卡无效。
    # 只在「刚弹出」这一下做一次，每次 tick 都切焦点会明显卡顿。
    if (-not $script:activated) {
      $script:activated = $true
      try { $null = [Microsoft.VisualBasic.Interaction]::AppActivate("Azeroth Survivors") } catch {}
    }
  }
})
$timer.Start()

# WinForms 会吞掉定时器里的异常，装个钩子把它们记下来，免得又变成"静默退出"
[System.Windows.Forms.Application]::add_ThreadException({
  param($s, $e)
  Log ("tick error: " + $e.Exception.Message)
})

Log "form ready, entering message loop"
[void]$form.Handle
Set-ClickThrough($form.Handle)
$form.Add_Shown({
  Set-ClickThrough($form.Handle)
  $form.TopMost = $true
})
try {
  [System.Windows.Forms.Application]::Run($form)
  Log "message loop exited normally"
} catch {
  Log ("fatal: " + $_.Exception.ToString())
}

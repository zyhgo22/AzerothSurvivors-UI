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

# ---- 预设命中的外发光 ----
# 徽标是 Label，自己画不了光晕；光晕画在 form 这一层（比子控件早画，正好在徽标下面）。
# 四种亮度循环 = 呼吸感，一眼能在三张牌里认出来。
$script:glowRects = @()
$script:glowPhase = 0
$script:glowPens = @()
foreach ($rgb in @(@(120, 86, 22), @(180, 130, 38), @(240, 196, 96), @(180, 130, 38))) {
  $script:glowPens += New-Object System.Drawing.Pen (
    [System.Drawing.Color]::FromArgb(255, $rgb[0], $rgb[1], $rgb[2])), 3
}
$form.Add_Paint({
    param($sender, $e)
    try {
      if ($script:glowRects.Count -eq 0) { return }
      $e.Graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::None
      $pen = $script:glowPens[$script:glowPhase % 4]
      foreach ($r in $script:glowRects) { $e.Graphics.DrawRectangle($pen, $r) }
    } catch {}
})

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
    $script:bossSig = ""
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

  # 第三行：BOSS 战口径。打大怪只剩一个目标，群体卡会明显掉价，
  # 这一行单独列出来，别跟副标题抢地方。
  $boss = New-Object System.Windows.Forms.Label
  $boss.AutoSize = $false
  $boss.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
  $boss.Font = $script:fontSub
  $boss.BackColor = [System.Drawing.Color]::FromArgb(18, 22, 30)
  $boss.ForeColor = [System.Drawing.Color]::FromArgb(150, 163, 157)
  $boss.Visible = $false

  $form.Controls.Add($main)
  $form.Controls.Add($sub)
  $form.Controls.Add($boss)
  $script:badges += [pscustomobject]@{ Main = $main; Sub = $sub; Boss = $boss }
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
# 技能名太长时用省略号收尾，不能任它画到伤害数字底下
$script:sfName = New-Object System.Drawing.StringFormat
$script:sfName.Alignment = [System.Drawing.StringAlignment]::Near
$script:sfName.LineAlignment = [System.Drawing.StringAlignment]::Near
$script:sfName.Trimming = [System.Drawing.StringTrimming]::EllipsisCharacter
$script:sfName.FormatFlags = [System.Drawing.StringFormatFlags]::NoWrap

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

# ---------------- 伤害统计面板（两个，并排） ----------------
# 游戏自带的那个框一次只显示一种：点一下「造成伤害」，想看得切到「首领伤害」，
# 打起来根本腾不出手。所以照它的样子并排画两个，一直挂在左上角：
# 左边整体，右边只算跟大怪交手的那段。样式尽力贴游戏原生那栏。
$script:dpsBuild = $null
$script:bossBuild = $null
$script:dpsSig = ""
$script:bossSig = ""
$script:dpsOn = $true

# 名次配色（1 红 / 2 蓝 / 3 金 / 4 绿 / 5 紫），第六名往后统一压暗
$script:rankBr = @()
foreach ($c in @(
    [System.Drawing.Color]::FromArgb(228, 92, 74),
    [System.Drawing.Color]::FromArgb(86, 152, 226),
    [System.Drawing.Color]::FromArgb(222, 188, 88),
    [System.Drawing.Color]::FromArgb(102, 194, 110),
    [System.Drawing.Color]::FromArgb(166, 114, 230))) {
  $script:rankBr += (New-Object System.Drawing.SolidBrush $c)
}
$script:brBoxBg = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(244, 16, 13, 8))
$script:brBoxHead = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 40, 29, 16))
$script:penBoxEdge = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(255, 76, 58, 33)), 1
$script:penGrip = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(90, 150, 124, 78)), 1
$script:brBoxTitle = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 226, 194, 118))
$script:brBoxDim = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 148, 136, 116))
$script:brBoxX = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(170, 150, 122, 82))

function Fmt-Dmg($v) {
  $n = 0.0
  try { $n = [double]$v } catch { return "0" }
  if ($n -ge 100000000) { return ("{0:N2}亿" -f ($n / 100000000)) }
  if ($n -ge 10000) { return ("{0:N1}万" -f ($n / 10000)) }
  return ([string][int]$n)
}

function Measure-StatH([int]$n) {
  $s = $script:fontScale
  if ($n -lt 1) { $n = 1 }
  $h = [int](23 * $s) + [int](6 * $s) + [int](17 * $s) * $n + [int](8 * $s)
  return [int]$h
}

# 一块统计框：标题栏（▼ 名字 … DPS ×）+ 若干行「名次 名字 伤害 占比」
function Draw-StatBox($g, [int]$W, [int]$H, $title, $dpsText, $rows, $emptyText) {
  $s = $script:fontScale
  $pad = [int](11 * $s)
  $titleH = [int](23 * $s)
  $rowH = [int](17 * $s)

  $g.FillRectangle($script:brBoxBg, 0, 0, $W, $H)
  $g.FillRectangle($script:brBoxHead, 1, 1, ($W - 2), $titleH)
  $g.DrawLine($script:penBoxEdge, 1, ($titleH + 1), ($W - 2), ($titleH + 1))
  $g.DrawRectangle($script:penBoxEdge, 0, 0, ($W - 1), ($H - 1))

  $ty = [int](($titleH - 15 * $s) / 2)
  $g.DrawString([string][char]0x25BC, $script:fPolSmall, $script:brBoxDim, [float]($pad - 3), [float]($ty + 1))
  $g.DrawString([string]$title, $script:fPolTitle, $script:brBoxTitle, [float]($pad + 12), [float]($ty - 2))
  $head = New-Object System.Drawing.RectangleF 0, ([float]($ty + 2)), ([float]($W - $pad - 20 * $s)), ([float]$titleH)
  $g.DrawString([string]$dpsText, $script:fPolSmall, $script:brBoxDim, $head, $script:sfRight)
  $g.DrawString([string][char]0x00D7, $script:fPolRow, $script:brBoxX, [float]($W - 15 * $s), [float]($ty - 1))

  $y = $titleH + [int](5 * $s)
  if (@($rows).Count -eq 0) {
    $g.DrawString([string]$emptyText, $script:fPolRow, $script:brBoxDim, [float]$pad, [float]$y)
    return
  }

  $colPct = $W - $pad
  $colDmg = $W - $pad - [int](50 * $s)
  $nameX = $pad + [int](11 * $s)
  $nameW = $colDmg - $nameX - [int](6 * $s)
  $i = 0
  foreach ($r in @($rows)) {
    $i++
    $br = if ($i -le $script:rankBr.Count) { $script:rankBr[$i - 1] } else { $script:brBoxDim }
    $g.DrawString([string]$i, $script:fPolSmall, $br, [float]($pad - 1), [float]($y + 2))
    $nr = New-Object System.Drawing.RectangleF ([float]$nameX), ([float]$y), ([float]$nameW), ([float]$rowH)
    $g.DrawString([string]$r.name, $script:fPolRow, $br, $nr, $script:sfName)
    $rect = New-Object System.Drawing.RectangleF 0, ([float]$y), ([float]$colDmg), ([float]$rowH)
    $g.DrawString([string]$r.dmg, $script:fPolRow, $script:brText, $rect, $script:sfRight)
    $rect2 = New-Object System.Drawing.RectangleF 0, ([float]($y + 1)), ([float]$colPct), ([float]$rowH)
    $g.DrawString([string]$r.share, $script:fPolSmall, $script:brBoxDim, $rect2, $script:sfRight)
    $y += $rowH
  }
}

# 两个统计框的位置和大小存在自己这个文件里，不写进 config.json ——
# 那边是工具箱管的，它一保存整份覆盖，放进去会被冲掉。
$script:boxFile = Join-Path $PSScriptRoot "box_layout.json"
$script:boxLayout = @{}

function Load-BoxLayout {
  $script:boxLayout = @{}
  try {
    if (Test-Path $script:boxFile) {
      $j = [System.IO.File]::ReadAllText($script:boxFile, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
      foreach ($k in @("dps", "boss")) {
        $v = $j.$k
        if ($null -ne $v) {
          $script:boxLayout[$k] = @{
            x = [int]$v.x; y = [int]$v.y; w = [int]$v.w; h = [int]$v.h; auto = [bool]$v.auto
          }
        }
      }
    }
  } catch { $script:boxLayout = @{} }
}

function Save-BoxLayout {
  try {
    $o = [ordered]@{}
    foreach ($k in @("dps", "boss")) {
      $v = $script:boxLayout[$k]
      if ($null -ne $v) {
        $o[$k] = [ordered]@{ x = $v.x; y = $v.y; w = $v.w; h = $v.h; auto = $v.auto }
      }
    }
    $json = ConvertTo-Json $o -Depth 3
    [System.IO.File]::WriteAllText($script:boxFile, $json, (New-Object System.Text.UTF8Encoding $false))
  } catch {}
}

# 换了显示器 / 分辨率之后，存下来的位置可能跑到屏幕外面，这时候回落到默认位置
function Test-OnScreen([int]$x, [int]$y) {
  foreach ($s in [System.Windows.Forms.Screen]::AllScreens) {
    $b = $s.Bounds
    if (($x -ge ($b.X - 40)) -and ($x -le ($b.X + $b.Width - 60)) -and
        ($y -ge ($b.Y - 10)) -and ($y -le ($b.Y + $b.Height - 40))) {
      return $true
    }
  }
  return $false
}

Load-BoxLayout

function New-StatWindow([string]$key, [string]$title) {
  $w = New-Object System.Windows.Forms.Form
  $w.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
  $w.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
  $w.ShowInTaskbar = $false
  $w.TopMost = $true
  $w.BackColor = [System.Drawing.Color]::FromArgb(16, 13, 8)
  $w.Text = "AZS-Stat-" + $key
  $w.Tag = $key
  $w.MinimumSize = New-Object System.Drawing.Size ([int](150 * $script:fontScale)), ([int](56 * $script:fontScale))
  try {
    $pi = $w.GetType().GetProperty("DoubleBuffered", [System.Reflection.BindingFlags]"Instance,NonPublic")
    $pi.SetValue($w, $true, $null)
  } catch {}
  return $w
}

# 拖动 / 缩放：
#   按住空白处拖 = 挪位置；按住右下角那块（会变成斜箭头）拖 = 改大小；
#   双击 = 把高度恢复成「跟着内容走」。
# 这两个框是独立小窗口，不是覆盖全屏的那层，所以能直接接收鼠标，
# 但加了 WS_EX_NOACTIVATE，点它不会把游戏窗口的焦点抢走（不然按 1/2/3 选卡就废了）。
$script:boxDrag = $null

function Attach-StatWindow($w) {
  $w.Add_HandleCreated({
      param($s, $e)
      try {
        $ex = [AZS.Win32]::GetWindowLong($s.Handle, $GWL_EXSTYLE)
        $ex = $ex -bor $WS_EX_NOACTIVATE -bor $WS_EX_TOOLWINDOW -bor 0x00000008
        [void][AZS.Win32]::SetWindowLong($s.Handle, $GWL_EXSTYLE, $ex)
      } catch {}
  })

  $w.Add_MouseDown({
      param($s, $e)
      if ($e.Button -ne [System.Windows.Forms.MouseButtons]::Left) { return }
      $mode = "move"
      if ((($s.ClientSize.Width - $e.X) -le 18) -and (($s.ClientSize.Height - $e.Y) -le 18)) {
        $mode = "size"
      }
      $script:boxDrag = @{
        win = $s; mode = $mode
        pt = [System.Windows.Forms.Cursor]::Position
        w0 = $s.Width; h0 = $s.Height
      }
      $s.Capture = $true
  })

  $w.Add_MouseMove({
      param($s, $e)
      $d = $script:boxDrag
      if ($null -eq $d) {
        if ((($s.ClientSize.Width - $e.X) -le 18) -and (($s.ClientSize.Height - $e.Y) -le 18)) {
          $s.Cursor = [System.Windows.Forms.Cursors]::SizeNWSE
        } else {
          $s.Cursor = [System.Windows.Forms.Cursors]::SizeAll
        }
        return
      }
      if ($d.win -ne $s) { return }
      $p = [System.Windows.Forms.Cursor]::Position
      if ($d.mode -eq "move") {
        $s.Left = $s.Left + ($p.X - $d.pt.X)
        $s.Top = $s.Top + ($p.Y - $d.pt.Y)
        $d.pt = $p
      } else {
        $nw = [int][math]::Max(150 * $script:fontScale, $d.w0 + ($p.X - $d.pt.X))
        $nh = [int][math]::Max(56 * $script:fontScale, $d.h0 + ($p.Y - $d.pt.Y))
        $s.Size = New-Object System.Drawing.Size $nw, $nh
      }
  })

  $w.Add_MouseUp({
      param($s, $e)
      $d = $script:boxDrag
      $script:boxDrag = $null
      $s.Capture = $false
      if ($null -eq $d) { return }
      $k = [string]$s.Tag
      # 拉过右下角就锁定高度，之后行数变了也不自动缩，免得跟手打架
      $auto = [bool](($script:boxLayout[$k] | ForEach-Object { $_.auto }))
      if ($d.mode -eq "size") { $auto = $false }
      $script:boxLayout[$k] = @{ x = $s.Left; y = $s.Top; w = $s.Width; h = $s.Height; auto = $auto }
      Save-BoxLayout
      $s.Invalidate()
  })

  $w.Add_DoubleClick({
      param($s, $e)
      $k = [string]$s.Tag
      $v = $script:boxLayout[$k]
      if ($null -ne $v) { $v.auto = $true; Save-BoxLayout }
      $script:dpsSig = ""
      $script:bossSig = ""
  })

  $w.Add_Paint({
      param($s, $e)
      $k = [string]$s.Tag
      $d = if ($k -eq "dps") { $script:dpsBuild } else { $script:bossBuild }
      $g = $e.Graphics
      try {
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::ClearTypeGridFit
      } catch {}
      if ($null -eq $d) {
        $g.Clear([System.Drawing.Color]::FromArgb(16, 13, 8))
        return
      }
      Draw-StatBox $g $s.ClientSize.Width $s.ClientSize.Height $d.title $d.dps $d.rows $d.empty
      # 右下角三道斜杠，暗示这里可以拉
      $gx = $s.ClientSize.Width - 5
      $gy = $s.ClientSize.Height - 5
      for ($i = 0; $i -lt 3; $i++) {
        $g.DrawLine($script:penGrip, [float]($gx - 5 - $i * 4), [float]($gy - 1), [float]($gx - 1), [float]($gy - 5 - $i * 4))
      }
  })
}

$script:dps = New-StatWindow "dps" "造成伤害"
$script:bossPanel = New-StatWindow "boss" "首领伤害"
Attach-StatWindow $script:dps
Attach-StatWindow $script:bossPanel

function Convert-StatRows($src, [int]$maxN) {
  $out = @()
  foreach ($r in @($src)) {
    if ($null -eq $r) { continue }
    $out += [pscustomobject]@{
      name = [string]$r.name
      dmg = (Fmt-Dmg $r.dmg)
      share = ("{0:N1}%" -f ([double]$r.share * 100))
    }
    if ($out.Count -ge $maxN) { break }
  }
  return ,$out
}

# 第一次显示时按默认位置摆好；之后位置完全听用户的（存在 box_layout.json 里），
# 只有「自动高度」状态下才随内容改高度。
function Place-StatWindow($w, [int]$defX, [int]$defY, [int]$defW, [int]$defH, [bool]$autoH) {
  $k = [string]$w.Tag
  $v = $script:boxLayout[$k]
  if ($null -eq $v) {
    $v = @{ x = $defX; y = $defY; w = $defW; h = $defH; auto = $autoH }
    $script:boxLayout[$k] = $v
    if ($defW -lt 150) { $defW = 150 }
    $w.Bounds = New-Object System.Drawing.Rectangle $defX, $defY, $defW, $defH
    return
  }
  if ($v.auto -and $v.h -ne $defH) {
    $v.h = $defH
    $w.Height = $defH
  }
  if (-not (Test-OnScreen $v.x $v.y)) {
    $v.x = $defX
    $v.y = $defY
    Save-BoxLayout
  }
  if (-not $w.Visible) {
    $w.Bounds = New-Object System.Drawing.Rectangle $v.x, $v.y, $v.w, $v.h
  }
}

function Update-DpsPanel($state) {
  $rowsAll = @()
  $rowsBoss = @()
  $total = "0"
  $bossDps = "0"
  if ($null -ne $state) {
    $rowsAll = Convert-StatRows $state.meter 9
    $rowsBoss = Convert-StatRows $state.boss_meter 9
    $total = [string]$state.total_dps
    $bossDps = [string]$state.boss_dps
  }
  $on = $script:ctrlVisible -and $script:dpsOn
  if (-not $on) {
    if ($script:dps.Visible) { $script:dps.Hide() }
    if ($script:bossPanel.Visible) { $script:bossPanel.Hide() }
    return
  }

  # 默认位置：贴着游戏窗口左上角。用户拖过就以 box_layout.json 为准。
  $pw = [int](280 * $script:fontScale)
  if ($pw -gt ($form.ClientSize.Width * 0.235)) { $pw = [int]($form.ClientSize.Width * 0.235) }
  if ($pw -lt 180) { $pw = 180 }
  $gap = [int](10 * $script:fontScale)
  $x0 = $rect.X + [int](16 * $script:fontScale)
  $y0 = $rect.Y + [int](16 * $script:fontScale)

  $sigA = (($rowsAll | ForEach-Object { "$($_.name)$($_.dmg)$($_.share)" }) -join "|") + "#" + $total
  if ($sigA -ne $script:dpsSig) {
    $script:dpsSig = $sigA
    $script:dpsBuild = [pscustomobject]@{
      title = "造成伤害"; dps = ("{0} DPS" -f $total)
      rows = $rowsAll; empty = "还没有造成伤害。"
    }
    Place-StatWindow $script:dps $x0 $y0 $pw (Measure-StatH $rowsAll.Count) $true
    $script:dps.Invalidate()
  }
  if (-not $script:dps.Visible) {
    Place-StatWindow $script:dps $x0 $y0 $pw (Measure-StatH $rowsAll.Count) $true
    $script:dps.Show()
  }

  $sigB = (($rowsBoss | ForEach-Object { "$($_.name)$($_.dmg)$($_.share)" }) -join "|") + "#" + $bossDps
  if ($sigB -ne $script:bossSig) {
    $script:bossSig = $sigB
    $script:bossBuild = [pscustomobject]@{
      title = "首领伤害"; dps = ("{0} DPS" -f $bossDps)
      rows = $rowsBoss; empty = "尚未与首领交战。"
    }
    Place-StatWindow $script:bossPanel ($x0 + $pw + $gap) $y0 $pw (Measure-StatH $rowsBoss.Count) $true
    $script:bossPanel.Invalidate()
  }
  if (-not $script:bossPanel.Visible) {
    Place-StatWindow $script:bossPanel ($x0 + $pw + $gap) $y0 $pw (Measure-StatH $rowsBoss.Count) $true
    $script:bossPanel.Show()
  }
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
$script:wasShownFlag = $false  # 上一次是否亮着，用来只在"刚灭"时记日志
$script:activated = $false     # 只在第一次弹出时把焦点还给游戏
$script:badgeWidth = [int](270 * $script:fontScale)
$script:badgeHeight = [int](44 * $script:fontScale)

$timer = New-Object System.Windows.Forms.Timer
# 250ms 而不是 200ms：brain 本身就是每 0.25 秒写一次 state.json，
# 读得比写得还勤，只会白解析同一份数据。
$timer.Interval = 250
$timer.Add_Tick({
 try {
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
      foreach ($b in $script:badges) { $b.Main.Visible = $false; $b.Sub.Visible = $false; $b.Boss.Visible = $false }
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
      foreach ($b in $script:badges) { $b.Main.Visible = $false; $b.Sub.Visible = $false; $b.Boss.Visible = $false }
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
  if (-not $show -and $script:wasShownFlag) {
    # 只在「本来是亮着的 → 刚刚灭掉」这一次记一笔，方便事后查为什么灭
    $script:wasShownFlag = $false
    try { Log ("hide: " + [string]$state.why) } catch {}
  } elseif ($show) {
    $script:wasShownFlag = $true
  }
  if (-not $show) {
    if ($script:anyShown) {
      foreach ($b in $script:badges) { $b.Main.Visible = $false; $b.Sub.Visible = $false; $b.Boss.Visible = $false }
      $script:hint.Visible = $false
      $script:anyShown = $false
      # 徽标藏起来之后清掉缓存，下次弹出来时按当前内容重排一次
      for ($k = 0; $k -lt 4; $k++) { $script:badgeKey[$k] = "" }
    }
    if ($script:glowRects.Count -gt 0) {
      foreach ($r in $script:glowRects) { try { $form.Invalidate($r) } catch {} }
      $script:glowRects = @()
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
  $newGlow = @()
  for ($i = 0; $i -lt 4; $i++) {
    $b = $script:badges[$i]
    if ($i -lt $slots.Count) {
      $s = $slots[$i]
      $cx = [int]($w * [double]$s.x)
      $cy = [int]($h * [double]$s.y)
      # 预设命中：在徽标外面套一圈呼吸金边
      if ([int]$s.preset -gt 0) {
        $newGlow += New-Object System.Drawing.Rectangle `
          (($cx - [int]($bw / 2)) - 4), ($cy - 4), ($bw + 8), ($bh + $sh * 2 + 8)
      }
      $main = [string]$s.main
      $note = [string]$s.note
      $bossLine = [string]$s.boss
      $bossKind = [string]$s.boss_kind
      $best = [bool]$s.best
      $spec = [string]$s.spec
      # 只有内容真的变了才动控件。WinForms 每次 set Bounds/Text 都会触发重排 +
      # 重绘，而这个 form 是覆盖全屏的分层窗口，一次重绘就是整整一屏。
      $key = "{0}|{1}|{2}|{3}|{4}|{5}|{6}|{7}" -f $cx, $cy, $main, $note, $best, ([string]$s.pct), $spec, $bossLine
      if ($script:badgeKey[$i] -ne $key) {
        $script:badgeKey[$i] = $key
        $b.Main.Bounds = New-Object System.Drawing.Rectangle (($cx - [int]($bw / 2))), $cy, $bw, $bh
        $b.Sub.Bounds = New-Object System.Drawing.Rectangle (($cx - [int]($bw / 2))), ($cy + $bh), $bw, $sh
        $b.Boss.Bounds = New-Object System.Drawing.Rectangle (($cx - [int]($bw / 2))), ($cy + $bh + $sh), $bw, $sh
        $b.Main.Text = $main
        $b.Sub.Text = $note
        $b.Boss.Text = $bossLine
        # 有首领特化 -> 荧光绿；纯群体卡打 BOSS 掉价 -> 橙；其余灰色
        if ($bossKind -eq "tag") {
          $b.Boss.ForeColor = [System.Drawing.Color]::FromArgb(61, 217, 127)
        } elseif ($bossKind -eq "weak") {
          $b.Boss.ForeColor = [System.Drawing.Color]::FromArgb(224, 168, 92)
        } else {
          $b.Boss.ForeColor = [System.Drawing.Color]::FromArgb(140, 152, 146)
        }
        if ($bossLine -eq "") { $b.Boss.Visible = $false } else { $b.Boss.Visible = $true }
        # 流派核心给一块暗金底 + 金字，跟其它卡一眼分开
        if ($spec -eq "must") {
          $b.Main.BackColor = [System.Drawing.Color]::FromArgb(82, 60, 16)
          $b.Sub.BackColor = [System.Drawing.Color]::FromArgb(82, 60, 16)
          $b.Boss.BackColor = [System.Drawing.Color]::FromArgb(82, 60, 16)
          $b.Main.ForeColor = [System.Drawing.Color]::FromArgb(255, 214, 102)
        } else {
          $b.Main.BackColor = [System.Drawing.Color]::FromArgb(18, 22, 30)
          $b.Sub.BackColor = [System.Drawing.Color]::FromArgb(18, 22, 30)
          $b.Boss.BackColor = [System.Drawing.Color]::FromArgb(18, 22, 30)
          $b.Main.ForeColor = Get-Color $s.pct $best
        }
      }
      if (-not $b.Main.Visible) { $b.Main.Visible = $true; $b.Sub.Visible = $true }
    } else {
      if ($b.Main.Visible) { $b.Main.Visible = $false; $b.Sub.Visible = $false; $b.Boss.Visible = $false }
    }
  }

  # ---- 外发光：亮度每 400ms 换一档，只在需要时重画那一小块 ----
  $phase = [int](([DateTime]::Now.Ticks / 4000000) % 4)
  if ($newGlow.Count -ne $script:glowRects.Count -or $phase -ne $script:glowPhase) {
    $old = $script:glowRects
    $script:glowRects = $newGlow
    $script:glowPhase = $phase
    foreach ($r in $old) { try { $form.Invalidate($r) } catch {} }
    foreach ($r in $newGlow) { try { $form.Invalidate($r) } catch {} }
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
 } catch {
   # 任何一帧出错都只记一行日志，绝不让定时器整体停掉
   # （以前 Tick 里抛一次异常，整个悬浮层就再也不更新，只能重启工具箱）
   try { Log ("tick: " + $_.Exception.Message) } catch {}
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

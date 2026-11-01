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
$script:badgeWidth = [int](270 * $script:fontScale)
$script:badgeHeight = [int](44 * $script:fontScale)

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 200
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

  $state = $null
  try {
    if (Test-Path $StateFile) {
      $raw = [System.IO.File]::ReadAllText($StateFile, [System.Text.Encoding]::UTF8)
      if ($raw) { $state = $raw | ConvertFrom-Json }
    }
  } catch {}

  if ($null -eq $state) {
    foreach ($b in $script:badges) { $b.Main.Visible = $false; $b.Sub.Visible = $false }
    $script:hint.Visible = $false
    return
  }

  if ($state.status -eq "closed") {
    # 游戏已退出：只隐藏，不退出。以前这里 Close() 会因读到上一次运行残留的
    # status=closed 而让整个悬浮层自杀。
    foreach ($b in $script:badges) { $b.Main.Visible = $false; $b.Sub.Visible = $false }
    $script:hint.Visible = $false
    $script:wasVisible = $false
    return
  }

  $show = [bool]$state.visible -and $script:ctrlVisible
  if (-not $show) {
    foreach ($b in $script:badges) { $b.Main.Visible = $false; $b.Sub.Visible = $false }
    $script:hint.Visible = $false
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
      $b.Main.Bounds = New-Object System.Drawing.Rectangle (($cx - [int]($bw / 2))), $cy, $bw, $bh
      $b.Sub.Bounds = New-Object System.Drawing.Rectangle (($cx - [int]($bw / 2))), ($cy + $bh), $bw, $sh
      $b.Main.Text = [string]$s.main
      $b.Sub.Text = [string]$s.note
      $col = Get-Color $s.pct ([bool]$s.best)
      $b.Main.ForeColor = $col
      $b.Main.Visible = $true
      $b.Sub.Visible = $true
    } else {
      $b.Main.Visible = $false
      $b.Sub.Visible = $false
    }
  }

  $script:hint.Bounds = New-Object System.Drawing.Rectangle 12, ($h - 42), 580, 22
  # 追加职业/等级/DoT层数：DoT 层数决定持续时间卡到底有没有收益，
  # 放在常驻行里方便随时对照
  $extra = ""
  if ($null -ne $state.stats.dots) { $extra = $extra + "  层数" + [string]$state.stats.dots }
  if ($null -ne $state.run_level) { $extra = $extra + "  Lv" + [string]$state.run_level }
  if ($state.class) { $extra = $extra + "  " + [string]$state.class }
  $script:hint.Text = "总DPS " + [string]$state.total_dps + "    伤害x" + [string]$state.stats.dmg + "  冷却x" + [string]$state.stats.cd + "  暴击" + [string]([math]::Round([double]$state.stats.crit * 100)) + "%" + $extra
  $script:hint.Visible = $true

  if (-not $script:wasVisible) {
    $script:wasVisible = $true
    # 把键盘焦点还给游戏，否则 1/2/3/4 选卡无效
    try { $null = [Microsoft.VisualBasic.Interaction]::AppActivate("Azeroth Survivors") } catch {}
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

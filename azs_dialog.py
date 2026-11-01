# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 系统对话框。

为什么单独一个文件：环境里没有 tkinter（内嵌 Python 不带），
但选目录/选文件这类系统窗口又不是不能用 —— Windows 自带。

注意：这里**不**直接拼命令行调外部程序，而是用一个小的 .ps1 脚本，
由 azs_paths 找到的 powershell 以 -File 方式执行。这样：
  * 路径里带空格、中文、引号都不会把参数拼错
  * 需要在别的模块里复用时，只要 import 这个文件
"""
import os
import subprocess
import tempfile

CREATE_NO_WINDOW = 0x08000000

_DIR_PS = r"""
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = $env:AZS_DLG_TITLE
$d.ShowNewFolderButton = $true
if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Out.Write($d.SelectedPath)
}
"""

_FILE_PS = r"""
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = $env:AZS_DLG_TITLE
$d.Filter = $env:AZS_DLG_FILTER
$d.Multiselect = $false
if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Out.Write($d.FileName)
}
"""


def _powershell():
    for name in ("powershell.exe", "pwsh.exe"):
        try:
            out = subprocess.run(["where", name], capture_output=True, text=True,
                                 timeout=5, creationflags=CREATE_NO_WINDOW)
            lines = (out.stdout or "").strip().splitlines()
            if lines:
                return lines[0]
        except Exception:
            pass
    root = os.environ.get("SystemRoot", r"C:\Windows")
    p = os.path.join(root, "System32", "WindowsPowerShell", "v1.0",
                     "powershell.exe")
    return p if os.path.isfile(p) else ""


def _run(script, env_extra, timeout=300):
    ps = _powershell()
    if not ps:
        return ""
    fd, path = tempfile.mkstemp(suffix=".ps1", prefix="azs_dlg_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig") as fh:
            fh.write(script)
        env = os.environ.copy()
        env.update(env_extra)
        out = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
                              "-STA", "-File", path],
                             capture_output=True, text=True, timeout=timeout,
                             env=env, creationflags=CREATE_NO_WINDOW)
        return (out.stdout or "").strip()
    except Exception:
        return ""
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


def open_dir(title="选择文件夹"):
    """弹「选择文件夹」窗口，返回路径；取消返回空串。"""
    return _run(_DIR_PS, {"AZS_DLG_TITLE": title})


def open_file(title="选择文件", filters=None):
    """弹「打开文件」窗口，返回路径；取消返回空串。

    filters 形如 [("更新包", "*.zip"), ("所有文件", "*.*")]
    """
    flt = "|".join("%s|%s" % (n, p) for n, p in (filters or [("所有文件", "*.*")]))
    return _run(_FILE_PS, {"AZS_DLG_TITLE": title, "AZS_DLG_FILTER": flt})

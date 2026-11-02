# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 在线更新。

设计目标：**朋友什么都不用做**。所以更新必须满足三条：

  1. 只下载变了的文件（我们这个项目代码加起来才几百 KB，一次更新几秒钟）
  2. 下载完要校验（sha256），断网、CDN 抽风、文件被截断都不能装进去
  3. 装之前先备份，装坏了能把旧的换回来

更新清单（manifest）长这样，发版脚本会自动生成：

    {
      "version": "1.0.1",
      "released": "2026-11-02 10:00",
      "notes": ["修复 xxx", "新增 yyy"],
      "files": {
        "azs_ui.py": {"sha256": "…", "size": 12345},
        "overlay.ps1": {"sha256": "…", "size": 6789}
      }
    }

清单放在哪儿都行（GitHub / jsDelivr / 自己的服务器），只要给个能直接下载的 https 地址。
文件按「清单地址所在目录 + 文件名」去取，所以清单和文件放在同一个仓库根目录即可。
"""
import hashlib
import io
import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import azs_paths as P

# 允许被更新覆盖的扩展名。白名单是为了防止一个恶意/损坏的包往 system32 之类的地方写东西。
SAFE_EXT = {".py", ".json", ".ps1", ".cmd", ".bat", ".txt", ".md",
            ".png", ".ico", ".jpg", ".dll", ".exe"}
# 这些是用户数据，更新包不许碰
PROTECTED = {"config.json", "settings.json", "control.json", "state.json",
             "spell_backup.json", "spell_backup_mem.json", "table_loc.json"}
PROTECTED_DIRS = {"logs", "save", "__pycache__"}

UA = "AZS-Toolkit-Updater/1.0"


# --------------------------------------------------------------------- 网络
def http_get(url, timeout=20, progress=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        chunks = []
        got = 0
        while True:
            buf = resp.read(65536)
            if not buf:
                break
            chunks.append(buf)
            got += len(buf)
            if progress:
                progress(got, total)
        return b"".join(chunks)


def _base_of(manifest_url):
    """https://host/a/b/latest.json → https://host/a/b/"""
    u = urllib.parse.urlsplit(manifest_url)
    path = u.path
    if path.endswith("/"):
        base_path = path
    else:
        base_path = path.rsplit("/", 1)[0] + "/"
    return urllib.parse.urlunsplit((u.scheme, u.netloc, base_path, "", ""))


# --------------------------------------------------------------------- 版本
def parse_version(s):
    out = []
    for part in str(s or "").replace("-", ".").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:4])


def is_newer(remote, local):
    return parse_version(remote) > parse_version(local)


# --------------------------------------------------------------------- 检查
def check(manifest_url, timeout=20, alts=None):
    """拉清单并比对版本。返回 dict，永远不抛异常（失败信息放 error）。

    第一个地址不通时会依次试 alts 里的备选地址 —— GitHub 直连在国内
    时通时不通，多留几条路比让用户自己改配置现实。
    """
    urls = []
    for u in [manifest_url] + list(alts or []):
        u = (u or "").strip()
        if u and u not in urls:
            urls.append(u)
    if not urls:
        return {"ok": False, "error": "还没填更新地址", "manifest": None,
                "base": "", "version": "", "notes": [], "released": "",
                "files": {}}

    last = None
    for i, u in enumerate(urls):
        res = _check_one(u, timeout)
        if res.get("ok"):
            res["used_url"] = u
            res["tried"] = i + 1
            res["total_urls"] = len(urls)
            return res
        last = res
    last = last or {"ok": False, "error": "检查失败"}
    last["total_urls"] = len(urls)
    if len(urls) > 1:
        last["error"] = "%s（%d 个地址都试过了）" % (last.get("error") or "", len(urls))
    return last


def _check_one(manifest_url, timeout=20):
    """单个地址的检查。"""
    res = {"ok": False, "error": "", "manifest": None, "base": "",
           "version": "", "notes": [], "released": "", "files": {}}
    if not manifest_url.lower().startswith(("http://", "https://")):
        res["error"] = "更新地址必须以 http:// 或 https:// 开头"
        return res
    try:
        raw = http_get(manifest_url, timeout=timeout)
    except urllib.error.HTTPError as e:
        res["error"] = "下载清单失败（HTTP %s）" % e.code
        return res
    except urllib.error.URLError as e:
        res["error"] = "连不上更新服务器：%s" % getattr(e, "reason", e)
        return res
    except Exception as e:
        res["error"] = "下载清单出错：%s" % e
        return res
    try:
        m = json.loads(raw.decode("utf-8-sig"))
    except Exception as e:
        res["error"] = "清单不是合法 JSON：%s" % e
        return res
    if not isinstance(m, dict) or "version" not in m:
        res["error"] = "清单格式不对（缺少 version 字段）"
        return res
    res["manifest"] = m
    res["base"] = _base_of(manifest_url)
    # 把下载根地址写进清单本身：调用方（比如 plan_download）只拿到 manifest
    # 这一份数据，不该再额外记住 base 从哪来
    m["base"] = res["base"]
    res["version"] = str(m.get("version") or "")
    res["notes"] = m.get("notes") or []
    res["released"] = m.get("released") or ""
    res["files"] = m.get("files") or {}
    res["ok"] = True
    return res


# --------------------------------------------------------------------- 下载
def plan_download(manifest, local_version):
    """列出需要下载的文件（对比本地 sha256）。"""
    base = manifest.get("base") or ""
    todo = []
    for name, info in (manifest.get("files") or {}).items():
        name = name.replace("\\", "/").lstrip("./")
        if not name or name.split("/")[0] in PROTECTED_DIRS:
            continue
        if os.path.basename(name) in PROTECTED:
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in SAFE_EXT:
            continue
        path = P.app_path(*name.split("/"))
        want = (info or {}).get("sha256") or ""
        if want and os.path.isfile(path):
            if _sha256_file(path) == want:
                continue        # 本地已经一样，跳过
        todo.append({"name": name, "url": urllib.parse.urljoin(base, name),
                     "sha256": want, "size": (info or {}).get("size") or 0})
    return todo


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def download(manifest, local_version, staged_dir, progress=None, log=None):
    """把需要的文件下到 staged_dir，逐个校验。返回 (ok, 错误信息, 文件列表)。"""
    todo = plan_download(manifest, local_version)
    if not todo:
        return True, "", []
    os.makedirs(staged_dir, exist_ok=True)
    done = 0
    for item in todo:
        name = item["name"]
        if log:
            log("下载 %s" % name)
        try:
            data = http_get(item["url"], progress=None)
        except Exception as e:
            return False, "下载 %s 失败：%s" % (name, e), []
        if item["sha256"] and _sha256_bytes(data) != item["sha256"]:
            return False, "%s 校验失败（文件可能没传完）" % name, []
        out = os.path.join(staged_dir, *name.split("/"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as fh:
            fh.write(data)
        done += 1
        if progress:
            progress(done, len(todo))
    return True, "", todo


# --------------------------------------------------------------------- 安装
def install(staged_dir, files, backup_root=None, dest_root=None):
    """把 staged 里的文件覆盖到程序目录，先把旧文件备份一份。

    dest_root 只给测试用：正式运行时目标永远是程序自己所在目录。
    """
    backup_root = backup_root or os.path.join(P.logs_dir(), "update_backup")
    dest_root = dest_root or P.app_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(backup_root, stamp)
    os.makedirs(backup_dir, exist_ok=True)
    installed = []
    for item in files:
        name = item["name"]
        src = os.path.join(staged_dir, *name.split("/"))
        dst = os.path.join(dest_root, *name.split("/"))
        if not os.path.isfile(src):
            continue
        if os.path.isfile(dst):
            bak = os.path.join(backup_dir, *name.split("/"))
            os.makedirs(os.path.dirname(bak), exist_ok=True)
            try:
                shutil.copy2(dst, bak)
            except Exception:
                pass
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.copy2(src, dst)
            installed.append(name)
        except Exception as e:
            return False, "覆盖 %s 失败：%s" % (name, e), installed
    return True, "", installed


def rollback(backup_dir):
    """把某个备份目录里的文件还原回去。"""
    n = 0
    for root, _dirs, files in os.walk(backup_dir):
        for fn in files:
            src = os.path.join(root, fn)
            rel = os.path.relpath(src, backup_dir)
            dst = P.app_path(*rel.split(os.sep))
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                n += 1
            except Exception:
                pass
    return n


def latest_backup():
    root = os.path.join(P.logs_dir(), "update_backup")
    try:
        names = sorted(d for d in os.listdir(root)
                       if os.path.isdir(os.path.join(root, d)))
        return os.path.join(root, names[-1]) if names else ""
    except Exception:
        return ""


# --------------------------------------------------------------------- 导入 zip
def inspect_zip(zip_path):
    """看一个更新包里有哪些文件、是什么版本。

    包里如果带 manifest.json 就用它；没有就按「解压出来直接覆盖」处理。
    """
    out = {"ok": False, "error": "", "version": "", "notes": [], "files": []}
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            # 有的压缩软件会多套一层文件夹，自动剥掉
            prefix = ""
            if names and all(n.startswith(names[0].split("/")[0] + "/") for n in names
                             if "/" in n):
                head = names[0].split("/")[0] + "/"
                if all(n.startswith(head) for n in names):
                    prefix = head
            mani = None
            for n in names:
                if n[len(prefix):] in ("manifest.json", "latest.json"):
                    try:
                        mani = json.loads(z.read(n).decode("utf-8-sig"))
                    except Exception:
                        pass
                    break
            picked = []
            for n in names:
                rel = n[len(prefix):] if prefix and n.startswith(prefix) else n
                rel = rel.replace("\\", "/").lstrip("./")
                if not rel or rel.split("/")[0] in PROTECTED_DIRS:
                    continue
                if os.path.basename(rel) in PROTECTED:
                    continue
                ext = os.path.splitext(rel)[1].lower()
                if ext not in SAFE_EXT:
                    continue
                picked.append({"name": rel, "zip_name": n,
                               "sha256": "", "size": 0})
            if not picked:
                out["error"] = "这个包里没有可安装的文件"
                return out
            out["files"] = picked
            if isinstance(mani, dict):
                out["version"] = str(mani.get("version") or "")
                out["notes"] = mani.get("notes") or []
            out["ok"] = True
            return out
    except zipfile.BadZipFile:
        out["error"] = "不是有效的 zip 文件"
    except Exception as e:
        out["error"] = "读压缩包出错：%s" % e
    return out


def extract_zip(zip_path, staged_dir):
    info = inspect_zip(zip_path)
    if not info["ok"]:
        return info
    os.makedirs(staged_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for item in info["files"]:
            out = os.path.join(staged_dir, *item["name"].split("/"))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(item["zip_name"]) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
    return info

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

    所有地址并行拉一遍，取「版本号最高的那份清单」，而不是「第一个连通的」。

    以前是顺序取第一个成功的，结果被镜像站的缓存坑住：1.2.4 推上去半小时，
    ghproxy 还在发 1.2.2 的旧清单，客户端于是认为「已是最新」，
    作者那边则表现为「你们怎么都不更新」。并行的代价只是多几次 HEAD 级别的
    小请求，比让人等半小时靠谱。
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

    results = []
    if len(urls) == 1:
        results = [(urls[0], _check_one(urls[0], timeout))]
    else:
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(urls))) as ex:
                results = list(zip(urls, ex.map(
                    lambda u: _check_one(u, timeout), urls)))
        except Exception:
            results = [(u, _check_one(u, timeout)) for u in urls]

    oks = [(u, r) for u, r in results if r.get("ok")]
    if oks:
        u, res = max(oks, key=lambda p: parse_version(p[1].get("version")))
        res["used_url"] = u
        res["tried"] = urls.index(u) + 1
        res["total_urls"] = len(urls)
        res["all_versions"] = {u: r.get("version") for u, r in results}
        # 下载时挨个镜像试：清单是新的，文件却可能还停在镜像的旧缓存里，
        # 哈希对不上就换下一家，比让更新直接失败强。
        bases = [res.get("base") or ""]
        for _u, r in results:
            b = r.get("base") or ""
            if r.get("ok") and b and b not in bases:
                bases.append(b)
        res["manifest"]["bases"] = [b for b in bases if b]
        return res

    last = results[-1][1] if results else {"ok": False, "error": "检查失败"}
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
    bases = [b for b in (manifest.get("bases") or []) if b] or ([base] if base else [])
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
        urls = [urllib.parse.urljoin(b, name) for b in bases]
        todo.append({"name": name, "url": urls[0] if urls else "",
                     "urls": urls, "sha256": want,
                     "size": (info or {}).get("size") or 0})
    return todo


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _match_hash(data, want):
    """返回和清单 sha256 对得上的字节；对不上返回 None。

    为什么要三种写法都试：GitHub 的 raw 发的是仓库里那份（LF），
    而作者机器上的文件是 CRLF（git 的 autocrlf 把换行换回去了）。
    两边字节不同，哈希自然不同 —— 以前一律判「文件没传完」，更新直接失败。

    这里按「原样 / 换成 LF / 换成 CRLF」依次比，哪个对得上就存哪个。
    优先存清单认定的那一份：作者是 CRLF，客户端也就落 CRLF，
    .cmd 这种只认 CRLF 的文件不会因为换行丢了而跑不起来。
    """
    if not want:
        return data
    if _sha256_bytes(data) == want:
        return data
    lf = data.replace(b"\r\n", b"\n")
    if _sha256_bytes(lf) == want:
        return lf
    crlf = lf.replace(b"\n", b"\r\n")
    if _sha256_bytes(crlf) == want:
        return crlf
    return None


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
        # 两个坑一起绕：
        #   · 镜像站第一次取某个文件要回源，慢起来能到二十几秒，一次超时不算数；
        #   · 镜像的缓存可能还停在上一版，下回来的字节对不上哈希 —— 换下一家。
        data = None
        last_err = ""
        urls = item.get("urls") or ([item["url"]] if item.get("url") else [])
        for url in urls:
            for attempt in range(2):
                try:
                    raw = http_get(url, timeout=25 if attempt == 0 else 45)
                except Exception as e:
                    last_err = str(e)
                    if attempt == 0:
                        time.sleep(1.2)
                    continue
                got = _match_hash(raw, item["sha256"])
                if got is not None:
                    data = got
                    break
                last_err = "内容对不上清单（镜像缓存可能是旧的）"
                break
            if data is not None:
                break
        if data is None:
            return False, "下载 %s 失败：%s" % (name, last_err), []
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

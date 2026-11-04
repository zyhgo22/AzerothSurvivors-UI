# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 检查更新页。

两条更新路径都在这一个页面上：

  A. **在线更新**：拉清单 → 比对版本 → 只下载变了的文件 → 校验 → 覆盖 → 提示重启
  B. **导入更新包**：手动选一个 zip（微信/网盘发过来的），同样校验后覆盖

为什么两条都要：在线通道可能被网络抽风卡住，这时候把 zip 丢给朋友照样能升级，
不至于「服务器一挂，所有人都卡在旧版本」。

所有耗时动作（拉清单、下载、解压）都在后台线程，界面只负责显示进度。
"""
import os
import threading
import time

import dearpygui.dearpygui as dpg

import azs_paths as P
import azs_theme as T
import azs_update as U
from azs_config import FALLBACK_MANIFEST_URLS


class UpdatePage:
    def __init__(self, tk):
        self.tk = tk
        self.busy = False
        self.stage = "idle"
        self.manifest = None
        self.notes = []
        self.remote_version = ""
        self._pending = None       # (kind, payload) 交给主线程处理
        self._progress = ""

    # ------------------------------------------------------------- 构建
    def build(self, parent, tag="page_update"):
        from azs_pages import card, card_head, rule, _cell_theme, _row_table

        with dpg.child_window(tag=tag, parent=parent, border=False,
                              width=-1, show=False):
            dpg.add_spacer(height=6)
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=24)
                with dpg.group():
                    t = dpg.add_text("检查更新")
                    T.set_font(t, "huge")
                    dpg.add_spacer(height=2)
                    s = dpg.add_text(
                        "有新版本时一键升级；你的方案、技能备份、日志都不会动。",
                        color=T.C_TEXT_DIM, wrap=820)
                    T.set_font(s, "small")
            dpg.add_spacer(height=8)

            # 表格负责左右留白：group(horizontal) 里的 width=-1 会按整页宽算，
            # 卡片右边缘直接伸到窗口外面去。
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                           width=-1, pad_outerX=True,
                           borders_innerV=False, borders_innerH=False,
                           borders_outerH=False, borders_outerV=False,
                           scrollX=False, scrollY=False) as _tb:
                dpg.bind_item_theme(_tb, _cell_theme(20))
                # 两张卡左右并排：上下叠着放，640 高的窗口里第二张永远只露半截。
                dpg.add_table_column(init_width_or_weight=1.12)
                dpg.add_table_column(init_width_or_weight=1.0)
                with dpg.table_row():
                    with dpg.table_cell():
                        with card(340):
                            card_head("版本信息", "在线 / 更新包都能用")
                            dpg.add_spacer(height=2)
                            rule()
                            dpg.add_spacer(height=2)
                            _row_table([
                                ("当前版本", "up_cur", "已安装的版本"),
                                ("最新版本", "up_latest", "检查后才知道"),
                                ("更新通道", "up_channel", "清单地址的主机"),
                            ])
                            dpg.add_spacer(height=6)
                            with dpg.group(horizontal=True):
                                dpg.add_spacer(width=22)
                                self.btn_main = dpg.add_button(
                                    label="  检 查 更 新  ", height=32,
                                    callback=self.on_check)
                                dpg.bind_item_theme(
                                    self.btn_main, T.accent_button_theme())
                                dpg.add_spacer(width=8)
                                dpg.add_button(
                                    label="  从更新包导入…  ", height=32,
                                    callback=self.on_import)
                            dpg.add_spacer(height=6)
                            with dpg.group(horizontal=True):
                                dpg.add_spacer(width=22)
                                dpg.add_text("", tag="up_msg",
                                             color=T.C_TEXT_DIM, wrap=330)
                    with dpg.table_cell():
                        with card(340):
                            card_head("更新内容", "检查后列出这次的改动")
                            dpg.add_spacer(height=2)
                            rule()
                            with dpg.group(horizontal=True):
                                dpg.add_spacer(width=18)
                                with dpg.child_window(width=-1, height=-1,
                                                      border=False):
                                    dpg.add_text("还没有检查过更新。",
                                                 tag="up_notes",
                                                 color=T.C_TEXT_FAINT,
                                                 wrap=330)
            dpg.add_spacer(height=6)
        return tag

    def _kv(self, label, tag):
        with dpg.group(horizontal=True):
            t = dpg.add_text(label + "　", color=T.C_TEXT_DIM)
            T.set_font(t, "small")
            v = dpg.add_text("—", tag=tag)
            T.set_font(v, "small")

    # ------------------------------------------------------------- 刷新
    def refresh_basic(self):
        dpg.set_value("up_cur", "v" + P.VERSION)
        url = self.tk.cfg.update_cfg.get("manifest_url") or ""
        # 地址太长会把这一行撑开，只显主机名够认路了
        if url:
            host = url.split("//", 1)[-1].split("/", 1)[0]
            dpg.set_value("up_channel", "GitHub · " + host)
        else:
            dpg.set_value("up_channel", "未配置（只能手动导入更新包）")
        if not self.remote_version:
            dpg.set_value("up_latest", "—")

    def poll(self):
        """主线程消费后台线程的结果。"""
        if self._pending is None:
            return
        kind, payload = self._pending
        self._pending = None
        self.busy = False
        if kind == "checked":
            self._after_check(payload)
        elif kind == "downloaded":
            self._after_download(payload)
        elif kind == "installed":
            self._after_install(payload)
        elif kind == "zipped":
            self._after_zip(payload)

    # ------------------------------------------------------------- 在线
    def on_check(self, sender=None, app_data=None, user_data=None):
        if self.busy:
            return
        if self.remote_version:
            # 第二次点 = 直接装
            self.on_install()
            return
        url = self.tk.cfg.update_cfg.get("manifest_url") or ""
        if not url:
            self._say("还没配置更新地址。可以先去「设置」里填，或者用右边的"
                      "「从更新包导入」手动升级。", T.C_AMBER)
            return
        self.busy = True
        self._set_button("   正 在 检 查 …   ")
        self._say("正在连接更新服务器…", T.C_TEXT_DIM)

        def work():
            res = U.check(url, alts=FALLBACK_MANIFEST_URLS)
            self._pending = ("checked", res)

        threading.Thread(target=work, daemon=True).start()

    def _after_check(self, res):
        self._set_button("   检 查 更 新   ")
        if not res.get("ok"):
            self._say(res.get("error") or "检查失败", T.C_RED)
            return
        self.manifest = dict(res["manifest"])
        self.manifest["base"] = res["base"]
        self.notes = res.get("notes") or []
        remote = res.get("version") or ""
        self.remote_version = remote
        dpg.set_value("up_latest", "v" + remote if remote else "—")
        if U.is_newer(remote, P.VERSION):
            dpg.configure_item("up_latest", color=T.C_GREEN)
            self._show_notes(self.notes)
            self._say("发现新版本 v%s，点按钮直接升级。" % remote, T.C_GREEN)
            self._set_button("   立 即 升 级 到 v%s   " % remote)
        else:
            dpg.configure_item("up_latest", color=T.C_TEXT_DIM)
            self._show_notes(self.notes, prefix="你已经是最新版本。\n\n本次版本说明：")
            self._say("已是最新版本（v%s）" % P.VERSION, T.C_TEXT_DIM)
            self._set_button("   检 查 更 新   ")

    def on_install(self, sender=None, app_data=None, user_data=None):
        if self.busy or not self.manifest:
            return
        self.busy = True
        self._set_button("   正 在 下 载 …   ")
        self._say("正在下载…", T.C_TEXT_DIM)
        staged = os.path.join(P.logs_dir(), "update_staged")
        try:
            if os.path.isdir(staged):
                import shutil
                shutil.rmtree(staged, ignore_errors=True)
        except Exception:
            pass

        def progress(done, total):
            self._progress = "%d/%d" % (done, total)
            try:
                dpg.set_value("up_msg", "正在下载 %s …" % self._progress)
            except Exception:
                pass

        def work():
            ok, err, files = U.download(self.manifest, P.VERSION, staged,
                                        progress=progress)
            if not ok:
                self._pending = ("downloaded", {"ok": False, "error": err})
                return
            ok2, err2, installed = U.install(staged, files)
            self._pending = ("installed", {"ok": ok2, "error": err2,
                                           "files": installed,
                                           "version": self.remote_version})

        threading.Thread(target=work, daemon=True).start()

    def _after_download(self, res):
        self._set_button("   重 试 升 级   ")
        self._say("下载失败：%s" % res.get("error"), T.C_RED)

    def _after_install(self, res):
        if not res.get("ok"):
            self._set_button("   重 试 升 级   ")
            self._say("安装失败：%s" % res.get("error"), T.C_RED)
            return
        n = len(res.get("files") or [])
        ver = res.get("version") or ""
        self.tk.cfg.data["installed_version"] = ver or P.VERSION
        try:
            self.tk.cfg.save(sync_settings=False)
        except Exception:
            pass
        self._set_button("   已 更 新 到 v%s   " % (ver or "?"))
        dpg.set_value("up_latest", "v" + (ver or "?"))
        self._say("已更新 %d 个文件到 v%s。**请关掉工具箱重新打开**，新版本才会生效。"
                  % (n, ver), T.C_GREEN)
        self._show_notes(["更新完成：%d 个文件" % n] + list(self.notes))

    # ------------------------------------------------------------- 导入包
    def on_import(self, sender=None, app_data=None, user_data=None):
        if self.busy:
            return
        from azs_pages import ask_file
        path = ask_file("选择更新包", [("更新包", "*.zip"), ("所有文件", "*.*")])
        if not path:
            return
        self.busy = True
        self._set_button("   正 在 解 压 …   ")
        self._say("正在读取更新包…", T.C_TEXT_DIM)
        staged = os.path.join(P.logs_dir(), "update_staged")

        def work():
            try:
                if os.path.isdir(staged):
                    import shutil
                    shutil.rmtree(staged, ignore_errors=True)
            except Exception:
                pass
            info = U.extract_zip(path, staged)
            if not info.get("ok"):
                self._pending = ("zipped", {"ok": False,
                                            "error": info.get("error")})
                return
            ok, err, installed = U.install(staged, info["files"])
            self._pending = ("zipped", {"ok": ok, "error": err,
                                        "files": installed,
                                        "version": info.get("version") or "",
                                        "notes": info.get("notes") or []})

        threading.Thread(target=work, daemon=True).start()

    def _after_zip(self, res):
        if not res.get("ok"):
            self._set_button("   从更新包导入…   ")
            self._say("导入失败：%s" % res.get("error"), T.C_RED)
            return
        n = len(res.get("files") or [])
        ver = res.get("version")
        self._set_button("   从更新包导入…   ")
        self._say("已从更新包导入 %d 个文件%s。**请关掉工具箱重新打开**。"
                  % (n, ("（v%s）" % ver) if ver else ""), T.C_GREEN)
        self._show_notes(list(res.get("notes") or []) or
                         ["更新包已安装：%d 个文件" % n])

    # ------------------------------------------------------------- 小工具
    def _set_button(self, label):
        try:
            dpg.configure_item(self.btn_main, label=label)
        except Exception:
            pass

    def _say(self, text, color=None):
        try:
            dpg.set_value("up_msg", text)
            dpg.configure_item("up_msg", color=color or T.C_TEXT_DIM)
        except Exception:
            pass
        try:
            self.tk._toast_msg(text.split("\n")[0][:80])
        except Exception:
            pass

    def _show_notes(self, notes, prefix=""):
        try:
            dpg.delete_item("up_notes", children_only=True)
        except Exception:
            return
        if prefix:
            dpg.add_text(prefix, parent="up_notes", color=T.C_TEXT_DIM,
                         wrap=740)
        if not notes:
            dpg.add_text("（这次更新没有写说明）", parent="up_notes",
                         color=T.C_TEXT_FAINT)
            return
        for line in notes:
            with dpg.group(horizontal=True, parent="up_notes"):
                dpg.add_text("  ·  ", color=T.C_GOLD)
                dpg.add_text(str(line), wrap=720)

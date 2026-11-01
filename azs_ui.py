# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 主界面外壳。

一个窗口装下所有东西：启动游戏、悬浮层、技能数值、方案天赋、更新、设置。
左边导航，右边内容，底部一条状态栏。

布局上有个坑：DearPyGui 的负尺寸（height=-1）是按「父容器整体大小」算的，
不会自动扣掉前面已经占位的兄弟控件。所以导航栏和内容区的高度必须自己算：
视口高度 − 顶栏高度，并在每次窗口缩放时重算。
"""
import os
import time

import dearpygui.dearpygui as dpg

import azs_config as C
import azs_paths as P
import azs_theme as T
from azs_hotkey import HotkeyManager, HotkeyError
from azs_proc import ProcManager

# 导航项只放纯文字：微软雅黑对 ▤ ✦ ⟳ 这类符号没有字形，渲染出来是问号。
# 用两位数编号代替图标，既有秩序感又不会掉字。
NAV_ITEMS = [
    ("home", "开始游戏", "01"),
    ("overlay", "悬浮层", "02"),
    ("spells", "技能数值", "03"),
    ("builds", "流派", "04"),
    ("profile", "方案与天赋", "05"),
    ("update", "检查更新", "06"),
    ("settings", "设置", "07"),
]

HEADER_H = 52
FOOTER_H = 26


def _msgbox(text, title="AZS 工具箱"):
    """系统弹窗。有些说明太长，顶栏那一行放不下，只能弹窗。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    except Exception:
        pass


def _save_editor_blocked():
    """存档修改器如果自检失败过、而且游戏 exe 没变，就别再拉起了。

    它的自检要在子进程里映射整个游戏 exe，要十来秒。用户第二次点的时候
    没必要再等一遍 —— 结论早就记在 改档工具/logs/probe.json 里了。
    """
    import io
    import json
    cache = P.app_path("改档工具", "logs", "probe.json")
    if not os.path.isfile(cache):
        return ""                       # 没自检过，让它自己去探
    try:
        st = os.stat(P.exe_path())
        key = "%d-%d" % (st.st_size, int(st.st_mtime))
        with io.open(cache, "r", encoding="utf-8") as fh:
            c = json.load(fh)
        if c.get("key") != key:
            return ""                   # 游戏更新过了，重新探一次
        if c.get("ok"):
            return ""
        return c.get("why") or "存档修改器暂时用不了。"
    except Exception:
        return ""


class Toolkit:
    def __init__(self):
        self.cfg = C.get()
        self.proc = ProcManager(self.cfg)
        self.hk = HotkeyManager()
        self.page = "home"
        self.pages = {}
        self._toast = ""
        self._toast_t = 0.0
        self._last_poll = 0.0
        self._status = {"game": False, "brain": False, "overlay": False}
        self._hotkey_error = ""
        self._closing = False
        self._pending_toggle_overlay = False
        self._pending_toggle_panel = False
        self._hwnd = 0

    # ================================================================= 构建
    def build(self):
        dpg.create_context()
        T.load_fonts()

        with dpg.window(tag="main", no_title_bar=True, no_scrollbar=True,
                        no_move=True, no_resize=True):
            self._build_header()
            with dpg.group(horizontal=True):
                with dpg.child_window(width=182, height=1, border=False,
                                      no_scrollbar=True, tag="nav"):
                    self._build_nav()
                with dpg.child_window(width=1, height=1, border=False,
                                      tag="body"):
                    pass

        T.theme()
        dpg.create_viewport(title=P.APP_TITLE_EN,
                            width=int(self.cfg.data["window"].get("width", 1220)),
                            height=int(self.cfg.data["window"].get("height", 800)),
                            min_width=1000, min_height=640,
                            resizable=True)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("main", True)
        dpg.set_viewport_resize_callback(self._relayout)

        self._build_pages()
        self._relayout()
        self.switch_page("home")
        self._bind_hotkeys()
        self._refresh_all()
        # 先渲染一帧，否则 Windows 会把窗口当成「未激活的最小化窗口」，
        # 之后怎么 ShowWindow 都被压回去（表现为双击了没反应）
        dpg.render_dearpygui_frame()
        self._hwnd = self._find_own_hwnd()

    def _build_header(self):
        with dpg.child_window(height=HEADER_H, border=False, no_scrollbar=True,
                              tag="header"):
            dpg.add_spacer(height=13)
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=22)
                t = dpg.add_text("AZS 工具箱")
                T.set_font(t, "bold")
                dpg.add_spacer(width=8)
                t2 = dpg.add_text("v" + P.VERSION, color=T.C_TEXT_FAINT)
                T.set_font(t2, "tiny")
                dpg.add_spacer(width=26)
                self.dot_game = dpg.add_text("●", color=T.C_TEXT_FAINT)
                t3 = dpg.add_text("游戏", color=T.C_TEXT_DIM)
                T.set_font(t3, "tiny")
                dpg.add_spacer(width=8)
                self.dot_brain = dpg.add_text("●", color=T.C_TEXT_FAINT)
                t4 = dpg.add_text("数据", color=T.C_TEXT_DIM)
                T.set_font(t4, "tiny")
                dpg.add_spacer(width=8)
                self.dot_overlay = dpg.add_text("●", color=T.C_TEXT_FAINT)
                t5 = dpg.add_text("悬浮层", color=T.C_TEXT_DIM)
                T.set_font(t5, "tiny")
                dpg.add_spacer(width=28)
                self.lbl_profile = dpg.add_text("", color=T.C_GOLD)
                T.set_font(self.lbl_profile, "small")
                dpg.add_spacer(width=26)
                self.lbl_toast = dpg.add_text("就绪", tag="toast",
                                              color=T.C_TEXT_FAINT)
                T.set_font(self.lbl_toast, "tiny")
        dpg.add_separator()

    def _build_nav(self):
        dpg.add_spacer(height=12)
        self.nav_items = {}
        for key, label, icon in NAV_ITEMS:
            item = dpg.add_selectable(label="   %s   %s" % (icon, label),
                                      tag="nav_" + key,
                                      callback=self._on_nav, user_data=key,
                                      height=38)
            self.nav_items[key] = item
        dpg.add_spacer(height=10)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=16)
            hint = dpg.add_text("热键", color=T.C_TEXT_FAINT)
            T.set_font(hint, "tiny")
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=16)
            self.lbl_nav_hotkey = dpg.add_text("", color=T.C_TEXT_DIM)
            T.set_font(self.lbl_nav_hotkey, "tiny")

    def _placeholder_footer(self):
        pass

    def _relayout(self):
        try:
            h = dpg.get_viewport_client_height()
        except Exception:
            return
        # 实测顶栏 + 分隔线 + item spacing 一共吃掉 68px，再留 6px 余量
        body_h = max(120, h - 68 - 6)
        for tag in ("nav", "body"):
            try:
                dpg.configure_item(tag, height=body_h)
            except Exception:
                pass
        try:
            dw = dpg.get_viewport_client_width()
            dpg.configure_item("nav", width=182)
            dpg.configure_item("body", width=max(400, dw - 182 - 2))
        except Exception:
            pass

    # ================================================================= 页面
    def _build_pages(self):
        from azs_pages import (build_home, build_overlay, build_profile,
                               build_settings, build_spells, build_update)
        from azs_page_builds import BuildsPage

        self.pages["home"] = build_home(self, "body")
        self.pages["overlay"] = build_overlay(self, "body")
        self.pages["spells"] = build_spells(self, "body")
        self.builds_page = BuildsPage(self)
        self.pages["builds"] = self.builds_page.build("body")
        self.pages["profile"] = build_profile(self, "body")
        self.pages["update"] = build_update(self, "body")
        self.pages["settings"] = build_settings(self, "body")
        for k, tag in self.pages.items():
            dpg.configure_item(tag, show=(k == "home"))

    def switch_page(self, key):
        if key not in self.pages:
            return
        self.page = key
        for k, tag in self.pages.items():
            dpg.configure_item(tag, show=(k == key))
            try:
                dpg.set_value("nav_" + k, k == key)
            except Exception:
                pass
        self._refresh_page(key)

    def _on_nav(self, sender, app_data, user_data):
        self.switch_page(user_data)

    # ================================================================= 热键
    def _bind_hotkeys(self):
        hk = self.cfg.hotkey
        if not hk.get("enabled", True):
            self._hotkey_error = "热键已关闭"
            return
        if not self.hk.start():
            self._hotkey_error = "热键线程没起来（可能被安全软件拦了）"
            return
        errs = []
        try:
            combo = hk.get("toggle_overlay") or "F8"
            self.hk.rebind("toggle_overlay", combo, self._hk_toggle_overlay)
        except HotkeyError as e:
            errs.append("显示/隐藏悬浮层：%s" % e)
        try:
            combo2 = hk.get("toggle_panel") or "F9"
            self.hk.rebind("toggle_panel", combo2, self._hk_toggle_panel)
        except HotkeyError as e:
            errs.append("呼出工具箱：%s" % e)
        self._hotkey_error = "；".join(errs)
        try:
            dpg.set_value(self.lbl_nav_hotkey, hk.get("toggle_overlay") or "—")
        except Exception:
            pass

    def _hk_toggle_overlay(self):
        # 热键回调跑在独立线程，绝对不能直接操作 DPG，排个队让主循环来做
        self._pending_toggle_overlay = True

    def _hk_toggle_panel(self):
        self._pending_toggle_panel = True

    def toggle_overlay_visible(self):
        vis = not self.cfg.overlay_visible()
        self.set_overlay_visible(vis)
        return vis

    def set_overlay_visible(self, vis):
        self.cfg.overlay["visible"] = bool(vis)
        self.cfg.write_control(visible=vis)
        self.cfg.save(sync_settings=False)
        self._refresh_page("overlay")
        self._toast_msg("悬浮层已" + ("显示" if vis else "隐藏"))

    def _bring_to_front(self):
        try:
            import ctypes
            u = ctypes.WinDLL("user32", use_last_error=True)
            hwnd = self._hwnd or self._find_own_hwnd()
            if hwnd:
                self._hwnd = hwnd
                u.ShowWindow(hwnd, 9)             # SW_RESTORE
                u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)
                u.SetForegroundWindow(hwnd)
        except Exception:
            pass

    @staticmethod
    def _find_own_hwnd():
        try:
            import ctypes
            from ctypes import wintypes as wt

            u = ctypes.WinDLL("user32", use_last_error=True)
            u.FindWindowW.restype = wt.HWND
            u.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
            return u.FindWindowW(None, P.APP_TITLE_EN) or 0
        except Exception:
            return 0

    # ================================================================= 主循环
    def run(self):
        while dpg.is_dearpygui_running():
            self._pump()
            dpg.render_dearpygui_frame()
        self.shutdown()

    def _pump(self):
        now = time.time()
        sp = getattr(self, "spells_page", None)
        if sp is not None:
            try:
                sp.poll()
            except Exception:
                pass
        if self._pending_toggle_overlay:
            self._pending_toggle_overlay = False
            self.toggle_overlay_visible()
        if self._pending_toggle_panel:
            self._pending_toggle_panel = False
            self._bring_to_front()

        if now - self._last_poll > 0.6:
            self._last_poll = now
            self._poll_status()

        if self._toast and now - self._toast_t > 5.0:
            self._toast = ""
            self._set_toast_text()

    def _poll_status(self):
        st = self.proc.status()
        if st != self._status:
            self._status = st
            self._paint_status()
            self._refresh_page("home")

    # ================================================================= 刷新
    def _refresh_all(self):
        self._paint_status()
        self._set_toast_text()
        for name in ("home", "overlay", "profile", "update", "settings",
                     "spells", "builds"):
            self._refresh_page(name)

    def _refresh_page(self, key):
        fn = getattr(self, "_refresh_" + key, None)
        if not fn:
            return
        try:
            fn()
        except Exception:
            pass

    def _paint_status(self):
        st = self._status
        for key, item in (("game", self.dot_game), ("brain", self.dot_brain),
                          ("overlay", self.dot_overlay)):
            try:
                dpg.configure_item(item, color=T.C_GREEN if st.get(key)
                                   else T.C_TEXT_FAINT)
            except Exception:
                pass

    def _toast_msg(self, msg):
        self._toast = msg
        self._toast_t = time.time()
        self._set_toast_text()

    def _set_toast_text(self):
        try:
            dpg.set_value("toast", self._toast or "就绪")
            dpg.configure_item("toast",
                               color=T.C_TEXT if self._toast else T.C_TEXT_FAINT)
        except Exception:
            pass

    # ----------------------------------------------------------- 各页刷新
    def _refresh_home(self):
        st = self._status
        dpg.set_value("home_game", "运行中" if st["game"] else "未运行")
        dpg.configure_item("home_game",
                           color=T.C_GREEN if st["game"] else T.C_TEXT_DIM)
        dpg.set_value("home_brain", "运行中" if st["brain"] else "未运行")
        dpg.configure_item("home_brain",
                           color=T.C_GREEN if st["brain"] else T.C_TEXT_DIM)
        dpg.set_value("home_overlay", "显示中" if st["overlay"] else "未运行")
        dpg.configure_item("home_overlay",
                           color=T.C_GREEN if st["overlay"] else T.C_TEXT_DIM)
        dpg.set_value("home_profile", self.cfg.data["profile"]["active"])
        dpg.configure_item("home_profile", color=T.C_GOLD)
        dpg.set_value(self.lbl_profile,
                      "方案：" + self.cfg.data["profile"]["active"])

        gd = self.cfg.game_dir()
        dpg.set_value("home_gamepath", gd or "未设置（去「设置」里选）")
        hk = self.cfg.hotkey.get("toggle_overlay") or "—"
        dpg.set_value("home_hotkey", hk)
        dpg.set_value("home_source",
                      os.path.join(gd, P.GAME_EXE_NAME) if gd else "—")
        dpg.set_value("home_version", "v" + P.VERSION)

    def _refresh_overlay(self):
        ov = self.cfg.overlay
        dpg.set_value("ov_enabled", bool(ov.get("enabled", True)))
        dpg.set_value("ov_font", float(ov.get("font_scale", 1.0)))
        dpg.set_value("ov_width", int(ov.get("badge_width", 270)))
        dpg.set_value("ov_y", float(ov.get("badge_y", 0.222)))
        dpg.set_value("ov_step", float(ov.get("card_step", 0.1958)))
        vis = self.cfg.overlay_visible()
        dpg.set_value("ov_visible_state",
                      "当前：" + ("显示" if vis else "隐藏"))
        dpg.configure_item("ov_visible_state",
                           color=T.C_GREEN if vis else T.C_TEXT_DIM)
        hk = self.cfg.hotkey
        dpg.set_value("ov_hotkey", hk.get("toggle_overlay") or "F8")
        dpg.set_value("ov_hotkey_panel", hk.get("toggle_panel") or "F9")
        dpg.set_value("ov_hotkey_on", bool(hk.get("enabled", True)))
        if self._hotkey_error:
            dpg.set_value("ov_hotkey_msg", self._hotkey_error)
            dpg.configure_item("ov_hotkey_msg", color=T.C_RED)

    def _refresh_profile(self):
        from azs_pages import refresh_profile_ui
        refresh_profile_ui(self)

    def _refresh_update(self):
        from azs_pages import refresh_update_ui
        refresh_update_ui(self)
        up = getattr(self, "update_page", None)
        if up is not None:
            try:
                up.poll()
            except Exception:
                pass

    def _refresh_spells(self):
        sp = getattr(self, "spells_page", None)
        if sp is not None and not sp._ready and not sp.loading:
            sp.refresh()

    def _refresh_builds(self):
        bp = getattr(self, "builds_page", None)
        if bp is not None:
            bp.tick()

    def _refresh_settings(self):
        dpg.set_value("set_gamedir", self.cfg.game_dir() or "")
        dpg.set_value("set_autolaunch",
                      bool(self.cfg.data["game"].get("auto_launch", True)))
        dpg.set_value("set_autoupdate",
                      bool(self.cfg.update_cfg.get("auto_check", True)))
        dpg.set_value("set_manifest", self.cfg.update_cfg.get("manifest_url") or "")

    # ================================================================= 动作
    def action_start(self):
        ok, msgs = self.proc.start_all()
        parts = ["%s%s" % (name, "✓" if good else "✗") for name, good, _ in msgs]
        bad = [m for name, good, m in msgs if not good]
        text = "启动：" + "  ".join(parts)
        if bad:
            text += "　（" + "；".join(bad) + "）"
        self._toast_msg(text)
        self._poll_status()
        self._refresh_page("home")

    def action_stop(self):
        self.proc.stop_overlay()
        self.proc.stop_brain()
        self._toast_msg("已停止后台进程（游戏不会被关掉）")
        self._poll_status()
        self._refresh_page("home")

    def open_save_editor(self):
        """存档修改器是个独立窗口（和技能面板同一套技术），一键拉起即可。"""
        import subprocess
        from azs_proc import find_python
        script = P.app_path("改档工具", "savepanel.py")
        if not os.path.isfile(script):
            self._toast_msg("找不到存档修改器（改档工具/savepanel.py）")
            return

        # 游戏更新后游戏内部的函数会挪位置，存档修改器的解密环节就对不上了。
        # 它自己自检过一次会记下结论，这里直接照着说，别再让用户干等十几秒。
        why = _save_editor_blocked()
        if why:
            self._toast_msg("存档修改器暂不支持当前游戏版本")
            _msgbox(why, "AZS 存档修改器")
            return

        py = find_python()
        if not py:
            self._toast_msg("找不到 Python 运行时")
            return
        try:
            subprocess.Popen([py, script],
                             cwd=os.path.dirname(script),
                             creationflags=0x08000000)
            self._toast_msg("存档修改器已打开（改之前它会自动备份）")
        except Exception as e:
            self._toast_msg("打开存档修改器失败：%s" % e)

    def action_restart(self):
        self.proc.restart_all()
        self._toast_msg("后台已重启")
        self._poll_status()

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        try:
            self.cfg.data["window"]["width"] = dpg.get_viewport_client_width()
            self.cfg.data["window"]["height"] = dpg.get_viewport_client_height()
            self.cfg.save(sync_settings=False)
        except Exception:
            pass
        for fn in (self.hk.stop, self.cfg.write_control, self.proc.shutdown,
                   dpg.destroy_context):
            try:
                fn()
            except Exception:
                pass

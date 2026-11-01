# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 各页面内容。

约定：每个 build_xxx(tk, parent) 建一个 child_window 并返回它的 tag，
壳（azs_ui.py）按导航切换 show；页面内部的控件直接建在当前容器里，
不额外传 parent，避免容器层级错乱。

刷新：壳每 0.6 秒调一次 tk._refresh_page(key)，页面自己实现
tk._refresh_<key>() 就能拿到更新时机。
"""
import os
import subprocess

import dearpygui.dearpygui as dpg

import azs_config as C
import azs_paths as P
import azs_theme as T

PAD = 26          # 内容区左右留白
CARD_H = 84


# --------------------------------------------------------------------- 小工具
def big_title(text, sub="", sub_wrap=880):
    t = dpg.add_text(text)
    T.set_font(t, "huge")
    if sub:
        dpg.add_spacer(height=2)
        s = dpg.add_text(sub, color=T.C_TEXT_DIM, wrap=sub_wrap)
        T.set_font(s, "small")
    return t


def hint(text, color=None, wrap=820):
    s = dpg.add_text(text, color=color or T.C_TEXT_FAINT, wrap=wrap)
    T.set_font(s, "small")
    return s


def card(title, value_tag, width=206):
    """状态卡片：小标题 + 大号状态值。"""
    with dpg.child_window(width=width, height=CARD_H, border=True,
                          no_scrollbar=True):
        dpg.add_spacer(height=10)
        dpg.add_text("  " + title, color=T.C_TEXT_DIM)
        v = dpg.add_text("  —", tag=value_tag)
        T.set_font(v, "bold")
    return v


def kv_row(label, value_tag, color=None):
    with dpg.group(horizontal=True):
        dpg.add_text(label, color=T.C_TEXT_DIM)
        v = dpg.add_text("—", tag=value_tag, color=color or T.C_TEXT)
        T.set_font(v, "small")
    return v


def pad(width=PAD):
    dpg.add_spacer(width=width)


# ===================================================================== 首页
def build_home(tk, parent):
    tag = "page_home"
    with dpg.child_window(tag=tag, parent=parent, border=False,
                          width=-1, show=False):
        dpg.add_spacer(height=10)
        with dpg.group(horizontal=True):
            pad()
            with dpg.group():
                big_title("开始游戏",
                          "从工具箱点「开始」才会带上调试参数；直接从桌面开游戏，"
                          "插件读不到选卡数据。")
        dpg.add_spacer(height=18)

        with dpg.group(horizontal=True):
            pad()
            card("游戏", "home_game")
            dpg.add_spacer(width=12)
            card("数据大脑", "home_brain")
            dpg.add_spacer(width=12)
            card("悬浮层", "home_overlay")
            dpg.add_spacer(width=12)
            card("当前方案", "home_profile")
        dpg.add_spacer(height=18)

        with dpg.group(horizontal=True):
            pad()
            b = dpg.add_button(label="   开 始   ", height=44,
                               callback=lambda: tk.action_start())
            dpg.bind_item_theme(b, T.accent_button_theme())
            dpg.add_spacer(width=10)
            dpg.add_button(label="   停 止 后 台   ", height=44,
                           callback=lambda: tk.action_stop())
            dpg.add_spacer(width=10)
            dpg.add_button(label="   重 启 后 台   ", height=44,
                           callback=lambda: tk.action_restart())
            dpg.add_spacer(width=10)
            dpg.add_button(label="   存 档 修 改 器   ", height=44,
                           callback=lambda: tk.open_save_editor())
        dpg.add_spacer(height=20)

        with dpg.group(horizontal=True):
            pad()
            with dpg.child_window(width=-1, height=250, border=True):
                dpg.add_spacer(height=12)
                dpg.add_text("  运行信息")
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    with dpg.group():
                        kv_row("游戏目录　　", "home_gamepath")
                        dpg.add_spacer(height=3)
                        kv_row("悬浮层热键　", "home_hotkey")
                        dpg.add_spacer(height=3)
                        kv_row("技能来源　　", "home_source")
                        dpg.add_spacer(height=3)
                        kv_row("工具箱版本　", "home_version")
                dpg.add_spacer(height=10)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    hint("提示：改完设置不用重启游戏；只有换了 brain.py / overlay.ps1 才需要点「重启后台」。",
                         wrap=760)
                dpg.add_spacer(height=10)
    return tag


# ===================================================================== 悬浮层
def build_overlay(tk, parent):
    tag = "page_overlay"
    with dpg.child_window(tag=tag, parent=parent, border=False,
                          width=-1, show=False):
        dpg.add_spacer(height=10)
        with dpg.group(horizontal=True):
            pad()
            with dpg.group():
                big_title("悬浮层", "屏幕上的收益徽标：显示 / 隐藏、字号、宽度、位置。")
        dpg.add_spacer(height=16)

        with dpg.group(horizontal=True):
            pad()
            dpg.add_checkbox(label="启用悬浮层", tag="ov_enabled",
                             default_value=True,
                             callback=cb_overlay_toggle, user_data=tk)
            dpg.add_spacer(width=20)
            b = dpg.add_button(label="   立 即 显 示 / 隐 藏   ", height=34,
                               callback=cb_overlay_flash, user_data=tk)
            dpg.bind_item_theme(b, T.accent_button_theme())
            dpg.add_spacer(width=14)
            dpg.add_text("", tag="ov_visible_state", color=T.C_TEXT_DIM)
            dpg.add_spacer(width=22)
            dpg.add_checkbox(label="选卡时显示流派提示", tag="ov_panel",
                             default_value=True,
                             callback=cb_overlay_panel, user_data=tk)
            dpg.add_spacer(width=16)
            dpg.add_checkbox(label="显示伤害统计框", tag="ov_dps",
                             default_value=True,
                             callback=cb_overlay_dps, user_data=tk)
        dpg.add_spacer(height=16)

        with dpg.group(horizontal=True):
            pad()
            with dpg.child_window(width=440, height=452, border=True):
                dpg.add_spacer(height=14)
                dpg.add_text("  外观（拖完立即生效）")
                dpg.add_spacer(height=8)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    dpg.add_slider_float(label="字号倍率", tag="ov_font", width=230,
                                         default_value=1.0, min_value=0.7,
                                         max_value=2.0, format="%.2f",
                                         callback=cb_overlay_live, user_data=tk)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    dpg.add_slider_int(label="徽标宽度", tag="ov_width", width=230,
                                       default_value=270, min_value=140,
                                       max_value=560,
                                       callback=cb_overlay_live, user_data=tk)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    dpg.add_slider_float(label="纵向位置", tag="ov_y", width=230,
                                         default_value=0.222, min_value=0.05,
                                         max_value=0.85, format="%.3f",
                                         callback=cb_overlay_live, user_data=tk)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    dpg.add_slider_float(label="卡牌间距", tag="ov_step", width=230,
                                         default_value=0.1958, min_value=0.10,
                                         max_value=0.40, format="%.4f",
                                         callback=cb_overlay_live, user_data=tk)
                dpg.add_spacer(height=10)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    hint("纵向位置决定徽标在卡牌上方的落点；卡牌间距要和游戏分辨率匹配，"
                         "错位时先把间距调回 0.1958 再微调。", wrap=390)
                dpg.add_spacer(height=10)

            dpg.add_spacer(width=16)

            with dpg.child_window(width=-1, height=452, border=True):
                dpg.add_spacer(height=14)
                dpg.add_text("  游戏内热键")
                dpg.add_spacer(height=8)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    dpg.add_combo(SUGGESTED_COMBOS, label="显示/隐藏悬浮层",
                                  tag="ov_hotkey", width=160,
                                  callback=cb_hotkey_change, user_data=tk)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    dpg.add_combo(SUGGESTED_COMBOS, label="呼出工具箱",
                                  tag="ov_hotkey_panel", width=160,
                                  callback=cb_hotkey_change, user_data=tk)
                dpg.add_spacer(height=8)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    dpg.add_checkbox(label="启用热键", tag="ov_hotkey_on",
                                     default_value=True,
                                     callback=cb_hotkey_change, user_data=tk)
                dpg.add_spacer(height=8)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    dpg.add_text("", tag="ov_hotkey_msg", color=T.C_TEXT_FAINT,
                                 wrap=420)
                dpg.add_spacer(height=10)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    hint("游戏全屏时也有效，只要工具箱还开着。\n"
                         "提示「已被占用」说明别的软件占了同一个键，换一个再试。",
                         wrap=420)
    return tag


# ===================================================================== 技能数值
def build_spells(tk, parent):
    """委托给 azs_page_spells.SpellsPage（那边有后台读取，界面不会假死）。"""
    from azs_page_spells import SpellsPage
    tk.spells_page = SpellsPage(tk)
    return tk.spells_page.build(parent, tag="page_spells")


# ===================================================================== 方案
LEFT_W = 350
PANEL_H = 468


def build_profile(tk, parent):
    tag = "page_profile"
    with dpg.child_window(tag=tag, parent=parent, border=False,
                          width=-1, show=False):
        dpg.add_spacer(height=10)
        with dpg.group(horizontal=True):
            pad()
            with dpg.group():
                big_title("方案与天赋",
                          "插件读不到游戏里的天赋树，在这里告诉它你点了什么，收益才算得准。")
        dpg.add_spacer(height=12)

        with dpg.group(horizontal=True):
            pad()

            # ---------------- 左：方案管理 ----------------
            with dpg.child_window(width=LEFT_W, height=PANEL_H, border=True):
                dpg.add_spacer(height=12)
                dpg.add_text("  我的方案")
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=14)
                    dpg.add_combo([], tag="pf_list", width=LEFT_W - 42,
                                  callback=cb_profile_pick, user_data=tk)
                dpg.add_spacer(height=8)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=14)
                    dpg.add_button(label=" 新建 ", callback=cb_profile_new,
                                   user_data=tk)
                    dpg.add_button(label=" 复制 ", callback=cb_profile_dup,
                                   user_data=tk)
                    dpg.add_button(label=" 改名 ", callback=cb_profile_rename,
                                   user_data=tk)
                    dpg.add_button(label=" 删除 ", callback=cb_profile_del,
                                   user_data=tk)
                dpg.add_spacer(height=12)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=14)
                    dpg.add_text("方案名字", color=T.C_TEXT_DIM)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=14)
                    dpg.add_input_text(tag="pf_name", width=LEFT_W - 42,
                                       hint="在上面输入新名字，再点新建/改名")
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=14)
                    dpg.add_text("", tag="pf_msg", color=T.C_TEXT_FAINT,
                                 wrap=LEFT_W - 42)
                dpg.add_spacer(height=12)
                dpg.add_separator()
                dpg.add_spacer(height=10)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=14)
                    with dpg.group():
                        hint("一个方案 = 一套「职业 + 天赋 + 参数」。", wrap=LEFT_W - 40)
                        dpg.add_spacer(height=4)
                        hint("换职业玩就新建一个，互不影响；", wrap=LEFT_W - 40)
                        dpg.add_spacer(height=2)
                        hint("改哪一项都是立刻保存的。", wrap=LEFT_W - 40)

            dpg.add_spacer(width=14)

            # ---------------- 右：方案内容 ----------------
            with dpg.child_window(width=-1, height=PANEL_H, border=True):
                dpg.add_spacer(height=12)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=14)
                    dpg.add_text("职业", color=T.C_TEXT_DIM)
                    dpg.add_spacer(width=8)
                    dpg.add_combo([c[1] for c in C.CLASSES], tag="pf_class",
                                  width=170, callback=cb_profile_field,
                                  user_data=tk)
                dpg.add_spacer(height=10)
                dpg.add_separator()
                dpg.add_spacer(height=8)

                dpg.add_text("  天赋开关（点了才打开）", color=T.C_TEXT_DIM)
                dpg.add_spacer(height=4)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    dpg.add_checkbox(label="吞噬魔法（术士宠物）",
                                     tag="pf_t_consume_magic",
                                     callback=cb_profile_field, user_data=tk)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=38)
                    hint("开启后，DoT 持续时间卡才有收益。", wrap=520)
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    dpg.add_checkbox(label="回声连锁（奥法终局）",
                                     tag="pf_t_echo_cascade",
                                     callback=cb_profile_field, user_data=tk)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=38)
                    hint("开启后，回声卡的收益按 1+E+E²+E³ 计算。", wrap=520)

                dpg.add_spacer(height=10)
                dpg.add_separator()
                dpg.add_spacer(height=8)
                dpg.add_text("  收益参数", color=T.C_TEXT_DIM)
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=16)
                    dpg.add_text("怪能活多久", color=T.C_TEXT_DIM)
                    dpg.add_slider_float(tag="pf_life", width=190,
                                         min_value=0.5, max_value=20.0,
                                         format="%.1f 秒",
                                         callback=cb_profile_field, user_data=tk)
                    dpg.add_spacer(width=10)
                    dpg.add_text("吞噬触发间隔", color=T.C_TEXT_DIM)
                    dpg.add_input_float(tag="pf_consume", width=80,
                                        format="%.1f", step=0,
                                        callback=cb_profile_field, user_data=tk)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=38)
                    hint("怪活不到 DoT 跳完时持续时间卡会打折；调大就更值钱。",
                         wrap=520)
        dpg.add_spacer(height=10)
        with dpg.group(horizontal=True):
            pad()
            b = dpg.add_button(label="   保 存 并 应 用 到 后 台   ", height=36,
                               callback=cb_profile_save, user_data=tk)
            dpg.bind_item_theme(b, T.accent_button_theme())
            dpg.add_spacer(width=12)
            dpg.add_text("改任何一项都会自动保存；点这个按钮会顺带重启数据大脑。",
                         color=T.C_TEXT_FAINT)
    return tag


# ===================================================================== 更新
def build_update(tk, parent):
    """委托给 azs_page_update.UpdatePage（那边管检查/下载/安装的完整流程）。"""
    from azs_page_update import UpdatePage
    tk.update_page = UpdatePage(tk)
    return tk.update_page.build(parent, tag="page_update")


# ===================================================================== 设置
def build_settings(tk, parent):
    tag = "page_settings"
    with dpg.child_window(tag=tag, parent=parent, border=False,
                          width=-1, show=False):
        dpg.add_spacer(height=10)
        with dpg.group(horizontal=True):
            pad()
            with dpg.group():
                big_title("设置", "游戏位置、启动行为、更新通道。")
        dpg.add_spacer(height=16)

        with dpg.group(horizontal=True):
            pad()
            with dpg.child_window(width=-1, height=430, border=True):
                dpg.add_spacer(height=14)
                dpg.add_text("  游戏位置")
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=18)
                    dpg.add_input_text(tag="set_gamedir", width=600, readonly=True)
                    dpg.add_button(label="   选择…   ", callback=cb_pick_game,
                                   user_data=tk)
                dpg.add_spacer(height=8)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=18)
                    dpg.add_checkbox(label="点「开始」时顺带启动游戏",
                                     tag="set_autolaunch", default_value=True,
                                     callback=cb_setting_change, user_data=tk)
                dpg.add_spacer(height=18)
                dpg.add_text("  更新")
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=18)
                    dpg.add_checkbox(label="启动时自动检查新版本",
                                     tag="set_autoupdate", default_value=True,
                                     callback=cb_setting_change, user_data=tk)
                dpg.add_spacer(height=8)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=18)
                    dpg.add_text("更新地址", color=T.C_TEXT_DIM)
                    dpg.add_input_text(tag="set_manifest", width=470,
                                       hint="留空 = 只能手动导入更新包")
                dpg.add_spacer(height=18)
                with dpg.group(horizontal=True):
                    dpg.add_spacer(width=18)
                    b = dpg.add_button(label="   保 存 设 置   ", height=36,
                                       callback=cb_save_settings, user_data=tk)
                    dpg.bind_item_theme(b, T.accent_button_theme())
                    dpg.add_spacer(width=10)
                    dpg.add_button(label="   打开插件文件夹   ",
                                   callback=cb_open_folder, user_data=tk)
                    dpg.add_spacer(width=10)
                    dpg.add_button(label="   打开日志文件夹   ",
                                   callback=cb_open_logs, user_data=tk)
    return tag


# --------------------------------------------------------------------- 回调
def cb_overlay_toggle(sender, app_data, tk):
    tk.cfg.overlay["enabled"] = bool(app_data)
    tk.cfg.write_control()
    tk.cfg.save(sync_settings=False)


def cb_overlay_flash(sender, app_data, tk):
    tk.toggle_overlay_visible()


def cb_overlay_dps(sender, app_data, tk):
    """游戏内左侧那块伤害统计（每个技能一行，伤害 + 占比）。"""
    tk.cfg.overlay["dps"] = bool(app_data)
    tk.cfg.write_control()
    tk.cfg.sync_settings()


def cb_overlay_panel(sender, app_data, tk):
    """游戏内那块「流派面板」的开关：技能清单 / 首饰清单 / 双列 DPS。

    走 control.json 而不是 settings.json —— 悬浮层每 200ms 读 control，
    改完立刻见效，不用重启后台。
    """
    tk.cfg.overlay["panel"] = bool(app_data)
    tk.cfg.write_control()
    tk.cfg.sync_settings()


def cb_overlay_live(sender, app_data, tk):
    ov = tk.cfg.overlay
    ov["font_scale"] = float(dpg.get_value("ov_font"))
    ov["badge_width"] = int(dpg.get_value("ov_width"))
    ov["badge_y"] = float(dpg.get_value("ov_y"))
    ov["card_step"] = float(dpg.get_value("ov_step"))
    tk.cfg.write_control()
    tk.cfg.sync_settings()


def cb_hotkey_change(sender, app_data, tk):
    hk = tk.cfg.hotkey
    hk["enabled"] = bool(dpg.get_value("ov_hotkey_on"))
    hk["toggle_overlay"] = dpg.get_value("ov_hotkey") or ""
    hk["toggle_panel"] = dpg.get_value("ov_hotkey_panel") or ""
    tk.cfg.save(sync_settings=False)
    tk._bind_hotkeys()
    msg = tk._hotkey_error or "热键已生效"
    dpg.set_value("ov_hotkey_msg", msg)
    dpg.configure_item("ov_hotkey_msg",
                       color=T.C_RED if tk._hotkey_error else T.C_GREEN)


def cb_setting_change(sender, app_data, tk):
    tk.cfg.data["game"]["auto_launch"] = bool(dpg.get_value("set_autolaunch"))
    tk.cfg.data["update"]["auto_check"] = bool(dpg.get_value("set_autoupdate"))
    tk.cfg.save(sync_settings=False)


def cb_save_settings(sender, app_data, tk):
    tk.cfg.data["update"]["manifest_url"] = dpg.get_value("set_manifest") or ""
    tk.cfg.save()
    tk._toast_msg("设置已保存")


def cb_pick_game(sender, app_data, tk):
    path = ask_directory("选择游戏根目录（AzerothSurvivors.exe 所在的那一层）")
    if not path:
        return
    if os.path.isfile(os.path.join(path, P.GAME_EXE_NAME)):
        tk.cfg.set_game_dir(path)
        tk.cfg.save(sync_settings=False)
        dpg.set_value("set_gamedir", path)
        tk._toast_msg("游戏目录已更新")
    else:
        tk._toast_msg("这个目录里没有 %s" % P.GAME_EXE_NAME)


def ask_directory(title="选择文件夹"):
    """弹系统选文件夹窗口。没有 tkinter 就用 PowerShell 兜底。"""
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        path = filedialog.askdirectory(title=title)
        root.destroy()
        if path:
            return os.path.normpath(path)
    except Exception:
        pass
    try:
        ps = ("Add-Type -AssemblyName System.Windows.Forms;"
              "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
              "$d.Description='%s';"
              "if($d.ShowDialog() -eq 'OK'){$d.SelectedPath}") % title
        out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                             capture_output=True, text=True, timeout=180,
                             creationflags=0x08000000)
        return os.path.normpath((out.stdout or "").strip()) if out.stdout.strip() else ""
    except Exception:
        return ""


def ask_file(title="选择文件", filters=None):
    """弹系统「打开文件」窗口。

    环境里没有 tkinter，先试 tkinter，失败就走系统自带的文件选择框
    （由 azs_dialog 封装，避免在业务代码里拼命令行）。
    filters 形如 [("更新包", "*.zip"), ("所有文件", "*.*")]
    """
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        path = filedialog.askopenfilename(title=title,
                                          filetypes=filters or [("所有文件", "*.*")])
        root.destroy()
        if path:
            return os.path.normpath(path)
    except Exception:
        pass
    try:
        import azs_dialog
        return azs_dialog.open_file(title, filters)
    except Exception:
        return ""


def cb_open_folder(sender, app_data, tk):
    try:
        os.startfile(P.app_dir())
    except Exception:
        pass


def cb_open_logs(sender, app_data, tk):
    try:
        os.startfile(P.logs_dir())
    except Exception:
        pass


# --------------------------------------------------------------------- 方案页
CN2KEY = dict((cn, key) for key, cn in C.CLASSES)


def refresh_profile_ui(tk):
    """把当前方案的内容铺到界面控件上。"""
    names = tk.cfg.profile_names()
    active = tk.cfg.data["profile"]["active"]
    dpg.configure_item("pf_list", items=names)
    dpg.set_value("pf_list", active)
    prof = tk.cfg.profile()
    dpg.set_value("pf_class", C.CLASS_CN.get(prof.get("class"), "圣骑士"))
    for key, _title, _desc in C.TALENT_ITEMS:
        dpg.set_value("pf_t_" + key, bool(prof["talents"].get(key, False)))
    dpg.set_value("pf_life", float(prof.get("dot_lifetime", 4.0)))
    dpg.set_value("pf_consume", float(prof.get("consume_interval", 6.0)))
    dpg.set_value("pf_name", active)
    dpg.set_value("pf_msg", "")


def _msg(text, color=None):
    try:
        dpg.set_value("pf_msg", text)
        dpg.configure_item("pf_msg", color=color or T.C_TEXT_DIM)
    except Exception:
        pass


def cb_profile_pick(sender, app_data, tk):
    if not app_data:
        return
    if tk.cfg.set_active_profile(app_data):
        tk.cfg.save()
        refresh_profile_ui(tk)
        tk._toast_msg("已切换到方案：" + app_data)


def cb_profile_new(sender, app_data, tk):
    name = (dpg.get_value("pf_name") or "").strip()
    ok, err = tk.cfg.add_profile(name)
    if not ok:
        _msg(err, T.C_RED)
        return
    tk.cfg.save()
    refresh_profile_ui(tk)
    tk._toast_msg("已新建方案：" + name)


def cb_profile_dup(sender, app_data, tk):
    base = tk.cfg.data["profile"]["active"]
    name = (dpg.get_value("pf_name") or "").strip() or (base + " 副本")
    if name == base:
        name = base + " 副本"
    ok, err = tk.cfg.add_profile(name, copy_from=base)
    if not ok:
        _msg(err, T.C_RED)
        return
    tk.cfg.save()
    refresh_profile_ui(tk)
    tk._toast_msg("已复制为：" + name)


def cb_profile_rename(sender, app_data, tk):
    old = tk.cfg.data["profile"]["active"]
    new = (dpg.get_value("pf_name") or "").strip()
    ok, err = tk.cfg.rename_profile(old, new)
    if not ok:
        _msg(err, T.C_RED)
        return
    tk.cfg.save()
    refresh_profile_ui(tk)
    tk._toast_msg("已改名为：" + new)


def cb_profile_del(sender, app_data, tk):
    name = tk.cfg.data["profile"]["active"]
    ok, err = tk.cfg.delete_profile(name)
    if not ok:
        _msg(err, T.C_RED)
        return
    tk.cfg.save()
    refresh_profile_ui(tk)
    tk._toast_msg("已删除方案：" + name)


def cb_profile_field(sender, app_data, tk):
    """任何一项改动都立刻落盘 —— 不给用户"忘了点保存"的机会。"""
    prof = tk.cfg.profile()
    prof["class"] = CN2KEY.get(dpg.get_value("pf_class"), "paladin")
    for key, _title, _desc in C.TALENT_ITEMS:
        try:
            prof["talents"][key] = bool(dpg.get_value("pf_t_" + key))
        except Exception:
            pass
    try:
        prof["dot_lifetime"] = float(dpg.get_value("pf_life"))
        prof["consume_interval"] = float(dpg.get_value("pf_consume"))
    except Exception:
        pass
    tk.cfg.save()
    _msg("已保存（重启数据大脑后生效）", T.C_GREEN)


def cb_profile_save(sender, app_data, tk):
    tk.cfg.save()
    ok, msg = tk.proc.restart_all()
    _msg("已保存并重启后台：" + msg if ok else "保存了，但重启后台失败：" + msg,
         T.C_GREEN if ok else T.C_RED)
    tk._toast_msg("方案已应用：" + tk.cfg.data["profile"]["active"])


# --------------------------------------------------------------------- 更新页
def refresh_update_ui(tk):
    up = getattr(tk, "update_page", None)
    if up is not None:
        up.refresh_basic()


from azs_hotkey import SUGGESTED as SUGGESTED_COMBOS   # noqa: E402

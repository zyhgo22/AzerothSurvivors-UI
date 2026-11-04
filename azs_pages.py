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

import contextlib

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
    """状态卡片：小标题 + 大号状态值。width 传 -1 表示撑满所在的表格单元格。"""
    with dpg.child_window(width=width, height=CARD_H, border=True,
                          no_scrollbar=True) as cw:
        dpg.bind_item_theme(cw, T.card_theme())
        dpg.add_spacer(height=12)
        dpg.add_text("  " + title, color=T.C_TEXT_DIM)
        v = dpg.add_text("  —", tag=value_tag)
        T.set_font(v, "bold")
    return v


def card_row(items, gap=6):
    """一排等宽状态卡，宽度按窗口自动平分。

    以前是固定 206px × 4：窗口一窄，第四张卡直接被裁掉半截，
    窗口一宽右边又空一大片。改成分栏表格，永远刚好铺满。
    """
    with dpg.child_window(width=-1, height=CARD_H + 8, border=False,
                          no_scrollbar=True) as wrap:
        _pad_theme(wrap)
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchSame,
                       width=-1, pad_outerX=False,
                       borders_innerV=False, borders_innerH=False,
                       borders_outerH=False, borders_outerV=False,
                       scrollX=False, scrollY=False) as tb:
            dpg.bind_item_theme(tb, _cell_theme(gap))
            for _ in items:
                dpg.add_table_column()
            with dpg.table_row():
                for title, tag in items:
                    with dpg.table_cell():
                        card(title, tag, width=-1)


_pad_theme_cache = None


def _pad_theme(container):
    """给容器加和页面一致的左右留白。"""
    global _pad_theme_cache
    if _pad_theme_cache is None:
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvChildWindow):
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, PAD, 0)
                dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 0)
        _pad_theme_cache = t
    dpg.bind_item_theme(container, _pad_theme_cache)


_cell_theme_cache = {}


def _cell_theme(gap=6):
    """表格单元格间距：卡片之间靠 cell padding 分开，不用在 cell 里塞 spacer。"""
    if gap not in _cell_theme_cache:
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvTable):
                dpg.add_theme_style(dpg.mvStyleVar_CellPadding, gap, 0)
        _cell_theme_cache[gap] = t
    return _cell_theme_cache[gap]


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
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            pad()
            with dpg.group():
                big_title("开始游戏", "")
                dpg.add_spacer(height=3)
                hint("先从工具箱点「开始」才会带上调试参数；直接开游戏，插件读不到数据。",
                     wrap=760)
        dpg.add_spacer(height=16)

        # 两栏：左边看状态，右边动手。以前全是一列往下堆，右半边全空着。
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                       width=-1, pad_outerX=True,
                       borders_innerV=False, borders_innerH=False,
                       borders_outerH=False, borders_outerV=False,
                       scrollX=False, scrollY=False) as tb:
            dpg.bind_item_theme(tb, _cell_theme(16))
            dpg.add_table_column(init_width_or_weight=1.35)
            dpg.add_table_column(init_width_or_weight=1.0)
            with dpg.table_row():
                with dpg.table_cell():
                    with card(276):
                        card_head("运行状态", "工具箱没开时游戏也起不来")
                        rule()
                        _row_table([
                            ("游戏", "home_game", "跟着工具箱一起起停"),
                            ("数据大脑", "home_brain", "读战斗记录、算收益"),
                            ("悬浮层", "home_overlay", "屏幕上的收益徽标"),
                            ("预设方案", "home_profile", "选卡提示按这套打分"),
                        ])
                        dpg.add_spacer(height=4)
                        hint("徽标会跟着游戏窗口走，最小化或切后台自动收起。", wrap=430)
                with dpg.table_cell():
                    with card(276):
                        card_head("快速开始", "开一局")
                        rule()
                        with dpg.group(horizontal=True):
                            dpg.add_spacer(width=20)
                            b = dpg.add_button(label="   开 始 游 戏   ", height=44, width=-1,
                                               callback=lambda: tk.action_start())
                            dpg.bind_item_theme(b, T.accent_button_theme())
                        dpg.add_spacer(height=8)
                        with dpg.group(horizontal=True):
                            dpg.add_spacer(width=20)
                            dpg.add_button(label=" 停止 ", height=34, width=112,
                                           callback=lambda: tk.action_stop())
                            dpg.add_spacer(width=8)
                            dpg.add_button(label=" 重启 ", height=34, width=112,
                                           callback=lambda: tk.action_restart())
                        dpg.add_spacer(height=8)
                        with dpg.group(horizontal=True):
                            dpg.add_spacer(width=20)
                            dpg.add_button(label=" 存档修改器 ", height=34, width=-1,
                                           callback=lambda: tk.open_save_editor())


        dpg.add_spacer(height=12)

        # 运行信息压成一条四格数据带：标签在上、值在下，比一行灰字好读
        with card(96):
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                           width=-1, pad_outerX=True,
                           borders_innerV=False, borders_innerH=False,
                           borders_outerH=False, borders_outerV=False,
                           scrollX=False, scrollY=False) as tb2:
                dpg.bind_item_theme(tb2, _cell_theme(18))
                for _ in range(4):
                    dpg.add_table_column()
                with dpg.table_row():
                    for label, vtag in (("版本", "home_version"),
                                        ("悬浮层热键", "home_hotkey"),
                                        ("技能来源", "home_source"),
                                        ("游戏目录", "home_gamepath")):
                        with dpg.table_cell():
                            t = dpg.add_text(label, color=T.C_TEXT_FAINT)
                            T.set_font(t, "tiny")
                            dpg.add_spacer(height=3)
                            v = dpg.add_text("—", tag=vtag)
                            T.set_font(v, "small")
        dpg.add_spacer(height=18)
    return tag


# ------------------------------------------------- 卡片骨架（照参考图那套）--
@contextlib.contextmanager
def card(height=180, pad_x=22, scroll=False):
    """一张大圆角黑卡，左右各留 pad_x 白。

    用表格做留白：child_window 的 WindowPadding 在 DPG 2.3 里对子容器不生效，
    只有表格的 CellPadding 是可靠的。

    pad_x 同时作为卡片内部内容的左右内缩 —— 卡片里就别再塞
    `group(horizontal) + spacer(width=22)` 了，那样每行都会额外吃高度。

    scroll=True：内容比卡片高的页面（参数多、窗口又小）开竖向滚动，
    免得底下几行被直接截掉、用户永远看不到。
    """
    with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                   width=-1, pad_outerX=True,
                   borders_innerV=False, borders_innerH=False,
                   borders_outerH=False, borders_outerV=False,
                   scrollX=False, scrollY=False) as tb:
        dpg.bind_item_theme(tb, _cell_theme(pad_x))
        dpg.add_table_column(init_width_or_weight=1.0)
        with dpg.table_row():
            with dpg.table_cell():
                with dpg.child_window(width=-1, height=height, border=True,
                                      no_scrollbar=not scroll) as cw:
                    dpg.bind_item_theme(cw, T.surface_theme())
                    yield cw


def card_head(title, sub=""):
    """卡片标题：左上 19px 粗体，右边跟一句灰色小字。"""
    dpg.add_spacer(height=11)
    with dpg.group(horizontal=True):
        dpg.add_spacer(width=20)
        t = dpg.add_text(title)
        T.set_font(t, "bold")
        if sub:
            dpg.add_spacer(width=12)
            h = dpg.add_text(sub, color=T.C_TEXT_FAINT)
            T.set_font(h, "tiny")
    dpg.add_spacer(height=7)


def rule():
    """卡片内的细分割线，两端各缩进 20px。"""
    with dpg.group(horizontal=True):
        dpg.add_spacer(width=20)
        with dpg.group():
            dpg.add_separator()
    dpg.add_spacer(height=1)


def _slabel(text, w=76):
    """滑杆左边那个固定宽度的标签，让几条滑杆能对齐。"""
    t = dpg.add_text(text, color=T.C_TEXT_DIM)
    T.set_font(t, "small")
    dpg.add_spacer(width=1, tag="sl_" + text)
    return t


def _mini(label, value_tag):
    """一行极简信息：灰标签 + 白值，用在页面底部。"""
    t = dpg.add_text(label, color=T.C_TEXT_FAINT)
    T.set_font(t, "tiny")
    dpg.add_spacer(width=6)
    v = dpg.add_text("—", tag=value_tag)
    T.set_font(v, "tiny")
    return v


def _row_table(rows, gap=20, value_font="small"):
    """一组「左标题 / 中间说明 / 右值」的列表行。

    说明不另起一行 —— 另起一行每行就要 48px，三行就把卡片顶穿了。
    压成一行后每行 31px，正好一屏放得下。

    rows: [(label, value_tag, note), ...]
    """
    with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                   width=-1, pad_outerX=True,
                   borders_innerV=False, borders_innerH=False,
                   borders_outerH=False, borders_outerV=False,
                   scrollX=False, scrollY=False) as tb:
        dpg.bind_item_theme(tb, _cell_theme(gap))
        dpg.add_table_column(width_fixed=True)
        dpg.add_table_column(init_width_or_weight=1.0)
        dpg.add_table_column(width_fixed=True)
        for label, value_tag, note in rows:
            with dpg.table_row():
                with dpg.table_cell():
                    t = dpg.add_text(label)
                    T.set_font(t, "base")
                with dpg.table_cell():
                    if note:
                        h = dpg.add_text(note, color=T.C_TEXT_FAINT)
                        T.set_font(h, "tiny")
                with dpg.table_cell():
                    v = dpg.add_text("—", tag=value_tag, color=T.C_TEXT_DIM)
                    T.set_font(v, value_font)
    dpg.add_spacer(height=4)


def _kv_table(pairs, gap=20):
    """两列 key-value，一行放两组，省高度。"""
    with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                   width=-1, pad_outerX=True,
                   borders_innerV=False, borders_innerH=False,
                   borders_outerH=False, borders_outerV=False,
                   scrollX=False, scrollY=False) as tb:
        dpg.bind_item_theme(tb, _cell_theme(gap))
        for _ in range(4):
            dpg.add_table_column(init_width_or_weight=0.42)
        half = (len(pairs) + 1) // 2
        for i in range(half):
            with dpg.table_row():
                for j in (i, i + half):
                    if j < len(pairs):
                        label, tag = pairs[j]
                        with dpg.table_cell():
                            t = dpg.add_text(label, color=T.C_TEXT_FAINT)
                            T.set_font(t, "tiny")
                        with dpg.table_cell():
                            v = dpg.add_text("—", tag=tag)
                            T.set_font(v, "small")
                    else:
                        with dpg.table_cell():
                            dpg.add_text("")
                        with dpg.table_cell():
                            dpg.add_text("")
    dpg.add_spacer(height=2)


# ===================================================================== 悬浮层
def build_overlay(tk, parent):
    tag = "page_overlay"
    with dpg.child_window(tag=tag, parent=parent, border=False,
                          width=-1, show=False):
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            pad()
            with dpg.group():
                big_title("悬浮层", "")
        dpg.add_spacer(height=8)

        # 顶部只留「总开关 + 试一下 + 状态」三件。
        # 以前三个显示开关也挤在这一行，窗口一窄最后一个"仅游戏在前台时显示"
        # 直接被顶出右边界，只剩下半个"仅"字。它们现在归到右栏卡里。
        with dpg.group(horizontal=True):
            pad()
            dpg.add_checkbox(label="启用悬浮层", tag="ov_enabled",
                             default_value=True,
                             callback=cb_overlay_toggle, user_data=tk)
            dpg.add_spacer(width=18)
            b = dpg.add_button(label="   立 即 显 示 / 隐 藏   ", height=34,
                               callback=cb_overlay_flash, user_data=tk)
            dpg.bind_item_theme(b, T.accent_button_theme())
            dpg.add_spacer(width=14)
            dpg.add_text("", tag="ov_visible_state", color=T.C_TEXT_DIM)
            dpg.add_spacer(width=26)
            dpg.add_checkbox(label="启用热键", tag="ov_hotkey_on",
                             default_value=True,
                             callback=cb_hotkey_change, user_data=tk)
            dpg.add_spacer(width=10)
            dpg.add_text("", tag="ov_hotkey_msg", color=T.C_TEXT_FAINT)
        dpg.add_spacer(height=10)

        # 左右分栏必须走表格。放在 group(horizontal=True) 里的话，
        # child_window(width=-1) 会按整个父宽算，右边那栏直接顶到窗口外。
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                       width=-1, pad_outerX=True,
                       borders_innerV=False, borders_innerH=False,
                       borders_outerH=False, borders_outerV=False,
                       scrollX=False, scrollY=False) as tb:
            dpg.bind_item_theme(tb, _cell_theme(20))
            dpg.add_table_column(init_width_or_weight=1.14)
            dpg.add_table_column(init_width_or_weight=1.0)
            with dpg.table_row():
                with dpg.table_cell():
                    _overlay_left(tk)
                with dpg.table_cell():
                    _overlay_right(tk)
    return tag


def _ov_slider(item, tk):
    """一格外观参数：小号灰标签在上，定宽滑杆在下。

    滑杆宽度写死 120：用 -1 会按外层容器算宽，卡片里必然溢出。
    """
    label, tag, default, lo, hi, fmt = item
    with dpg.group():
        t = dpg.add_text(label, color=T.C_TEXT_DIM)
        T.set_font(t, "tiny")
        dpg.add_spacer(height=1)
        if tag == "ov_width":
            dpg.add_slider_int(tag=tag, width=120,
                               default_value=int(default),
                               min_value=lo, max_value=hi,
                               format=fmt,
                               callback=cb_overlay_live,
                               user_data=tk)
        else:
            dpg.add_slider_float(tag=tag, width=120,
                                 default_value=float(default),
                                 min_value=lo, max_value=hi,
                                 format=fmt,
                                 callback=cb_overlay_live,
                                 user_data=tk)
        dpg.bind_item_theme(tag, T.slider_theme())


def _overlay_left(tk):
    """外观：四个滑杆 + 说明。卡高与右栏对齐。"""
    with card(316):
        # 标题行自带「恢复默认」：单独起一行放按钮的话，卡片底部会把它切掉。
        dpg.add_spacer(height=9)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=20)
            t = dpg.add_text("外观")
            T.set_font(t, "bold")
            dpg.add_spacer(width=12)
            h = dpg.add_text("拖完立即生效", color=T.C_TEXT_FAINT)
            T.set_font(h, "tiny")
            dpg.add_spacer(width=14)
            rb = dpg.add_button(label=" 恢复默认 ", height=24,
                                callback=cb_overlay_defaults, user_data=tk)
            dpg.bind_item_theme(rb, T.ghost_button_theme())
        dpg.add_spacer(height=7)
        rule()
        rows = (
            ("字号倍率", "ov_font", 1.0, 0.7, 2.0, "%.2f"),
            ("徽标宽度", "ov_width", 286, 240, 560, "%d"),
            ("纵向位置", "ov_y", 0.222, 0.05, 0.85, "%.3f"),
            ("卡牌间距", "ov_step", 0.1958, 0.12, 0.40, "%.4f"),
        )
        # 这里千万别用嵌套表格：卡片里的 table 设 width=-1 会按外层表格的
        # 宽度算，直接溢出卡片左边被裁掉（标签只剩半个字）。而 DPG 自带的
        # label 在 width=-1 时又会被画到卡片外。所以用 2×2 小格：
        # 标签在上、定宽滑杆在下，宽度自己算得出来。
        for pair in (rows[:2], rows[2:]):
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=18)
                for item in pair:
                    _ov_slider(item, tk)
                    dpg.add_spacer(width=16)
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=20)
            hint("纵向位置决定徽标落在卡牌的哪一段；卡牌间距要和游戏分辨率匹配，"
                 "错位时先点「恢复默认」再微调。", wrap=310)


def _overlay_right(tk):
    """右栏：显示内容 + 游戏内热键，一张卡里分两节。"""
    with card(320):
        card_head("显示设置", "改完立即生效")
        rule()
        # 三个开关排成 2+1：三个各占一行的话，卡片高度会顶到窗口外，
        # 底下的「游戏内热键」整节直接看不见。
        opts = (
            ("选卡时显示预设面板", "ov_panel", cb_overlay_panel,
             "触发选卡时，在游戏里侧显示这块流派面板。"),
            ("显示伤害统计框", "ov_dps", cb_overlay_dps,
             "游戏左侧那块每技能一行的伤害 / 占比统计。"),
            ("仅游戏在前台时显示", "ov_onlyfg", cb_overlay_onlyfg,
             "关掉后悬浮层会变成到处贴的贴图：游戏最小化、切到浏览器，"
             "它仍然盖在最上面。"),
        )
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchSame,
                       width=-1, pad_outerX=False,
                       borders_innerV=False, borders_innerH=False,
                       borders_outerH=False, borders_outerV=False,
                       scrollX=False, scrollY=False) as tb:
            dpg.bind_item_theme(tb, _cell_theme(8))
            dpg.add_table_column()
            dpg.add_table_column()
            for i in range(0, 3, 2):
                with dpg.table_row():
                    for k in (i, i + 1):
                        with dpg.table_cell():
                            if k >= len(opts):
                                continue
                            label, tag, cb, tip = opts[k]
                            c = dpg.add_checkbox(label=label, tag=tag,
                                                 default_value=True,
                                                 callback=cb, user_data=tk)
                            with dpg.tooltip(c):
                                dpg.add_text(tip, wrap=250,
                                             color=T.C_TEXT_DIM)
        dpg.add_spacer(height=6)
        rule()
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=20)
            t = dpg.add_text("游戏内热键")
            T.set_font(t, "small")
            dpg.add_spacer(width=8)
            hint("全屏也有效", wrap=120)
        dpg.add_spacer(height=2)
        # 两个下拉并排：竖着排第二个会被卡片下沿切掉。
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=20)
            for label, tag in (("显示 / 隐藏", "ov_hotkey"),
                               ("呼出工具箱", "ov_hotkey_panel")):
                with dpg.group():
                    t = dpg.add_text(label, color=T.C_TEXT_DIM)
                    T.set_font(t, "tiny")
                    dpg.add_spacer(height=1)
                    dpg.add_combo(SUGGESTED_COMBOS, tag=tag, width=130,
                                  callback=cb_hotkey_change, user_data=tk)
                dpg.add_spacer(width=16)


# ===================================================================== 技能数值
def build_spells(tk, parent):
    """委托给 azs_page_spells.SpellsPage（那边有后台读取，界面不会假死）。"""
    from azs_page_spells import SpellsPage
    tk.spells_page = SpellsPage(tk)
    return tk.spells_page.build(parent, tag="page_spells")


# ===================================================================== 方案
LEFT_W = 350
PANEL_H = 446


def build_profile(tk, parent):
    tag = "page_profile"
    with dpg.child_window(tag=tag, parent=parent, border=False,
                          width=-1, show=False):
        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True):
            pad()
            with dpg.group():
                big_title("方案与天赋", "")
        dpg.add_spacer(height=6)

        # 两栏走表格，理由同悬浮层页：group(horizontal) 里的 width=-1
        # 会按整页宽算，右栏直接被顶到窗口外面。
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                       width=-1, pad_outerX=True,
                       borders_innerV=False, borders_innerH=False,
                       borders_outerH=False, borders_outerV=False,
                       scrollX=False, scrollY=False) as _tb:
            dpg.bind_item_theme(_tb, _cell_theme(20))
            dpg.add_table_column(init_width_or_weight=0.78)
            dpg.add_table_column(init_width_or_weight=1.0)
            with dpg.table_row():
                with dpg.table_cell():
                    _profile_left(tk)
                with dpg.table_cell():
                    _profile_right(tk)
    return tag


def _profile_left(tk):
    # ---------------- 左：方案管理 ----------------
    with card(PANEL_H):
        card_head("我的方案", "一个方案 = 一套职业+天赋+参数")
        dpg.add_spacer(height=4)
        rule()
        with dpg.group(horizontal=True):
            dpg.add_combo([], tag="pf_list", width=-1,
                          callback=cb_profile_pick, user_data=tk)
        dpg.add_spacer(height=8)
        with dpg.group(horizontal=True):
            dpg.add_button(label=" 新建 ", callback=cb_profile_new,
                           user_data=tk)
            dpg.add_button(label=" 复制 ", callback=cb_profile_dup,
                           user_data=tk)
            dpg.add_button(label=" 改名 ", callback=cb_profile_rename,
                           user_data=tk)
            dpg.add_button(label=" 删除 ", callback=cb_profile_del,
                           user_data=tk)
        dpg.add_spacer(height=12)
        lb = dpg.add_text("方案名字", color=T.C_TEXT_DIM)
        T.set_font(lb, "small")
        dpg.add_spacer(height=4)
        dpg.add_input_text(tag="pf_name", width=-1,
                           hint="在上面输入新名字，再点新建/改名")
        dpg.add_spacer(height=6)
        dpg.add_text("", tag="pf_msg", color=T.C_TEXT_FAINT, wrap=300)
        dpg.add_spacer(height=8)
        rule()
        dpg.add_spacer(height=6)
        # 保存按钮原来放在卡片外面，640 高的窗口里直接被切走，现在收进卡里。
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=20)
            b = dpg.add_button(label="  保 存 并 应 用  ", height=32,
                               callback=cb_profile_save, user_data=tk)
            dpg.bind_item_theme(b, T.accent_button_theme())


def _profile_right(tk):
    # ---------------- 右：方案内容 ----------------
    with card(PANEL_H):
        card_head("方案内容", "改一项自动保存")
        dpg.add_spacer(height=2)
        rule()
        with dpg.group(horizontal=True):
            lb = dpg.add_text("职业", color=T.C_TEXT_DIM)
            T.set_font(lb, "small")
            dpg.add_spacer(width=8)
            dpg.add_combo([c[1] for c in C.CLASSES], tag="pf_class",
                          width=170, callback=cb_profile_field,
                          user_data=tk)
        dpg.add_spacer(height=4)

        lb2 = dpg.add_text("天赋开关", color=T.C_TEXT_DIM)
        T.set_font(lb2, "tiny")
        dpg.add_spacer(height=2)
        dpg.add_checkbox(label="吞噬魔法（术士宠物 · DoT 持续卡才有收益）",
                         tag="pf_t_consume_magic",
                         callback=cb_profile_field, user_data=tk)
        dpg.add_checkbox(label="回声连锁（奥法终局 · 按 1+E+E²+E³ 算）",
                         tag="pf_t_echo_cascade",
                         callback=cb_profile_field, user_data=tk)

        dpg.add_spacer(height=8)
        rule()
        lb3 = dpg.add_text("收益参数", color=T.C_TEXT_DIM)
        T.set_font(lb3, "tiny")
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_text("怪能活多久", color=T.C_TEXT_DIM)
            dpg.add_slider_float(tag="pf_life", width=170,
                                 min_value=0.5, max_value=20.0,
                                 format="%.1f 秒",
                                 callback=cb_profile_field, user_data=tk)
        with dpg.group(horizontal=True):
            dpg.add_text("吞噬间隔", color=T.C_TEXT_DIM)
            dpg.add_input_float(tag="pf_consume", width=80,
                                format="%.1f", step=0,
                                callback=cb_profile_field, user_data=tk)



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

        # 上下堆两张卡在 640 高的默认窗口里永远差几十像素：卡够高内容才放得下，
        # 按钮就被切；卡压矮，内容又被切。改成左右并排，一次省掉 130px。
        # 左右留白不能用 child_window 的 WindowPadding 做 —— DPG 2.3 里
        # 这个 style 对 child_window 不生效（首页卡片一直是贴着边 6px 的原因）。
        # 改由表格自己的 pad_outerX 负责：左右各留一个 CellPadding。
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                       width=-1, pad_outerX=True,
                       borders_innerV=False, borders_innerH=False,
                       borders_outerH=False, borders_outerV=False,
                       scrollX=False, scrollY=False) as tb:
            dpg.bind_item_theme(tb, _cell_theme(20))
            dpg.add_table_column(init_width_or_weight=1.0)
            dpg.add_table_column(init_width_or_weight=0.92)
            with dpg.table_row():
                with dpg.table_cell():
                    _settings_game(tk)
                with dpg.table_cell():
                    _settings_update(tk)

    return tag


def _settings_game(tk):
    """左卡：游戏装在哪、要不要顺手拉起来。"""
    with dpg.child_window(width=-1, height=408, border=True,
                          no_scrollbar=True):
        dpg.bind_item_theme(dpg.last_item(), T.card_theme())
        card_head("游戏位置", "指到 AzerothSurvivors.exe 那一层")
        dpg.add_spacer(height=2)
        rule()
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=18)
            lb = dpg.add_text("游戏目录", color=T.C_TEXT_DIM)
            T.set_font(lb, "small")
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=18)
            dpg.add_input_text(tag="set_gamedir", width=196, readonly=True)
            dpg.add_spacer(width=8)
            dpg.add_button(label=" 选择… ", height=30, callback=cb_pick_game,
                           user_data=tk)
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=18)
            hint("换目录后要点一下保存，后台才会按新路径重启。", wrap=300)
        dpg.add_spacer(height=12)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=18)
            dpg.add_checkbox(label="点「开始」时顺带启动游戏",
                             tag="set_autolaunch", default_value=True,
                             callback=cb_setting_change, user_data=tk)
        dpg.add_spacer(height=10)
        rule()
        dpg.add_spacer(height=8)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=18)
            b = dpg.add_button(label="   保 存 设 置   ", height=34,
                               callback=cb_save_settings, user_data=tk)
            dpg.bind_item_theme(b, T.accent_button_theme())


def _settings_update(tk):
    """右卡：自动检查要不要开、清单从哪拉。"""
    with dpg.child_window(width=-1, height=408, border=True,
                          no_scrollbar=True):
        dpg.bind_item_theme(dpg.last_item(), T.card_theme())
        card_head("更新", "默认走 ghproxy 镜像")
        dpg.add_spacer(height=2)
        rule()
        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=18)
            dpg.add_checkbox(label="启动时自动检查新版本",
                             tag="set_autoupdate", default_value=True,
                             callback=cb_setting_change, user_data=tk)
        dpg.add_spacer(height=10)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=18)
            lb = dpg.add_text("更新地址", color=T.C_TEXT_DIM)
            T.set_font(lb, "small")
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=18)
            dpg.add_input_text(tag="set_manifest", width=-1)
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=18)
            hint("拉清单走的地址。能直连 GitHub 的话，换成原始 raw 链接会快很多。",
                 wrap=300)
        dpg.add_spacer(height=10)
        rule()
        dpg.add_spacer(height=8)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=18)
            dpg.add_button(label="  打开插件文件夹  ", height=32,
                           callback=cb_open_folder, user_data=tk)
            dpg.add_spacer(width=8)
            dpg.add_button(label="  打开日志文件夹  ", height=32,
                           callback=cb_open_logs, user_data=tk)


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


def cb_overlay_onlyfg(sender, app_data, tk):
    """只在游戏位于前台时显示。

    关掉它，悬浮层就会变成「永远置顶的贴图」—— 游戏最小化、切到浏览器
    都还赖在屏幕上。默认开启。
    """
    tk.cfg.overlay["only_fg"] = bool(app_data)
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


def cb_overlay_defaults(sender, app_data, tk):
    """把四个外观参数拉回出厂值。

    徽标错位最麻烦的是一次次试出来的怪值，留一个一键回到能用的起点。
    """
    for tag, val in (("ov_font", 1.0), ("ov_width", 286),
                     ("ov_y", 0.222), ("ov_step", 0.1958)):
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, val)
    cb_overlay_live(sender, None, tk)


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

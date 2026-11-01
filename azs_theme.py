# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 外观（主题 / 字体）。

单独拎出来，是因为 DearPyGui 2.3 有两个很坑的地方，踩过一次不想再踩第二次：

1. **viewport 标题不能带中文。** 带中文在某些 Windows 环境下会让整个进程直接崩掉，
   连错误都抓不到。所以窗口标题一律用 "AZS Toolkit"，界面上再画中文标题。

2. **字体必须 add_font + bind_font。** 2.3 里 `with dpg.font(...)` 已经不是容器了，
   写 with 块不仅字体不生效（中文全变问号），还会抛「No container to pop」。
"""
import os

import dearpygui.dearpygui as dpg

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑（Win8+ 都有）
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\simhei.ttf",    # 黑体
    r"C:\Windows\Fonts\deng.ttf",      # 等线
    r"C:\Windows\Fonts\simsun.ttc",    # 宋体（最后兜底）
]

# ---- 配色：深色 + 魔兽那种暗金，晚上看不刺眼 --------------------------------
C_BG = (16, 18, 22)
C_NAV = (12, 13, 16)
C_PANEL = (26, 29, 35)
C_PANEL_HI = (33, 37, 44)
C_BORDER = (44, 49, 58)
C_TEXT = (206, 212, 222)
C_TEXT_DIM = (128, 136, 148)
C_TEXT_FAINT = (86, 93, 104)
C_GOLD = (232, 184, 75)
C_BLUE = (86, 156, 214)
C_GREEN = (86, 190, 126)
C_RED = (232, 106, 106)
C_AMBER = (222, 165, 70)

_fonts = {}


def load_fonts():
    """挂载中文字体，返回是否成功。"""
    path = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
    if not path:
        return False
    try:
        with dpg.font_registry():
            _fonts["base"] = dpg.add_font(path, 16)
            _fonts["small"] = dpg.add_font(path, 14)
            _fonts["tiny"] = dpg.add_font(path, 13)
            _fonts["bold"] = dpg.add_font(path, 21)
            _fonts["huge"] = dpg.add_font(path, 30)
        dpg.bind_font(_fonts["base"])
        return True
    except Exception:
        return False


def font(name="base"):
    return _fonts.get(name)


def set_font(item, name="base"):
    """给单个控件换字号（标题用 bold/huge，辅助说明用 small/tiny）。"""
    f = _fonts.get(name)
    if f and item:
        try:
            dpg.bind_item_font(item, f)
        except Exception:
            pass
    return item


def theme():
    """全局主题：暗色底、克制的高亮、按钮有明确的悬停反馈。"""
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, C_BG)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, C_BG)
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, C_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_Border, C_BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_Text, C_TEXT)
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, C_TEXT_FAINT)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, C_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, C_PANEL_HI)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (44, 49, 60))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg, C_NAV)
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, C_NAV)
            dpg.add_theme_color(dpg.mvThemeCol_Button, (38, 42, 50))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (52, 58, 70))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (64, 71, 85))
            dpg.add_theme_color(dpg.mvThemeCol_Header, (44, 49, 60))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (55, 61, 74))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (66, 73, 88))
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark, C_GOLD)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, C_GOLD)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, (245, 202, 110))
            dpg.add_theme_color(dpg.mvThemeCol_Separator, C_BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, (54, 60, 72))
            dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg, C_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderStrong, C_BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderLight, (34, 38, 46))
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBg, (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBgAlt, (255, 255, 255, 6))
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 10, 6)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 8, 8)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize, 10)
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 12, 7)
        with dpg.theme_component(dpg.mvSelectable):
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 0, 2)
    dpg.bind_theme(t)
    return t


def accent_button_theme(color=None):
    """主行动按钮（开始游戏 / 检查更新）的金色主题。"""
    color = color or C_GOLD
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, color)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,
                                tuple(min(255, c + 18) for c in color))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,
                                tuple(max(0, c - 14) for c in color))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (24, 26, 30))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 5)
    return t


def ghost_button_theme():
    """次要按钮：透明底 + 细边，不抢视线。"""
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (38, 43, 52))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (50, 56, 68))
            dpg.add_theme_color(dpg.mvThemeCol_Text, C_TEXT_DIM)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 1)
            dpg.add_theme_color(dpg.mvThemeCol_Border, C_BORDER)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
    return t

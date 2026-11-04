# -*- coding: utf-8 -*-
"""AZS 工具箱 —— 外观（主题 / 字体）。

单独拎出来，是因为 DearPyGui 2.3 有几个很坑的地方，踩过一次不想再踩第二次：

1. **viewport 标题不能带中文。** 带中文在某些 Windows 环境下会让整个进程直接崩掉，
   连错误都抓不到。所以窗口标题一律用 "AZS Toolkit"，界面上再画中文标题。

2. **字体必须 add_font + bind_font。** 2.3 里 `with dpg.font(...)` 已经不是容器了，
   写 with 块不仅字体不生效（中文全变问号），还会抛「No container to pop」。

3. **主题会继承给子容器。** 给外层套 WindowPadding / ChildBorderSize，里面的卡片
   会被一起改掉，边框和内边距都保不住。要"只刷漆不改骨架"就单开一个主题。

== v1.2 视觉语言 ==
抄的是游戏手机控制台那一套：纯黑底、荧光绿强调、左侧图标导航、大圆角卡片。
跟之前"深灰 + 暗金"是两个东西，不是换色号：
  - 底色压到纯黑 #000000，卡片 #0D1110 靠 1px 边框分层
  - 强调色换成荧光绿 #3DD97F，开关 / 选中 / 数值全用它
  - 导航带图标，选中项整行暗绿底 + 绿字
  - 卡片标题 19px 粗体，右上角放分段控件
"""
import os

import dearpygui.dearpygui as dpg

FONT_DIR = r"C:\Windows\Fonts"
FONT_REG = [r"msyh.ttc", r"msyh.ttf", r"deng.ttf", r"simhei.ttf"]
FONT_BOLD = [r"msyhbd.ttc", r"msyhbd.ttf", r"simhei.ttf"]


# ---- 配色：近黑 + 薄荷绿 ----------------------------------------------------
# 纯黑 (#000) 会把卡片边界吃掉，也显得廉价；改成带一点绿味的近黑，
# 卡片靠 1px 边框和 2~3 级明度差分层。
C_BG = (6, 8, 7)             # 页面底  近黑 #060807
C_NAV = (8, 11, 10)          # 导航底  比页面亮一点点
C_PANEL = (15, 20, 18)       # 卡片    #0F1412
C_PANEL_HI = (23, 30, 27)    # 卡片/输入框悬停
C_SUNK = (11, 15, 13)        # 输入框 / 凹陷区
C_BORDER = (36, 46, 41)      # 边框    #242E29
C_LINE = (26, 34, 30)        # 卡片内的细分隔线

C_TEXT = (236, 242, 238)     # 正文
C_TEXT_DIM = (154, 168, 161)  # 次要
C_TEXT_FAINT = (124, 138, 132)  # 说明

C_GREEN = (70, 224, 140)     # 强调主色 #46E08C
C_GREEN_DIM = (22, 92, 56)   # 绿色暗部（选中底）
C_ACCENT = C_GREEN
C_GOLD = C_GREEN             # 旧名字，页面还在引用，值跟绿走
C_BLUE = (86, 156, 214)
C_RED = (255, 92, 92)
C_AMBER = (232, 176, 74)

_fonts = {}


def _pick(names):
    for n in names:
        p = os.path.join(FONT_DIR, n)
        if os.path.exists(p):
            return p
    return None


def load_fonts():
    """挂载中文字体，返回是否成功。正文和粗体分开注册，标题才有字重差。"""
    reg = _pick(FONT_REG)
    if not reg:
        return False
    bold = _pick(FONT_BOLD) or reg
    try:
        with dpg.font_registry():
            _fonts["tiny"] = dpg.add_font(reg, 12)
            _fonts["small"] = dpg.add_font(reg, 13)
            _fonts["base"] = dpg.add_font(reg, 15)
            _fonts["bold"] = dpg.add_font(bold, 19)
            _fonts["huge"] = dpg.add_font(bold, 27)
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
    """全局主题：纯黑底、绿色强调、控件深色凹陷。"""
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, C_BG)
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, C_BG)
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (16, 20, 19))
            dpg.add_theme_color(dpg.mvThemeCol_Border, C_BORDER)
            dpg.add_theme_color(dpg.mvThemeCol_Text, C_TEXT)
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, C_TEXT_FAINT)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, C_SUNK)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, C_PANEL_HI)
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (26, 33, 30))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg, C_BG)
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, C_BG)
            dpg.add_theme_color(dpg.mvThemeCol_Button, (23, 29, 27))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (33, 41, 38))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (42, 52, 48))
            dpg.add_theme_color(dpg.mvThemeCol_Header, (23, 29, 27))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (31, 39, 36))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (39, 49, 45))
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark, C_GREEN)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, C_GREEN)
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, (120, 240, 170))
            dpg.add_theme_color(dpg.mvThemeCol_Separator, C_LINE)
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, (44, 54, 50))
            dpg.add_theme_color(dpg.mvThemeCol_TableHeaderBg, (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderStrong, C_LINE)
            dpg.add_theme_color(dpg.mvThemeCol_TableBorderLight, C_LINE)
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBg, (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_TableRowBgAlt, (255, 255, 255, 6))
            dpg.add_theme_color(dpg.mvThemeCol_ResizeGrip, (0, 0, 0, 0))
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 11, 6)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 10, 9)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 14)
            dpg.add_theme_style(dpg.mvStyleVar_PopupRounding, 10)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize, 10)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarRounding, 5)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_TabRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 0)
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 14, 7)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 9)
        with dpg.theme_component(dpg.mvCheckbox):
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 5)
        with dpg.theme_component(dpg.mvSelectable):
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 0, 2)
    dpg.bind_theme(t)
    return t


# ------------------------------------------------------------------ 按钮
def accent_button_theme(color=None):
    """主行动按钮：荧光绿底 + 黑字。"""
    color = color or C_GREEN
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, color)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,
                                tuple(min(255, c + 16) for c in color))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,
                                tuple(max(0, c - 22) for c in color))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (4, 16, 10))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 9)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 20, 8)
    return t


def ghost_button_theme():
    """次要按钮：透明底 + 一圈淡边。"""
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 0, 0, 0))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (24, 31, 28))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (33, 42, 38))
            dpg.add_theme_color(dpg.mvThemeCol_Text, C_TEXT_DIM)
            dpg.add_theme_color(dpg.mvThemeCol_Border, (44, 54, 50))
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 1)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 9)
    return t


_pill_on = None
_pill_off = None


_slider_theme = None


def slider_theme():
    """滑杆：轨道要比卡片亮一点，否则纯黑底上只剩一个小绿点。"""
    global _slider_theme
    if _slider_theme is not None:
        return _slider_theme
    with dpg.theme() as t:
        for kind in (dpg.mvSliderFloat, dpg.mvSliderInt):
            with dpg.theme_component(kind):
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (31, 39, 36))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (38, 48, 44))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (44, 56, 51))
                dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 8)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 8)
    _slider_theme = t
    return t


def brand_theme():
    """顶栏品牌字：荧光绿。"""
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvText):
            dpg.add_theme_color(dpg.mvThemeCol_Text, C_GREEN)
    return t


def dim_theme():
    """顶栏的小灰字。"""
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvText):
            dpg.add_theme_color(dpg.mvThemeCol_Text, C_TEXT_FAINT)
    return t


def pill_theme(on=True):
    """分段控件 / 状态芯片：选中的暗绿底绿字，未选中的透明灰字。"""
    global _pill_on, _pill_off
    cache = _pill_on if on else _pill_off
    if cache is not None:
        return cache
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            if on:
                dpg.add_theme_color(dpg.mvThemeCol_Button, (24, 92, 56))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (30, 110, 67))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (20, 80, 48))
                dpg.add_theme_color(dpg.mvThemeCol_Text, C_GREEN)
            else:
                # 芯片要有底色才像参考图那排胶囊，透明的只剩几个灰字
                dpg.add_theme_color(dpg.mvThemeCol_Button, (22, 27, 25))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (30, 37, 34))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (38, 47, 43))
                dpg.add_theme_color(dpg.mvThemeCol_Text, C_TEXT_FAINT)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 8)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 14, 5)
    if on:
        _pill_on = t
    else:
        _pill_off = t
    return t


# ------------------------------------------------------------------ 导航
# 参考图里导航是「图标 + 文字」，选中项图标和文字都变绿、整行一块暗绿底。
_nav_cache = {}


def _make_nav_theme(active):
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvSelectable):
            if active:
                dpg.add_theme_color(dpg.mvThemeCol_Header, (18, 46, 32))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (22, 56, 39))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (24, 62, 43))
                dpg.add_theme_color(dpg.mvThemeCol_Text, C_GREEN)
            else:
                dpg.add_theme_color(dpg.mvThemeCol_Header, (0, 0, 0, 0))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (16, 21, 19))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (22, 29, 26))
                dpg.add_theme_color(dpg.mvThemeCol_Text, C_TEXT_DIM)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 10)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 0, 2)
    return t


def nav_theme(active=False):
    """导航项主题，两种状态各建一次，之后复用（别每次切页都新建，会泄漏）。"""
    key = "on" if active else "off"
    if key not in _nav_cache:
        _nav_cache[key] = _make_nav_theme(active)
    return _nav_cache[key]


# ------------------------------------------------------------------ 容器
_card_theme = None


def card_theme():
    """状态卡片：比页面亮一点点 + 一圈细边 + 大圆角。"""
    global _card_theme
    if _card_theme is not None:
        return _card_theme
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvChildWindow):
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, C_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_Border, C_BORDER)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 14)
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 18, 0)
    _card_theme = t
    return t


_surface_theme = None


def surface_theme():
    """通用卡片：只改底色 / 边框 / 圆角，不动内边距。"""
    global _surface_theme
    if _surface_theme is not None:
        return _surface_theme
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvChildWindow):
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, C_PANEL)
            dpg.add_theme_color(dpg.mvThemeCol_Border, C_BORDER)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 14)
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
    _surface_theme = t
    return t


_bar_theme = None


def bar_theme():
    """顶栏 / 侧栏：纯黑底、无边框。"""
    global _bar_theme
    if _bar_theme is not None:
        return _bar_theme
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvChildWindow):
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, C_BG)
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 0)
    _bar_theme = t
    return t


def paint_cards(root):
    """把 root 底下所有带边框的容器刷成黑卡（没单独绑主题的那些）。"""
    try:
        kids = dpg.get_item_children(root, 1) or []
    except Exception:
        return
    for k in kids:
        try:
            if dpg.get_item_type(k).endswith("ChildWindow"):
                cfg = dpg.get_item_configuration(k)
                if cfg.get("border"):
                    try:
                        has = bool(dpg.get_item_theme(k))
                    except Exception:
                        has = False
                    if not has:
                        dpg.bind_item_theme(k, surface_theme())
        except Exception:
            pass
        try:
            paint_cards(k)
        except Exception:
            pass

"""AZS 技能面板 —— 改技能的伤害 / 冷却 / 施法距离 / 范围半径 / 弹道 / 持续。

界面用 DearPyGui，读写直接用同目录 spellcore.py（ctypes 改内存或改 exe）。
启动：双击 技能面板.cmd

命令行：
  python spellpanel.py                 正常启动
  python spellpanel.py --file          默认改 exe 文件（重启生效）
  python spellpanel.py --selftest      建界面、渲染几帧、自检后退出
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
import traceback

import dearpygui.dearpygui as dpg

import spellcore as core

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\deng.ttf",
]

FIELD_ORDER = ["dmg", "cd", "range", "radius", "cnt", "dur"]
# 这里的换行是手排的：不用 wrap 自动折行（2.3 里自动折行后的续行行距会压到上一行，
# 出现字叠字），所以每行控制在 ~28 个汉字以内。
HINT = ("伤害：每次命中 / 每跳的基础伤害。\n"
        "冷却：基础冷却，实战还要再乘一次冷却缩减。\n"
        "施法距离：最远能扔多远。地面技改大后会挑怪最多的\n"
        "　　　　　地方扔；自身周围技（奉献）保持 0。\n"
        "范围半径：命中范围多大。奉献原本 8 码，改 100 就满地开花。\n"
        "弹道：多目标数（多重射击 3、奥术飞弹 4、闪电链 5）。\n"
        "持续：地面 / DoT 持续秒数；瞬发技为 0。")
HINT_LINES = HINT.split("\n")
HINT_SLOTS = len(HINT_LINES) + 4       # 多留 4 行给「待应用：…」
BODY_H = 462                           # 主体区高度：要装得下 7 行说明

FIELD_CN = core.FIELD_CN

TARGET_MEM = "内存 (立即生效)"
TARGET_FILE = "文件 (重启生效)"


def _frame_raise(sender=None, app_data=None, user_data=None):
    """第 3 帧再置顶：此时窗口一定已经建好，而且用的是 DPG 自己的循环，安全。"""
    p = user_data
    if getattr(p, "_raised", False):
        return
    p._raised = True
    p.raise_window()


class Panel:
    def __init__(self, default_target="mem"):
        self.records = []       # 最近一次读取的 113 条
        self.base = {}          # (off, field) -> 目标里的真实值
        self._raised = False
        self.view = []          # listbox 索引 -> records 索引
        self.items = []         # listbox 当前条目
        self.cur = None         # 当前选中 record
        self.target = default_target

    # ---------------------------------------------------------------- 生命周期
    def build(self):
        dpg.create_context()
        self._load_fonts()
        with dpg.window(tag="main"):
            self._build_top()
            self._build_global()
            self._build_body()
            self._build_log()

        # 注意：viewport 标题不能带中文，DearPyGui 2.3 在 Windows 上会因此崩溃
        dpg.create_viewport(title="AZS Spell Panel", width=1010, height=780,
                            x_pos=120, y_pos=60, resizable=True)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("main", True)
        dpg.set_viewport_always_top(True)
        # 必须在进主循环前先渲染一帧：否则 Windows 会一直把窗口当成「未激活的最小化窗口」，
        # 之后无论怎么 ShowWindow 还原，都会被反复压回最小化（表现为「点了没反应」）。
        dpg.render_dearpygui_frame()

    def raise_window(self):
        """游戏全屏 / DPG 自己把窗口最小化时，用户会以为「没反应」。
        按标题或 pid 找到自己的 hwnd，强制还原 + 置顶 + 抢焦点。"""
        if os.environ.get("AZS_NO_RAISE"):
            self.trace("raise_window: 已按环境变量跳过")
            return
        try:
            u = ctypes.WinDLL("user32", use_last_error=True)
            u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.DWORD)]
            u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
            u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_uint]
            u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
            ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
            me = os.getpid()

            def find():
                hit = []

                def cb(h, lp):
                    pid = wt.DWORD()
                    u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                    if pid.value != me:
                        return True
                    n = ctypes.create_unicode_buffer(512)
                    u.GetWindowTextW(h, n, 512)
                    if n.value:
                        hit.append((h, n.value))
                    return True

                u.EnumWindows(ENUM(cb), 0)
                return hit

            hwnd = None
            for _ in range(25):
                for h, title in find():
                    if "AZS Spell Panel" in title:
                        hwnd = h
                        break
                if hwnd is None:
                    cand = find()
                    if cand:
                        hwnd = cand[0][0]
                if hwnd:
                    break
                time.sleep(0.05)
            if not hwnd:
                self.trace("raise_window: 没找到窗口")
                return
            h = ctypes.c_void_p(hwnd)
            u.ShowWindow(h, 9)                     # SW_RESTORE：先把最小化还原
            time.sleep(0.05)
            u.SetWindowPos(h, ctypes.c_void_p(-1 & 0xFFFFFFFFFFFFFFFF),
                           0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)   # TOPMOST|NOSIZE|NOMOVE|SHOW
            u.BringWindowToTop(h)
            u.SetForegroundWindow(h)
            r = wt.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            self.trace("raise_window ok hwnd=%s rect=%s" % (hwnd, (r.left, r.top, r.right - r.left, r.bottom - r.top)))
        except Exception as e:
            self.trace("raise_window 失败: %r" % e)

    def _load_fonts(self):
        """挂载中文字体。

        坑：2.3 里 dpg.font(...) 已经不是容器了，写成 with dpg.font(...): ... 时
        字体根本不会生效（with 块还会抛「No container to pop」），表现出来就是
        界面上所有中文变成问号。必须用 add_font + bind_font。
        """
        path = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
        if not path:
            self.trace("找不到任何中文字体，中文会显示成问号")
            return
        try:
            with dpg.font_registry():
                big = dpg.add_font(path, 17)
                small = dpg.add_font(path, 15)
            dpg.bind_font(big)
            self.small_font = small
            self.trace("字体已挂载 %s" % path)
        except Exception as e:
            self.trace("字体加载失败 %r" % e)

    # -------------------------------------------------------------------- 顶部
    def _build_top(self):
        with dpg.group(horizontal=True):
            dpg.add_text("目标")
            dpg.add_radio_button(items=[TARGET_MEM, TARGET_FILE],
                                 default_value=TARGET_MEM if self.target == "mem" else TARGET_FILE,
                                 horizontal=True, tag="target", callback=self.on_target)
            dpg.add_button(label="刷新", width=70, callback=self.on_refresh)
            dpg.add_button(label="还原全部", width=90, callback=self.on_restore)
            dpg.add_text("未读取", tag="status", color=(120, 200, 255))
        dpg.add_separator()

    def _build_global(self):
        with dpg.group(horizontal=True):
            dpg.add_text("全局")
            dpg.add_input_float(label="伤害×", tag="mul_dmg", default_value=1.0, width=110,
                                step=0, format="%.2f", min_value=0.0, min_clamped=True)
            dpg.add_input_float(label="冷却×", tag="mul_cd", default_value=1.0, width=110,
                                step=0, format="%.2f", min_value=0.0, min_clamped=True)
            dpg.add_input_int(label="弹道+", tag="add_cnt", default_value=0, width=110,
                              step=0)
            dpg.add_button(label="套用到全部技能", width=150, callback=self.on_global)
        dpg.add_text("倍率是在「当前值」上再乘，连点两次 2 倍就是 4 倍；"
                     "三个都保持 1 / 0 时不会改动任何技能。", color=(130, 138, 150))
        dpg.add_separator()

    # -------------------------------------------------------------------- 主体
    def _build_body(self):
        with dpg.group(horizontal=True):
            with dpg.child_window(width=342, height=BODY_H, border=True):
                dpg.add_text("搜索（中文 / 英文 / spellid）")
                dpg.add_input_text(tag="search", width=-1, callback=self.on_search)
                # 不用 listbox：它在底部总会露出半行残影，改用可滚动区 + 可选行
                dpg.add_child_window(tag="listarea", width=-1, height=-1, border=False)
            with dpg.child_window(width=-1, height=BODY_H, border=True):
                dpg.add_text("未选中", tag="cur_name", color=(255, 210, 120))
                dpg.add_text("", tag="cur_sub", color=(140, 148, 160))
                dpg.add_separator()
                with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit):
                    for _w in (78, 110, 96):
                        dpg.add_table_column(width_fixed=True,
                                             init_width_or_weight=_w)
                    for f in FIELD_ORDER:
                        with dpg.table_row():
                            with dpg.table_cell():
                                dpg.add_text(FIELD_CN[f])
                            with dpg.table_cell():
                                dpg.add_input_float(tag="in_" + f, default_value=0.0,
                                                    width=100, step=0, format="%.3f",
                                                    label="##" + f)
                            with dpg.table_cell():
                                dpg.add_text("", tag="old_" + f, color=(120, 128, 140))
                dpg.add_separator()
                with dpg.group(horizontal=True):
                    dpg.add_button(label="应用到这个技能", width=170, callback=self.on_apply_one)
                    dpg.add_button(label="恢复该技能原值", width=150, callback=self.on_revert_one)
                # 说明文字用「一行一个 text」拼：DPG 的 mvText 用 \n 分行时行距由字体
                # 决定，15px 字挤在 ~14px 行高里会字叠字，改用独立 text 自己控行距。
                with dpg.group(tag="hintbox"):
                    for _i in range(HINT_SLOTS):
                        _t = dpg.add_text("", tag="hint_%d" % _i, color=(140, 148, 160))
                        if getattr(self, "small_font", None):
                            dpg.bind_item_font(_t, self.small_font)

    def set_hint(self, extra=None):
        lines = HINT_LINES + list(extra or [])
        for i in range(HINT_SLOTS):
            tag = "hint_%d" % i
            if i < len(lines):
                dpg.set_value(tag, lines[i])
                dpg.configure_item(tag, show=True)
            else:
                dpg.configure_item(tag, show=False)

    def _build_log(self):
        dpg.add_separator()
        with dpg.child_window(height=-1, border=True, tag="logbox"):
            dpg.add_text("就绪", tag="log_first", color=(150, 158, 170))

    # -------------------------------------------------------------------- 工具
    def trace(self, text):
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "logs", "panel_trace.log"), "a", encoding="utf-8") as f:
                f.write("%s  %s\n" % (time.strftime("%H:%M:%S"), text))
        except Exception:
            pass

    def log(self, msg, color=(150, 158, 170)):
        try:
            dpg.add_text(msg, parent="logbox", color=color, wrap=940)
            dpg.set_y_scroll("logbox", dpg.get_y_scroll_max("logbox"))
        except Exception:
            pass

    def target_key(self):
        v = dpg.get_value("target")
        return "mem" if v == TARGET_MEM else "file"

    def fmt(self, v):
        return ("%.3f" % float(v)).rstrip("0").rstrip(".")

    # -------------------------------------------------------------------- 读取
    def on_target(self, sender=None, app_data=None, user_data=None):
        self.refresh()

    def on_refresh(self, sender=None, app_data=None, user_data=None):
        self.refresh()

    def refresh(self):
        keep_off = self.cur["off"] if self.cur else None
        t = self.target_key()
        res = core.do_read(t)
        if not res.get("ok") and t == "mem":
            self.log("内存读不到（游戏没在运行？）—— 自动切到「文件」模式", (255, 180, 120))
            dpg.set_value("target", TARGET_FILE)
            t = "file"
            res = core.do_read(t)
        if not res.get("ok"):
            dpg.set_value("status", "读取失败")
            self.log("读取失败：%s" % res.get("reason", "?"), (255, 120, 120))
            return

        self.records = res["records"]
        self.base = {}
        for r in self.records:
            for f in FIELD_ORDER:
                self.base[(r["off"], f)] = float(r[f])
        self.cur = None

        where = "内存 pid %s" % res.get("pid") if res["target"] == "mem" else "exe 文件"
        dpg.set_value("status", "已读取 %d 个技能  ←  %s" % (len(self.records), where))
        self.log("读取 %d 条 ← %s" % (len(self.records), where))
        self.rebuild_list()

        if keep_off is not None:
            for pos, i in enumerate(self.view):
                if self.records[i]["off"] == keep_off:
                    try:
                        dpg.set_value("spelllist", self.items[pos])
                    except Exception:
                        pass
                    self.select_index(i)
                    break

    def rebuild_list(self):
        kw = (dpg.get_value("search") or "").strip().lower()
        view = []
        for i, r in enumerate(self.records):
            hay = "%s %s %s" % (r["zh"], r["key"], r["sid"])
            if kw and kw not in hay.lower():
                continue
            view.append(i)
        self.view = view
        dpg.delete_item("listarea", children_only=True)
        self.row_tags = []
        for i in view:
            r = self.records[i]
            tag = "row_%d" % i
            self.row_tags.append((tag, i))
            dpg.add_selectable(label="%03d  %s  %s" % (i + 1, r["zh"] or "未命名", r["key"]),
                               tag=tag, parent="listarea", default_value=False,
                               callback=self.on_row, user_data=i)
        if view:
            self.select_index(view[0])
        else:
            dpg.set_value("cur_name", "没有匹配的技能")
            dpg.set_value("cur_sub", "")

    def on_row(self, sender=None, app_data=None, user_data=None):
        if user_data is None:
            return
        self.select_index(int(user_data))

    def mark_row(self, idx):
        for tag, i in getattr(self, "row_tags", []):
            try:
                dpg.set_value(tag, i == idx)
            except Exception:
                pass

    def on_search(self, sender=None, app_data=None, user_data=None):
        self.rebuild_list()

    # -------------------------------------------------------------------- 选中
    def on_select(self, sender=None, app_data=None, user_data=None):
        label = str(app_data)
        if not label:
            return
        try:
            idx = int(label.split()[0]) - 1
        except Exception:
            return
        self.select_index(idx)

    def select_index(self, idx):
        if idx < 0 or idx >= len(self.records):
            return
        r = self.records[idx]
        self.cur = r
        dpg.set_value("cur_name", "%s   （%s）" % (r["zh"] or "未命名", r["key"] or "?"))
        dpg.set_value("cur_sub", "spellid %d    记录偏移 0x%X" % (r["sid"], r["off"]))
        for f in FIELD_ORDER:
            dpg.set_value("in_" + f, float(r[f]))
        for f in FIELD_ORDER:
            b = self.base.get((r["off"], f))
            dpg.set_value("old_" + f, ("原 %s" % self.fmt(b)) if b is not None else "")
        self.set_hint()
        self.mark_row(idx)
        self.show_slot_dirty(idx)

    def current_values(self):
        return {f: float(dpg.get_value("in_" + f)) for f in FIELD_ORDER}

    def show_slot_dirty(self, idx):
        if idx < 0 or idx >= len(self.records):
            return
        r = self.records[idx]
        diffs = []
        for f in FIELD_ORDER:
            new = float(dpg.get_value("in_" + f))
            old = self.base.get((r["off"], f), new)
            if abs(new - old) > 5e-4:
                diffs.append("%s %s→%s" % (FIELD_CN[f], self.fmt(old), self.fmt(new)))
        extra = []
        if diffs:
            # 自己按 34 个字折行（这里每行都是一个独立 text，行距可控）
            body = "，".join(diffs)
            cur = "待应用："
            for part in body.split("，"):
                piece = part + "，"
                if len(cur) + len(piece) > 34 and len(cur) > 4:
                    extra.append(cur.rstrip("，"))
                    cur = "　　　　"
                cur += piece
            extra.append(cur.rstrip("，"))
        self.set_hint(extra)

    # -------------------------------------------------------------------- 写入
    def apply_changes(self, changes, note=""):
        if not changes:
            self.log("没有改动", (200, 200, 120))
            return False
        t = self.target_key()
        res = core.do_apply(t, changes)
        if not res.get("ok") and res.get("reason"):
            self.log(res["reason"], (255, 120, 120))
            return False
        where = "内存" if res.get("target") == "mem" else "exe 文件"
        if res.get("ok"):
            self.log("%s写入 %d 个字段 → %s（已备份 %d 项）"
                     % (note + "：" if note else "", res["applied"], where, res.get("backup", 0)),
                     (140, 230, 150))
            self.refresh()
            return True
        self.log("部分失败：%s" % "; ".join(res.get("errors", [])), (255, 140, 120))
        self.refresh()
        return False

    def collect_one(self):
        if not self.cur:
            return []
        r = self.cur
        changes = []
        for f in FIELD_ORDER:
            new = float(dpg.get_value("in_" + f))
            old = self.base.get((r["off"], f))
            if old is None:
                continue
            if abs(new - old) > 5e-4:
                changes.append((r["off"], f, new))
        return changes

    def on_apply_one(self, sender=None, app_data=None, user_data=None):
        ch = self.collect_one()
        if not ch:
            self.log("这个技能没有改动", (200, 200, 120))
            return
        name = self.cur["zh"] or self.cur["key"]
        self.apply_changes(ch, name)

    def on_revert_one(self, sender=None, app_data=None, user_data=None):
        if not self.cur:
            return
        r = self.cur
        t = self.target_key()
        res = core.do_revert(t, [r["off"]])
        rev = res.get("reverted") or []
        if not rev:
            self.log("这个技能没有改过的记录（备份里没有它）", (200, 200, 120))
            return
        txt = "，".join("%s→%s" % (FIELD_CN[f], self.fmt(v)) for f, v in rev)
        self.log("已还原 %s：%s" % (r["zh"] or r["key"], txt), (140, 230, 150))
        self.refresh()

    def on_global(self, sender=None, app_data=None, user_data=None):
        if not self.records:
            self.log("先点刷新读一次", (200, 200, 120))
            return
        dm = float(dpg.get_value("mul_dmg"))
        cm = float(dpg.get_value("mul_cd"))
        ca = int(dpg.get_value("add_cnt"))
        if dm == 1.0 and cm == 1.0 and ca == 0:
            self.log("倍率都是 1 / 0，没有可套用的", (200, 200, 120))
            return
        changes = []
        for r in self.records:
            off = r["off"]
            if dm != 1.0:
                changes.append((off, "dmg", self.base[(off, "dmg")] * dm))
            if cm != 1.0:
                changes.append((off, "cd", self.base[(off, "cd")] * cm))
            if ca != 0:
                changes.append((off, "cnt", self.base[(off, "cnt")] + ca))
        self.log("套用全局：伤害×%s 冷却×%s 弹道+%d（%d 项）" % (dm, cm, ca, len(changes)))
        self.apply_changes(changes, "全局倍率")

    def on_restore(self, sender=None, app_data=None, user_data=None):
        t = self.target_key()
        res = core.do_restore(t)
        if res.get("ok"):
            self.log("已还原 %d 个字段（%s）" % (res.get("restored", 0),
                                              "内存" if t == "mem" else "exe 文件"),
                     (140, 230, 150))
        else:
            self.log("还原失败：%s" % res.get("reason", ""), (255, 120, 120))
        self.refresh()

    # -------------------------------------------------------------------- 主循环
    def run(self):
        self.trace("run() 进入")
        self.refresh()
        if not self._raised:
            dpg.set_frame_callback(3, _frame_raise, user_data=self)
        self.trace("refresh 完成，进入主循环")
        dpg.start_dearpygui()
        self.trace("主循环结束")
        dpg.destroy_context()


def main():
    args = sys.argv[1:]
    default_target = "file" if "--file" in args else "mem"
    p = Panel(default_target)
    p.build()
    if "--selftest" in args:
        p.refresh()
        for _ in range(5):
            dpg.render_dearpygui_frame()
        n = len(p.records)
        print("selftest: records=%d target=%s status=%s"
              % (n, p.target_key(), dpg.get_value("status")))
        if n:
            hit = [i for i, r in enumerate(p.records) if r["key"] == "consecration"]
            if hit:
                p.select_index(hit[0])
                print("selftest: consecration =", p.current_values())
        dpg.destroy_context()
        return
    try:
        p.run()
    except Exception:
        traceback.print_exc()
        input("出错了，回车退出")


if __name__ == "__main__":
    import faulthandler
    LOGDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        _fh = open(os.path.join(LOGDIR, "panel_crash.log"), "a", buffering=1)
        _fh.write("\n===== 启动 %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        faulthandler.enable(file=_fh)
    except Exception:
        pass
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        traceback.print_exc()
        try:
            _fh.write("异常:\n" + tb)
        except Exception:
            pass
        try:
            ctypes.windll.user32.MessageBoxW(
                None, "技能面板启动失败：\n\n" + tb[-1200:] +
                "\n\n完整日志：" + os.path.join(LOGDIR, "panel_crash.log"),
                "AZS 技能面板", 0x10)
        except Exception:
            pass

"""AZS 存档修改器 —— 改重置次数 / 特权等级 / 统计数值。

存档是 %APPDATA%\\wenilla-rogue\\characters.txt，格式 "RSE1:" + base64(盐16 + 密文 + tag20)。
密钥流和 tag 都借游戏 exe 里的函数算（本机映射一份 exe 镜像直接调用），
所以不需要知道内部密钥，也不怕游戏更新换密钥 —— 只要 exe 还是这一版就能改。

启动：双击 存档修改器.cmd
   python savepanel.py            正常启动
   python savepanel.py --selftest 建界面、渲染几帧、自检后退出
"""
import os
import sys
import time
import traceback
import datetime

import dearpygui.dearpygui as dpg

import savecrypt
import savedata

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyh.ttf",
    r"C:\Windows\Fonts\deng.ttf",
]

SAVE_DIR = os.path.dirname(savecrypt.SAVE)
BACKUP_DIR = os.path.join(SAVE_DIR, '改档备份')

# stat 行里会放出来编辑的键，顺序即界面顺序
STAT_KEYS = [
    ("gold", "金币"),
    ("marks", "牌子总数"),
    ("kills", "总击杀"),
    ("bosses", "首领击杀"),
    ("caches", "宝箱"),
    ("guardians", "守护者"),
    ("runs", "局数"),
    ("wins", "胜利"),
]

C_DIM = (135, 143, 155)
C_OK = (120, 210, 140)
C_WARN = (240, 190, 100)
C_ERR = (245, 120, 120)
C_KEY = (130, 175, 235)


def game_running():
    """游戏进程在跑的话，我们的改动会被它下次自动存档覆盖。"""
    try:
        import ctypes
        from ctypes import wintypes as wt
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        TH32CS_SNAPPROCESS = 0x2

        class PE32(ctypes.Structure):
            _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                        ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                        ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                        ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260)]

        snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        e = PE32()
        e.dwSize = ctypes.sizeof(PE32)
        ok = k.Process32First(snap, ctypes.byref(e))
        found = []
        while ok:
            name = e.szExeFile.decode('mbcs', 'ignore')
            if name.lower().startswith('azerothsurvivors'):
                found.append(e.th32ProcessID)
            ok = k.Process32Next(snap, ctypes.byref(e))
        k.CloseHandle(snap)
        return found
    except Exception:
        return []


class Panel:
    def __init__(self):
        self.text = ""            # 当前明文
        self.salt = None
        self.loaded_at = 0.0
        self.perks = []           # 特权表
        self.perk_base = {}       # 读取时的特权等级
        self.stat_widgets = []    # [(key, tag)]
        self.mark_widgets = []    # [(行下标, 职业, tag)]
        self._raised = False
        self.small_font = None

    # ------------------------------------------------------------------ 字体
    def _load_fonts(self):
        path = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
        if not path:
            return
        try:
            with dpg.font_registry():
                big = dpg.add_font(path, 17)
                small = dpg.add_font(path, 15)
            dpg.bind_font(big)
            self.small_font = small
        except Exception:
            pass

    def small(self, text, color=None, tag=None):
        # 注意：add_text 传 tag=None 会抛 SystemError（2.3 的坑），所以只能按需传
        kw = {}
        if color is not None:
            kw['color'] = color
        if tag is not None:
            kw['tag'] = tag
        t = dpg.add_text(text, **kw)
        if self.small_font:
            dpg.bind_item_font(t, self.small_font)
        return t

    # ------------------------------------------------------------------ 界面
    def build(self):
        # 特权表要先读：界面按它生成每一行
        try:
            self.perks = savedata.perk_table()
        except Exception:
            self.perks = []
        dpg.create_context()
        self._load_fonts()
        with dpg.window(tag="main"):
            self._build_head()
            with dpg.tab_bar(tag="tabs"):
                with dpg.tab(label="  快捷修改  ", tag="tab_quick"):
                    self._build_quick()
                with dpg.tab(label="  原文编辑  ", tag="tab_raw"):
                    self._build_raw()
            self._build_foot()

        # viewport 标题不能带中文，2.3 在 Windows 上会崩
        dpg.create_viewport(title="AZS Save Editor", width=900, height=772,
                            x_pos=140, y_pos=50, resizable=True)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("main", True)
        dpg.set_viewport_always_top(True)
        dpg.render_dearpygui_frame()      # 先渲染一帧，否则窗口会被当成最小化
        time.sleep(0.1)

    def _build_head(self):
        with dpg.group(horizontal=True):
            dpg.add_text("存档", color=C_KEY)
            self.small(SAVE_DIR, C_DIM)
            dpg.add_button(label="重新读取", width=90, callback=self.on_reload)
            dpg.add_button(label="打开备份目录", width=120, callback=self.on_open_backup)
        self.small("正在加载游戏引擎（首次约 5~10 秒）…", C_WARN, tag="status")
        self.small("", C_WARN, tag="warn")
        dpg.add_separator()

    def _build_quick(self):
        # ---- 重置次数
        with dpg.child_window(height=84, border=True):
            dpg.add_text("重置次数", color=C_KEY)
            self.small("用掉一次重置会把特权点数全部退还。每次开档只有 2 次，这里改的是「已用次数」。", C_DIM)
            with dpg.group(horizontal=True):
                dpg.add_text("已用 0 / 2", tag="reset_txt")
                dpg.add_button(label="清零（恢复 2 次重置）", width=200, callback=self.on_reset_zero)
                dpg.add_button(label="+1 已用", width=90, callback=self.on_reset_inc)

        # ---- 特权：按钮放在滚动区外面，表格两列排
        with dpg.group(horizontal=True):
            dpg.add_text("特权等级", color=C_KEY)
            self.small("上限就是游戏允许的最高级，超了会被夹回去。", C_DIM)
        with dpg.group(horizontal=True):
            dpg.add_button(label="全部拉满", width=100, callback=self.on_perk_max)
            dpg.add_button(label="全部清零", width=100, callback=self.on_perk_zero)
            dpg.add_button(label="还原", width=80, callback=self.on_perk_restore)
            dpg.add_text("预计花费 0", tag="perk_cost", color=C_WARN)
        with dpg.child_window(height=254, border=True):
            with dpg.table(header_row=True, row_background=True,
                           borders_innerH=True, borders_outerH=False,
                           borders_innerV=False, borders_outerV=False,
                           policy=dpg.mvTable_SizingStretchProp):
                for _ in range(2):
                    dpg.add_table_column(label="特权", init_width_or_weight=1.6)
                    dpg.add_table_column(label="等级", width_fixed=True, init_width_or_weight=80)
                    dpg.add_table_column(label="上限 / 每级花费", init_width_or_weight=1.3)
                for i in range(0, len(self.perks), 2):
                    with dpg.table_row():
                        for p in self.perks[i:i + 2]:
                            dpg.add_text(p['name'], color=(212, 216, 222))
                            dpg.add_input_int(tag="perk_" + p['id'], width=76, step=0,
                                              default_value=0, min_value=0,
                                              max_value=p['max_rank'], min_clamped=True,
                                              max_clamped=True,
                                              callback=self.on_perk_changed)
                            self.small("%d 级 · %s" % (p['max_rank'],
                                       '/'.join(str(c) for c in p['costs'])), C_DIM)
                        if len(self.perks[i:i + 2]) == 1:      # 奇数个时补空格子
                            for _ in range(3):
                                dpg.add_text("")

        # ---- 数值
        with dpg.child_window(height=166, border=True):
            dpg.add_text("统计数值", color=C_KEY)
            self.small("只影响面板显示，不改战斗强度。改完点「写入存档」。", C_DIM)
            for start in (0, 4):
                with dpg.group(horizontal=True):
                    for key, cn in STAT_KEYS[start:start + 4]:
                        with dpg.group():
                            self.small(cn, C_DIM)
                            dpg.add_input_int(tag="stat_" + key, width=160, step=0,
                                              min_value=0, min_clamped=True,
                                              callback=self.on_stat_changed)

    def _build_raw(self):
        self.small("下面是解出来的存档明文，一行一条。改坏了可以用「还原原文」退回本次读取时的内容。",
                   C_DIM)
        with dpg.group(horizontal=True):
            dpg.add_button(label="从控件重建原文", width=150, callback=self.on_rebuild_raw)
            dpg.add_button(label="还原原文", width=110, callback=self.on_raw_restore)
            dpg.add_text("", tag="raw_info", color=C_DIM)
        dpg.add_input_text(tag="raw", multiline=True, width=-1, height=-1,
                           tab_input=True)

    def _build_foot(self):
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_button(label="写入存档", width=140, callback=self.on_write)
            dpg.add_button(label="只校验不写", width=130, callback=self.on_verify)
            self.small("", C_OK, tag="foot")

    # ------------------------------------------------------------------ 数据
    def load(self):
        t0 = time.time()
        try:
            text, salt = savecrypt.load_plain()
        except Exception as e:
            dpg.set_value("status", "读取失败：%s" % e)
            dpg.configure_item("status", color=C_ERR)
            return False
        self.text = text.decode('utf-8')
        self.salt = salt
        self.loaded_at = time.time()
        if not self.perks:
            try:
                self.perks = savedata.perk_table()
            except Exception:
                self.perks = []
        lines = savedata.lines_of(self.text)
        self.perk_base = savedata.perk_parse(savedata.get_value(lines, 'perk', ''))
        self.refresh_widgets()
        pid = game_running()
        dpg.set_value("status", "已读取：明文 %d 字节，用时 %.1f 秒" % (len(text), time.time() - t0))
        dpg.configure_item("status", color=C_OK)
        if pid:
            dpg.set_value("warn", "⚠ 游戏正在运行（pid %d）：请先退出游戏再写入，否则会被它的自动存档覆盖" % pid[0])
        else:
            dpg.set_value("warn", "游戏未运行，可以放心写入")
        return True

    def refresh_widgets(self):
        lines = savedata.lines_of(self.text)
        used = savedata.reset_used(lines)
        dpg.set_value("reset_txt", "已用 %d / %d　（剩 %d 次）"
                      % (used, savedata.RESET_TOTAL,
                         max(0, savedata.RESET_TOTAL - used)))
        # 特权
        cur = savedata.perk_parse(savedata.get_value(lines, 'perk', ''))
        for p in self.perks:
            if dpg.does_item_exist("perk_" + p['id']):
                dpg.set_value("perk_" + p['id'], cur.get(p['id'], 0))
        # stat
        stats = dict(savedata.kv_parse(savedata.get_value(lines, 'stat', '')))
        for key, cn in STAT_KEYS:
            tag = "stat_" + key
            if dpg.does_item_exist(tag):
                try:
                    dpg.set_value(tag, int(stats.get(key, 0)))
                except ValueError:
                    dpg.set_value(tag, 0)
        self.update_perk_cost()
        dpg.set_value("raw", self.text)
        dpg.set_value("raw_info", "%d 行 / %d 字节" % (len(lines), len(self.text)))

    def update_perk_cost(self):
        total = 0
        for p in self.perks:
            tag = "perk_" + p['id']
            if not dpg.does_item_exist(tag):
                continue
            r = dpg.get_value(tag)
            for i in range(min(r, len(p['costs']))):
                total += p['costs'][i]
        dpg.set_value("perk_cost", "预计花费 %d" % total)

    # ------------------------------------------------------------------ 回调
    def on_stat_changed(self, *a):
        pass

    def on_perk_changed(self, *a):
        self.update_perk_cost()

    def on_reset_zero(self, *a):
        self._set_resets(0)

    def on_reset_inc(self, *a):
        lines = savedata.lines_of(self.text)
        self._set_resets(savedata.reset_used(lines) + 1)

    def _set_resets(self, n):
        lines = savedata.lines_of(self.text)
        savedata.set_value(lines, 'resets', str(max(0, n)))
        self.text = '\n'.join(lines)
        self.refresh_widgets()
        dpg.set_value("foot", "已设为 已用 %d 次（还没写回存档）" % max(0, n))
        dpg.configure_item("foot", color=C_WARN)

    def on_perk_max(self, *a):
        for p in self.perks:
            if dpg.does_item_exist("perk_" + p['id']):
                dpg.set_value("perk_" + p['id'], p['max_rank'])
        self.update_perk_cost()
        dpg.set_value("foot", "特权已全部拉满（还没写回存档）")
        dpg.configure_item("foot", color=C_WARN)

    def on_perk_zero(self, *a):
        for p in self.perks:
            if dpg.does_item_exist("perk_" + p['id']):
                dpg.set_value("perk_" + p['id'], 0)
        self.update_perk_cost()

    def on_perk_restore(self, *a):
        for p in self.perks:
            if dpg.does_item_exist("perk_" + p['id']):
                dpg.set_value("perk_" + p['id'], self.perk_base.get(p['id'], 0))
        self.update_perk_cost()

    def on_reload(self, *a):
        dpg.set_value("foot", "重新读取…")
        self.load()

    def on_open_backup(self, *a):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        os.startfile(BACKUP_DIR)

    def on_raw_restore(self, *a):
        dpg.set_value("raw", self.text)

    def on_rebuild_raw(self, *a):
        """把快捷页的控件状态写进明文（不涉及原文编辑框）。"""
        lines = savedata.lines_of(self.text)
        perks = {}
        for p in self.perks:
            tag = "perk_" + p['id']
            if dpg.does_item_exist(tag):
                r = int(dpg.get_value(tag))
                if r > 0:
                    perks[p['id']] = r
        savedata.set_value(lines, 'perk', savedata.perk_build(perks))
        pairs = savedata.kv_parse(savedata.get_value(lines, 'stat', ''))
        for key, cn in STAT_KEYS:
            tag = "stat_" + key
            if not dpg.does_item_exist(tag):
                continue
            val = str(int(dpg.get_value(tag)))
            for i, (k, v) in enumerate(pairs):
                if k == key:
                    pairs[i] = (k, val)
                    break
            else:
                pairs.append((key, val))
        savedata.set_value(lines, 'stat', savedata.kv_build(pairs))
        self.text = '\n'.join(lines)
        dpg.set_value("raw", self.text)
        dpg.set_value("foot", "已按控件重建原文")
        dpg.configure_item("foot", color=C_OK)

    # ------------------------------------------------------------------ 写入
    def _collect_and_apply(self):
        """把控件状态合并进明文并写回。返回 (ok, msg)"""
        self.on_rebuild_raw()
        text = dpg.get_value("raw") if dpg.does_item_exist("raw") else self.text
        if not text.endswith('\n'):
            text += '\n'
        raw = text.encode('utf-8')
        # 先自己验一遍：能不能加解密回去
        blob = savecrypt.encrypt_blob(raw)
        back, _ = savecrypt.decode_blob(blob)
        if back != raw:
            return False, "自检失败：加解密往返不一致，已放弃写入"
        # 备份
        os.makedirs(BACKUP_DIR, exist_ok=True)
        if os.path.exists(savecrypt.SAVE):
            stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
            dst = os.path.join(BACKUP_DIR, 'characters-%s.txt' % stamp)
            with open(savecrypt.SAVE, 'rb') as f:
                old = f.read()
            with open(dst, 'wb') as f:
                f.write(old)
        else:
            dst = None
        with open(savecrypt.SAVE, 'wb') as f:
            f.write(blob.encode())
        # 写完再读一遍确认真能解开
        try:
            chk, _ = savecrypt.load_plain()
        except Exception as e:
            return False, "写入后无法解回（%s）！备份在 %s" % (e, dst)
        self.text = chk.decode('utf-8')
        return True, "已写入 %d 字节（明文 %d）%s" % (
            len(blob), len(raw), ('，原档备份 ' + os.path.basename(dst)) if dst else '')

    def on_write(self, *a):
        pid = game_running()
        try:
            ok, msg = self._collect_and_apply()
        except Exception as e:
            traceback.print_exc()
            dpg.set_value("foot", "写入出错：%s" % e)
            dpg.configure_item("foot", color=C_ERR)
            return
        if ok and pid:
            msg += "　⚠ 游戏还在跑，请重启游戏"
        dpg.set_value("foot", msg)
        dpg.configure_item("foot", color=C_OK if ok else C_ERR)
        if ok:
            self.refresh_widgets()

    def on_verify(self, *a):
        self.on_rebuild_raw()
        text = dpg.get_value("raw")
        raw = text.encode('utf-8')
        try:
            blob = savecrypt.encrypt_blob(raw)
        except Exception as e:
            dpg.set_value("foot", "校验失败：%s" % e)
            dpg.configure_item("foot", color=C_ERR)
            return
        back, _ = savecrypt.decode_blob(blob)
        ok = back == raw
        lines = savedata.lines_of(text)
        dpg.set_value("foot", "校验通过：%d 行 / %d 字节 / 加密后 %d 字节 / resets=%s / perk=%s"
                      % (len(lines), len(raw), len(blob),
                         savedata.get_value(lines, 'resets', '?'),
                         savedata.get_value(lines, 'perk', '(空)')))
        dpg.configure_item("foot", color=C_OK if ok else C_ERR)

    # ------------------------------------------------------------------ 主循环
    def run(self):
        self.build()
        if '--raw' in sys.argv:
            dpg.set_value("tabs", "tab_raw")
        if not self.load():
            # 读不到存档也让界面留着，用户能看错误
            pass
        dpg.set_viewport_always_top(False)
        while dpg.is_dearpygui_running():
            dpg.render_dearpygui_frame()
        dpg.destroy_context()


def selftest():
    p = Panel()
    p.build()
    ok = p.load()
    print("load:", ok, "perks:", len(p.perks))
    # 试改：resets 归零 + 特权满 + 金币
    dpg.set_value("perk_rerolls", 3)
    p.on_perk_max()
    p.on_rebuild_raw()
    text = dpg.get_value("raw")
    print("resets 行:", [l for l in text.split('\n') if l.startswith('resets')])
    print("perk 行:", [l for l in text.split('\n') if l.startswith('perk')][:1])
    blob = savecrypt.encrypt_blob(text.encode('utf-8'))
    back, _ = savecrypt.decode_blob(blob)
    print("往返一致:", back.decode('utf-8') == text, " 长度", len(blob))
    for _ in range(5):
        dpg.render_dearpygui_frame()
    # 走一遍真实写入路径（备份 + 加密 + 回读校验）。
    # 注意：自检里点过「全部拉满」，所以只能写临时文件，绝不能碰真存档。
    import shutil, tempfile
    global BACKUP_DIR
    real_save = savecrypt.SAVE
    tmp = os.path.join(tempfile.gettempdir(), '_azs_save_selftest.txt')
    shutil.copy2(real_save, tmp)
    savecrypt.SAVE = tmp
    BACKUP_DIR = os.path.join(tempfile.gettempdir(), '_azs_save_selftest_bk')
    ok, msg = p._collect_and_apply()
    savecrypt.SAVE = real_save
    print("写入路径:", ok, msg)
    print("临时备份:", os.listdir(BACKUP_DIR)[-2:] if os.path.isdir(BACKUP_DIR) else '无')
    print("真存档未动:", open(real_save,'rb').read()[:5] == b'RSE1:')
    dpg.destroy_context()
    print("selftest done")


def _die(exc):
    """启动失败时把话说清楚。

    pythonw 没有控制台，只写日志的话用户看到的就是「点了没反应」，
    所以这里必须弹窗。另外 pythonw 下 sys.stderr 是 None，
    traceback.print_exc() 本身就会抛异常，得先挡住。
    """
    logdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    try:
        os.makedirs(logdir, exist_ok=True)
        log = os.path.join(logdir, 'save_crash.log')
        with open(log, 'a', encoding='utf-8') as f:
            f.write('\n===== %s =====\n' % datetime.datetime.now())
            traceback.print_exc(file=f)
    except Exception:
        log = '(日志没写成)'
    try:
        if sys.stderr:
            traceback.print_exc()
    except Exception:
        pass

    import pe as _pe
    if isinstance(exc, (_pe.ExeNotFound, savecrypt.NotCompatible)):
        msg = str(exc)
    else:
        msg = '存档修改器启动失败：\n%s: %s\n\n详细日志：\n%s' % (
            type(exc).__name__, exc, log)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, 'AZS 存档修改器', 0x10)
    except Exception:
        pass


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        selftest()
    else:
        import pe as _pe
        try:
            _pe.exe_path()          # 先确认找得到游戏，找不到就走 _die 给人话提示
            ok, why = savecrypt.probe()
            if not ok:
                raise savecrypt.NotCompatible(why)
            Panel().run()
        except Exception as e:
            _die(e)

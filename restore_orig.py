# -*- coding: utf-8 -*-
"""把「奉献」恢复成游戏原始数值。

原始值（改之前从 exe 里 dump 出来的，也在 spell_backup.json 里核对过）：
    伤害 96    冷却 8    施法距离 0    范围半径 8    弹道 0    持续 8

用法：
    python restore_orig.py            游戏开着就等它退出，然后自动还原
    python restore_orig.py --now      不等待，立刻尝试还原
    python restore_orig.py --exe xx   改指定文件（自测用）
    python restore_orig.py --mem      只把当前这局内存里的值改回去
"""
import json
import os
import struct
import sys
import time

import spellcore as core

def find_off():
    """奉献记录当前的文件偏移 —— 游戏更新后会变，所以每次现查。"""
    loc = core.locate()
    rows = json.load(open(core.TABLE_JSON, encoding="utf-8"))
    for i, r in enumerate(rows):
        if r.get("key") == "consecration":
            return loc["fo"] + i * loc["stride"]
    raise RuntimeError("技能表里找不到 consecration（奉献）")


OFF = None          # 由 find_off() 在运行时填
ORIG_F = {"dmg": (4, 96.0), "cd": (8, 8.0), "range": (12, 0.0),
          "radius": (16, 8.0), "dur": (24, 8.0)}
ORIG_CNT = 0
NAMES = [("伤害", 4, "f"), ("冷却", 8, "f"), ("施法距离", 12, "f"),
         ("范围半径", 16, "f"), ("弹道", 20, "I"), ("持续", 24, "f")]


def read_rec(path):
    with open(path, "rb") as f:
        f.seek(OFF)
        return f.read(120)


def show(blob, tag):
    vals = []
    for name, off, ty in NAMES:
        v = struct.unpack_from("<f" if ty == "f" else "<I", blob, off)[0]
        vals.append("%s %s" % (name, ("%.3f" % v).rstrip("0").rstrip(".")))
    print("  %s：%s" % (tag, "  ".join(vals)))


def restore_file(path):
    before = read_rec(path)
    show(before, "改动前")
    with open(path, "r+b") as f:
        for field, (off, val) in ORIG_F.items():
            f.seek(OFF + off)
            f.write(struct.pack("<f", val))
        f.seek(OFF + 20)
        f.write(struct.pack("<I", ORIG_CNT))
    after = read_rec(path)
    show(after, "改动后")
    ok = (abs(struct.unpack_from("<f", after, 4)[0] - 96.0) < 1e-3
          and abs(struct.unpack_from("<f", after, 16)[0] - 8.0) < 1e-3
          and struct.unpack_from("<I", after, 20)[0] == 0)
    print("  结果：" + ("已恢复成原始数值" if ok else "写入后数值不符，请把这段输出发给我"))
    return ok


def restore_mem():
    res = core.do_apply("mem", [(OFF, f, v) for f, (o, v) in ORIG_F.items()]
                        + [(OFF, "cnt", ORIG_CNT)])
    print("  内存写入：", res.get("ok"), res.get("applied"), res.get("reason", ""))
    m = core.do_read("mem")
    con = [x for x in m.get("records", []) if x["key"] == "consecration"]
    if con:
        c = con[0]
        print("  内存现值：伤害 %s / 冷却 %s / 施法距离 %s / 半径 %s / 弹道 %s / 持续 %s"
              % (c["dmg"], c["cd"], c["range"], c["radius"], c["cnt"], c["dur"]))
    return bool(res.get("ok"))


def cleanup_backups():
    """全部恢复成原值后，旧的备份就没意义了（还留着的话面板会显示「原 5000」这种误导值）。"""
    for name in ("spell_backup.json", "spell_backup_mem.json"):
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
        if os.path.exists(p):
            try:
                os.remove(p)
                print("  已清理旧备份", name)
            except Exception as e:
                print("  备份 %s 删不掉：%s" % (name, e))


def main():
    global OFF
    args = sys.argv[1:]
    if "--exe" in args:
        path = args[args.index("--exe") + 1]
    else:
        path = core.EXE

    OFF = find_off()
    print("奉献记录位置：文件偏移 0x%X" % OFF)

    if "--mem" in args:
        print("只改当前这局的内存值：")
        restore_mem()
        return

    print("把奉献恢复成原始数值（伤害 96 / 冷却 8 / 施法距离 0 / 半径 8 / 弹道 0 / 持续 8）")
    waited = False
    while "--exe" not in args:          # 自测模式改的是副本，与游戏无关，不必等待
        pid = core.game_pid()
        if not pid:
            break
        if "--now" in args or "--exe" in args:
            print("  游戏正在运行（pid %s），exe 被占用，写不进去。" % pid)
            print("  请先退出游戏，再双击「还原原值.cmd」。")
            return
        if not waited:
            print("  游戏正在运行（pid %s）。" % pid)
            print("  请正常退出游戏，这个窗口会自动完成还原，不用再操作（别关这个黑窗口）。")
            waited = True
        time.sleep(3)

    ok = restore_file(path)
    if ok:
        cleanup_backups()
        print("\n完成。下次启动游戏，奉献就是原始效果了。")
        if not ("--exe" in args):
            print("想以后再改，双击「技能面板.cmd」，选 奉献 改数字，点「应用到这个技能」。")
    else:
        print("\n没有完全成功，先不要启动游戏，把上面的输出发给我。")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
    # 关闭窗口的事交给 还原原值.cmd 里的 pause

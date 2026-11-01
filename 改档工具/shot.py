"""按窗口标题抓图，输出 BMP（无 Pillow 依赖，自己处理方向）。

DPG 用 Dx11 渲染，PrintWindow(PW_RENDERFULLCONTENT) 抓出来的像素是正常的，
只要把 BMP 头写成 top-down（负 biHeight）就对了。

用法：python shot.py "AZS Save Editor" out.bmp
"""
import ctypes
import struct
import sys
from ctypes import wintypes as wt

u = ctypes.WinDLL('user32', use_last_error=True)
g = ctypes.WinDLL('gdi32', use_last_error=True)

u.FindWindowW.restype = wt.HWND
u.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
u.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                           ctypes.c_int, ctypes.c_int, wt.UINT]
u.GetDC.argtypes = [wt.HWND]
u.ReleaseDC.argtypes = [wt.HWND, wt.HDC]

g.CreateCompatibleDC.argtypes = [wt.HDC]
g.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
g.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
g.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                        ctypes.c_void_p, ctypes.c_void_p, wt.UINT]


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


def grab(title, out):
    hwnd = u.FindWindowW(None, title)
    if not hwnd:
        raise SystemExit('找不到窗口：%s' % title)
    u.ShowWindow(hwnd, 9)
    # 这里以前还有一句 SetWindowPos 把窗口挪到 (60,40)，结果 DPG 的 viewport
    # 收到 WM_WINDOWPOSCHANGING 之后会把客户区算成 0×0，GetWindowRect 拿到全零，
    # 抓出来是一张空图。别加回来 —— 窗口在哪儿不影响抓图。
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    cw, ch = r.right - r.left, r.bottom - r.top
    hdc = u.GetDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, cw, ch)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, 2)
    bi = BIH()
    bi.biSize = ctypes.sizeof(BIH)
    bi.biWidth, bi.biHeight = cw, -ch
    bi.biPlanes, bi.biBitCount = 1, 32
    buf = ctypes.create_string_buffer(cw * ch * 4)
    g.GetDIBits(mdc, bmp, 0, ch, buf, ctypes.byref(bi), 0)
    u.ReleaseDC(hwnd, hdc)
    raw = buf.raw[:cw * ch * 4]

    # GetDIBits 给的就是自上而下的行，所以 BMP 头用负高度（top-down）直接写，
    # 千万别再按 bottom-up 写，否则整幅图会上下翻过来。
    rowbytes = cw * 4

    with open(out, 'wb') as f:
        f.write(b'BM' + struct.pack('<IHHI', 54 + rowbytes * ch, 0, 0, 54))
        f.write(struct.pack('<IiiHHIIiiII', 40, cw, -ch, 1, 32, 0,
                            rowbytes * ch, 2835, 2835, 0, 0))
        f.write(raw)
    print('%s  %dx%d' % (out, cw, ch))


if __name__ == '__main__':
    grab(sys.argv[1] if len(sys.argv) > 1 else 'AZS Save Editor',
         sys.argv[2] if len(sys.argv) > 2 else 'shot.bmp')

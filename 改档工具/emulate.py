# 在独立进程内映射 exe 镜像，并把 IAT 修正到我方进程的真实函数，从而直接调用内部函数
import ctypes, struct
from ctypes import wintypes as wt
import pe

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.VirtualAlloc.restype = ctypes.c_void_p
k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
k32.LoadLibraryA.argtypes = [ctypes.c_char_p]
k32.LoadLibraryA.restype = ctypes.c_void_p
k32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
k32.GetProcAddress.restype = ctypes.c_void_p

MEM_COMMIT=0x1000; MEM_RESERVE=0x2000; PAGE_EXECUTE_READWRITE=0x40

_data = None
_img  = None

def _imports(data):
    pe_off = struct.unpack_from('<I', data, 0x3c)[0]
    optsz  = struct.unpack_from('<H', data, pe_off+20)[0]
    opt    = pe_off+24
    magic  = struct.unpack_from('<H', data, opt)[0]
    ddoff  = opt + (112 if magic == 0x20b else 96)
    imp_rva, imp_sz = struct.unpack_from('<II', data, ddoff+8)   # dir[1] = import
    if not imp_rva: return []
    out=[]
    o = pe.rva2fo(imp_rva)
    while True:
        desc = data[o:o+20]
        if len(desc)<20 or desc == b'\0'*20: break
        oft, ts, fc, name_rva, ft = struct.unpack('<IIIII', desc)
        dll = data[pe.rva2fo(name_rva):].split(b'\0')[0].decode()
        h = k32.LoadLibraryA(dll.encode())
        thunk_rva = oft or ft
        p = pe.rva2fo(thunk_rva); i = 0
        while True:
            v = struct.unpack_from('<Q', data, p+i*8)[0]
            if v == 0: break
            if v & (1<<63):
                nm = ('#%d' % (v & 0xffff)).encode()
            else:
                nm = data[pe.rva2fo(v)+2:].split(b'\0')[0]
            addr = k32.GetProcAddress(h, nm)
            out.append((ft + i*8, addr, dll, nm))
            i += 1
        o += 20
    return out

def map_image(patch_iat=True, verbose=False):
    global _data, _img
    _data = pe.data()          # pe 里已经缓存过整份 exe，别自己再读一遍
    size = 0
    for name,va,vs,raw,rs in pe.SEC:
        size = max(size, va+max(vs,rs))
    size = (size + 0xFFFF) & ~0xFFFF
    p = k32.VirtualAlloc(ctypes.c_void_p(pe.IB), size, MEM_COMMIT|MEM_RESERVE, PAGE_EXECUTE_READWRITE)
    if not p: raise OSError('VirtualAlloc 0x%X failed err=%d' % (pe.IB, ctypes.get_last_error()))
    if p != pe.IB: raise OSError('mapped at 0x%X instead of 0x%X' % (p, pe.IB))
    for name,va,vs,raw,rs in pe.SEC:
        ctypes.memmove(pe.IB+va, _data[raw:raw+rs], min(vs or rs, rs))
    _img = p
    n=0; miss=0
    if patch_iat:
        for rva, addr, dll, nm in _imports(_data):
            if addr:
                struct.pack_into('<Q', (ctypes.c_char*8).from_address(pe.IB+rva), 0, addr); n+=1
            else:
                miss+=1
                if verbose: print('  missing %s!%s' % (dll, nm))
    if verbose: print('IAT patched %d, missing %d' % (n, miss))
    return p

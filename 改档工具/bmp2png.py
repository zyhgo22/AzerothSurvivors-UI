"""BMP(32bpp, top-down) -> PNG，纯标准库，不依赖 Pillow。"""
import struct, sys, zlib


def convert(src, dst):
    d = open(src, 'rb').read()
    off = struct.unpack_from('<I', d, 10)[0]
    hsz = struct.unpack_from('<I', d, 14)[0]
    w, h = struct.unpack_from('<ii', d, 18)
    bpp = struct.unpack_from('<H', d, 28)[0]
    assert bpp == 32, '只处理 32 位 BMP（这里是 %d）' % bpp
    topdown = h < 0
    h = abs(h)
    row = w * 4
    raw = d[off:off + row * h]
    lines = []
    for y in range(h):
        r = raw[y * row:(y + 1) * row]
        if not topdown:
            r = raw[(h - 1 - y) * row:(h - y) * row]
        # BGRA -> RGB
        out = bytearray(1 + w * 3)
        out[0] = 0
        for x in range(w):
            b, g, rr = r[x * 4], r[x * 4 + 1], r[x * 4 + 2]
            out[1 + x * 3:4 + x * 3] = bytes((rr, g, b))
        lines.append(bytes(out))
    body = zlib.compress(b''.join(lines), 9)

    def chunk(t, data):
        c = t + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', body)
           + chunk(b'IEND', b''))
    open(dst, 'wb').write(png)
    print('%s -> %s  %dx%d  %d 字节' % (src, dst, w, h, len(png)))


if __name__ == '__main__':
    convert(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else sys.argv[1].rsplit('.', 1)[0] + '.png')

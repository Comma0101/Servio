import struct

def construct_bytedance_header(pv: int, hsv: int, mt: int, mf: int, sm: int, cm: int) -> bytes:
    """Constructs the common header for Bytedance API requests."""
    return struct.pack('>BBBB', (pv << 4) | hsv, (mt << 4) | mf, (sm << 4) | cm, 0)

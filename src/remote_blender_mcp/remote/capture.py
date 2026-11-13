"""Capture the virtual X11 display, independent of Blender's GL front buffer."""

import ctypes as C
import ctypes.util
import os
import struct
import zlib

import numpy as np


class XImage(C.Structure):
    _fields_ = [
        (name, typ)
        for name, typ in [
            ("width", C.c_int),
            ("height", C.c_int),
            ("xoffset", C.c_int),
            ("format", C.c_int),
            ("data", C.c_void_p),
            ("byte_order", C.c_int),
            ("bitmap_unit", C.c_int),
            ("bitmap_bit_order", C.c_int),
            ("bitmap_pad", C.c_int),
            ("depth", C.c_int),
            ("bytes_per_line", C.c_int),
            ("bits_per_pixel", C.c_int),
            ("red_mask", C.c_ulong),
            ("green_mask", C.c_ulong),
            ("blue_mask", C.c_ulong),
        ]
    ]


def capture(path, width=1280, height=800):
    lib = C.CDLL(ctypes.util.find_library("X11"))
    lib.XOpenDisplay.argtypes = [C.c_char_p]
    lib.XOpenDisplay.restype = C.c_void_p
    lib.XDefaultRootWindow.argtypes = [C.c_void_p]
    lib.XDefaultRootWindow.restype = C.c_ulong
    lib.XGetImage.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_ulong, C.c_int]
    lib.XGetImage.restype = C.POINTER(XImage)
    lib.XDestroyImage.argtypes = [C.POINTER(XImage)]
    lib.XCloseDisplay.argtypes = [C.c_void_p]
    display = lib.XOpenDisplay(os.environ.get("DISPLAY", ":99").encode())
    if not display:
        raise RuntimeError("Cannot connect to the virtual display")
    image = None
    try:
        image = lib.XGetImage(
            display, lib.XDefaultRootWindow(display), 0, 0, width, height, C.c_ulong(-1).value, 2
        )
        if not image:
            raise RuntimeError("XGetImage failed")
        x = image.contents
        if x.bits_per_pixel != 32 or (x.red_mask, x.green_mask, x.blue_mask) != (0xFF0000, 0xFF00, 0xFF):
            raise RuntimeError("Unsupported virtual display pixel format")
        data = C.string_at(x.data, x.bytes_per_line * height)
        pixels = np.frombuffer(data, dtype="<u4" if x.byte_order == 0 else ">u4").reshape(
            height, x.bytes_per_line // 4
        )[:, :width]
        rgb = np.stack(((pixels >> 16) & 255, (pixels >> 8) & 255, pixels & 255), axis=2).astype("uint8")
        rows = b"".join(b"\x00" + row.tobytes() for row in rgb)

        def chunk(kind, payload):
            return (
                struct.pack("!I", len(payload))
                + kind
                + payload
                + struct.pack("!I", zlib.crc32(kind + payload))
            )

        path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b"")
        )
    finally:
        if image:
            lib.XDestroyImage(image)
        lib.XCloseDisplay(display)

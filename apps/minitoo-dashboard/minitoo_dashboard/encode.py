from __future__ import annotations

import io
import struct
from pathlib import Path
from typing import List

from PIL import Image

WIDTH, HEIGHT = 160, 128
CELL_ROWS, CELL_COLS = 8, 10
CHUNK = 256
OPCODE = 0x8B


def build_blob(pages: List[Image.Image], speed_ms: int, quality: int = 90) -> bytes:
    if not 1 <= len(pages) <= 255:
        raise ValueError(f"need 1..255 frames, got {len(pages)}")
    if not 1 <= speed_ms <= 0xFFFF:
        raise ValueError(f"speed must fit uint16 ms, got {speed_ms}")
    blob = bytes([0x23, len(pages), (speed_ms >> 8) & 0xFF, speed_ms & 0xFF, CELL_ROWS, CELL_COLS])
    for page in pages:
        if page.size != (WIDTH, HEIGHT):
            raise ValueError(f"page must be {WIDTH}x{HEIGHT}, got {page.size}")
        buf = io.BytesIO()
        page.convert("RGB").save(buf, format="JPEG", quality=quality, subsampling=0)
        jpeg = buf.getvalue()
        blob += b"\x01" + struct.pack(">I", len(jpeg)) + jpeg
    return blob


def rawfile_lines(payload: bytes) -> List[str]:
    total = struct.pack("<I", len(payload))
    lines = [(bytes([OPCODE, 0x00]) + total).hex(" ")]
    for index in range((len(payload) + CHUNK - 1) // CHUNK):
        chunk = payload[index * CHUNK:(index + 1) * CHUNK]
        lines.append((bytes([OPCODE, 0x01]) + total + struct.pack("<H", index) + chunk).hex(" "))
    return lines


def write_rawfile(payload: bytes, path: Path) -> int:
    lines = rawfile_lines(payload)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="ascii")
    tmp.replace(path)
    return len(lines) - 1

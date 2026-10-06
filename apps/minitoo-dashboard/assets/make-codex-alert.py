#!/usr/bin/env python3
"""Draws codex-alerting.gif: the face shown while Codex waits for an approval.

A teal terminal creature in Clauddy's style (5 px blocks on a 160x160 canvas,
Clauddy's background) with a ">_" prompt and a bouncing "?". The MiniToo
custom face is 160x128, so everything stays inside rows 16..143 (cover crop).

    python3 make-codex-alert.py          # rewrites codex-alerting.gif next to this file
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Tuple

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUT = HERE / "codex-alerting.gif"
BLOCK, GRID = 5, 32
BG = (41, 40, 49)          # Clauddy's background
TEAL = (16, 163, 127)      # the dashboard's Codex colour
TEAL_DARK = (11, 120, 94)
SCREEN = (18, 36, 33)
GLYPH = (120, 236, 200)
YELLOW = (255, 214, 64)
AMBER = (255, 168, 30)
RUST = (122, 60, 56)
FRAME_MS = 250

QUESTION = [".###.",
            "#...#",
            "....#",
            "..##.",
            "..#..",
            ".....",
            "..#.."]
QUESTION_SMALL = ["##.",
                  "..#",
                  ".#.",
                  "...",
                  ".#."]

Cells = Iterable[Tuple[int, int]]


def block(d: ImageDraw.ImageDraw, x: int, y: int, color) -> None:
    d.rectangle([x * BLOCK, y * BLOCK, x * BLOCK + BLOCK - 1, y * BLOCK + BLOCK - 1], fill=color)


def sprite(d: ImageDraw.ImageDraw, rows: List[str], x0: int, y0: int, color) -> None:
    for dy, row in enumerate(rows):
        for dx, ch in enumerate(row):
            if ch == "#":
                block(d, x0 + dx, y0 + dy, color)


def creature(d: ImageDraw.ImageDraw, *, hop: int = 0, arm: str = "down", cursor: bool = True,
             wide_legs: bool = False) -> None:
    top = 14 - hop
    for x in range(9, 23):          # body with cut corners
        for y in range(top, top + 10):
            corner = (x in (9, 22)) and (y in (top, top + 9))
            if not corner:
                block(d, x, y, TEAL)
    for x in range(10, 22):         # shade along the bottom edge
        block(d, x, top + 9, TEAL_DARK)
    for x in range(11, 21):         # screen
        for y in range(top + 2, top + 8):
            block(d, x, y, SCREEN)
    for x, y in ((12, 3), (13, 4), (12, 5)):   # ">"
        block(d, x, top + y, GLYPH)
    if cursor:
        for x in range(15, 18):                 # "_"
            block(d, x, top + 6, GLYPH)
    legs = (9, 12, 19, 22) if wide_legs else (10, 13, 18, 21)
    for x in legs:
        for y in range(top + 10, 26):
            block(d, x, y, TEAL_DARK)
    block(d, 23, top + 4, TEAL)                 # right arm, always down
    block(d, 24, top + 5, TEAL)
    if arm == "up":                             # left arm waving
        block(d, 8, top + 3, TEAL)
        block(d, 7, top + 2, TEAL)
        block(d, 6, top + 1, TEAL)
    elif arm == "mid":
        block(d, 8, top + 4, TEAL)
        block(d, 7, top + 3, TEAL)
    else:
        block(d, 8, top + 4, TEAL)
        block(d, 7, top + 5, TEAL)


def sparkles(d: ImageDraw.ImageDraw, y: int, color=YELLOW) -> None:
    for x, dy in ((11, 1), (10, 3), (20, 1), (21, 3)):
        block(d, x, y + dy, color)


def frame(q: str = "full", q_color=YELLOW, q_y: int = 5, spark: bool = False, **body) -> Image.Image:
    im = Image.new("RGB", (GRID * BLOCK, GRID * BLOCK), BG)
    d = ImageDraw.Draw(im)
    creature(d, **body)
    if q == "dot":
        block(d, 15, 10, q_color)
    elif q == "small":
        sprite(d, QUESTION_SMALL, 14, q_y + 2, q_color)
    elif q == "full":
        sprite(d, QUESTION, 13, q_y, q_color)
    if spark:
        sparkles(d, q_y)
    return im


def frames() -> List[Image.Image]:
    return [
        frame(q="dot", q_color=AMBER),
        frame(q="small", q_color=AMBER),
        frame(q_color=AMBER, q_y=4, hop=1),
        frame(q_color=RUST, cursor=False),
        frame(arm="mid", spark=True),
        frame(arm="up", spark=True, cursor=False),
        frame(arm="mid", q_y=4),
        frame(arm="up", q_y=4, cursor=False, spark=True),
        frame(arm="mid"),
        frame(arm="up", cursor=False),
        frame(q_y=4, hop=1),
        frame(q_color=RUST, cursor=False),
        frame(spark=True, wide_legs=True),
        frame(spark=True, wide_legs=True, cursor=False, q_y=4),
        frame(q_color=AMBER),
        frame(q_color=YELLOW, cursor=False, spark=True),
    ]


def main() -> None:
    pages = frames()
    pages[0].save(OUT, save_all=True, append_images=pages[1:], duration=FRAME_MS, loop=0,
                  optimize=False, disposal=1)
    print(f"{OUT} ({len(pages)} frames, {FRAME_MS} ms)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate the 112x112 Overclock app icon (opaque, launcher friendly)."""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw, ImageFilter

S = 8                      # supersampling factor
SIZE = 112
W = SIZE * S

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
PREVIEW_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "preview")

C_BG = (32, 36, 44)
C_BG2 = (18, 25, 34)
C_CYAN = (34, 230, 255)
C_CYAN_DIM = (18, 113, 132)
C_AMBER = (255, 190, 64)
C_AMBER_HI = (255, 230, 160)
C_DARK = (8, 15, 23)
C_WHITE = (239, 250, 255)


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(PREVIEW_DIR, exist_ok=True)

    # Base: dark card colour with a soft radial pool of light behind the chip.
    base = Image.new("RGBA", (W, W), C_BG + (255,))
    d = ImageDraw.Draw(base)
    cx = cy = W / 2
    for r in range(int(W * 0.72), 0, -2):
        t = 1 - r / (W * 0.72)
        col = lerp(C_BG, (16, 48, 62), t * t * 0.55)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col + (255,))

    # Faint engineering grid.
    grid = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    step = 14 * S
    for x in range(0, W + step, step):
        gd.line((x, 0, x, W), fill=(70, 110, 128, 22), width=max(1, S // 4))
    for y in range(0, W + step, step):
        gd.line((0, y, W, y), fill=(70, 110, 128, 22), width=max(1, S // 4))
    base = Image.alpha_composite(base, grid)

    # Background circuit traces.
    trace = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    td = ImageDraw.Draw(trace)
    lw = 2 * S
    for pts in (
        [(0, 28 * S), (16 * S, 28 * S), (22 * S, 34 * S), (22 * S, 44 * S)],
        [(0, 84 * S), (14 * S, 84 * S), (20 * S, 78 * S), (20 * S, 68 * S)],
        [(112 * S, 80 * S), (96 * S, 80 * S), (90 * S, 74 * S), (90 * S, 64 * S)],
        [(112 * S, 30 * S), (100 * S, 30 * S), (96 * S, 34 * S), (96 * S, 44 * S)],
    ):
        td.line(pts, fill=(34, 230, 255, 34), width=lw, joint="curve")
    for x, y in ((22, 44), (20, 68), (90, 64), (96, 44)):
        td.ellipse(((x * S - 2 * S), (y * S - 2 * S), (x * S + 2 * S), (y * S + 2 * S)),
                   fill=(34, 230, 255, 55))
    base = Image.alpha_composite(base, trace)

    # Cool glow ring.
    glow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    gld = ImageDraw.Draw(glow)
    gld.ellipse((17 * S, 17 * S, 95 * S, 95 * S), outline=(30, 224, 255, 45), width=3 * S)
    gld.ellipse((12 * S, 12 * S, 100 * S, 100 * S), outline=(30, 224, 255, 18), width=2 * S)
    base = Image.alpha_composite(base, glow.filter(ImageFilter.GaussianBlur(2.2 * S)))

    # Top/left tick marks for an instrument feel.
    tick = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    tid = ImageDraw.Draw(tick)
    for i, alpha in enumerate((110, 60, 30)):
        off = i * 3 * S
        tid.line((28 * S, (18 * S) - off, 40 * S, (18 * S) - off), fill=(34, 230, 255, alpha), width=S)
        tid.line(((18 * S) - off, 28 * S, (18 * S) - off, 40 * S), fill=(34, 230, 255, alpha), width=S)
    base = Image.alpha_composite(base, tick)

    # ---------------- CPU chip ----------------
    x0, y0, x1, y1 = 29 * S, 29 * S, 83 * S, 83 * S
    r = 11 * S
    shadow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    rounded(sd, (x0 + 2.5 * S, y0 + 6 * S, x1 + 2.5 * S, y1 + 6 * S), r, fill=(0, 0, 0, 165))
    base = Image.alpha_composite(base, shadow.filter(ImageFilter.GaussianBlur(4 * S)))

    # pins
    pins = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pins)
    for pos in (40, 56, 72):
        # left / right
        pd.rounded_rectangle((20 * S, pos * S - 2 * S, 31 * S, pos * S + 2 * S),
                             radius=2 * S, fill=C_CYAN_DIM + (255,))
        pd.rounded_rectangle((81 * S, pos * S - 2 * S, 92 * S, pos * S + 2 * S),
                             radius=2 * S, fill=C_CYAN_DIM + (255,))
        # top / bottom
        pd.rounded_rectangle((pos * S - 2 * S, 20 * S, pos * S + 2 * S, 31 * S),
                             radius=2 * S, fill=C_CYAN_DIM + (255,))
        pd.rounded_rectangle((pos * S - 2 * S, 81 * S, pos * S + 2 * S, 92 * S),
                             radius=2 * S, fill=C_CYAN_DIM + (255,))
    pins = pins.filter(ImageFilter.GaussianBlur(0.35 * S))
    base = Image.alpha_composite(base, pins)

    # chip body
    plate = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    pld = ImageDraw.Draw(plate)
    rounded(pld, (x0, y0, x1, y1), r, fill=C_DARK + (250,), outline=C_CYAN + (255,), width=2 * S)
    rounded(pld, (x0 + 2.8 * S, y0 + 2.8 * S, x1 - 2.8 * S, y1 - 2.8 * S), r - 2.5 * S,
            outline=(34, 230, 255, 60), width=S)
    # corner brackets inside
    b = 7 * S
    inset = 5 * S
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        ax = x0 + inset if sx > 0 else x1 - inset
        ay = y0 + inset if sy > 0 else y1 - inset
        pld.line((ax, ay, ax + sx * b, ay), fill=C_AMBER + (180,), width=S)
        pld.line((ax, ay, ax, ay + sy * b), fill=C_AMBER + (180,), width=S)
    base = Image.alpha_composite(base, plate)

    # Chip glow behind bolt.
    bglow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    bgd = ImageDraw.Draw(bglow)
    bgd.ellipse((40 * S, 38 * S, 74 * S, 76 * S), fill=(255, 165, 40, 110))
    base = Image.alpha_composite(base, bglow.filter(ImageFilter.GaussianBlur(5 * S)))

    # Lightning bolt, slightly overshooting the chip so it reads at 112 px.
    bolt = [
        (59.5, 24.0),
        (38.5, 60.0),
        (51.8, 60.0),
        (46.5, 88.0),
        (75.5, 48.5),
        (61.0, 48.5),
        (68.5, 24.0),
    ]
    bpoly = [(x * S, y * S) for x, y in bolt]
    bolt_img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bolt_img)
    bd.polygon(bpoly, fill=C_AMBER + (255,))
    # white-hot left facet
    highlight = [
        (59.5, 26.5),
        (43.0, 57.0),
        (51.0, 57.0),
        (47.2, 81.0),
        (55.0, 66.0),
    ]
    bd.polygon([(x * S, y * S) for x, y in highlight], fill=C_AMBER_HI + (230,))
    bd.line([(x * S, y * S) for x, y in bolt] + [bpoly[0]], fill=C_WHITE + (220,),
            width=max(1, S // 2), joint="curve")
    base = Image.alpha_composite(base, bolt_img)

    # Tiny power LED and thermal ticks at bottom.
    ui = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    ud = ImageDraw.Draw(ui)
    ud.ellipse((40 * S, 95 * S, 44 * S, 99 * S), fill=(54, 241, 162, 255))
    for i in range(4):
        alpha = 230 - i * 45
        ud.rounded_rectangle(((50 + i * 4.5) * S, 95.5 * S, (52.5 + i * 4.5) * S, 98.5 * S),
                             radius=S, fill=(255, 190, 64, alpha))
    base = Image.alpha_composite(base, ui)

    icon = base.convert("RGB").resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    icon_path = os.path.join(OUT_DIR, "icon.png")
    icon.save(icon_path, "PNG", optimize=True)
    icon.resize((448, 448), Image.Resampling.NEAREST).save(
        os.path.join(PREVIEW_DIR, "icon_448.png"), "PNG", optimize=True)
    print("saved", icon_path)


if __name__ == "__main__":
    main()

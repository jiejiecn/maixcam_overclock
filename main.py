#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Overclock — CPU / NPU frequency control and thermal monitor for MaixCAM Pro.

The app talks to the two vendor kernel modules bundled in assets/modules:
  * mpll_freq.ko  -> retunes clk_mpll, the CPU (C906) PLL
  * npu_freq.ko   -> retunes clk_tpll, the NPU (TPU) PLL

Both modules are loaded only for the duration of a retune and unloaded again.
The resulting clock rates stay in effect until the board is rebooted.

UI:
  * CPU 850..1000 MHz in 25 MHz steps
  * NPU 700 / 750 / 800 MHz
  * live SoC temperature
  * Apply and factory Reset (CPU 850 MHz, NPU 700 MHz)

CLI:
  python3 main.py --status
  python3 main.py --set-cpu 900 --set-npu 750
  python3 main.py --reset
  python3 main.py --preview /tmp/overclock.png --lang zh
"""

from __future__ import annotations

import argparse
import math
import os
import queue
import subprocess
import sys
import threading
import time

from maix import app, display, image, touchscreen
from maix import sys as maix_sys

APP_DIR = os.path.dirname(os.path.realpath(__file__))
MODULE_DIR = os.path.join(APP_DIR, "assets", "modules")
FONT_PATH = "/maixapp/share/font/SourceHanSansCN-Regular.otf"

CPU_CHOICES = tuple(range(850, 1001, 25))
NPU_CHOICES = (700, 750, 800)
DEFAULT_CPU = 850
DEFAULT_NPU = 700
MHZ = 1_000_000

CPU_CLOCK = "/sys/kernel/debug/clk/clk_c906_0/clk_rate"
NPU_CLOCK = "/sys/kernel/debug/clk/clk_tpu/clk_rate"
NPU_ENABLE = "/sys/kernel/debug/clk/clk_tpu/clk_enable_count"
TEMP_PATH = "/sys/class/thermal/thermal_zone0/temp"

CPU_MODULE = "mpll_freq"
NPU_MODULE = "npu_freq"
CPU_MODULE_FILE = os.path.join(MODULE_DIR, "mpll_freq.ko")
NPU_MODULE_FILE = os.path.join(MODULE_DIR, "npu_freq.ko")

W_DESIGN, H_DESIGN = 640, 480
M = 16
HEADER_H = 64
GAP = 12
CARD_Y = 74
CARD_H = 286
CARD_W = (W_DESIGN - 2 * M - GAP) // 2
CPU_X = M
NPU_X = M + CARD_W + GAP
ACTION_Y = 388
ACTION_H = 76

# Palette (tuples; Color objects are built after maix.image is available).
P = {
    "bg": (7, 10, 15),
    "bg2": (11, 17, 25),
    "header": (9, 13, 19),
    "surface": (17, 23, 32),
    "surface2": (24, 32, 44),
    "surface3": (31, 41, 55),
    "line": (35, 48, 64),
    "line_soft": (25, 35, 48),
    "text": (237, 245, 255),
    "muted": (125, 143, 163),
    "dim": (78, 94, 112),
    "cyan": (34, 230, 255),
    "cyan_dim": (15, 112, 132),
    "cyan_deep": (9, 48, 60),
    "amber": (255, 190, 64),
    "amber_dim": (146, 100, 28),
    "green": (54, 241, 162),
    "red": (255, 93, 115),
    "black": (3, 6, 10),
    "darktext": (5, 18, 22),
}
C = {k: image.Color.from_rgb(*v) for k, v in P.items()}

_zh = False
_fonts_ready = False
_measure_cache = {}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def t(en: str, zh: str) -> str:
    return zh if _zh else en


def _read_int(path: str):
    try:
        with open(path, "r", encoding="ascii") as f:
            return int(f.read().strip())
    except Exception:
        return None


def nearest_choice(choices, mhz):
    if mhz is None:
        return choices[0]
    return min(choices, key=lambda c: abs(c - mhz))


def temp_rgb(temp):
    if temp is None:
        return P["dim"]
    if temp >= 75:
        return P["red"]
    if temp >= 65:
        return P["amber"]
    return P["green"]


def temp_status(temp):
    if temp is None:
        return t("N/A", "未知")
    if temp >= 75:
        return t("HOT", "高温")
    if temp >= 65:
        return t("WARM", "偏热")
    return t("COOL", "正常")


def init_fonts() -> bool:
    global _fonts_ready
    if _fonts_ready:
        return True
    if not os.path.exists(FONT_PATH):
        return False
    for name, size in (
        ("f12", 12),
        ("f14", 14),
        ("f17", 17),
        ("f20", 20),
        ("f24", 24),
        ("f28", 28),
        ("f48", 48),
    ):
        try:
            image.load_font(name, FONT_PATH, size)
        except Exception:
            pass
    try:
        image.set_default_font("f14")
    except Exception:
        pass
    _fonts_ready = True
    return True


def text_w(s: str, font: str = "f14") -> int:
    key = (s, font)
    if key in _measure_cache:
        return _measure_cache[key]
    try:
        w = int(image.string_size(s, font=font).width())
    except Exception:
        w = len(s) * 8
    _measure_cache[key] = w
    return w


def draw_text(img, x: int, y: int, s: str, color, font: str = "f14"):
    try:
        img.draw_string(x, y, s, color=color, font=font)
    except Exception:
        try:
            img.draw_string(x, y, s, color=color)
        except Exception:
            pass


def draw_center_text(img, cx: int, y: int, s: str, color, font: str = "f14"):
    draw_text(img, int(cx - text_w(s, font) / 2), y, s, color, font)


def draw_right_text(img, right: int, y: int, s: str, color, font: str = "f14"):
    draw_text(img, int(right - text_w(s, font)), y, s, color, font)


def draw_tracked(img, x: int, y: int, s: str, color, font: str = "f14", tracking: int = 1):
    for ch in s:
        draw_text(img, x, y, ch, color, font)
        x += text_w(ch, font) + tracking


def fit_text(s: str, max_w: int, font: str = "f14") -> str:
    if text_w(s, font) <= max_w:
        return s
    while len(s) > 1 and text_w(s + "…", font) > max_w:
        s = s[:-1]
    return s + "…"


def wrap_text(s: str, max_w: int, font: str = "f14"):
    lines = []
    for para in (s or "").splitlines() or [""]:
        cur = ""
        for ch in para:
            if text_w(cur + ch, font) <= max_w:
                cur += ch
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


def fill_round(img, x, y, w, h, r, color):
    r = max(0, min(r, w // 2, h // 2))
    img.draw_rect(x + r, y, w - 2 * r, h, color=color, thickness=-1)
    img.draw_rect(x, y + r, w, h - 2 * r, color=color, thickness=-1)
    for cx, cy in (
        (x + r, y + r),
        (x + w - r, y + r),
        (x + r, y + h - r),
        (x + w - r, y + h - r),
    ):
        img.draw_circle(cx, cy, r, color=color, thickness=-1)


def panel(img, x, y, w, h, r, fill, border=None, border_w=1):
    if border is not None:
        fill_round(img, x, y, w, h, r, border)
        fill_round(img, x + border_w, y + border_w, w - 2 * border_w, h - 2 * border_w,
                   max(0, r - border_w), fill)
    else:
        fill_round(img, x, y, w, h, r, fill)


def draw_bolt(img, cx: float, cy: float, scale: float, color, thickness: int = 2):
    pts = [
        (cx + 2.2 * scale, cy - 9.0 * scale),
        (cx - 5.2 * scale, cy + 1.3 * scale),
        (cx - 0.2 * scale, cy + 1.3 * scale),
        (cx - 3.8 * scale, cy + 9.0 * scale),
        (cx + 6.0 * scale, cy - 1.2 * scale),
        (cx + 0.8 * scale, cy - 1.2 * scale),
        (cx + 4.0 * scale, cy - 9.0 * scale),
        (cx + 2.2 * scale, cy - 9.0 * scale),
    ]
    for i in range(len(pts) - 1):
        img.draw_line(int(pts[i][0]), int(pts[i][1]), int(pts[i + 1][0]),
                      int(pts[i + 1][1]), color=color, thickness=thickness)


def draw_logo(img, x: int, y: int):
    cx, cy = x + 17, y + 16
    pts = []
    for i in range(6):
        a = math.radians(30 + i * 60)
        pts.append((cx + 15 * math.cos(a), cy + 15 * math.sin(a)))
    for i in range(6):
        p1 = pts[i]
        p2 = pts[(i + 1) % 6]
        img.draw_line(int(p1[0]), int(p1[1]), int(p2[0]), int(p2[1]),
                      color=C["cyan"], thickness=2)
    fill_round(img, cx - 9, cy - 9, 18, 18, 9, C["cyan_deep"])
    draw_bolt(img, cx, cy, 0.72, C["amber"], 2)
    img.draw_circle(cx, cy, 18, color=C["cyan_deep"], thickness=1)


def draw_close_icon(img, x, y, w, h, color, thickness=2):
    px, py = x + w // 2, y + h // 2
    img.draw_line(px - 6, py - 6, px + 6, py + 6, color=color, thickness=thickness)
    img.draw_line(px + 6, py - 6, px - 6, py + 6, color=color, thickness=thickness)


def draw_arrow_icon(img, cx, cy, color):
    # circular reset arrow
    pts = []
    for deg in range(-210, 41, 18):
        a = math.radians(deg)
        pts.append((cx + 8 * math.cos(a), cy + 8 * math.sin(a)))
    for i in range(len(pts) - 1):
        img.draw_line(int(pts[i][0]), int(pts[i][1]), int(pts[i + 1][0]),
                      int(pts[i + 1][1]), color=color, thickness=2)
    ax, ay = pts[-1]
    img.draw_line(int(ax), int(ay), int(ax - 5), int(ay - 1), color=color, thickness=2)
    img.draw_line(int(ax), int(ay), int(ax + 1), int(ay + 5), color=color, thickness=2)


# ---------------------------------------------------------------------------
# Frequency control
# ---------------------------------------------------------------------------
class FrequencyController:
    """Load the vendor .ko, retune, verify and unload again."""

    def __init__(self):
        self.insmod = "/usr/sbin/insmod" if os.path.exists("/usr/sbin/insmod") else "insmod"
        self.rmmod = "/usr/sbin/rmmod" if os.path.exists("/usr/sbin/rmmod") else "rmmod"
        self.dmesg = "/usr/bin/dmesg" if os.path.exists("/usr/bin/dmesg") else "dmesg"

    # -- read-only state ---------------------------------------------------
    def read_cpu_hz(self):
        return _read_int(CPU_CLOCK)

    def read_npu_hz(self):
        return _read_int(NPU_CLOCK)

    def read_temp_c(self):
        milli = _read_int(TEMP_PATH)
        if milli is None:
            try:
                return float(maix_sys.cpu_temp().get("cpu"))
            except Exception:
                return None
        return milli / 1000.0

    def npu_enable_count(self):
        return _read_int(NPU_ENABLE)

    def npu_idle(self):
        n = self.npu_enable_count()
        return True if n is None else n == 0

    def cpu_mhz(self):
        hz = self.read_cpu_hz()
        return None if hz is None else int(round(hz / MHZ))

    def npu_mhz(self):
        hz = self.read_npu_hz()
        return None if hz is None else int(round(hz / MHZ))

    def setup_error(self):
        if os.geteuid() != 0:
            return t("Root permission is required to load the clock modules.",
                     "加载时钟模块需要 root 权限。")
        if not os.path.exists(CPU_CLOCK) or not os.path.exists(NPU_CLOCK):
            return t("Clock debugfs entries are missing. Is debugfs mounted?",
                     "找不到时钟 debugfs 节点，请确认 debugfs 已挂载。")
        for path in (CPU_MODULE_FILE, NPU_MODULE_FILE):
            if not os.path.exists(path):
                return t("Bundled kernel module is missing:\n" + path,
                         "缺少随应用打包的内核模块：\n" + path)
        return None

    # -- helpers -----------------------------------------------------------
    def _rmmod(self, name):
        try:
            subprocess.run([self.rmmod, name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass

    def _module_log(self, marker):
        try:
            r = subprocess.run([self.dmesg], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=4, text=True)
            lines = [ln for ln in (r.stdout or "").splitlines() if marker in ln]
            return "\n".join(lines[-4:])
        except Exception:
            return ""

    def _retune(self, label, module, path, param, target_hz, clock_path):
        if not os.path.exists(path):
            raise RuntimeError(t("Kernel module not found: " + path,
                                 "找不到内核模块：" + path))
        self._rmmod(module)
        cmd = [self.insmod, path, "%s=%d" % (param, target_hz), "delay_ms=0"]
        try:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=8, text=True)
        except Exception as e:
            self._rmmod(module)
            raise RuntimeError("%s: insmod failed: %s" % (label, e))
        if r.returncode != 0:
            msg = ((r.stderr or "") + (r.stdout or "")).strip()
            self._rmmod(module)
            raise RuntimeError("%s: insmod failed (%d): %s" % (label, r.returncode, msg))

        actual = None
        deadline = time.monotonic() + 3.2
        try:
            while time.monotonic() < deadline:
                actual = _read_int(clock_path)
                if actual == target_hz:
                    break
                time.sleep(0.02)
            if actual != target_hz:
                log = self._module_log(module)
                raise RuntimeError(
                    "%s PLL did not reach %d MHz (actual: %s MHz).\n%s"
                    % (label, target_hz // MHZ,
                       "?" if actual is None else str(actual // MHZ),
                       log[-500:])
                )
        finally:
            self._rmmod(module)
        return actual

    # -- public ------------------------------------------------------------
    def apply(self, cpu_mhz: int, npu_mhz: int) -> dict:
        """Retune CPU and NPU. Returns a result dict; never raises."""
        result = {
            "ok": False,
            "error": "",
            "changed": [],
            "cpu_hz": self.read_cpu_hz(),
            "npu_hz": self.read_npu_hz(),
        }
        try:
            if cpu_mhz not in CPU_CHOICES:
                raise ValueError("CPU must be one of %s MHz" % (CPU_CHOICES,))
            if npu_mhz not in NPU_CHOICES:
                raise ValueError("NPU must be one of %s MHz" % (NPU_CHOICES,))
            setup = self.setup_error()
            if setup:
                raise RuntimeError(setup)

            cpu_now = self.read_cpu_hz()
            npu_now = self.read_npu_hz()
            cpu_target = cpu_mhz * MHZ
            npu_target = npu_mhz * MHZ

            # NPU must be idle during a tpll retune. Check before touching CPU,
            # so a busy NPU never leaves a half-applied request behind.
            if npu_target != npu_now and not self.npu_idle():
                raise RuntimeError(
                    t("NPU is busy (clk_tpu enable_count=%s). Close AI apps and retry."
                      % self.npu_enable_count(),
                      "NPU 正在使用中（clk_tpu enable_count=%s），请关闭 AI 应用后重试。"
                      % self.npu_enable_count())
                )

            if cpu_target != cpu_now:
                self._retune("CPU", CPU_MODULE, CPU_MODULE_FILE, "target",
                             cpu_target, CPU_CLOCK)
                result["changed"].append("CPU")
            if npu_target != npu_now:
                self._retune("NPU", NPU_MODULE, NPU_MODULE_FILE, "freq",
                             npu_target, NPU_CLOCK)
                result["changed"].append("NPU")

            result["cpu_hz"] = self.read_cpu_hz()
            result["npu_hz"] = self.read_npu_hz()
            if result["cpu_hz"] != cpu_target:
                raise RuntimeError(
                    t("CPU verification failed: expected %d MHz, got %s MHz."
                      % (cpu_mhz, "?" if result["cpu_hz"] is None else result["cpu_hz"] // MHZ),
                      "CPU 校验失败：目标 %d MHz，实际 %s MHz。"
                      % (cpu_mhz, "?" if result["cpu_hz"] is None else result["cpu_hz"] // MHZ))
                )
            if result["npu_hz"] != npu_target:
                raise RuntimeError(
                    t("NPU verification failed: expected %d MHz, got %s MHz."
                      % (npu_mhz, "?" if result["npu_hz"] is None else result["npu_hz"] // MHZ),
                      "NPU 校验失败：目标 %d MHz，实际 %s MHz。"
                      % (npu_mhz, "?" if result["npu_hz"] is None else result["npu_hz"] // MHZ))
                )
            result["ok"] = True
            return result
        except Exception as e:
            result["cpu_hz"] = self.read_cpu_hz()
            result["npu_hz"] = self.read_npu_hz()
            result["error"] = str(e)
            return result


# ---------------------------------------------------------------------------
# UI state
# ---------------------------------------------------------------------------
class UIState:
    def __init__(self):
        self.cpu_sel = DEFAULT_CPU
        self.npu_sel = DEFAULT_NPU
        self.cpu_hz = None
        self.npu_hz = None
        self.temp = None
        self.npu_idle = True
        self.pressed = ""
        self.busy = False
        self.busy_cpu = DEFAULT_CPU
        self.busy_npu = DEFAULT_NPU
        self.apply_reason = "apply"
        self.modal = None
        self.message = ""
        self.kind = "muted"

    def dirty(self):
        cpu_target = self.cpu_sel * MHZ
        npu_target = self.npu_sel * MHZ
        return (self.cpu_hz != cpu_target) or (self.npu_hz != npu_target)

    def delta_text(self):
        parts = []
        if self.cpu_hz is not None:
            d = self.cpu_sel - int(round(self.cpu_hz / MHZ))
            if d:
                parts.append("CPU %+d" % d)
        if self.npu_hz is not None:
            d = self.npu_sel - int(round(self.npu_hz / MHZ))
            if d:
                parts.append("NPU %+d" % d)
        return "  ".join(parts)


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------
class Renderer:
    def __init__(self, width: int, height: int):
        self.w = width
        self.h = height
        self.canvas = image.Image(width, height, image.Format.FMT_RGB888)
        self.bg = self._make_background()
        self.L = self._layout()

    # -- static background -------------------------------------------------
    def _make_background(self):
        img = image.Image(self.w, self.h, image.Format.FMT_RGB888)
        # 32 horizontal bands produce a subtle vertical gradient cheaply.
        bands = 40
        for i in range(bands):
            t0 = i / bands
            band = tuple(int(P["bg"][k] + (P["bg2"][k] - P["bg"][k]) * t0) for k in range(3))
            col = image.Color.from_rgb(*band)
            y0 = int(self.h * i / bands)
            y1 = int(self.h * (i + 1) / bands)
            img.draw_rect(0, y0, self.w, y1 - y0 + 1, color=col, thickness=-1)

        # Dotted engineering grid.
        for gy in range(8, self.h, 24):
            for gx in range(8, self.w, 24):
                img.draw_circle(gx, gy, 1, color=image.Color.from_rgb(23, 34, 47), thickness=-1)

        # Faint circuit traces in the corners.
        line = image.Color.from_rgb(18, 30, 42)
        img.draw_line(0, 418, 42, 418, color=line, thickness=1)
        img.draw_line(42, 418, 58, 434, color=line, thickness=1)
        img.draw_line(58, 434, 90, 434, color=line, thickness=1)
        img.draw_line(640, 92, 600, 92, color=line, thickness=1)
        img.draw_line(600, 92, 584, 108, color=line, thickness=1)
        img.draw_circle(42, 418, 2, color=image.Color.from_rgb(32, 52, 67), thickness=-1)
        img.draw_circle(600, 92, 2, color=image.Color.from_rgb(32, 52, 67), thickness=-1)

        # Header line + accent segments.
        img.draw_rect(0, HEADER_H - 1, self.w, 1, color=C["line"], thickness=-1)
        img.draw_rect(0, HEADER_H - 3, 116, 2, color=C["cyan"], thickness=-1)
        img.draw_rect(116, HEADER_H - 3, 28, 2, color=C["cyan_dim"], thickness=-1)

        # Cards.
        for x, accent in ((CPU_X, C["cyan"]), (NPU_X, C["amber"])):
            panel(img, x, CARD_Y, CARD_W, CARD_H, 18, C["surface"], C["line"], 1)
            fill_round(img, x + 18, CARD_Y + 1, 52, 3, 2, accent)

        # Action row separator.
        img.draw_line(M, ACTION_Y - 10, self.w - M, ACTION_Y - 10,
                      color=C["line_soft"], thickness=1)
        return img

    def _layout(self):
        btn_w = (CARD_W - 32 - 16) // 2
        cpu_minus = (CPU_X + 16, CARD_Y + 172, btn_w, 90)
        cpu_plus = (CPU_X + 16 + btn_w + 16, CARD_Y + 172, btn_w, 90)
        npu_boxes = []
        seg_w = (CARD_W - 32 - 16) // 3
        for i in range(3):
            npu_boxes.append((NPU_X + 16 + i * (seg_w + 8), CARD_Y + 172, seg_w, 90))
        apply_box = (self.w - M - 176, ACTION_Y, 176, ACTION_H)
        reset_box = (apply_box[0] - 12 - 136, ACTION_Y, 136, ACTION_H)
        exit_box = (self.w - 56, 14, 40, 36)
        return {
            "cpu_minus": cpu_minus,
            "cpu_plus": cpu_plus,
            "npu": npu_boxes,
            "apply": apply_box,
            "reset": reset_box,
            "exit": exit_box,
        }

    # -- primitive wrappers ------------------------------------------------
    def _button(self, img, box, fill, pressed_fill, pressed, border=None):
        x, y, w, h = box
        f = pressed_fill if pressed else fill
        panel(img, x, y, w, h, 11, f, border, 1)

    def _state_color(self, st):
        if st.kind == "ok":
            return C["green"]
        if st.kind == "warn":
            return C["amber"]
        if st.kind == "err":
            return C["red"]
        return C["muted"]

    # -- sections ----------------------------------------------------------
    def _draw_header(self, img, st):
        draw_logo(img, 18, 16)
        draw_tracked(img, 64, 8, "OVERCLOCK", C["text"], "f28", 1)
        draw_tracked(img, 65, 42, "MAIXCAM PRO  /  SG2002", C["muted"], "f12", 1)

        # thermal pill
        x, y, w, h = self.w - 252, 9, 180, 46
        tcol = image.Color.from_rgb(*temp_rgb(st.temp))
        panel(img, x, y, w, h, 11, C["surface"], C["line"], 1)
        img.draw_rect(x + 1, y + 12, 3, 22, color=tcol, thickness=-1)
        draw_text(img, x + 13, y + 6, t("CHIP TEMP", "芯片温度"), C["muted"], "f12")
        txt = "--.-°C" if st.temp is None else "%.1f°C" % st.temp
        draw_text(img, x + 13, y + 21, txt, tcol, "f24")
        draw_right_text(img, x + w - 12, y + 8, temp_status(st.temp), tcol, "f12")
        img.draw_circle(x + w - 17, y + 34, 3, color=tcol, thickness=-1)

        # exit button
        ex, ey, ew, eh = self.L["exit"]
        pressed = st.pressed == "exit"
        panel(img, ex, ey, ew, eh, 10,
              C["surface3"] if pressed else C["surface"], C["line"], 1)
        draw_close_icon(img, ex, ey, ew, eh,
                        C["text"] if pressed else C["muted"], 2)

    def _draw_card_title(self, img, x, accent, title, subtitle):
        img.draw_rect(x + 18, CARD_Y + 14, 3, 20, color=accent, thickness=-1)
        draw_text(img, x + 28, CARD_Y + 11, title, accent, "f20")
        draw_text(img, x + 28 + text_w(title, "f20") + 10, CARD_Y + 18,
                  subtitle, C["muted"], "f14")

    def _draw_big_value(self, img, x, mhz):
        txt = str(mhz)
        draw_text(img, x + 2, CARD_Y + 36, txt, C["black"], "f48")
        draw_text(img, x, CARD_Y + 34, txt, C["text"], "f48")
        mx = x + text_w(txt, "f48") + 9
        draw_text(img, mx, CARD_Y + 63, "MHz", C["muted"], "f20")

    def _draw_actual(self, img, x, hz, extra_right=""):
        if hz is None:
            label = t("RUNNING  —", "当前  —")
            color = C["red"]
        else:
            label = t("RUNNING  %d MHz" % (hz // MHZ), "当前  %d MHz" % (hz // MHZ))
            color = C["muted"]
        y = CARD_Y + 96
        img.draw_circle(x + 22, y + 8, 3, color=color, thickness=-1)
        draw_text(img, x + 31, y, label, color, "f14")
        if extra_right:
            draw_right_text(img, x + CARD_W - 18, y, extra_right[0], extra_right[1], "f14")

    def _draw_cpu(self, img, st):
        x = CPU_X
        self._draw_card_title(img, x, C["cyan"], "CPU", "C906 / RISC-V")
        self._draw_big_value(img, x + 18, st.cpu_sel)
        self._draw_actual(img, x, st.cpu_hz)
        img.draw_line(x + 22, CARD_Y + 120, x + CARD_W - 22, CARD_Y + 120,
                      color=C["line_soft"], thickness=1)

        draw_center_text(img, x + CARD_W // 2, CARD_Y + 138,
                         t("850 – 1000 MHz  ·  25 MHz step",
                           "850 – 1000 MHz  ·  25 MHz 步进"),
                         C["dim"], "f14")

        # Two large step buttons; no drag control.
        minus_box = self.L["cpu_minus"]
        plus_box = self.L["cpu_plus"]
        idx = CPU_CHOICES.index(st.cpu_sel)
        self._button(img, minus_box, C["surface2"], C["surface3"],
                     st.pressed == "cpu_minus", C["line"])
        self._button(img, plus_box, C["surface2"], C["surface3"],
                     st.pressed == "cpu_plus", C["line"])
        mcol = C["text"] if idx > 0 else C["dim"]
        pcol = C["text"] if idx < len(CPU_CHOICES) - 1 else C["dim"]
        mx, my, mw, mh = minus_box
        img.draw_line(mx + mw // 2 - 19, my + mh // 2, mx + mw // 2 + 19,
                      my + mh // 2, color=mcol, thickness=5)
        px, py, pw, ph = plus_box
        img.draw_line(px + pw // 2 - 19, py + ph // 2, px + pw // 2 + 19,
                      py + ph // 2, color=pcol, thickness=5)
        img.draw_line(px + pw // 2, py + ph // 2 - 19, px + pw // 2,
                      py + ph // 2 + 19, color=pcol, thickness=5)

    def _draw_npu(self, img, st):
        x = NPU_X
        self._draw_card_title(img, x, C["amber"], "NPU", "TPU / INT8")
        self._draw_big_value(img, x + 18, st.npu_sel)
        idle_txt = t("IDLE", "空闲") if st.npu_idle else t("BUSY", "占用")
        idle_col = C["green"] if st.npu_idle else C["red"]
        self._draw_actual(img, x, st.npu_hz, (idle_txt, idle_col))
        img.draw_line(x + 22, CARD_Y + 120, x + CARD_W - 22, CARD_Y + 120,
                      color=C["line_soft"], thickness=1)

        labels = (t("BASE", "基础"), t("FAST", "快速"), t("MAX", "极速"))
        for i, val in enumerate(NPU_CHOICES):
            bx, by, bw, bh = self.L["npu"][i]
            selected = val == st.npu_sel
            pressed = st.pressed == "npu_%d" % i
            if selected:
                fill = C["amber"] if not pressed else image.Color.from_rgb(224, 163, 48)
                text_col = C["darktext"]
                sub_col = C["darktext"]
                border = C["amber"]
            else:
                fill = C["surface3"] if pressed else C["surface2"]
                text_col = C["text"] if pressed else C["muted"]
                sub_col = C["dim"]
                border = C["line"]
            panel(img, bx, by, bw, bh, 12, fill, border, 1)
            draw_center_text(img, bx + bw // 2, by + 13, "%d" % val, text_col, "f28")
            draw_center_text(img, bx + bw // 2, by + 56, labels[i], sub_col, "f14")

    def _draw_actions(self, img, st):
        x = M + 2
        if st.busy:
            word, word_col = t("APPLYING", "正在应用"), C["cyan"]
        elif st.modal or st.kind == "err":
            word, word_col = t("ERROR", "错误"), C["red"]
        elif st.dirty():
            word, word_col = t("PENDING", "待应用"), C["amber"]
        elif st.kind == "ok":
            if st.apply_reason == "reset":
                word, word_col = t("FACTORY", "原厂设置"), C["green"]
            else:
                word, word_col = t("APPLIED", "已生效"), C["green"]
        else:
            word, word_col = t("READY", "就绪"), C["green"]

        draw_text(img, x, ACTION_Y + 1, "STATUS", C["muted"], "f12")
        img.draw_circle(x + 6, ACTION_Y + 31, 4, color=word_col, thickness=-1)
        draw_text(img, x + 17, ACTION_Y + 17, word, word_col, "f28")
        msg = st.message or t("Changes apply immediately", "点击应用后立即生效")
        if st.busy:
            msg = t("PLL retune in progress — do not power off",
                    "PLL 正在重锁 — 请勿断电")
        draw_text(img, x, ACTION_Y + 54, fit_text(msg, 264, "f12"),
                  self._state_color(st), "f12")

        # Reset — 136 x 76, bottom right.
        rx, ry, rw, rh = self.L["reset"]
        panel(img, rx, ry, rw, rh, 15,
              C["surface3"] if st.pressed == "reset" else C["surface2"], C["line"], 1)
        draw_arrow_icon(img, rx + 27, ry + rh // 2, C["red"])
        draw_text(img, rx + 50, ry + 24, t("RESET", "恢复"), C["text"], "f24")

        # Apply — 176 x 76, bottom right.
        ax, ay, aw, ah = self.L["apply"]
        fill = image.Color.from_rgb(22, 178, 199) if st.pressed == "apply" else C["cyan"]
        panel(img, ax, ay, aw, ah, 15, fill, None, 0)
        draw_bolt(img, ax + 30, ay + ah // 2, 1.15, C["darktext"], 3)
        draw_text(img, ax + 54, ay + 24, t("APPLY", "应用"), C["darktext"], "f24")

    def _draw_busy(self, img, st, angle):
        pw, ph = 380, 128
        px, py = (self.w - pw) // 2, 180
        panel(img, px + 5, py + 7, pw, ph, 16, C["black"], None, 0)
        panel(img, px, py, pw, ph, 16, C["surface2"], C["cyan_dim"], 1)
        cx, cy = px + 52, py + ph // 2
        for i in range(10):
            a = math.radians(angle + i * 36)
            r = 18
            col = image.Color.from_rgb(
                int(10 + 24 * (i / 9.0)),
                int(60 + 170 * (i / 9.0)),
                int(72 + 183 * (i / 9.0)),
            )
            img.draw_circle(int(cx + r * math.cos(a)), int(cy + r * math.sin(a)),
                            3, color=col, thickness=-1)
        draw_tracked(img, px + 92, py + 26, t("APPLYING", "正在应用"), C["text"], "f20", 1)
        draw_text(img, px + 92, py + 58,
                  "CPU %d MHz  /  NPU %d MHz" % (st.busy_cpu, st.busy_npu),
                  C["cyan"], "f14")
        draw_text(img, px + 92, py + 84,
                  t("PLL retune in progress — do not power off",
                    "PLL 正在重锁 — 请勿断电"), C["muted"], "f12")

    def _draw_modal(self, img, st):
        title, detail = st.modal
        pw = 520
        lines = wrap_text(detail, pw - 52, "f14")[:6]
        ph = max(176, 132 + len(lines) * 23)
        px, py = (self.w - pw) // 2, (self.h - ph) // 2
        panel(img, px + 5, py + 7, pw, ph, 16, C["black"], None, 0)
        panel(img, px, py, pw, ph, 16, C["surface2"], C["red"], 1)
        img.draw_rect(px + 16, py + 16, 3, 24, color=C["red"], thickness=-1)
        draw_text(img, px + 30, py + 14, title, C["text"], "f20")
        for i, line in enumerate(lines):
            draw_text(img, px + 26, py + 56 + i * 23, line, C["muted"], "f14")
        draw_right_text(img, px + pw - 24, py + ph - 28,
                        t("Tap anywhere to dismiss", "点击任意位置关闭"),
                        C["dim"], "f12")
        img.draw_line(px + 24, py + ph - 40, px + pw - 24, py + ph - 40,
                      color=C["line"], thickness=1)

    # -- final frame -------------------------------------------------------
    def render(self, st: UIState, angle: float = 0.0):
        img = self.canvas
        img.draw_image(0, 0, self.bg)
        self._draw_header(img, st)
        self._draw_cpu(img, st)
        self._draw_npu(img, st)
        self._draw_actions(img, st)
        if st.busy:
            self._draw_busy(img, st, angle)
        if st.modal:
            self._draw_modal(img, st)
        return img

    def hit(self, x: int, y: int):
        for name, box in (
            ("exit", self.L["exit"]),
            ("cpu_minus", self.L["cpu_minus"]),
            ("cpu_plus", self.L["cpu_plus"]),
            ("apply", self.L["apply"]),
            ("reset", self.L["reset"]),
        ):
            bx, by, bw, bh = box
            if bx <= x < bx + bw and by <= y < by + bh:
                return name
        for i, (bx, by, bw, bh) in enumerate(self.L["npu"]):
            if bx <= x < bx + bw and by <= y < by + bh:
                return "npu_%d" % i
        return ""


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
def _exit_app(disp, ts):
    try:
        ts.close()
    except Exception:
        pass
    try:
        disp.close()
    except Exception:
        pass
    os._exit(0)


def _set_message(st: UIState):
    if st.dirty():
        d = st.delta_text()
        st.message = t("Pending changes · tap APPLY", "有未应用的更改 · 点击应用")
        if d:
            st.message = t("Pending · %s MHz" % d, "待应用 · %s MHz" % d)
        st.kind = "warn"
    else:
        st.message = t("Ready · changes are immediate", "就绪 · 应用后立即生效")
        st.kind = "muted"


def run_gui() -> None:
    disp = display.Display()
    # A custom input device is only used by the development/test harness; the
    # launcher leaves this unset and MaixPy picks the built-in hyn_ts panel.
    test_touch = os.environ.get("OVERCLOCK_TOUCH_DEVICE", "")
    ts = touchscreen.TouchScreen(test_touch) if test_touch else touchscreen.TouchScreen()
    width, height = disp.width(), disp.height()
    snapshot_path = os.environ.get("OVERCLOCK_SNAPSHOT", "")

    def snapshot(img):
        if not snapshot_path:
            return
        try:
            img.save(snapshot_path)
        except Exception:
            pass
    init_fonts()
    ctl = FrequencyController()
    st = UIState()

    st.cpu_hz = ctl.read_cpu_hz()
    st.npu_hz = ctl.read_npu_hz()
    st.temp = ctl.read_temp_c()
    st.npu_idle = ctl.npu_idle()
    st.cpu_sel = nearest_choice(CPU_CHOICES, None if st.cpu_hz is None else int(round(st.cpu_hz / MHZ)))
    st.npu_sel = nearest_choice(NPU_CHOICES, None if st.npu_hz is None else int(round(st.npu_hz / MHZ)))
    _set_message(st)

    setup = ctl.setup_error()
    if setup:
        st.modal = (t("SETUP PROBLEM", "环境异常"), setup)
        st.kind = "err"

    r = Renderer(width, height)
    start_img = r.render(st)
    disp.show(start_img)
    snapshot(start_img)

    result_q = queue.Queue()
    worker = None
    angle = 0.0
    down = False
    active = ""
    dirty_frame = False
    last_temp = time.monotonic()
    last_spin = 0.0

    def worker_fn(cpu_mhz, npu_mhz):
        result_q.put(ctl.apply(cpu_mhz, npu_mhz))

    def start_apply(force=False, reason="apply"):
        nonlocal worker, angle
        if st.busy:
            return
        if not force and not st.dirty():
            st.message = t("Already running at the selected clocks", "当前已经是所选频率")
            st.kind = "ok"
            return
        st.busy = True
        st.apply_reason = reason
        st.busy_cpu = st.cpu_sel
        st.busy_npu = st.npu_sel
        st.pressed = ""
        st.modal = None
        angle = 0.0
        worker = threading.Thread(target=worker_fn, args=(st.cpu_sel, st.npu_sel), daemon=True)
        worker.start()

    while not app.need_exit():
        now = time.monotonic()

        # Worker completion.
        if st.busy:
            try:
                res = result_q.get_nowait()
            except queue.Empty:
                res = None
            if res is not None:
                st.cpu_hz = res.get("cpu_hz")
                st.npu_hz = res.get("npu_hz")
                st.npu_idle = ctl.npu_idle()
                st.temp = ctl.read_temp_c()
                st.busy = False
                worker = None
                if res.get("ok"):
                    if st.apply_reason == "reset":
                        st.message = t("Factory defaults restored",
                                       "已恢复原厂设置")
                    else:
                        st.message = t(
                            "Applied · CPU %d / NPU %d MHz" % (st.cpu_hz // MHZ, st.npu_hz // MHZ),
                            "已生效 · CPU %d / NPU %d MHz" % (st.cpu_hz // MHZ, st.npu_hz // MHZ),
                        )
                    st.kind = "ok"
                else:
                    st.kind = "err"
                    st.message = t("Apply failed — see details", "应用失败 — 查看详情")
                    title = (t("RESET FAILED", "恢复失败") if st.apply_reason == "reset"
                             else t("APPLY FAILED", "应用失败"))
                    st.modal = (title, res.get("error") or "Unknown error")
                snapshot(r.render(st))
                dirty_frame = True
            else:
                if now - last_spin > 0.06:
                    angle = (angle + 34) % 360
                    last_spin = now
                    disp.show(r.render(st, angle))
                time.sleep(0.01)
                continue

        # Periodic sensor refresh (never during a retune).
        if now - last_temp >= 1.0:
            last_temp = now
            new_temp = ctl.read_temp_c()
            new_idle = ctl.npu_idle()
            if new_temp != st.temp or new_idle != st.npu_idle:
                st.temp = new_temp
                st.npu_idle = new_idle
                dirty_frame = True

        x, y, pressed = ts.read()

        if st.modal:
            if pressed and not down:
                down = True
            elif down and not pressed:
                st.modal = None
                down = False
                st.pressed = ""
                _set_message(st)
                dirty_frame = True
            if dirty_frame:
                disp.show(r.render(st))
                dirty_frame = False
            else:
                time.sleep(0.012)
            continue

        if st.busy:
            # Ignore touch while the PLLs are being retuned; make sure a
            # release is consumed so the next tap works normally.
            down = pressed
            continue

        if pressed and not down:
            down = True
            active = r.hit(x, y)
            st.pressed = active
            dirty_frame = True
        elif not pressed and down:
            down = False
            released = r.hit(x, y)
            action = active if released == active else ""
            st.pressed = ""
            active = ""
            if action == "exit":
                _exit_app(disp, ts)
            elif action == "cpu_minus":
                i = CPU_CHOICES.index(st.cpu_sel)
                if i > 0:
                    st.cpu_sel = CPU_CHOICES[i - 1]
                    _set_message(st)
            elif action == "cpu_plus":
                i = CPU_CHOICES.index(st.cpu_sel)
                if i < len(CPU_CHOICES) - 1:
                    st.cpu_sel = CPU_CHOICES[i + 1]
                    _set_message(st)
            elif action.startswith("npu_"):
                i = int(action.split("_")[1])
                st.npu_sel = NPU_CHOICES[i]
                _set_message(st)
            elif action == "apply":
                start_apply()
            elif action == "reset":
                st.cpu_sel = DEFAULT_CPU
                st.npu_sel = DEFAULT_NPU
                if ctl.read_cpu_hz() == DEFAULT_CPU * MHZ and ctl.read_npu_hz() == DEFAULT_NPU * MHZ:
                    st.cpu_hz = DEFAULT_CPU * MHZ
                    st.npu_hz = DEFAULT_NPU * MHZ
                    st.message = t("Factory settings already active", "当前已是原厂设置")
                    st.kind = "ok"
                else:
                    st.message = t("Restoring factory clocks…", "正在恢复原厂频率…")
                    st.kind = "warn"
                    start_apply(force=True, reason="reset")
            dirty_frame = True
        elif pressed and down and active in ("cpu_minus", "cpu_plus", "apply", "reset",
                                             "exit", "npu_0", "npu_1", "npu_2"):
            if r.hit(x, y) != active and st.pressed:
                st.pressed = ""
                dirty_frame = True

        if dirty_frame:
            frame = r.render(st, angle)
            disp.show(frame)
            if not pressed and down is False:
                snapshot(frame)
            dirty_frame = False

        time.sleep(0.008)

    _exit_app(disp, ts)


# ---------------------------------------------------------------------------
# CLI / preview
# ---------------------------------------------------------------------------
def print_status(ctl: FrequencyController):
    print("CPU : %s MHz" % (ctl.cpu_mhz() if ctl.cpu_mhz() is not None else "?"))
    print("NPU : %s MHz" % (ctl.npu_mhz() if ctl.npu_mhz() is not None else "?"))
    temp = ctl.read_temp_c()
    print("TEMP: %s °C" % ("?" if temp is None else "%.1f" % temp))
    print("NPU_IDLE: %s (enable_count=%s)" % (ctl.npu_idle(), ctl.npu_enable_count()))


def cli_apply(args) -> int:
    ctl = FrequencyController()
    cpu = args.set_cpu if args.set_cpu is not None else (ctl.cpu_mhz() or DEFAULT_CPU)
    npu = args.set_npu if args.set_npu is not None else (ctl.npu_mhz() or DEFAULT_NPU)
    if args.reset:
        cpu, npu = DEFAULT_CPU, DEFAULT_NPU
    print("request: CPU %d MHz / NPU %d MHz" % (cpu, npu))
    res = ctl.apply(cpu, npu)
    print_status(ctl)
    if not res["ok"]:
        print("ERROR:", res["error"])
        return 1
    print("OK: changed", ", ".join(res["changed"]) if res["changed"] else "none")
    return 0


def preview(args) -> int:
    global _zh
    _zh = (args.lang or "en").lower().startswith("zh")
    init_fonts()
    ctl = FrequencyController()
    st = UIState()
    st.cpu_sel = args.cpu if args.cpu in CPU_CHOICES else DEFAULT_CPU
    st.npu_sel = args.npu if args.npu in NPU_CHOICES else DEFAULT_NPU
    if args.actual_cpu is not None:
        st.cpu_hz = args.actual_cpu * MHZ
    else:
        st.cpu_hz = ctl.read_cpu_hz()
    if args.actual_npu is not None:
        st.npu_hz = args.actual_npu * MHZ
    else:
        st.npu_hz = ctl.read_npu_hz()
    if args.temp is not None:
        st.temp = args.temp
    else:
        st.temp = ctl.read_temp_c()
    st.npu_idle = ctl.npu_idle()
    _set_message(st)
    if args.pending:
        st.message = t("Pending · tap APPLY", "有未应用的更改 · 点击应用")
        st.kind = "warn"
    if args.busy:
        st.busy = True
        st.busy_cpu = st.cpu_sel
        st.busy_npu = st.npu_sel
    if args.modal:
        st.modal = (t("APPLY FAILED", "应用失败"),
                    t("NPU is busy (clk_tpu enable_count=1). Close AI apps and retry.",
                      "NPU 正在使用中（clk_tpu enable_count=1），请关闭 AI 应用后重试。"))
    r = Renderer(W_DESIGN, H_DESIGN)
    img = r.render(st, 120.0)
    img.save(args.preview)
    print("saved %s, cpu=%d, npu=%d, temp=%s" %
          (args.preview, st.cpu_sel, st.npu_sel, st.temp))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(description="MaixCAM Pro Overclock")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--set-cpu", type=int)
    ap.add_argument("--set-npu", type=int)
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--preview")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--cpu", type=int, default=DEFAULT_CPU)
    ap.add_argument("--npu", type=int, default=DEFAULT_NPU)
    ap.add_argument("--actual-cpu", type=int)
    ap.add_argument("--actual-npu", type=int)
    ap.add_argument("--temp", type=float)
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--busy", action="store_true")
    ap.add_argument("--modal", action="store_true")
    ap.add_argument("--devtest", help=argparse.SUPPRESS)
    return ap


def main() -> int:
    global _zh
    # launcher_daemon always appends start_param to argv, even when it is an
    # empty string. Drop those empty arguments before argparse sees them.
    argv = [a for a in sys.argv[1:] if a != ""]
    args = build_parser().parse_args(argv)

    if args.preview:
        return preview(args)
    if args.status:
        print_status(FrequencyController())
        return 0
    if args.set_cpu is not None or args.set_npu is not None or args.reset:
        return cli_apply(args)
    if args.devtest:
        os.environ["OVERCLOCK_TOUCH_DEVICE"] = args.devtest
        os.environ["OVERCLOCK_SNAPSHOT"] = "/tmp/overclock_live.png"

    try:
        locale = app.get_sys_config_kv("language", "locale") or ""
        _zh = locale.lower().startswith("zh")
    except Exception:
        _zh = False
    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

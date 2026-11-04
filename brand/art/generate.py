#!/usr/bin/env python3
"""墨采输入法的全部品牌图：标志、各平台图标、吉祥物「墨墨」和 66 张表情贴纸。

所有图都由这里的代码画成 SVG，再用 librsvg 渲染，所以改配色、改笔画、加一张贴纸都只改这个文件，然后重新生成。
输出写进 brand/assets/，目录结构与上游仓库一一对应；brand/apply.py 打包前把它们逐个覆盖到改写树里。

    brew install librsvg webp          # rsvg-convert、cwebp；iconutil、tiffutil、sips 是 macOS 自带的
    python3 brand/art/generate.py      # 重新生成 brand/assets/ 下的全部图片
    python3 brand/art/generate.py --sheet <目录>   # 另外输出贴纸和吉祥物的对照图，便于检查

贴纸上的字用霞鹜文楷（LXGW WenKai，SIL OFL 1.1），只在生成时用，不随应用分发；字体文件放在 MOCAI_FONT_DIR（缺省
~/worktrees/mocai-deps/fonts），librsvg 经那里的 fonts.conf 找到它。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import pathlib
import shutil
import struct
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
BRAND = HERE.parent
ROOT = BRAND.parent
ASSETS = BRAND / "assets"
FONT_DIR = pathlib.Path(os.environ.get("MOCAI_FONT_DIR", pathlib.Path.home() / "worktrees/mocai-deps/fonts"))
FONT = "LXGW WenKai"

# ---------- 标志 ----------

# 墨底、金边、白色笔画。笔画是一滴墨，里面一道回锋，画在与上游 msime.svg 相同的 110 单位画布里，外框 5 到 105。
FIELD = "#1E2230"
EDGE = "#E9B44C"
# iOS 的四个备选图标只换边色，名字和上游一样，颜色也沿用上游选的那四种：它们都与金色的默认边分得开。
VARIANTS = {"Forest": "#287B61", "Sky": "#70DCF0", "Dusk": "#A896EF", "Vermilion": "#B74332"}
# 笔画只用 M 和 C，Swift 那一份（CGPath）照着同样的命令画，见 swift_stroke_body()。
STROKE = [
    ("M", (55, 24)),
    ("C", (61, 35), (75, 48), (75, 62)),
    ("C", (75, 74), (66, 83), (55, 83)),
    ("C", (44, 83), (35, 74), (35, 62)),
    ("C", (35, 48), (49, 35), (55, 24)),
    ("M", (47, 64)),
    ("C", (47, 70), (51, 74), (57, 74)),
]
STROKE_WIDTH = 8
# 笔画的外包围盒（含线宽的一半），键盘品牌图按它居中。
STROKE_BBOX = (31.0, 20.0, 79.0, 87.0)


def fmt(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def inner(svg: str) -> str:
    """<svg ...> 和 </svg> 之间的正文。"""
    return re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)


def path_d(commands, transform=lambda point: point) -> str:
    parts = []
    for command, *points in commands:
        parts.append(command + " ".join(f"{fmt(x)} {fmt(y)}" for x, y in map(transform, points)))
    return "".join(parts)


def to_mark_units(point):
    """110 单位画布到上游 MsimeMark / MSIMELogo 的 132 单位画布：放大 1.2 倍，外框左上角落在 (-2.15686, 5.8335)。"""
    x, y = point
    return (x * 1.2 - 8.15686, y * 1.2 - 0.1665)


STROKE_D = path_d(STROKE)
STROKE_D_132 = path_d(STROKE, to_mark_units)


def swift_stroke_body() -> str:
    """MSIMELogo.strokePath(in:) 的函数体，与 STROKE 同一组命令。"""
    lines = []
    for command, *points in STROKE:
        mapped = [to_mark_units(point) for point in points]
        if command == "M":
            lines.append(f"    path.move(to: point({fmt(mapped[0][0])}, {fmt(mapped[0][1])}))")
        else:
            (c1x, c1y), (c2x, c2y), (x, y) = mapped
            lines.append(
                f"    path.addCurve(to: point({fmt(x)}, {fmt(y)}), control1: point({fmt(c1x)}, {fmt(c1y)}), "
                f"control2: point({fmt(c2x)}, {fmt(c2y)}))"
            )
    return "\n".join(lines)


def appkit_stroke_body() -> str:
    """macOS 悬浮工具栏上的小标志（FloatingToolbarPanel.mm）：按边长 side 的比例画在 mark 里，AppKit 的 y 轴朝上。
    比例取 110 单位画布里外框 5 到 105 那一段。"""

    def point(xy) -> str:
        x, y = xy
        u, v = (x - 5) / 100, 1 - (y - 5) / 100
        return f"NSMakePoint(NSMinX(mark) + side * {u:.3f}, NSMinY(mark) + side * {v:.3f})"

    lines = ["    NSBezierPath *stroke = [NSBezierPath bezierPath];"]
    for command, *points in STROKE:
        if command == "M":
            lines.append(f"    [stroke moveToPoint:{point(points[0])}];")
        else:
            c1, c2, end = points
            lines.append(f"    [stroke curveToPoint:{point(end)}")
            lines.append(f"           controlPoint1:{point(c1)}")
            lines.append(f"           controlPoint2:{point(c2)}];")
    return "\n".join(lines)


def hex_channels(color: str, template: str) -> str:
    """把 #RRGGBB 填进平台代码里「0xRR / 255.0」这类写法。"""
    red, green, blue = color[1:3], color[3:5], color[5:7]
    return template.format(r=red.upper(), g=green.upper(), b=blue.upper())


def tile_svg(edge: str = EDGE) -> str:
    """单独显示的标志：深色圆角方块，5 宽的彩色边，上面一笔白色笔画。对应上游 packages/ui/src/assets/msime.svg。"""
    return (
        "<!-- 墨采标志：墨色圆角方块、金边，白色笔画是一滴墨和一道回锋。由 brand/art/generate.py 生成，改图改那个脚本。 -->\n"
        '<svg width="110" height="110" viewBox="0 0 110 110" fill="none" xmlns="http://www.w3.org/2000/svg">\n'
        f'  <rect x="5" y="5" width="100" height="100" rx="22" fill="{FIELD}" stroke="{edge}" stroke-width="5"/>\n'
        f'  <path d="{STROKE_D}" fill="none" stroke="#FFFFFF" stroke-width="{STROKE_WIDTH}" stroke-linecap="round"/>\n'
        "</svg>\n"
    )


def home_screen_svg(edge: str) -> str:
    """iOS 主屏图标：标志放大到边贴着画布，底色是边色。iOS 自己裁圆角，透明边会露出黑底。"""
    scale = 110 / 105
    body = inner(tile_svg(edge))
    offset = 55 - 55 * scale
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="110" height="110" viewBox="0 0 110 110" fill="none">\n'
        f'<rect width="110" height="110" fill="{edge}"/>\n'
        f'<g transform="translate({fmt(offset)} {fmt(offset)}) scale({scale:.6f})">{body}</g>\n</svg>\n'
    )


def menu_icon_svg() -> str:
    """输入菜单的通用图标：标志的单色模板图，笔画从圆角方块里镂空，菜单栏按浅色、深色和高亮各自着色。"""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!-- 输入菜单的通用图标：墨采标志的单色模板图。由 brand/art/generate.py 生成，同时写出 MSIMEClientInputMethodMenuIcon.tiff。 -->\n"
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="-1.0 -1.0 112.0 112.0">\n'
        "  <title>墨采输入法 input menu icon</title>\n"
        "  <defs>\n"
        '    <mask id="stroke">\n'
        '      <rect x="2.5" y="2.5" width="105" height="105" rx="24.5" fill="#fff"/>\n'
        f'      <path d="{STROKE_D}" fill="none" stroke="#000" stroke-linecap="round" stroke-width="{STROKE_WIDTH + 1}"/>\n'
        "    </mask>\n"
        "  </defs>\n"
        '  <rect x="2.5" y="2.5" width="105" height="105" rx="24.5" fill="#000" mask="url(#stroke)"/>\n'
        "</svg>\n"
    )


def keyboard_brand_svg() -> str:
    """只有笔画，黑笔白底：platforms/ios/SharedResources/KeyboardBrand.png。上游已不读它，换掉只是为了包里不留水杉的笔画。"""
    left, top, right, bottom = STROKE_BBOX
    scale = 0.9 * 110 / max(right - left, bottom - top)
    transform = f"translate({fmt(55 - scale * (left + right) / 2)} {fmt(55 - scale * (top + bottom) / 2)}) scale({scale:.6f})"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="110" height="110" viewBox="0 0 110 110" fill="none">\n'
        '<rect width="110" height="110" fill="white"/>\n'
        f'<g transform="{transform}"><path d="{STROKE_D}" stroke="black" stroke-width="{STROKE_WIDTH}" stroke-linecap="round"/></g>\n'
        "</svg>\n"
    )


# ---------- 墨墨 ----------

INK_TOP = "#4A5476"
INK = "#1E2230"
OUTLINE = "#141824"
WHITE = "#FFFFFF"
PINK = "#FF8FA3"
GOLD = "#E9B44C"
RED = "#D9573F"
TEAR = "#7CC8F2"
CREAM = "#FFF4E0"
GREEN = "#4FB477"
SKY = "#70DCF0"


class Canvas:
    """一张图的 SVG 片段。坐标都是 320 单位的画布。"""

    def __init__(self, size: int = 320) -> None:
        self.size = size
        self.defs: list[str] = []
        self.back: list[str] = []
        self.parts: list[str] = []

    def add(self, fragment: str) -> None:
        self.parts.append(fragment)

    def svg(self) -> str:
        defs = "".join(self.defs)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.size}" height="{self.size}" viewBox="0 0 320 320">'
            f"<defs>{defs}</defs>{''.join(self.back)}{''.join(self.parts)}</svg>"
        )


def drop_d(cx: float, cy: float, r: float) -> str:
    """墨滴：尖在上，下面是半径 r 的圆。两侧的贝塞尔在 (cx±r, cy) 处与圆相切。"""
    tip = cy - 1.9 * r
    return (
        f"M{fmt(cx)} {fmt(tip)}"
        f"C{fmt(cx + 0.3 * r)} {fmt(cy - 1.45 * r)} {fmt(cx + r)} {fmt(cy - 0.85 * r)} {fmt(cx + r)} {fmt(cy)}"
        f"A{fmt(r)} {fmt(r)} 0 0 1 {fmt(cx - r)} {fmt(cy)}"
        f"C{fmt(cx - r)} {fmt(cy - 0.85 * r)} {fmt(cx - 0.3 * r)} {fmt(cy - 1.45 * r)} {fmt(cx)} {fmt(tip)}Z"
    )


def heart_d(x: float, y: float, s: float) -> str:
    """以 (x, y) 为中心、宽约 2s 的心形。"""
    return (
        f"M{fmt(x)} {fmt(y + 0.9 * s)}"
        f"C{fmt(x - 1.4 * s)} {fmt(y)} {fmt(x - 1.1 * s)} {fmt(y - 1.1 * s)} {fmt(x)} {fmt(y - 0.45 * s)}"
        f"C{fmt(x + 1.1 * s)} {fmt(y - 1.1 * s)} {fmt(x + 1.4 * s)} {fmt(y)} {fmt(x)} {fmt(y + 0.9 * s)}Z"
    )


def sparkle_d(x: float, y: float, s: float) -> str:
    return (
        f"M{fmt(x)} {fmt(y - s)}Q{fmt(x + 0.18 * s)} {fmt(y - 0.18 * s)} {fmt(x + s)} {fmt(y)}"
        f"Q{fmt(x + 0.18 * s)} {fmt(y + 0.18 * s)} {fmt(x)} {fmt(y + s)}"
        f"Q{fmt(x - 0.18 * s)} {fmt(y + 0.18 * s)} {fmt(x - s)} {fmt(y)}"
        f"Q{fmt(x - 0.18 * s)} {fmt(y - 0.18 * s)} {fmt(x)} {fmt(y - s)}Z"
    )


def outlined(shape: str, fill: str, width: float = 3) -> str:
    return shape.replace("/>", f' fill="{fill}" stroke="{OUTLINE}" stroke-width="{fmt(width)}" stroke-linejoin="round"/>', 1)


class Momo:
    """墨墨：一滴会表情的墨。每张图是同一个身体，换眼睛、嘴、眉毛、手和道具。"""

    def __init__(self, canvas: Canvas, cx: float = 160, cy: float = 178, r: float = 84, rotate: float = 0) -> None:
        self.c = canvas
        self.cx, self.cy, self.r = cx, cy, r
        self.rotate = rotate
        self.face: list[str] = []
        self.front: list[str] = []
        self.behind: list[str] = []

    # 坐标换算：以身体圆心为原点、以半径为单位
    def p(self, u: float, v: float) -> tuple[float, float]:
        return self.cx + u * self.r, self.cy + v * self.r

    def body(self) -> str:
        gradient = "ink"
        if not any('id="ink"' in d for d in self.c.defs):
            self.c.defs.append(
                f'<linearGradient id="ink" x1="0" y1="0" x2="0.35" y2="1"><stop offset="0" stop-color="{INK_TOP}"/>'
                f'<stop offset="1" stop-color="{INK}"/></linearGradient>'
            )
        d = drop_d(self.cx, self.cy, self.r)
        hx, hy = self.p(-0.5, -0.62)
        return (
            # 外圈白边让贴纸在深色聊天背景上也看得清
            f'<path d="{d}" fill="{WHITE}" stroke="{WHITE}" stroke-width="16" stroke-linejoin="round"/>'
            f'<path d="{d}" fill="url(#{gradient})" stroke="{OUTLINE}" stroke-width="4" stroke-linejoin="round"/>'
            # 高光，与标志里的回锋同一个意思
            f'<path d="M{fmt(hx)} {fmt(hy)}Q{fmt(self.p(-0.72, -0.25)[0])} {fmt(self.p(-0.72, -0.25)[1])} '
            f'{fmt(self.p(-0.68, 0.12)[0])} {fmt(self.p(-0.68, 0.12)[1])}" fill="none" stroke="{WHITE}" '
            f'stroke-opacity="0.45" stroke-width="{fmt(0.1 * self.r)}" stroke-linecap="round"/>'
            # 墨尖旁的一点金，「采」
            f'<path d="{sparkle_d(*self.p(0.46, -1.62), 0.16 * self.r)}" fill="{GOLD}" stroke="{OUTLINE}" stroke-width="2.5" stroke-linejoin="round"/>'
        )

    # ----- 眼睛 -----
    def eyes(self, kind: str) -> None:
        r = self.r
        for side in (-1, 1):
            x, y = self.p(0.38 * side, -0.08)
            self.face.append(self.eye(kind, x, y, side, r))

    def eye(self, kind: str, x: float, y: float, side: int, r: float) -> str:
        w = 0.15 * r
        stroke = f'fill="none" stroke="{WHITE}" stroke-width="{fmt(0.075 * r)}" stroke-linecap="round" stroke-linejoin="round"'
        if kind == "normal":
            return (
                f'<ellipse cx="{fmt(x)}" cy="{fmt(y)}" rx="{fmt(w)}" ry="{fmt(1.2 * w)}" fill="{WHITE}"/>'
                f'<circle cx="{fmt(x + 0.1 * w)}" cy="{fmt(y + 0.15 * w)}" r="{fmt(0.62 * w)}" fill="{INK}"/>'
                f'<circle cx="{fmt(x + 0.35 * w)}" cy="{fmt(y - 0.25 * w)}" r="{fmt(0.24 * w)}" fill="{WHITE}"/>'
            )
        if kind == "wide":
            return (
                f'<ellipse cx="{fmt(x)}" cy="{fmt(y)}" rx="{fmt(1.2 * w)}" ry="{fmt(1.45 * w)}" fill="{WHITE}"/>'
                f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="{fmt(0.36 * w)}" fill="{INK}"/>'
            )
        if kind == "side":
            return (
                f'<ellipse cx="{fmt(x)}" cy="{fmt(y)}" rx="{fmt(w)}" ry="{fmt(1.2 * w)}" fill="{WHITE}"/>'
                f'<circle cx="{fmt(x + 0.45 * w)}" cy="{fmt(y + 0.1 * w)}" r="{fmt(0.55 * w)}" fill="{INK}"/>'
            )
        if kind == "happy":
            return f'<path d="M{fmt(x - w)} {fmt(y + 0.3 * w)}Q{fmt(x)} {fmt(y - 1.1 * w)} {fmt(x + w)} {fmt(y + 0.3 * w)}" {stroke}/>'
        if kind == "closed":
            return f'<path d="M{fmt(x - w)} {fmt(y - 0.1 * w)}Q{fmt(x)} {fmt(y + 0.9 * w)} {fmt(x + w)} {fmt(y - 0.1 * w)}" {stroke}/>'
        if kind == "flat":
            return f'<path d="M{fmt(x - w)} {fmt(y)}L{fmt(x + w)} {fmt(y)}" {stroke}/>'
        if kind == "smug":
            return (
                f'<path d="M{fmt(x - w)} {fmt(y)}L{fmt(x + w)} {fmt(y)}" {stroke}/>'
                f'<path d="M{fmt(x - 0.7 * w)} {fmt(y + 0.1 * w)}Q{fmt(x)} {fmt(y + 0.8 * w)} {fmt(x + 0.7 * w)} {fmt(y + 0.1 * w)}" fill="{WHITE}"/>'
            )
        if kind == "dot":
            return f'<circle cx="{fmt(x)}" cy="{fmt(y)}" r="{fmt(0.32 * w)}" fill="{WHITE}"/>'
        if kind == "tight":
            # > <
            a = -side
            return (
                f'<path d="M{fmt(x - a * w)} {fmt(y - 0.8 * w)}L{fmt(x + a * 0.8 * w)} {fmt(y)}L{fmt(x - a * w)} {fmt(y + 0.8 * w)}" {stroke}/>'
            )
        if kind == "x":
            return (
                f'<path d="M{fmt(x - 0.8 * w)} {fmt(y - 0.8 * w)}L{fmt(x + 0.8 * w)} {fmt(y + 0.8 * w)}'
                f'M{fmt(x + 0.8 * w)} {fmt(y - 0.8 * w)}L{fmt(x - 0.8 * w)} {fmt(y + 0.8 * w)}" {stroke}/>'
            )
        if kind == "spiral":
            points = []
            for step in range(0, 46):
                angle = step * 0.42
                radius = 0.08 * w + step * 0.024 * w
                points.append((x + radius * math.cos(angle * side), y + radius * math.sin(angle * side)))
            d = "M" + "L".join(f"{fmt(px)} {fmt(py)}" for px, py in points)
            return f'<path d="{d}" fill="none" stroke="{WHITE}" stroke-width="{fmt(0.05 * r)}" stroke-linecap="round"/>'
        if kind == "star":
            return f'<path d="{sparkle_d(x, y, 1.25 * w)}" fill="{GOLD}" stroke="{WHITE}" stroke-width="2"/>'
        if kind == "heart":
            return f'<path d="{heart_d(x, y, 1.0 * w)}" fill="{PINK}" stroke="{WHITE}" stroke-width="2.5"/>'
        if kind == "teary":
            return (
                f'<ellipse cx="{fmt(x)}" cy="{fmt(y)}" rx="{fmt(w)}" ry="{fmt(1.2 * w)}" fill="{WHITE}"/>'
                f'<circle cx="{fmt(x)}" cy="{fmt(y + 0.2 * w)}" r="{fmt(0.7 * w)}" fill="{INK}"/>'
                f'<circle cx="{fmt(x + 0.3 * w)}" cy="{fmt(y - 0.2 * w)}" r="{fmt(0.28 * w)}" fill="{WHITE}"/>'
                f'<circle cx="{fmt(x - 0.3 * w)}" cy="{fmt(y + 0.45 * w)}" r="{fmt(0.16 * w)}" fill="{WHITE}"/>'
                f'<path d="M{fmt(x - w)} {fmt(y + 1.0 * w)}Q{fmt(x)} {fmt(y + 1.5 * w)} {fmt(x + w)} {fmt(y + 1.0 * w)}" fill="none" stroke="{TEAR}" stroke-width="{fmt(0.05 * r)}" stroke-linecap="round"/>'
            )
        if kind == "wink":
            return self.eye("normal" if side < 0 else "happy", x, y, side, r)
        if kind == "cry":
            return self.eye("closed", x, y, side, r) + (
                f'<path d="M{fmt(x - 0.3 * w)} {fmt(y + 0.6 * w)}L{fmt(x - 0.4 * w)} {fmt(y + 4.0 * w)}" '
                f'stroke="{TEAR}" stroke-width="{fmt(0.11 * r)}" stroke-linecap="round" stroke-opacity="0.9"/>'
            )
        raise ValueError(kind)

    def brows(self, kind: str) -> None:
        if not kind:
            return
        for side in (-1, 1):
            x, y = self.p(0.38 * side, -0.33)
            w = 0.15 * self.r
            # 内侧是 x - side * w。担心时内侧高，生气时内侧低。
            if kind == "angry":
                d = f"M{fmt(x - side * w)} {fmt(y + 0.45 * w)}L{fmt(x + side * w)} {fmt(y - 0.45 * w)}"
            elif kind == "worried":
                d = f"M{fmt(x - side * w)} {fmt(y - 0.45 * w)}L{fmt(x + side * w)} {fmt(y + 0.45 * w)}"
            else:
                d = f"M{fmt(x - w)} {fmt(y)}Q{fmt(x)} {fmt(y - 0.6 * w)} {fmt(x + w)} {fmt(y)}"
            self.face.append(f'<path d="{d}" fill="none" stroke="{WHITE}" stroke-width="{fmt(0.06 * self.r)}" stroke-linecap="round"/>')

    # ----- 嘴 -----
    def mouth(self, kind: str) -> None:
        x, y = self.p(0, 0.3)
        w = 0.16 * self.r
        line = f'fill="none" stroke="{WHITE}" stroke-width="{fmt(0.06 * self.r)}" stroke-linecap="round" stroke-linejoin="round"'
        if kind == "smile":
            d = f'<path d="M{fmt(x - w)} {fmt(y - 0.2 * w)}Q{fmt(x)} {fmt(y + 0.9 * w)} {fmt(x + w)} {fmt(y - 0.2 * w)}" {line}/>'
        elif kind == "grin":
            d = (
                f'<path d="M{fmt(x - w)} {fmt(y - 0.3 * w)}Q{fmt(x)} {fmt(y + 1.6 * w)} {fmt(x + w)} {fmt(y - 0.3 * w)}Z" fill="{PINK}" stroke="{WHITE}" stroke-width="{fmt(0.045 * self.r)}" stroke-linejoin="round"/>'
            )
        elif kind == "laugh":
            d = (
                f'<path d="M{fmt(x - 1.4 * w)} {fmt(y - 0.4 * w)}Q{fmt(x)} {fmt(y + 2.4 * w)} {fmt(x + 1.4 * w)} {fmt(y - 0.4 * w)}Z" fill="{PINK}" stroke="{WHITE}" stroke-width="{fmt(0.05 * self.r)}" stroke-linejoin="round"/>'
                f'<ellipse cx="{fmt(x)}" cy="{fmt(y + 0.95 * w)}" rx="{fmt(0.6 * w)}" ry="{fmt(0.3 * w)}" fill="{RED}" opacity="0.6"/>'
            )
        elif kind == "o":
            d = f'<ellipse cx="{fmt(x)}" cy="{fmt(y + 0.2 * w)}" rx="{fmt(0.45 * w)}" ry="{fmt(0.55 * w)}" fill="{PINK}" stroke="{WHITE}" stroke-width="{fmt(0.045 * self.r)}"/>'
        elif kind == "big_o":
            d = f'<ellipse cx="{fmt(x)}" cy="{fmt(y + 0.5 * w)}" rx="{fmt(0.75 * w)}" ry="{fmt(1.05 * w)}" fill="{PINK}" stroke="{WHITE}" stroke-width="{fmt(0.05 * self.r)}"/>'
        elif kind == "frown":
            d = f'<path d="M{fmt(x - w)} {fmt(y + 0.6 * w)}Q{fmt(x)} {fmt(y - 0.5 * w)} {fmt(x + w)} {fmt(y + 0.6 * w)}" {line}/>'
        elif kind == "sob":
            d = f'<path d="M{fmt(x - 1.1 * w)} {fmt(y + 1.0 * w)}Q{fmt(x)} {fmt(y - 0.9 * w)} {fmt(x + 1.1 * w)} {fmt(y + 1.0 * w)}Z" fill="{PINK}" stroke="{WHITE}" stroke-width="{fmt(0.05 * self.r)}" stroke-linejoin="round"/>'
        elif kind == "wavy":
            pts = []
            for i in range(9):
                pts.append((x - w + i * w / 4, y + (0.25 * w if i % 2 else -0.15 * w)))
            d = f'<path d="M{"L".join(f"{fmt(a)} {fmt(b)}" for a, b in pts)}" {line}/>'
        elif kind == "flat":
            d = f'<path d="M{fmt(x - 0.7 * w)} {fmt(y + 0.2 * w)}L{fmt(x + 0.7 * w)} {fmt(y + 0.2 * w)}" {line}/>'
        elif kind == "cat":
            d = (
                f'<path d="M{fmt(x - w)} {fmt(y - 0.1 * w)}Q{fmt(x - 0.5 * w)} {fmt(y + 0.8 * w)} {fmt(x)} {fmt(y)}'
                f'Q{fmt(x + 0.5 * w)} {fmt(y + 0.8 * w)} {fmt(x + w)} {fmt(y - 0.1 * w)}" {line}/>'
            )
        elif kind == "tongue":
            d = (
                f'<path d="M{fmt(x - w)} {fmt(y - 0.2 * w)}Q{fmt(x)} {fmt(y + 0.9 * w)} {fmt(x + w)} {fmt(y - 0.2 * w)}" {line}/>'
                f'<path d="M{fmt(x - 0.1 * w)} {fmt(y + 0.3 * w)}Q{fmt(x + 0.1 * w)} {fmt(y + 1.5 * w)} {fmt(x + 0.75 * w)} {fmt(y + 0.15 * w)}Z" fill="{PINK}" stroke="{WHITE}" stroke-width="3"/>'
            )
        elif kind == "pout":
            d = f'<path d="M{fmt(x - 0.4 * w)} {fmt(y + 0.3 * w)}Q{fmt(x)} {fmt(y - 0.3 * w)} {fmt(x + 0.4 * w)} {fmt(y + 0.3 * w)}" {line}/>'
        elif kind == "kiss":
            d = (
                f'<path d="M{fmt(x - 0.15 * w)} {fmt(y - 0.5 * w)}Q{fmt(x + 0.6 * w)} {fmt(y - 0.3 * w)} {fmt(x)} {fmt(y + 0.05 * w)}'
                f'Q{fmt(x + 0.6 * w)} {fmt(y + 0.4 * w)} {fmt(x - 0.15 * w)} {fmt(y + 0.6 * w)}" {line}/>'
            )
        elif kind == "drool":
            d = (
                f'<path d="M{fmt(x - w)} {fmt(y - 0.2 * w)}Q{fmt(x)} {fmt(y + 0.9 * w)} {fmt(x + w)} {fmt(y - 0.2 * w)}" {line}/>'
                f'<path d="M{fmt(x + 0.6 * w)} {fmt(y + 0.25 * w)}Q{fmt(x + 0.95 * w)} {fmt(y + 1.4 * w)} {fmt(x + 0.6 * w)} {fmt(y + 1.6 * w)}'
                f'Q{fmt(x + 0.25 * w)} {fmt(y + 1.4 * w)} {fmt(x + 0.6 * w)} {fmt(y + 0.25 * w)}Z" fill="{TEAR}" stroke="{WHITE}" stroke-width="2"/>'
            )
        elif kind == "none":
            return
        else:
            raise ValueError(kind)
        self.face.append(d)

    def blush(self, strength: float = 0.55) -> None:
        for side in (-1, 1):
            x, y = self.p(0.64 * side, 0.18)
            self.face.append(
                f'<ellipse cx="{fmt(x)}" cy="{fmt(y)}" rx="{fmt(0.15 * self.r)}" ry="{fmt(0.08 * self.r)}" fill="{PINK}" opacity="{strength}"/>'
            )

    # ----- 手 -----
    def arm(self, u: float, v: float, angle: float, size: float = 1.0) -> str:
        x, y = self.p(u, v)
        rx, ry = 0.16 * self.r * size, 0.24 * self.r * size
        return (
            f'<ellipse cx="{fmt(x)}" cy="{fmt(y)}" rx="{fmt(rx)}" ry="{fmt(ry)}" transform="rotate({fmt(angle)} {fmt(x)} {fmt(y)})" '
            f'fill="{INK_TOP}" stroke="{OUTLINE}" stroke-width="3.5"/>'
        )

    def arms(self, pose: str) -> None:
        a = self.arm
        poses = {
            "down": ([a(-1.0, 0.42, 25), a(1.0, 0.42, -25)], "behind"),
            "up": ([a(-1.08, -0.42, -35), a(1.08, -0.42, 35)], "behind"),
            "wave": ([a(-1.0, 0.42, 25), a(1.12, -0.5, 40)], "behind"),
            "raise": ([a(-1.0, 0.42, 25), a(0.95, -0.95, 15)], "behind"),
            "front": ([a(-0.42, 0.72, -60, 0.9), a(0.42, 0.72, 60, 0.9)], "front"),
            "pray": ([a(-0.14, 0.62, -20, 0.85), a(0.14, 0.62, 20, 0.85)], "front"),
            "cheeks": ([a(-0.72, 0.32, 10, 0.75), a(0.72, 0.32, -10, 0.75)], "front"),
            "mouth": ([a(-1.0, 0.42, 25), a(0.2, 0.5, -30, 0.7)], "front"),
            "ears": ([a(-0.95, -0.5, 10, 0.8), a(0.95, -0.5, -10, 0.8)], "front"),
            "salute": ([a(-1.0, 0.42, 25), a(0.72, -0.5, -55, 0.85)], "front"),
            "shrug": ([a(-1.18, 0.05, -70), a(1.18, 0.05, 70)], "behind"),
            "hug": ([a(-1.22, -0.05, -80, 1.05), a(1.22, -0.05, 80, 1.05)], "behind"),
            "none": ([], "behind"),
        }
        shapes, layer = poses[pose]
        (self.front if layer == "front" else self.behind).extend(shapes)

    def draw(self) -> None:
        group = f'<g transform="rotate({fmt(self.rotate)} {fmt(self.cx)} {fmt(self.cy)})">' if self.rotate else "<g>"
        self.c.add(group + "".join(self.behind) + self.body() + "".join(self.face) + "".join(self.front) + "</g>")


# ----- 道具 -----

def prop_heart(c: Canvas, x, y, s, fill=PINK):
    c.add(f'<path d="{heart_d(x, y, s)}" fill="{fill}" stroke="{OUTLINE}" stroke-width="3" stroke-linejoin="round"/>')


def prop_sparkle(c: Canvas, x, y, s, fill=GOLD):
    c.add(f'<path d="{sparkle_d(x, y, s)}" fill="{fill}" stroke="{OUTLINE}" stroke-width="2.5" stroke-linejoin="round"/>')


def prop_sweat(c: Canvas, x, y, s=14):
    c.add(
        f'<path d="M{fmt(x)} {fmt(y - s)}Q{fmt(x + 0.9 * s)} {fmt(y + 0.2 * s)} {fmt(x)} {fmt(y + 0.8 * s)}'
        f'Q{fmt(x - 0.9 * s)} {fmt(y + 0.2 * s)} {fmt(x)} {fmt(y - s)}Z" fill="{TEAR}" stroke="{OUTLINE}" stroke-width="2.5"/>'
    )


def prop_text(c: Canvas, x, y, text, size=44, fill=GOLD, rotate=0):
    c.add(
        f'<text x="{fmt(x)}" y="{fmt(y)}" font-family="{FONT}" font-weight="500" font-size="{size}" text-anchor="middle" '
        f'fill="{fill}" stroke="{OUTLINE}" stroke-width="3" paint-order="stroke" transform="rotate({rotate} {fmt(x)} {fmt(y)})">{text}</text>'
    )


def prop_zzz(c: Canvas, x, y):
    prop_text(c, x, y, "Z", 40, SKY, -12)
    prop_text(c, x + 28, y - 30, "z", 30, SKY, -12)
    prop_text(c, x + 50, y - 54, "z", 22, SKY, -12)


def prop_anger(c: Canvas, x, y, s=16):
    arcs = []
    for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        ax, ay = x + dx * 0.35 * s, y + dy * 0.35 * s
        arcs.append(
            f"M{fmt(ax + dx * 0.8 * s)} {fmt(ay)}Q{fmt(ax)} {fmt(ay)} {fmt(ax)} {fmt(ay + dy * 0.8 * s)}"
        )
    c.add(f'<path d="{"".join(arcs)}" fill="none" stroke="{RED}" stroke-width="6" stroke-linecap="round"/>')


def prop_lines(c: Canvas, segments, color=OUTLINE, width=5):
    d = "".join(f"M{fmt(a)} {fmt(b)}L{fmt(e)} {fmt(f)}" for a, b, e, f in segments)
    c.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round"/>')


def prop_confetti(c: Canvas):
    colors = [GOLD, RED, SKY, GREEN, PINK]
    spots = [(40, 70), (270, 60), (60, 150), (285, 140), (30, 230), (290, 220), (110, 30), (215, 28)]
    for i, (x, y) in enumerate(spots):
        color = colors[i % len(colors)]
        c.add(
            f'<rect x="{x - 7}" y="{y - 4}" width="14" height="8" rx="2" fill="{color}" stroke="{OUTLINE}" stroke-width="2" '
            f'transform="rotate({(i * 47) % 180} {x} {y})"/>'
        )


def prop_cup(c: Canvas, x, y, fill, lid=None, straw=False, boba=False, foam=False):
    c.add(f'<path d="M{x - 26} {y - 34}L{x + 26} {y - 34}L{x + 20} {y + 30}L{x - 20} {y + 30}Z" fill="{fill}" stroke="{OUTLINE}" stroke-width="3.5" stroke-linejoin="round"/>')
    if foam:
        c.add(f'<path d="M{x - 30} {y - 30}Q{x - 22} {y - 50} {x - 8} {y - 40}Q{x} {y - 56} {x + 12} {y - 42}Q{x + 26} {y - 52} {x + 30} {y - 30}Z" fill="{WHITE}" stroke="{OUTLINE}" stroke-width="3"/>')
        c.add(f'<path d="M{x + 24} {y - 18}Q{x + 44} {y - 14} {x + 42} {y + 4}Q{x + 40} {y + 18} {x + 22} {y + 14}" fill="none" stroke="{OUTLINE}" stroke-width="5"/>')
    if lid:
        c.add(f'<rect x="{x - 30}" y="{y - 42}" width="60" height="10" rx="4" fill="{lid}" stroke="{OUTLINE}" stroke-width="3"/>')
    if straw:
        c.add(f'<path d="M{x + 4} {y - 42}L{x + 14} {y - 74}" stroke="{RED}" stroke-width="7" stroke-linecap="round"/>')
    if boba:
        for bx, by in ((x - 10, y + 18), (x + 2, y + 22), (x + 12, y + 16), (x - 2, y + 10)):
            c.add(f'<circle cx="{bx}" cy="{by}" r="5" fill="{INK}"/>')


PROPS = {}


def prop(name):
    def register(function):
        PROPS[name] = function
        return function

    return register


@prop("onigiri")
def _(c):
    c.add(f'<path d="M160 176Q190 222 196 248Q160 262 124 248Q130 222 160 176Z" fill="{WHITE}" stroke="{OUTLINE}" stroke-width="3.5" stroke-linejoin="round"/>')
    c.add(f'<rect x="142" y="226" width="36" height="28" rx="4" fill="{INK}"/>')


@prop("milktea")
def _(c):
    prop_cup(c, 160, 236, "#E8C9A0", lid=WHITE, straw=True, boba=True)


@prop("water")
def _(c):
    prop_cup(c, 160, 236, "#CBEFFA")
    c.add(f'<path d="M140 226L180 226" stroke="{SKY}" stroke-width="4"/>')


@prop("beer")
def _(c):
    prop_cup(c, 236, 120, "#F4C542", foam=True)


@prop("watermelon")
def _(c):
    c.add(f'<path d="M110 220A50 50 0 0 0 210 220Z" fill="#FF6B6B" stroke="{OUTLINE}" stroke-width="3.5"/>')
    c.add(f'<path d="M106 220A54 54 0 0 0 214 220" fill="none" stroke="{GREEN}" stroke-width="9"/>')
    for sx, sy in ((140, 232), (160, 240), (180, 232), (160, 226)):
        c.add(f'<ellipse cx="{sx}" cy="{sy}" rx="3" ry="5" fill="{INK}"/>')


@prop("glowsticks")
def _(c):
    for x, color, angle in ((62, SKY, -25), (258, PINK, 25)):
        c.add(f'<rect x="{x - 8}" y="40" width="16" height="78" rx="8" fill="{color}" stroke="{OUTLINE}" stroke-width="3" transform="rotate({angle} {x} 110)"/>')
    prop_lines(c, [(30, 40, 18, 28), (52, 26, 50, 10), (290, 40, 302, 28), (268, 26, 270, 10)], GOLD, 4)


@prop("notebook")
def _(c):
    c.add(f'<rect x="112" y="206" width="96" height="70" rx="6" fill="{CREAM}" stroke="{OUTLINE}" stroke-width="3.5"/>')
    prop_lines(c, [(124, 224, 196, 224), (124, 240, 196, 240), (124, 256, 176, 256)], "#C9B79C", 3)
    c.add(f'<path d="M222 196L244 180L250 188L228 204Z" fill="{GOLD}" stroke="{OUTLINE}" stroke-width="3" stroke-linejoin="round"/>')


@prop("keyboard")
def _(c):
    c.add(f'<rect x="92" y="226" width="136" height="50" rx="10" fill="#D9DEE8" stroke="{OUTLINE}" stroke-width="3.5"/>')
    for row in range(2):
        for col in range(6):
            c.add(f'<rect x="{102 + col * 20}" y="{234 + row * 18}" width="14" height="12" rx="3" fill="{WHITE}" stroke="{OUTLINE}" stroke-width="1.5"/>')


@prop("laptop")
def _(c):
    c.add(f'<rect x="200" y="176" width="92" height="62" rx="6" fill="#D9DEE8" stroke="{OUTLINE}" stroke-width="3.5"/>')
    c.add(f'<rect x="208" y="184" width="76" height="46" rx="3" fill="{SKY}" opacity="0.6"/>')
    c.add(f'<path d="M190 240L302 240L294 252L198 252Z" fill="#B8BFCC" stroke="{OUTLINE}" stroke-width="3" stroke-linejoin="round"/>')


@prop("fish")
def _(c):
    c.add(f'<ellipse cx="236" cy="236" rx="40" ry="20" fill="{SKY}" stroke="{OUTLINE}" stroke-width="3.5"/>')
    c.add(f'<path d="M274 236L298 218L298 254Z" fill="{SKY}" stroke="{OUTLINE}" stroke-width="3.5" stroke-linejoin="round"/>')
    c.add(f'<circle cx="214" cy="230" r="4" fill="{INK}"/>')


@prop("dust")
def _(c):
    for x, y, r in ((44, 250, 20), (70, 262, 16), (24, 268, 13), (92, 272, 12)):
        c.add(f'<circle cx="{x}" cy="{y}" r="{r}" fill="#E6E6EA" stroke="{OUTLINE}" stroke-width="2.5"/>')
    prop_lines(c, [(30, 150, 66, 150), (20, 180, 60, 180), (36, 210, 62, 210)], "#9AA3B5", 5)


@prop("broken_heart")
def _(c):
    c.add(f'<path d="{heart_d(250, 74, 30)}" fill="{RED}" stroke="{OUTLINE}" stroke-width="3" stroke-linejoin="round"/>')
    c.add(f'<path d="M250 62L242 76L256 86L246 100" fill="none" stroke="{WHITE}" stroke-width="5" stroke-linejoin="round"/>')


@prop("umbrella")
def _(c):
    c.add(f'<path d="M60 92Q160 -10 260 92Q235 80 210 92Q185 80 160 92Q135 80 110 92Q85 80 60 92Z" fill="{RED}" stroke="{OUTLINE}" stroke-width="3.5" stroke-linejoin="round"/>')
    c.add(f'<path d="M160 92L160 124" stroke="{OUTLINE}" stroke-width="5" stroke-linecap="round"/>')
    for x, y in ((40, 130), (280, 120), (30, 200), (292, 196), (52, 262), (270, 268)):
        prop_sweat(c, x, y, 11)


@prop("cake")
def _(c):
    c.add(f'<rect x="108" y="220" width="104" height="54" rx="8" fill="{CREAM}" stroke="{OUTLINE}" stroke-width="3.5"/>')
    c.add(f'<path d="M108 236Q121 250 134 236Q147 250 160 236Q173 250 186 236Q199 250 212 236" fill="none" stroke="{PINK}" stroke-width="8"/>')
    c.add(f'<rect x="155" y="196" width="10" height="26" rx="3" fill="{SKY}" stroke="{OUTLINE}" stroke-width="2.5"/>')
    c.add(f'<path d="M160 176Q170 190 160 196Q150 190 160 176Z" fill="{GOLD}" stroke="{OUTLINE}" stroke-width="2.5"/>')


@prop("blanket")
def _(c):
    c.add(f'<path d="M40 300Q30 190 160 182Q290 190 280 300Z" fill="#8FB8E8" stroke="{OUTLINE}" stroke-width="4" stroke-linejoin="round"/>')
    for x, y in ((96, 240), (160, 262), (224, 236), (130, 288), (200, 290)):
        c.add(f'<circle cx="{x}" cy="{y}" r="7" fill="{WHITE}" opacity="0.8"/>')


@prop("magnifier")
def _(c):
    c.add(f'<circle cx="196" cy="168" r="34" fill="{SKY}" fill-opacity="0.25" stroke="{OUTLINE}" stroke-width="7"/>')
    c.add(f'<path d="M220 194L254 232" stroke="{GOLD}" stroke-width="12" stroke-linecap="round"/>')


@prop("gift")
def _(c):
    c.add(f'<rect x="118" y="214" width="84" height="64" rx="6" fill="{RED}" stroke="{OUTLINE}" stroke-width="3.5"/>')
    c.add(f'<rect x="153" y="214" width="14" height="64" fill="{GOLD}"/>')
    c.add(f'<path d="M160 214Q136 186 128 202Q126 214 160 214Q194 214 192 202Q184 186 160 214Z" fill="{GOLD}" stroke="{OUTLINE}" stroke-width="3"/>')


@prop("sun")
def _(c):
    rays = [(250 + 46 * math.cos(i * math.pi / 4), 62 + 46 * math.sin(i * math.pi / 4), 250 + 60 * math.cos(i * math.pi / 4), 62 + 60 * math.sin(i * math.pi / 4)) for i in range(8)]
    prop_lines(c, rays, GOLD, 6)
    c.add(f'<circle cx="250" cy="62" r="32" fill="{GOLD}" stroke="{OUTLINE}" stroke-width="3.5"/>')


@prop("moon")
def _(c):
    c.add(f'<path d="M260 30A40 40 0 1 0 290 96A32 32 0 1 1 260 30Z" fill="{GOLD}" stroke="{OUTLINE}" stroke-width="3.5"/>')
    prop_sparkle(c, 214, 40, 9, WHITE)


@prop("bubbles")
def _(c):
    for x, y, r in ((236, 70, 18), (268, 36, 12), (206, 34, 9), (280, 88, 7)):
        c.add(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{SKY}" fill-opacity="0.35" stroke="{OUTLINE}" stroke-width="2.5"/>')
        c.add(f'<circle cx="{x - r * 0.35}" cy="{y - r * 0.35}" r="{r * 0.22}" fill="{WHITE}"/>')


@prop("check")
def _(c):
    c.add(f'<circle cx="250" cy="72" r="34" fill="{GREEN}" stroke="{OUTLINE}" stroke-width="3.5"/>')
    c.add(f'<path d="M234 72L246 84L268 60" fill="none" stroke="{WHITE}" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>')


@prop("medal")
def _(c):
    c.add(f'<path d="M232 34L250 74L268 34Z" fill="{RED}" stroke="{OUTLINE}" stroke-width="3"/>')
    c.add(f'<circle cx="250" cy="90" r="26" fill="{GOLD}" stroke="{OUTLINE}" stroke-width="3.5"/>')
    prop_sparkle(c, 250, 90, 12, WHITE)


@prop("thermometer")
def _(c):
    c.add(f'<rect x="176" y="208" width="58" height="10" rx="5" fill="{WHITE}" stroke="{OUTLINE}" stroke-width="2.5" transform="rotate(-20 176 212)"/>')
    c.add(f'<circle cx="236" cy="192" r="8" fill="{RED}" stroke="{OUTLINE}" stroke-width="2.5"/>')


@prop("hand_on_head")
def _(c):
    c.add(f'<ellipse cx="200" cy="40" rx="44" ry="24" fill="{CREAM}" stroke="{OUTLINE}" stroke-width="3.5" transform="rotate(-18 200 40)"/>')
    prop_lines(c, [(250, 20, 268, 10), (256, 44, 276, 42)], GOLD, 4)


@prop("thumbs")
def _(c):
    c.add(f'<path d="M232 120L232 80Q232 64 244 58L252 40Q262 38 262 52L258 70L282 70Q296 72 292 86L284 116Q282 124 272 124L240 124Q232 124 232 120Z" fill="{GOLD}" stroke="{OUTLINE}" stroke-width="3.5" stroke-linejoin="round"/>')
    c.add(f'<rect x="214" y="76" width="18" height="50" rx="5" fill="{GOLD}" stroke="{OUTLINE}" stroke-width="3.5"/>')


@prop("drumstick")
def _(c):
    c.add(f'<ellipse cx="246" cy="84" rx="30" ry="24" fill="#D98B4A" stroke="{OUTLINE}" stroke-width="3.5" transform="rotate(-30 246 84)"/>')
    c.add(f'<path d="M226 104L206 128" stroke="{CREAM}" stroke-width="12" stroke-linecap="round"/>')
    c.add(f'<circle cx="202" cy="134" r="8" fill="{CREAM}" stroke="{OUTLINE}" stroke-width="2.5"/>')


@prop("steam")
def _(c):
    for x in (84, 236):
        c.add(f'<path d="M{x} 70Q{x - 14} 54 {x} 40Q{x + 14} 26 {x} 10" fill="none" stroke="#C9CDD6" stroke-width="7" stroke-linecap="round"/>')


@prop("sigh")
def _(c):
    c.add(f'<path d="M206 206Q228 190 246 204Q266 196 274 214Q290 222 278 238Q266 250 246 240Q226 250 214 234Q196 226 206 206Z" fill="#E6E6EA" stroke="{OUTLINE}" stroke-width="2.5"/>')


@prop("orbit")
def _(c):
    c.add(f'<ellipse cx="160" cy="42" rx="70" ry="16" fill="none" stroke="{GOLD}" stroke-width="4" stroke-dasharray="10 10"/>')
    for x, y in ((96, 46), (220, 36), (160, 58)):
        prop_sparkle(c, x, y, 11)


@prop("notes")
def _(c):
    prop_text(c, 50, 100, "♪", 52, SKY, -10)
    prop_text(c, 272, 82, "♫", 48, PINK, 12)


@prop("dots")
def _(c):
    for x in (222, 244, 266):
        c.add(f'<circle cx="{x}" cy="60" r="7" fill="{WHITE}" stroke="{OUTLINE}" stroke-width="2.5"/>')


@prop("lightning")
def _(c):
    for x, flip in ((52, 1), (268, -1)):
        c.add(f'<path d="M{x} 30L{x + flip * 18} 70L{x - flip * 2} 70L{x + flip * 14} 110" fill="none" stroke="{GOLD}" stroke-width="7" stroke-linejoin="round" stroke-linecap="round"/>')


@prop("tremble")
def _(c):
    prop_lines(c, [(48, 140, 38, 160), (38, 160, 48, 180), (272, 140, 282, 160), (282, 160, 272, 180)], "#9AA3B5", 5)


@prop("clap")
def _(c):
    prop_lines(c, [(118, 196, 104, 184), (202, 196, 216, 184), (160, 178, 160, 160)], GOLD, 5)


@prop("wave_lines")
def _(c):
    prop_lines(c, [(284, 52, 300, 40), (290, 80, 308, 78)], GOLD, 5)


@prop("hearts_float")
def _(c):
    prop_heart(c, 260, 60, 18)
    prop_heart(c, 290, 28, 11)


@prop("hearts_around")
def _(c):
    prop_heart(c, 56, 70, 16)
    prop_heart(c, 266, 60, 18)
    prop_heart(c, 40, 190, 11)


@prop("big_heart_top")
def _(c):
    prop_heart(c, 160, 44, 30, RED)


@prop("question")
def _(c):
    prop_text(c, 262, 92, "?", 72)


@prop("exclaim")
def _(c):
    prop_text(c, 262, 92, "!", 72)


@prop("anger")
def _(c):
    prop_anger(c, 250, 70)


@prop("sweat")
def _(c):
    prop_sweat(c, 250, 104)


@prop("sweats")
def _(c):
    prop_sweat(c, 246, 100)
    prop_sweat(c, 72, 122, 11)


@prop("zzz")
def _(c):
    prop_zzz(c, 236, 92)


@prop("sparkles")
def _(c):
    prop_sparkle(c, 54, 70, 16)
    prop_sparkle(c, 270, 56, 20)
    prop_sparkle(c, 284, 150, 11)


@prop("confetti")
def _(c):
    prop_confetti(c)


@prop("peek_wall")
def _(c):
    c.add(f'<rect x="0" y="0" width="92" height="320" fill="#C9B79C" stroke="{OUTLINE}" stroke-width="4"/>')


# 66 张贴纸，顺序和名字与上游清单一致（文件名 01 到 66），换成墨墨的表情。
STICKERS = [
    ("开心", dict(eyes="happy", mouth="grin", arms="down", props=["sparkles"])),
    ("大笑", dict(eyes="tight", mouth="laugh", arms="up", props=["sparkles"])),
    ("害羞", dict(eyes="closed", mouth="smile", arms="cheeks", blush=0.95)),
    ("生气", dict(eyes="normal", brows="angry", mouth="frown", arms="down", props=["anger", "steam"])),
    ("大哭", dict(eyes="cry", brows="worried", mouth="sob", arms="down")),
    ("惊讶", dict(eyes="wide", mouth="o", arms="shrug", props=["exclaim"])),
    ("犯困", dict(eyes="closed", mouth="o", arms="down", props=["zzz"])),
    ("得意", dict(eyes="smug", mouth="cat", arms="down", props=["sparkles"])),
    ("疑惑", dict(eyes="normal", brows="worried", mouth="flat", arms="down", props=["question"])),
    ("点赞", dict(eyes="happy", mouth="smile", arms="raise", props=["thumbs"])),
    ("加油", dict(eyes="normal", brows="angry", mouth="grin", arms="up", props=["sparkles"])),
    ("谢谢", dict(eyes="closed", mouth="smile", arms="pray", props=["hearts_float"])),
    ("晚安", dict(eyes="closed", mouth="smile", arms="down", props=["moon", "zzz"])),
    ("OK", dict(eyes="wink", mouth="smile", arms="raise", props=["sparkles"])),
    ("无语", dict(eyes="flat", mouth="flat", arms="down", props=["sweat"])),
    ("比心", dict(eyes="happy", mouth="smile", arms="up", props=["big_heart_top"])),
    ("馋", dict(eyes="star", mouth="drool", arms="down", props=["drumstick"])),
    ("委屈", dict(eyes="teary", brows="worried", mouth="wavy", arms="front")),
    ("害怕", dict(eyes="wide", brows="worried", mouth="wavy", arms="front", props=["tremble", "sweat"])),
    ("偷笑", dict(eyes="happy", mouth="cat", arms="mouth", blush=0.8)),
    ("鼓掌", dict(eyes="happy", mouth="grin", arms="pray", props=["clap", "sparkles"])),
    ("举手", dict(eyes="normal", mouth="o", arms="raise", props=["exclaim"])),
    ("敬礼", dict(eyes="normal", brows="arch", mouth="smile", arms="salute")),
    ("拜拜", dict(eyes="happy", mouth="smile", arms="wave", props=["wave_lines"])),
    ("啃饭团", dict(eyes="happy", mouth="none", arms="front", props=["onigiri"])),
    ("喝奶茶", dict(eyes="happy", mouth="none", arms="front", props=["milktea"])),
    ("抱抱", dict(eyes="closed", mouth="smile", arms="hug", props=["hearts_around"], blush=0.8)),
    ("被摸头", dict(eyes="closed", mouth="cat", arms="down", props=["hand_on_head"], blush=0.9)),
    ("飞吻", dict(eyes="wink", mouth="kiss", arms="mouth", props=["hearts_float"])),
    ("发烧", dict(eyes="x", mouth="flat", arms="down", props=["thermometer", "sweat"], blush=1.0)),
    ("加班", dict(eyes="flat", brows="worried", mouth="wavy", arms="down", props=["laptop", "sweat"])),
    ("庆祝", dict(eyes="tight", mouth="laugh", arms="up", props=["confetti"])),
    ("震惊", dict(eyes="wide", mouth="big_o", arms="shrug", props=["lightning"])),
    ("晕", dict(eyes="spiral", mouth="wavy", arms="down", props=["orbit"])),
    ("无奈", dict(eyes="flat", mouth="flat", arms="shrug", props=["sweat"])),
    ("求求了", dict(eyes="teary", brows="worried", mouth="o", arms="pray", props=["sparkles"])),
    ("偷看", dict(eyes="side", mouth="none", arms="none", cx=122, props_after=["peek_wall"])),
    ("躺平", dict(eyes="flat", mouth="flat", arms="down", rotate=-90, cx=176, cy=176, props=["zzz"])),
    ("生闷气", dict(eyes="flat", brows="angry", mouth="pout", arms="down", props=["anger"], blush=0.9)),
    ("哼", dict(eyes="closed", brows="angry", mouth="pout", arms="shrug", props=["sigh"])),
    ("收到", dict(eyes="normal", mouth="smile", arms="salute", props=["check"])),
    ("冒泡", dict(eyes="side", mouth="o", arms="raise", props=["bubbles"])),
    ("早安", dict(eyes="happy", mouth="grin", arms="up", props=["sun"])),
    ("干杯", dict(eyes="happy", mouth="laugh", arms="raise", props=["beer"])),
    ("吃瓜", dict(eyes="side", mouth="none", arms="front", props=["watermelon"])),
    ("打call", dict(eyes="star", mouth="laugh", arms="up", props=["glowsticks"])),
    ("记笔记", dict(eyes="normal", brows="arch", mouth="smile", arms="front", props=["notebook"])),
    ("认真打字", dict(eyes="normal", brows="angry", mouth="flat", arms="front", props=["keyboard"])),
    ("摸鱼", dict(eyes="smug", mouth="cat", arms="down", props=["fish"])),
    ("溜了", dict(eyes="side", mouth="smile", arms="wave", cx=184, props=["dust"])),
    ("我错了", dict(eyes="teary", brows="worried", mouth="wavy", arms="pray", props=["sweat"])),
    ("叹气", dict(eyes="flat", mouth="o", arms="down", props=["sigh"])),
    ("心碎", dict(eyes="cry", brows="worried", mouth="frown", arms="down", props=["broken_heart"])),
    ("好耶", dict(eyes="star", mouth="laugh", arms="up", props=["sparkles", "confetti"])),
    ("呆住", dict(eyes="dot", mouth="flat", arms="down", props=["dots"])),
    ("嘘", dict(eyes="wink", mouth="pout", arms="mouth")),
    ("不听", dict(eyes="tight", mouth="wavy", arms="ears", props=["notes"])),
    ("喝水", dict(eyes="closed", mouth="none", arms="front", props=["water"])),
    ("观察", dict(eyes="side", brows="arch", mouth="flat", arms="down", props=["magnifier"])),
    ("送你", dict(eyes="happy", mouth="grin", arms="front", props=["gift"], blush=0.8)),
    ("躲被窝", dict(eyes="side", mouth="none", arms="none", props_after=["blanket"])),
    ("生日快乐", dict(eyes="happy", mouth="laugh", arms="up", props=["cake", "confetti"])),
    ("下雨了", dict(eyes="teary", mouth="frown", arms="down", props_before=["umbrella"])),
    ("紧张", dict(eyes="wide", brows="worried", mouth="wavy", arms="front", props=["sweats"])),
    ("鬼脸", dict(eyes="wink", mouth="tongue", arms="ears")),
    ("求表扬", dict(eyes="smug", mouth="cat", arms="down", props=["medal", "sparkles"], blush=0.7)),
]


# 拿在身前的道具，贴纸里往上提一些，免得压住底下的字
HELD = {"onigiri", "milktea", "water", "watermelon", "notebook", "keyboard", "gift", "cake"}


def caption(c: Canvas, text: str) -> None:
    size = 46 if len(text) <= 2 else 40 if len(text) == 3 else 36
    c.add(
        f'<text x="160" y="306" font-family="{FONT}" font-weight="500" font-size="{size}" text-anchor="middle" '
        f'fill="{INK}" stroke="{WHITE}" stroke-width="10" stroke-linejoin="round" paint-order="stroke">{text}</text>'
    )


def sticker_svg(name: str, spec: dict) -> str:
    c = Canvas()
    for item in spec.get("props_before", []):
        PROPS[item](c)
    momo = Momo(c, cx=spec.get("cx", 160), cy=spec.get("cy", 168), r=78, rotate=spec.get("rotate", 0))
    momo.arms(spec.get("arms", "down"))
    momo.eyes(spec["eyes"])
    momo.brows(spec.get("brows", ""))
    momo.mouth(spec.get("mouth", "smile"))
    momo.blush(spec.get("blush", 0.55))
    momo.draw()
    for item in spec.get("props", []):
        if item in HELD:
            c.add('<g transform="translate(0 -18)">')
            PROPS[item](c)
            c.add("</g>")
        else:
            PROPS[item](c)
    for item in spec.get("props_after", []):
        PROPS[item](c)
    caption(c, name)
    return c.svg()


# 设置页的十个空状态插画，没有字，墨墨配一件道具。
def scene_prop(c: Canvas, scene: str) -> None:
    if scene == "input":
        PROPS["keyboard"](c)
    elif scene == "notes":
        PROPS["notebook"](c)
    elif scene == "privacy":
        c.add(f'<path d="M252 40L288 54L288 92Q288 122 252 138Q216 122 216 92L216 54Z" fill="{GREEN}" stroke="{OUTLINE}" stroke-width="3.5" stroke-linejoin="round"/>')
        c.add(f'<rect x="238" y="84" width="28" height="24" rx="4" fill="{WHITE}" stroke="{OUTLINE}" stroke-width="2.5"/>')
        c.add(f'<path d="M244 84L244 76Q252 64 260 76L260 84" fill="none" stroke="{OUTLINE}" stroke-width="4"/>')
    elif scene == "sync":
        c.add(f'<path d="M222 70A32 32 0 0 1 282 70" fill="none" stroke="{SKY}" stroke-width="9" stroke-linecap="round"/>')
        c.add(f'<path d="M282 100A32 32 0 0 1 222 100" fill="none" stroke="{GOLD}" stroke-width="9" stroke-linecap="round"/>')
        c.add(f'<path d="M270 62L284 74L294 58" fill="none" stroke="{SKY}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>')
        c.add(f'<path d="M234 108L220 96L210 112" fill="none" stroke="{GOLD}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>')
    elif scene == "settings":
        teeth = []
        for i in range(8):
            angle = i * math.pi / 4
            teeth.append(f'<rect x="246" y="38" width="16" height="22" rx="3" fill="{GOLD}" stroke="{OUTLINE}" stroke-width="2.5" transform="rotate({fmt(math.degrees(angle))} 254 82)"/>')
        c.add("".join(teeth))
        c.add(f'<circle cx="254" cy="82" r="32" fill="{GOLD}" stroke="{OUTLINE}" stroke-width="3.5"/>')
        c.add(f'<circle cx="254" cy="82" r="12" fill="{WHITE}" stroke="{OUTLINE}" stroke-width="3"/>')
    elif scene == "welcome":
        PROPS["sparkles"](c)
    elif scene == "updated":
        PROPS["confetti"](c)
        PROPS["check"](c)
    elif scene == "empty":
        c.add(f'<path d="M198 214L292 214L282 280L208 280Z" fill="#D9B98C" stroke="{OUTLINE}" stroke-width="3.5" stroke-linejoin="round"/>')
        c.add(f'<path d="M198 214L180 190L270 190L292 214" fill="#E8CBA0" stroke="{OUTLINE}" stroke-width="3.5" stroke-linejoin="round"/>')
        PROPS["question"](c)
    elif scene == "offline":
        c.add(f'<path d="M222 110Q204 110 206 92Q208 76 226 78Q232 54 258 58Q282 62 282 84Q298 86 296 100Q294 112 278 110Z" fill="#E6E6EA" stroke="{OUTLINE}" stroke-width="3.5" stroke-linejoin="round"/>')
        c.add(f'<path d="M226 52L292 122" stroke="{RED}" stroke-width="8" stroke-linecap="round"/>')
    elif scene == "downloading":
        c.add(f'<path d="M254 36L254 92M232 72L254 96L276 72" fill="none" stroke="{SKY}" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>')
        c.add(f'<path d="M222 108L222 120L286 120L286 108" fill="none" stroke="{OUTLINE}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>')


SCENES = {
    "input": dict(eyes="normal", brows="arch", mouth="smile", arms="front"),
    "notes": dict(eyes="happy", mouth="smile", arms="front"),
    "privacy": dict(eyes="normal", mouth="smile", arms="down"),
    "sync": dict(eyes="happy", mouth="grin", arms="wave"),
    "settings": dict(eyes="normal", brows="arch", mouth="cat", arms="raise"),
    "welcome": dict(eyes="happy", mouth="laugh", arms="up"),
    "updated": dict(eyes="star", mouth="grin", arms="up"),
    "empty": dict(eyes="side", mouth="o", arms="shrug"),
    "offline": dict(eyes="teary", brows="worried", mouth="wavy", arms="down"),
    "downloading": dict(eyes="normal", mouth="o", arms="front"),
}


def scene_svg(scene: str) -> str:
    spec = SCENES[scene]
    c = Canvas()
    momo = Momo(c, cx=140 if scene not in ("input", "notes", "downloading") else 160, cy=182, r=86)
    momo.arms(spec["arms"])
    momo.eyes(spec["eyes"])
    momo.brows(spec.get("brows", ""))
    momo.mouth(spec["mouth"])
    momo.blush()
    momo.draw()
    scene_prop(c, scene)
    return c.svg()


# ---------- 渲染与写文件 ----------


def environment() -> dict:
    env = dict(os.environ)
    conf = FONT_DIR / "fonts.conf"
    if conf.is_file():
        env["FONTCONFIG_FILE"] = str(conf)
    return env


def render(svg: str, destination: pathlib.Path, width: int, height: int | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False, encoding="utf-8") as handle:
        handle.write(svg)
        source = handle.name
    try:
        subprocess.run(
            ["rsvg-convert", "-w", str(width), "-h", str(height or width), source, "-o", str(destination)],
            check=True,
            env=environment(),
        )
    finally:
        os.unlink(source)


def webp(svg: str, destination: pathlib.Path, pixels: int) -> None:
    with tempfile.TemporaryDirectory() as directory:
        png = pathlib.Path(directory) / "image.png"
        render(svg, png, pixels)
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cwebp", "-quiet", "-q", "92", "-alpha_q", "100", "-m", "6", str(png), "-o", str(destination)], check=True)


def icns(svg: str, destination: pathlib.Path, names: list[str]) -> None:
    with tempfile.TemporaryDirectory() as directory:
        iconset = pathlib.Path(directory) / "icon.iconset"
        iconset.mkdir()
        for name in names:
            base = int(name.split("_")[1].split("x")[0])
            render(svg, iconset / name, base * (2 if "@2x" in name else 1))
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(destination)], check=True)


def ico(svg: str, destination: pathlib.Path, sizes: list[int]) -> None:
    """PNG 压缩的 ICO：每个尺寸一张 PNG，Windows Vista 起都认。"""
    with tempfile.TemporaryDirectory() as directory:
        images = []
        for size in sizes:
            png = pathlib.Path(directory) / f"{size}.png"
            render(svg, png, size)
            images.append((size, png.read_bytes()))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(header + entries + blobs)


def template_tiff(svg: str, destination: pathlib.Path) -> None:
    """16 点的模板图：1x 和 2x 两张合进一个 TIFF，与上游 render_menu_icon.swift 的产物结构相同。"""
    with tempfile.TemporaryDirectory() as directory:
        tiffs = []
        for pixels, dpi in ((16, 72), (32, 144)):
            png = pathlib.Path(directory) / f"{pixels}.png"
            tiff = pathlib.Path(directory) / f"{pixels}.tiff"
            render(svg, png, pixels)
            subprocess.run(["sips", "-s", "format", "tiff", "-s", "dpiWidth", str(dpi), "-s", "dpiHeight", str(dpi), str(png), "--out", str(tiff)], check=True, capture_output=True)
            tiffs.append(str(tiff))
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["tiffutil", "-cathidpicheck", *tiffs, "-out", str(destination)], check=True, capture_output=True)


def asset(relative: str) -> pathlib.Path:
    return ASSETS / relative


def upstream_png_sizes(directory: str) -> dict[str, tuple[int, int]]:
    """上游目录里每张 PNG 的像素尺寸，照着它们出同尺寸的图，文件名和 Contents.json 都不用改。"""
    sizes = {}
    for path in sorted((ROOT / directory).glob("*.png")):
        out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)], capture_output=True, text=True, check=True).stdout
        width = int(out.split("pixelWidth:")[1].split()[0])
        height = int(out.split("pixelHeight:")[1].split()[0])
        sizes[path.name] = (width, height)
    return sizes


def generate() -> None:
    if ASSETS.exists():
        shutil.rmtree(ASSETS)
    tile = tile_svg()
    # 标志的两份源图
    asset("packages/ui/src/assets/msime.svg").parent.mkdir(parents=True, exist_ok=True)
    asset("packages/ui/src/assets/msime.svg").write_text(tile, encoding="utf-8")
    asset("apps/desktop/app-icon.svg").parent.mkdir(parents=True, exist_ok=True)
    asset("apps/desktop/app-icon.svg").write_text(tile, encoding="utf-8")
    # macOS 输入法和设置应用
    icns(tile, asset("platforms/macos/resources/MSIMEClientInputMethod.icns"),
         ["icon_16x16.png", "icon_16x16@2x.png", "icon_32x32.png", "icon_32x32@2x.png", "icon_128x128.png", "icon_128x128@2x.png", "icon_256x256.png"])
    menu = menu_icon_svg()
    asset("platforms/macos/resources/MSIMEClientInputMethodMenuIcon.svg").write_text(menu, encoding="utf-8")
    template_tiff(menu, asset("platforms/macos/resources/MSIMEClientInputMethodMenuIcon.tiff"))
    tauri = "apps/desktop/src-tauri/icons"
    for name, (width, height) in upstream_png_sizes(tauri).items():
        render(tile, asset(f"{tauri}/{name}"), width, height)
    icns(tile, asset(f"{tauri}/icon.icns"),
         ["icon_16x16.png", "icon_16x16@2x.png", "icon_32x32.png", "icon_32x32@2x.png", "icon_128x128.png", "icon_128x128@2x.png",
          "icon_256x256.png", "icon_256x256@2x.png", "icon_512x512.png", "icon_512x512@2x.png"])
    ico(tile, asset(f"{tauri}/icon.ico"), [16, 24, 32, 48, 64, 256])
    # iOS
    catalog = "platforms/ios/App/Resources/Assets.xcassets"
    render(home_screen_svg(EDGE), asset(f"{catalog}/AppIcon.appiconset/AppIcon.png"), 1024)
    render(home_screen_svg(EDGE), asset(f"{catalog}/AppIconPreviewClassic.imageset/Preview.png"), 256)
    for name, edge in VARIANTS.items():
        art = home_screen_svg(edge)
        for file, (width, height) in upstream_png_sizes(f"{catalog}/AppIcon{name}.appiconset").items():
            render(art, asset(f"{catalog}/AppIcon{name}.appiconset/{file}"), width, height)
        render(art, asset(f"{catalog}/AppIconPreview{name}.imageset/Preview.png"), 256)
    render(tile, asset(f"{catalog}/MSIMELogo.imageset/logo.png"), 1024)
    render(keyboard_brand_svg(), asset("platforms/ios/SharedResources/KeyboardBrand.png"), 1024)
    # 吉祥物
    for scene in SCENES:
        webp(scene_svg(scene), asset(f"packages/ui/src/assets/mascot/{scene}.webp"), 256)
    # 贴纸：目录名 shuishan 是内部 id，只换图和显示名
    stickers = "resources/stickers/shuishan"
    manifest = {"name": "墨墨", "stickers": []}
    for index, (name, spec) in enumerate(STICKERS, 1):
        file = f"{index:02d}.webp"
        webp(sticker_svg(name, spec), asset(f"{stickers}/{file}"), 320)
        manifest["stickers"].append({"file": file, "name": name})
    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    # 与上游清单同样一行一张
    text = text.replace('{\n      "file"', '{"file"').replace('",\n      "name"', '", "name"').replace('"\n    }', '"}')
    asset(f"{stickers}/manifest.json").write_text(text + "\n", encoding="utf-8")


def sheets(directory: pathlib.Path) -> None:
    """把贴纸和吉祥物各拼一张对照图，便于目检。"""
    directory.mkdir(parents=True, exist_ok=True)
    columns, cell = 11, 160
    rows = math.ceil(len(STICKERS) / columns)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{columns * cell}" height="{rows * cell}"><rect width="100%" height="100%" fill="#ECEFF4"/>']
    for index, (name, spec) in enumerate(STICKERS):
        body = sticker_svg(name, spec)
        fragment = inner(body).replace('id="ink"', f'id="ink{index}"').replace("url(#ink)", f"url(#ink{index})")
        x, y = (index % columns) * cell, (index // columns) * cell
        parts.append(f'<g transform="translate({x} {y}) scale(0.5)">{fragment}</g>')
    parts.append("</svg>")
    render("".join(parts), directory / "stickers.png", columns * cell, rows * cell)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{5 * cell}" height="{2 * cell}"><rect width="100%" height="100%" fill="#FFFFFF"/>']
    for index, scene in enumerate(SCENES):
        fragment = inner(scene_svg(scene)).replace('id="ink"', f'id="ink{index}"').replace("url(#ink)", f"url(#ink{index})")
        parts.append(f'<g transform="translate({(index % 5) * cell} {(index // 5) * cell}) scale(0.5)">{fragment}</g>')
    parts.append("</svg>")
    render("".join(parts), directory / "mascot.png", 5 * cell, 2 * cell)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{6 * cell}" height="{cell}"><rect width="100%" height="100%" fill="#F4F1EA"/>']
    for index, edge in enumerate([EDGE, *VARIANTS.values()]):
        fragment = inner(home_screen_svg(edge))
        parts.append(f'<g transform="translate({index * cell + 15} 15) scale(1.18)"><clipPath id="c{index}"><rect width="110" height="110" rx="25"/></clipPath><g clip-path="url(#c{index})">{fragment}</g></g>')
    parts.append("</svg>")
    render("".join(parts), directory / "icons.png", 6 * cell, cell)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sheet", type=pathlib.Path, help="另外把对照图写到这个目录")
    parser.add_argument("--sheet-only", action="store_true", help="只出对照图，不写 brand/assets/")
    args = parser.parse_args()
    for tool in ("rsvg-convert", "cwebp", "iconutil", "tiffutil", "sips"):
        if not shutil.which(tool):
            raise SystemExit(f"缺少 {tool}：brew install librsvg webp（iconutil、tiffutil、sips 是 macOS 自带的）")
    if not args.sheet_only:
        generate()
    if args.sheet:
        sheets(args.sheet)


if __name__ == "__main__":
    main()

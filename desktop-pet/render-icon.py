#!/usr/bin/env python3
"""把 pet-profile.json 里的音灵渲染成应用图标 PNG（含光晕 + 配饰）。"""

from __future__ import annotations

import json
import math
import os
import sys

from PySide6.QtCore import QByteArray, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QRadialGradient
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_FILE = os.path.join(BASE_DIR, "pet-profile.json")
SIZE = 1024


def main() -> int:
    with open(PROFILE_FILE, encoding="utf-8") as handle:
        profile = json.load(handle)

    app = QApplication(sys.argv)
    pixmap = QPixmap(SIZE, SIZE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    body = QColor(profile["bodyColor"])
    accent = QColor(profile["accentColor"])

    # 背景圆角 + 径向光晕
    center = SIZE / 2
    radius = SIZE * 0.46
    bg = QRadialGradient(center, center, radius)
    top = QColor(accent)
    top.setAlpha(120)
    bottom = QColor(body)
    bottom.setAlpha(150)
    bg.setColorAt(0.0, top)
    bg.setColorAt(1.0, bottom)
    painter.setBrush(bg)
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(QPoint(int(center), int(center)), int(radius), int(radius))

    # 主体 + 配饰
    body_rect = QRectF(SIZE * 0.14, SIZE * 0.14, SIZE * 0.72, SIZE * 0.72)
    renderer = QSvgRenderer(QByteArray(profile["speciesSvg"].encode("utf-8")))
    if renderer.isValid():
        renderer.render(painter, body_rect)
    if profile.get("accessorySvg"):
        acc = QSvgRenderer(QByteArray(profile["accessorySvg"].encode("utf-8")))
        if acc.isValid():
            acc.render(painter, body_rect)

    painter.end()

    out = os.path.join(BASE_DIR, "MeloPet-icon.png")
    if pixmap.save(out):
        print(f"✅ 图标已生成：{out}")
        return 0
    print("❌ 图标保存失败", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

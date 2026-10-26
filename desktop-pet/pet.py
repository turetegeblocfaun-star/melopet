#!/usr/bin/env python3
"""MeloPet 桌面宠物（macOS 透明置顶窗口）——由歌单流程固定生成。

与旧版不同，本版本不再是硬编码的「静默小伴」演示素材，而是读取
`pet-profile.json`（由 build-pet-profile.py 从后端全链路结果生成），
把已选音灵候选的物种、配色、配饰、动作、名字、台词、情绪画像固定下来，
用矢量 SVG 确定性渲染：

- 透明无边框、始终置顶、不抢焦点
- 左键拖拽移动 / 单击轮换待机、聆听、跳舞、睡觉并弹出台词
- 网页/命令文件状态推送 + 12 秒自动轮播（可关闭）
- 右键与托盘菜单：动作、置顶、低动态、位置重置、登录自启动、隐藏、退出
- 按画像 motionId 播放固定待机动画（活泼=弹跳 / 灵动=漂浮 / 安静=呼吸）
- 位置持久化保存

所有渲染都来自同一只音灵（物种 + 主/辅色 + 配饰），实现「一种固定的」音灵。
"""

import json
import math
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QSystemTrayIcon, QWidget

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_FILE = os.path.join(BASE_DIR, "pet-profile.json")
STATE_FILE = os.path.join(BASE_DIR, "pet-state.json")
LOCK_FILE = os.path.join(BASE_DIR, "pet.lock")
COMMAND_FILE = os.path.join(BASE_DIR, "pet-command.json")
STARTUP_LABEL = "com.melopet.pet"
STARTUP_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{STARTUP_LABEL}.plist"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# 目标显示高度（逻辑像素）——比旧版 240 更小。
TARGET_HEIGHT = 160
FRAME_PAD = 30  # 帧四周留白，容纳动画位移与光晕
AUTO_CYCLE_MS = 12_000
COMMAND_POLL_MS = 250

ACTIONS = ["idle", "listen", "dance", "sleep"]
ACTION_LABELS = {"idle": "待机", "listen": "聆听", "dance": "跳舞", "sleep": "睡觉"}
ACTION_LINES = {
    "idle": "我就在这里，安静陪着你。",
    "listen": "听见了，我们一起跟着旋律点头。",
    "dance": "这一拍很适合动起来！",
    "sleep": "先眯一会儿，好音乐会留在梦里。",
}

FALLBACK_PROFILE = {
    "name": "静默小伴",
    "intro": "这份歌单以 calm_focus 氛围为主，生成了这只开发态音灵。",
    "clickLines": ["一起听见今天的节奏。", "歌单的光正在闪动。", "需要时，我会安静待着。"],
    "speciesName": "兔子",
    "motionId": "motion_breathe_dev",
    "motionName": "安静",
    "bodyColor": "#64748b",
    "accentColor": "#cbd5e1",
    "primaryMoodName": "平静/专注",
    "primaryMoodPercent": 100,
    "energy": 24,
    "speciesSvg": "",
    "accessorySvg": "",
    "headphoneSvg": "",
}


def startup_enabled() -> bool:
    """返回用户是否明确开启了登录自启动。"""
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
                command, _ = winreg.QueryValueEx(key, "MeloPet")
            return os.path.abspath(__file__) in str(command)
        except OSError:
            return False
    if sys.platform == "darwin":
        return STARTUP_PLIST.is_file()
    return False


def set_startup_enabled(enabled: bool) -> None:
    """配置 macOS LaunchAgent 或 Windows HKCU Run；默认不开启。"""
    if sys.platform == "win32":
        import winreg

        if enabled:
            python = Path(sys.executable)
            pythonw = python.with_name("pythonw.exe")
            if python.name.lower() == "python.exe" and pythonw.is_file():
                python = pythonw
            command = subprocess.list2cmdline([str(python), os.path.abspath(__file__)])
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
                winreg.SetValueEx(key, "MeloPet", 0, winreg.REG_SZ, command)
        else:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    WINDOWS_RUN_KEY,
                    0,
                    winreg.KEY_SET_VALUE,
                ) as key:
                    winreg.DeleteValue(key, "MeloPet")
            except FileNotFoundError:
                pass
        return
    if sys.platform != "darwin":
        raise OSError("当前系统暂不支持登录自启动")
    if not enabled:
        STARTUP_PLIST.unlink(missing_ok=True)
        return
    STARTUP_PLIST.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": STARTUP_LABEL,
        "ProgramArguments": [sys.executable, os.path.abspath(__file__)],
        "WorkingDirectory": BASE_DIR,
        "RunAtLoad": True,
        "ProcessType": "Interactive",
        "StandardOutPath": os.path.join(BASE_DIR, "pet.log"),
        "StandardErrorPath": os.path.join(BASE_DIR, "pet.log"),
    }
    temporary = STARTUP_PLIST.with_suffix(".tmp")
    with temporary.open("wb") as handle:
        plistlib.dump(payload, handle)
    os.replace(temporary, STARTUP_PLIST)


class Bubble(QWidget):
    """悬浮气泡台词窗口。"""

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # macOS 默认会在应用失去焦点时隐藏 Qt.Tool/NSPanel。桌宠气泡应与
        # 桌宠一样跨应用保持可见，只按自身的 4.2 秒计时器主动收起。
        if sys.platform == "darwin":
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setStyleSheet(
            "background-color: rgba(255,255,255,240);"
            "color: #333333;"
            "font-size: 14px;"
            "padding: 10px 14px;"
            "border: 1px solid rgba(0,0,0,24);"
            "border-radius: 10px;"
        )
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def show_text(self, text: str, near_rect: QRect):
        self.label.setText(text)
        self.label.adjustSize()
        self.adjustSize()
        x = near_rect.center().x() - self.width() // 2
        y = near_rect.top() - self.height() - 12
        target_screen = QApplication.screenAt(near_rect.center()) or QApplication.primaryScreen()
        screen = target_screen.availableGeometry()
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        y = max(screen.top() + 8, y)
        self.move(x, y)
        self.show()
        self.raise_()
        self.hide_timer.start(4200)


class PetWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # Qt.Tool 在 macOS 上映射为 NSPanel，默认随应用失焦而隐藏。
        # 开启此属性后，浏览器缩小、最小化或切到其他应用时桌宠仍驻留；
        # 只有菜单、托盘或本地 visible=false 指令会调用 hide()。
        if sys.platform == "darwin":
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setMouseTracking(True)

        self.profile = self._load_profile()
        self.name = self.profile["name"]
        self.motion_id = self.profile["motionId"]
        self.body_color = QColor(self.profile["bodyColor"])
        self.accent_color = QColor(self.profile["accentColor"])

        self.species_svg = self.profile.get("speciesSvg") or ""
        self.accessory_svg = self.profile.get("accessorySvg") or ""
        self.headphone_svg = self.profile.get("headphoneSvg") or ""

        self.current_action = "idle"
        self._visibility_requested = True
        self.auto_cycle_enabled = True
        self.reduced_motion = False
        self.always_on_top = True
        self._last_command_mtime_ns = 0
        self._tray_action_items: dict[str, QAction] = {}
        self.tray: QSystemTrayIcon | None = None
        self.image_label = QLabel(self)

        frame = TARGET_HEIGHT + FRAME_PAD * 2
        self.setFixedSize(frame, frame)
        self.image_label.setGeometry(0, 0, frame, frame)

        self._t = 0.0
        self._reaction_t = 0.0
        self._drag_offset = QPoint()
        self._press_global = QPoint()
        self._dragging = False
        self.bubble = Bubble()

        self._load_state()
        self._apply_window_flags(show_after=False)

        # 30 FPS 逐帧动画；低动态模式降为 10 FPS 并移除大幅位移。
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._tick)
        self.anim_timer.start(100 if self.reduced_motion else 33)
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self._auto_advance)
        self.auto_timer.start(AUTO_CYCLE_MS)
        self.command_timer = QTimer(self)
        self.command_timer.timeout.connect(self._poll_command)
        self.command_timer.start(COMMAND_POLL_MS)
        self.screen_timer = QTimer(self)
        self.screen_timer.timeout.connect(self._clamp_to_visible_screen)
        self.screen_timer.start(3000)

        self._render_frame()
        self._setup_tray()
        self.show()
        self.raise_()
        self._refresh_control_state()
        self.setToolTip(
            f"{self.name} · {self.profile.get('speciesName', '')} · "
            f"{self.profile.get('motionName', '')} · "
            f"{self.profile.get('primaryMoodName', '')} {self.profile.get('primaryMoodPercent', 0)}%"
        )

    # ---- 配置加载 ----
    @staticmethod
    def _load_profile() -> dict:
        profile = dict(FALLBACK_PROFILE)
        if os.path.exists(PROFILE_FILE):
            try:
                with open(PROFILE_FILE, encoding="utf-8") as handle:
                    loaded = json.load(handle)
                for key, value in loaded.items():
                    if value not in (None, ""):
                        profile[key] = value
            except Exception as exc:  # noqa: BLE001
                print(f"读取 pet-profile.json 失败，使用回退配置：{exc}", file=sys.stderr)
        return profile

    # ---- 位置持久化 ----
    def _load_state(self):
        screen = QApplication.primaryScreen().availableGeometry()
        default = QPoint(
            screen.right() - self.width() - 40,
            screen.bottom() - self.height() - 40,
        )
        pos = default
        data = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, encoding="utf-8") as handle:
                    data = json.load(handle)
                pos = QPoint(data.get("x", default.x()), data.get("y", default.y()))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                pos = default
                data = {}
        action = data.get("currentAction")
        if action in ACTIONS:
            self.current_action = action
        self.auto_cycle_enabled = bool(data.get("autoCycle", True))
        self.reduced_motion = bool(data.get("reducedMotion", False))
        self.always_on_top = bool(data.get("alwaysOnTop", True))
        pos = QPoint(
            max(screen.left(), min(pos.x(), screen.right() - self.width())),
            max(screen.top(), min(pos.y(), screen.bottom() - self.height())),
        )
        self.move(pos)

    def _save_state(self):
        try:
            payload = {
                "schemaVersion": 2,
                "x": self.x(),
                "y": self.y(),
                "currentAction": self.current_action,
                "autoCycle": self.auto_cycle_enabled,
                "reducedMotion": self.reduced_motion,
                "alwaysOnTop": self.always_on_top,
            }
            temporary = f"{STATE_FILE}.tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(temporary, STATE_FILE)
        except (OSError, TypeError, ValueError) as exc:
            print(f"保存桌宠状态失败：{exc}", file=sys.stderr)

    # ---- 动画参数（按动作 + 画像 motion 确定） ----
    def _frame_params(self, t: float):
        """返回 (dy, angle_deg, scale, dim_opacity, show_zzz, show_headphone)。"""
        if self.reduced_motion:
            return 0.0, 0.0, 1.0, 0.28 if self.current_action == "sleep" else 0.0, self.current_action == "sleep", self.current_action == "listen"
        if self.current_action == "listen":
            return math.sin(t * 2.2) * 4.0, math.sin(t * 2.2) * 7.0, 1.0, 0.0, False, True
        if self.current_action == "dance":
            return (
                -abs(math.sin(t * 3.2)) * (TARGET_HEIGHT * 0.10),
                math.sin(t * 3.2) * 11.0,
                1.0 + 0.035 * abs(math.sin(t * 3.2)),
                0.0,
                False,
                False,
            )
        if self.current_action == "sleep":
            return math.sin(t * 0.8) * 2.0, 0.0, 1.0 + 0.04 * math.sin(t * 0.8), 0.28, True, False
        # idle：按画像 motionId 固定
        if self.motion_id == "motion_bounce_dev":
            return -abs(math.sin(t * 3.0)) * (TARGET_HEIGHT * 0.12), math.sin(t * 1.5) * 3.0, 1.0, 0.0, False, False
        if self.motion_id == "motion_float_dev":
            return math.sin(t * 1.8) * (TARGET_HEIGHT * 0.07), math.sin(t * 1.1) * 3.5, 1.0, 0.0, False, False
        # motion_breathe_dev（默认）
        return (
            math.sin(t * 1.7) * (TARGET_HEIGHT * 0.035),
            math.sin(t * 0.9) * 2.5,
            1.0 + 0.045 * math.sin(t * 1.7),
            0.0,
            False,
            False,
        )

    def _tick(self):
        elapsed = 0.1 if self.reduced_motion else 0.033
        self._t += elapsed
        if self._reaction_t > 0.0:
            self._reaction_t = max(0.0, self._reaction_t - elapsed)
        if self.isVisible():
            self._render_frame()

    def _render_frame(self):
        dpr = self.devicePixelRatioF()
        frame = self.width()
        pixmap = QPixmap(int(frame * dpr), int(frame * dpr))
        pixmap.fill(Qt.transparent)
        pixmap.setDevicePixelRatio(dpr)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        center_x = frame / 2.0
        center_y = frame / 2.0
        dy, angle, scale, dim, show_zzz, show_headphone = self._frame_params(self._t)
        if self._reaction_t > 0.0:
            scale *= 1.0 + 0.14 * math.sin(self._reaction_t * 24.0)

        # 光晕（aura）
        aura = QRadialGradient(center_x, center_y, TARGET_HEIGHT * 0.62)
        color = QColor(self.accent_color)
        color.setAlpha(70)
        aura.setColorAt(0.0, color)
        color.setAlpha(0)
        aura.setColorAt(1.0, color)
        painter.setBrush(aura)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(
            QPoint(int(center_x), int(center_y)),
            int(TARGET_HEIGHT * 0.62),
            int(TARGET_HEIGHT * 0.62),
        )

        # 持续旋转的环境光点让待机状态也有清晰可见的逐帧变化。
        # 低动态模式由用户主动开启，此时只保留静态主体。
        if not self.reduced_motion:
            painter.save()
            painter.setPen(Qt.NoPen)
            orbit_speed = {
                "idle": 0.75,
                "listen": 1.35,
                "dance": 2.8,
                "sleep": 0.32,
            }[self.current_action]
            for index in range(9):
                orbit = self._t * orbit_speed + index * math.tau / 9
                dot = QColor(self.accent_color if index % 2 else "#ffffff")
                dot.setAlpha(95 + int(110 * (0.5 + 0.5 * math.sin(orbit * 1.7))))
                painter.setBrush(dot)
                radius = TARGET_HEIGHT * (0.55 + 0.035 * math.sin(orbit * 2.1))
                dot_size = 2 + (index % 3)
                painter.drawEllipse(
                    QPoint(
                        int(center_x + math.cos(orbit) * radius),
                        int(center_y + math.sin(orbit) * radius),
                    ),
                    dot_size,
                    dot_size,
                )
            painter.restore()

        # 主体与配饰（同一坐标系内变换，保证配饰跟随身体）
        painter.save()
        painter.translate(center_x, center_y + dy)
        painter.scale(scale, scale)
        painter.rotate(angle)
        painter.translate(-TARGET_HEIGHT / 2.0, -TARGET_HEIGHT / 2.0)
        body_rect = QRectF(0, 0, TARGET_HEIGHT, TARGET_HEIGHT)
        if self.species_svg:
            self._render_svg(painter, self.species_svg, body_rect)
        if self.accessory_svg:
            self._render_svg(painter, self.accessory_svg, body_rect)
        if show_headphone and self.headphone_svg:
            self._render_svg(painter, self.headphone_svg, body_rect)
        painter.restore()

        # 声明式状态的轻量逐帧特效，不依赖额外动图素材。
        if not self.reduced_motion and self.current_action == "listen":
            painter.save()
            note_font = QFont()
            note_font.setPixelSize(22)
            note_font.setBold(True)
            painter.setFont(note_font)
            painter.setPen(self.accent_color)
            phase = (math.sin(self._t * 2.0) + 1.0) * 6.0
            painter.drawText(QPoint(int(center_x + 55), int(center_y - 45 - phase)), "♪")
            painter.drawText(QPoint(int(center_x - 70), int(center_y - 25 + phase)), "♫")
            painter.restore()
        elif not self.reduced_motion and self.current_action == "dance":
            painter.save()
            painter.setPen(Qt.NoPen)
            for index in range(6):
                orbit = self._t * 2.8 + index * math.tau / 6
                sparkle = QColor(self.accent_color if index % 2 else self.body_color)
                sparkle.setAlpha(130 + int(90 * abs(math.sin(orbit))))
                painter.setBrush(sparkle)
                radius = TARGET_HEIGHT * (0.48 + 0.04 * math.sin(orbit * 1.7))
                painter.drawEllipse(
                    QPoint(int(center_x + math.cos(orbit) * radius), int(center_y + math.sin(orbit) * radius)),
                    3,
                    3,
                )
            painter.restore()
        if self._reaction_t > 0.0:
            painter.save()
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.accent_color)
            for index in range(8):
                angle_rad = index * math.tau / 8
                distance = TARGET_HEIGHT * (0.48 + (0.45 - self._reaction_t) * 0.5)
                painter.drawEllipse(
                    QPoint(
                        int(center_x + math.cos(angle_rad) * distance),
                        int(center_y + math.sin(angle_rad) * distance),
                    ),
                    3,
                    3,
                )
            painter.restore()

        # 睡眠压暗 + Zzz
        if dim > 0:
            overlay = QColor(18, 20, 40, int(255 * dim))
            painter.fillRect(0, 0, frame, frame, overlay)
        if show_zzz:
            painter.save()
            font = QFont()
            font.setPixelSize(int(TARGET_HEIGHT * 0.20))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#8b9bb4"))
            zx = center_x + TARGET_HEIGHT * 0.30
            for index, glyph in enumerate(("z", "Z", "Z")):
                painter.drawText(
                    QPoint(int(zx + index * TARGET_HEIGHT * 0.10),
                           int(FRAME_PAD + index * TARGET_HEIGHT * 0.12)),
                    glyph,
                )
            painter.restore()

        painter.end()
        self.image_label.setPixmap(pixmap)

    @staticmethod
    def _render_svg(painter: QPainter, svg: str, rect: QRectF):
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        if renderer.isValid():
            renderer.render(painter, rect)

    # ---- 动作切换 ----
    def set_action(self, action: str, say: bool = False):
        if action not in ACTIONS:
            return
        self.current_action = action
        self._t = 0.0
        self._reaction_t = 0.32
        self.auto_timer.start(AUTO_CYCLE_MS)
        self._render_frame()
        self._save_state()
        self._refresh_control_state()
        if say:
            self.say(ACTION_LINES[action])

    def _auto_advance(self):
        if not self.auto_cycle_enabled or self._dragging or not self.isVisible():
            return
        current = ACTIONS.index(self.current_action)
        self.set_action(ACTIONS[(current + 1) % len(ACTIONS)], say=False)

    def _poll_command(self):
        try:
            modified = os.stat(COMMAND_FILE).st_mtime_ns
            if modified <= self._last_command_mtime_ns:
                return
            with open(COMMAND_FILE, encoding="utf-8") as handle:
                command = json.load(handle)
            self._last_command_mtime_ns = modified
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        auto_cycle = command.get("autoCycle")
        if isinstance(auto_cycle, bool):
            self.set_auto_cycle(auto_cycle)
        visible = command.get("visible")
        if isinstance(visible, bool):
            self.set_pet_visible(visible)
        action = command.get("action")
        if isinstance(action, str):
            self.set_action(action, say=command.get("say") is True)

    def say(self, text: str):
        self.bubble.show_text(text, self.frameGeometry())

    def greet(self):
        mood = f"{self.profile.get('primaryMoodName', '')} {self.profile.get('primaryMoodPercent', 0)}%"
        self.say(f"你好，我是{self.name} 🎧（{mood}）")

    # ---- 鼠标交互 ----
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = self._press_global - self.frameGeometry().topLeft()
            self._dragging = False
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            g = event.globalPosition().toPoint()
            if (g - self._press_global).manhattanLength() > 6:
                self._dragging = True
            if self._dragging:
                self.move(g - self._drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and not self._dragging:
            current = ACTIONS.index(self.current_action)
            self.set_action(ACTIONS[(current + 1) % len(ACTIONS)], say=True)
        elif event.button() == Qt.LeftButton:
            self._clamp_to_visible_screen()
            self._save_state()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._save_state()
        event.accept()

    # ---- 托盘 / 右键菜单 ----
    def _build_control_menu(self, *, tray_menu: bool = False) -> QMenu:
        menu = QMenu(self)
        visibility = QAction("隐藏桌宠" if self.isVisible() else "显示桌宠", menu)
        visibility.triggered.connect(lambda: self.set_pet_visible(not self.isVisible()))
        menu.addAction(visibility)
        menu.addSeparator()
        for action in ACTIONS:
            item = QAction(ACTION_LABELS[action], menu)
            item.setCheckable(True)
            item.setChecked(action == self.current_action)
            item.triggered.connect(lambda _=False, act=action: self.set_action(act, say=True))
            menu.addAction(item)
            if tray_menu:
                self._tray_action_items[action] = item
        menu.addSeparator()
        auto_item = QAction("自动轮播动作", menu)
        auto_item.setCheckable(True)
        auto_item.setChecked(self.auto_cycle_enabled)
        auto_item.triggered.connect(self.set_auto_cycle)
        menu.addAction(auto_item)
        reduced_item = QAction("低动态模式", menu)
        reduced_item.setCheckable(True)
        reduced_item.setChecked(self.reduced_motion)
        reduced_item.triggered.connect(self.set_reduced_motion)
        menu.addAction(reduced_item)
        top_item = QAction("始终置顶", menu)
        top_item.setCheckable(True)
        top_item.setChecked(self.always_on_top)
        top_item.triggered.connect(self.set_always_on_top)
        menu.addAction(top_item)
        startup_item = QAction("登录时自动启动", menu)
        startup_item.setCheckable(True)
        startup_item.setChecked(startup_enabled())
        startup_item.triggered.connect(self.set_startup)
        menu.addAction(startup_item)
        menu.addSeparator()
        reset_item = QAction("重置到屏幕右下角", menu)
        reset_item.triggered.connect(self.reset_position)
        menu.addAction(reset_item)
        hide_item = QAction("隐藏 10 分钟", menu)
        hide_item.triggered.connect(self.hide_for_a_while)
        menu.addAction(hide_item)
        quit_item = QAction("退出桌宠", menu)
        quit_item.triggered.connect(QApplication.instance().quit)
        menu.addAction(quit_item)
        if tray_menu:
            self._tray_visibility_item = visibility
            self._tray_auto_item = auto_item
            self._tray_reduced_item = reduced_item
            self._tray_top_item = top_item
            self._tray_startup_item = startup_item
        return menu

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self)
        current_pixmap = self.image_label.pixmap()
        self.tray.setIcon(QIcon(current_pixmap) if current_pixmap else QIcon())
        self.tray.setToolTip(f"MeloPet · {self.name}")
        self.tray.setContextMenu(self._build_control_menu(tray_menu=True))
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.set_pet_visible(not self.isVisible())

    def _refresh_control_state(self):
        for action, item in self._tray_action_items.items():
            item.setChecked(action == self.current_action)
        if self.tray is None:
            return
        self._tray_visibility_item.setText("隐藏桌宠" if self.isVisible() else "显示桌宠")
        self._tray_auto_item.setChecked(self.auto_cycle_enabled)
        self._tray_reduced_item.setChecked(self.reduced_motion)
        self._tray_top_item.setChecked(self.always_on_top)
        self._tray_startup_item.setChecked(startup_enabled())

    def contextMenuEvent(self, event):
        menu = self._build_control_menu()
        menu.exec(event.globalPos())

    def set_auto_cycle(self, enabled: bool):
        self.auto_cycle_enabled = bool(enabled)
        self.auto_timer.start(AUTO_CYCLE_MS)
        self._save_state()
        self._refresh_control_state()

    def set_reduced_motion(self, enabled: bool):
        self.reduced_motion = bool(enabled)
        self.anim_timer.start(100 if self.reduced_motion else 33)
        self._save_state()
        self._refresh_control_state()

    def _apply_window_flags(self, *, show_after: bool = True):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.always_on_top)
        if show_after:
            self.show()
            self.raise_()

    def set_always_on_top(self, enabled: bool):
        self.always_on_top = bool(enabled)
        self._apply_window_flags()
        self._save_state()
        self._refresh_control_state()

    def set_startup(self, enabled: bool):
        try:
            set_startup_enabled(bool(enabled))
            self.say("已开启登录自启动。" if enabled else "已关闭登录自启动。")
        except OSError as exc:
            self.say(f"自启动设置失败：{exc}")
        self._refresh_control_state()

    def set_pet_visible(self, visible: bool):
        self._visibility_requested = bool(visible)
        if visible:
            self._clamp_to_visible_screen()
            self.show()
            self.raise_()
        else:
            self.bubble.hide()
            self.hide()
        self._refresh_control_state()

    def hideEvent(self, event):
        """抵消平台因应用失焦造成的隐藏，同时尊重用户主动隐藏。"""
        super().hideEvent(event)
        if self._visibility_requested and not QApplication.closingDown():
            QTimer.singleShot(0, self._restore_requested_visibility)

    def _restore_requested_visibility(self):
        if self._visibility_requested and not QApplication.closingDown():
            self.show()
            self.raise_()

    def reset_position(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 40, screen.bottom() - self.height() - 40)
        self._save_state()

    def _clamp_to_visible_screen(self):
        if self._dragging:
            return
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        geometry = screen.availableGeometry()
        x = max(geometry.left(), min(self.x(), geometry.right() - self.width()))
        y = max(geometry.top(), min(self.y(), geometry.bottom() - self.height()))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)
            self._save_state()

    def hide_for_a_while(self):
        self.set_pet_visible(False)
        QTimer.singleShot(10 * 60 * 1000, lambda: self.set_pet_visible(True))

    def closeEvent(self, event):
        self._visibility_requested = False
        self._save_state()
        self.bubble.close()
        if self.tray is not None:
            self.tray.hide()
        event.accept()


def _windows_pid_is_running(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        return _windows_pid_is_running(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        check=False,
        text=True,
    )
    status = result.stdout.strip()
    return result.returncode == 0 and bool(status) and not status.startswith("Z")


def _acquire_lock() -> bool:
    """单实例锁：若已有桌宠进程存活则返回 False，避免重复启动。"""
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, encoding="utf-8") as handle:
                pid = int(handle.read().strip())
            if _pid_is_running(pid):
                return False
        except (ValueError, OSError):
            pass
    try:
        with open(LOCK_FILE, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except OSError as exc:
        print(f"写入桌宠单实例锁失败：{exc}", file=sys.stderr)
    return True


def main():
    if not _acquire_lock():
        print("桌宠已在运行。", file=sys.stderr)
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName("MeloPet 桌宠")
    pet = PetWindow()
    QTimer.singleShot(400, pet.greet)
    code = app.exec()
    try:
        if os.path.exists(LOCK_FILE):
            os.unlink(LOCK_FILE)
    except OSError as exc:
        print(f"清理桌宠单实例锁失败：{exc}", file=sys.stderr)
    sys.exit(code)


if __name__ == "__main__":
    main()

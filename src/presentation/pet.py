"""WhaleGirl artwork and interactions, with neutral tool reactions."""
import ctypes
import json
import os
import random
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMenu,
    QSystemTrayIcon,
    QWidget,
    QWidgetAction,
)

from character import PetReaction, default_character
from character.defaults import PNG_ANIMATIONS
from character.dialogue import (
    IDLE_ANIMATION_SPEECH,
    AmbientDialogue,
    dialogue_groups,
    fixed_dialogue_groups,
    fixed_line,
)
from character.motion import GAITS, TouchProgression, inward_facing
from companion.paths import ensure_state_dir
from companion.preferences import InteractionPreferences

from .keyposes import DragPosePlayer, FacingFrames, TouchPosePlayer
from .panels import ChatWindow, StatusWindow
from .renderers import create_renderer
from .speech import SpeechBubble, head_anchor
from .transitions import FrameTransition

# ---- 控制台输出编码加固：Windows GBK 控制台打印 “¥/中文” 不再崩溃 ----
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream is not None and hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 —— pythonw/打包后可能没有控制台
        pass

# ================================================================================
# 一、路径 / 素材 / 台词 / 固定文案 常量
# ================================================================================


def app_base_dir() -> str:
    """运行基准目录：源码=main.py 所在目录；PyInstaller 冻结=exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def asset_library_dir() -> str:
    """
    定位 asset_library 素材目录：
      - 源码运行：main.py 同级的 asset_library；
      - PyInstaller 打包：优先 exe 同目录的 asset_library（用户可直接替换图片，
        重启即生效），找不到时回退到打进 exe 的解包目录 _MEIPASS（自包含兜底）。
    依次探测，取第一个真实存在的目录，保证打包/源码两用都不崩溃。
    """
    candidates: list = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable),
                                       "asset_library"))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "asset_library"))
    else:
        candidates.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                       "asset_library"))
    for cand in candidates:
        if os.path.isdir(cand):
            return cand
    return candidates[0]


BASE_DIR: str = app_base_dir()
ASSET_ROOT: str = asset_library_dir()          # 素材根目录

# ---- 图片素材相对路径（相对 asset_library）----
IMAGE_RELS = PNG_ANIMATIONS

# ---- 右键菜单（桌宠本体与托盘保持一致、严格按此顺序；含一条分隔线与红色【退出】）----
# ---- 窗口位置记忆 config.json（用户数据目录，运行时生成、不打进包）----
CONFIG_REL: str = "config.json"
CONFIG_PATH: str = str(ensure_state_dir() / CONFIG_REL)

# ---- 登场（cheerup_show）固定气泡台词 ----
ENTRANCE_SPEECH: str = dialogue_groups()["bound.entrance"][0]

# ---- 本地资料（用户数据目录 RAG 文件夹）----
RAG_DIR: str = str(ensure_state_dir() / "RAG")
SUPPORTED_EXT: tuple = (".txt", ".docx")     # 仅支持 txt/docx，不支持旧版 .doc
RAG_MAX_TOTAL: int = 20000                   # 全部文档合并总文本上限（字）
RAG_MAX_DOC: int = 8000                      # 单个文档文本上限（字）
DOCUMENT_MAX_BYTES: int = 2 * 1024 * 1024
DOCX_XML_MAX_BYTES: int = 8 * 1024 * 1024

# ---- 台词文本相对路径 ----
TALK_TOUCH_REL: str = os.path.join("talk", "touch.txt")
TALK_NOTHING_REL: str = os.path.join("talk", "nothing.txt")

# ---- idle 待机状态机（固定顺序轮转，总周期 5 分钟）----
IDLE_ORDER: list = ["nothing", "eat", "think"]
IDLE_MS: dict = {"nothing": 240_000, "eat": 30_000, "think": 30_000}

# ---- 时间常量（毫秒）----
BUBBLE_MS: int = 3000            # 气泡固定存在 3 秒
TOUCH_MS: int = 3000             # touch 交互 3 秒倒计时
IDLE_TALK_MS: int = 45_000       # 独立于动作，每 45s 尝试一句日常台词
DRAG_THRESHOLD_PX: int = 10      # 判定为“拖拽”的最小移动距离（像素）
FILE_IMPORT_MS: int = 3000       # file_import 反馈状态持续 3 秒
SHAKE_MS: int = 300              # 导入失败窗口抖动总时长 0.3 秒
SHAKE_STEP_MS: int = 30          # 抖动单步时长（毫秒）
VIEW_POLL_MS: int = 700          # 监听 RAG 文件夹窗口关闭的轮询间隔
VIEW_OPEN_TRIES: int = 20        # 等待资源管理器窗口出现的最大轮询次数(≈14s)
CHEERUP_MS: int = 4200           # 完整庆祝后留出恢复姿态

# ---- 固定台词从打包台词本读取 ----
PICKUP_SPEECH: str = dialogue_groups()["bound.pickup"][0]

# ---- RAG / 文件导入相关固定文案 ----
FILE_OK_SPEECH: str = dialogue_groups()["bound.file_ok"][0]
FILE_FAIL_SPEECH: str = dialogue_groups()["bound.file_failed"][0]
VIEW_COUNT_SPEECH = fixed_dialogue_groups()["template.files.count"][0]
VIEW_OPEN_FAIL_SPEECH: str = fixed_line("files.open_failed")
RAG_LABEL_TEXT = "已加载RAG文本：{loaded} / {max}字"

# ---- 兜底台词（txt 缺失 / 为空时使用，保证程序不崩溃）----
TOUCH_FALLBACK: list = list(fixed_dialogue_groups()["legacy.touch"])
NOTHING_FALLBACK: list = list(fixed_dialogue_groups()["legacy.idle"])

_QUOTE_PAIRS = (("“", "”"), ('"', '"'), ("‘", "’"))


def extract_line_text(raw: str):
    """从一行中取出引号内台词（兼容中文全角引号“ ”与英文引号）。"""
    s = raw.strip()
    if not s:
        return None
    for open_q, close_q in _QUOTE_PAIRS:
        if len(s) >= 2 and s.startswith(open_q) and s.endswith(close_q):
            inner = s[len(open_q):-len(close_q)].strip()
            return inner if inner else None
    m = re.search(r"[“‘\"]([^”’\"]+)[”’\"]", s)
    if m:
        inner = m.group(1).strip()
        return inner if inner else None
    return s


def load_talk_pool(rel_path: str, fallback: list) -> list:
    """
    读取台词池：txt 每行一条“台词文本”，忽略空行；返回引号内文本列表。
    文件不存在 / 为空 / 读取异常 → 返回内置兜底台词，程序不崩溃。
    """
    path = os.path.join(ASSET_ROOT, rel_path)
    pool: list = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                for raw in fh:
                    text = extract_line_text(raw)
                    if text:
                        pool.append(text)
        except OSError as exc:
            print(f"[鲸鱼娘] 【警告】台词文件读取异常，改用内置兜底台词：{exc}")
            pool = []
    else:
        print(f"[鲸鱼娘] 【警告】台词文件不存在，改用内置兜底台词：{path}")
    if not pool:
        pool = list(fallback)
    return pool


# ================================================================================
# 二·五、RAG 知识库工具（目录 / 文档提取 / 上限截断 / 资源管理器窗口检测）
# ================================================================================

def ensure_rag_dir() -> str:
    """RAG 文件夹不存在则自动创建（程序启动即调用，幂等）。"""
    try:
        os.makedirs(RAG_DIR, exist_ok=True)
    except OSError as exc:
        print(f"[鲸鱼娘] 【警告】创建 RAG 文件夹失败：{exc}")
    return RAG_DIR


def is_supported_document(path: str) -> bool:
    """仅支持 .txt / .docx，不支持旧版 .doc 二进制文件。"""
    return os.path.splitext(path)[1].lower() in SUPPORTED_EXT


def count_rag_documents() -> int:
    """实时统计 RAG 文件夹内 .txt + .docx 文件总数（异常返回 0，不崩溃）。"""
    try:
        if not os.path.isdir(RAG_DIR):
            return 0
        return sum(1 for name in os.listdir(RAG_DIR)
                   if os.path.isfile(os.path.join(RAG_DIR, name))
                   and is_supported_document(name))
    except OSError as exc:
        print(f"[鲸鱼娘] 【警告】统计 RAG 文档数失败：{exc}")
        return 0


def _read_txt_text(path: str) -> str:
    """读取 .txt 纯文本（utf-8 → gb18030 兜底，尽量不乱码）。"""
    with open(path, "rb") as fh:
        raw = fh.read(DOCUMENT_MAX_BYTES + 1)
    if len(raw) > DOCUMENT_MAX_BYTES:
        raise ValueError("文本文件超过 2 MiB")
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx_text(path: str) -> str:
    """提取 .docx 纯文本（zip + 解析 word/document.xml；损坏/异常抛出由调用方容错）。"""
    with zipfile.ZipFile(path) as zf:
        if "word/document.xml" not in zf.namelist():
            raise ValueError("docx 缺少 word/document.xml")
        if zf.getinfo("word/document.xml").file_size > DOCX_XML_MAX_BYTES:
            raise ValueError("docx 解压文本超过 8 MiB")
        data = zf.read("word/document.xml")
    root = ET.fromstring(data)
    lines = []
    for para in root.iter(_W_NS + "p"):
        text = "".join(t.text or "" for t in para.iter(_W_NS + "t")).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def extract_document_text(path: str) -> str:
    """按后缀提取纯文本：txt / docx；其余类型抛 ValueError。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt":
        return _read_txt_text(path)
    if ext == ".docx":
        return extract_docx_text(path)
    raise ValueError(f"不支持的文档格式：{path}")


def load_rag_text() -> dict:
    """
    读取 RAG 文件夹全部 .txt/.docx 的纯文本并做开销保护：
      - 单文档 > RAG_MAX_DOC(8000) 自动截断；
      - 合并总文本 > RAG_MAX_TOTAL(20000) 截断并置 combined_truncated。
    损坏文档自动跳过并控制台警告，绝不崩溃。
    返回 dict：text / loaded / combined_truncated / doc_truncated / skipped。
    """
    # 逐篇累积到总上限，而不是先把所有文档拼成一个大字符串再截断；这样资料
    # 较多时只保留本地资料预览需要的有限文本。
    docs: list = []
    skipped = 0
    doc_truncated = False
    combined_truncated = False
    remaining = RAG_MAX_TOTAL
    if os.path.isdir(RAG_DIR):
        try:
            names = sorted(os.listdir(RAG_DIR))
        except OSError as exc:
            print(f"[鲸鱼娘] 【警告】读取 RAG 目录失败：{exc}")
            names = []
        for name in names:
            path = os.path.join(RAG_DIR, name)
            if not (os.path.isfile(path) and is_supported_document(name)):
                continue
            try:
                text = extract_document_text(path).strip()
            except Exception as exc:  # noqa: BLE001 —— 损坏文档自动跳过
                print(f"[鲸鱼娘] 【警告】文档损坏已跳过：{path}（{type(exc).__name__}）")
                skipped += 1
                continue
            if not text:
                continue
            if len(text) > RAG_MAX_DOC:          # 单文档上限 8000
                text = text[:RAG_MAX_DOC]
                doc_truncated = True
            separator_len = 2 if docs else 0
            text_capacity = remaining - separator_len
            if text_capacity <= 0:
                combined_truncated = True
                break
            if len(text) > text_capacity:
                text = text[:text_capacity]
                combined_truncated = True
            docs.append(text)
            remaining -= separator_len + len(text)
            if remaining <= 0:
                break
    merged = "\n\n".join(docs)
    return {
        "text": merged,
        "loaded": len(merged),
        "combined_truncated": combined_truncated,
        "doc_truncated": doc_truncated,
        "skipped": skipped,
    }


# ---- 资源管理器窗口检测（Windows）----

def _enumerate_window_titles() -> list:
    """枚举所有可见顶层窗口标题（仅 Windows；异常返回空表，不崩溃）。"""
    titles: list = []
    try:
        user32 = ctypes.windll.user32
    except Exception:  # noqa: BLE001 —— 非 Windows 平台
        return titles
    try:
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool,
                                       ctypes.c_void_p, ctypes.c_void_p)

        def _cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            text = buf.value.strip()
            if text:
                titles.append(text)
            return True

        user32.EnumWindows(proc_type(_cb), 0)
    except Exception as exc:  # noqa: BLE001
        print(f"[鲸鱼娘] 【警告】枚举窗口标题失败：{exc}")
    return titles


def find_folder_window(path: str) -> bool:
    """按标题查找资源管理器窗口（标题=文件夹名或完整路径）；找不到返回 False。"""
    try:
        norm = os.path.normpath(path).lower()
        base = os.path.basename(norm)
        for title in _enumerate_window_titles():
            low = title.lower()
            if low == base or low == norm:
                return True
            if low.startswith(base + " - "):      # 形如 “RAG - 文件资源管理器”
                return True
    except Exception as exc:  # noqa: BLE001
        print(f"[鲸鱼娘] 【警告】查找文件夹窗口失败：{exc}")
    return False


def open_folder_in_explorer(path: str) -> bool:
    """调用系统资源管理器打开 RAG 文件夹窗口；失败返回 False（不抛异常）。"""
    try:
        if sys.platform == "win32":
            os.startfile(path)                    # noqa: S606 —— Windows 原生
            return True
        return QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    except Exception as exc:  # noqa: BLE001
        print(f"[鲸鱼娘] 【警告】打开资源管理器失败：{exc}")
        return False


# ================================================================================
# 二·七、config.json（窗口位置记忆）与红色【退出】菜单项工具
# ================================================================================

def load_window_position():
    """
    读取 config.json（{window_x, window_y}，int）。
    文件不存在 / 损坏 / 字段缺失 / 类型错误 / 权限不足 → 返回 None（使用默认右下角）。
    """
    try:
        if not os.path.exists(CONFIG_PATH):
            return None
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        x = int(data["window_x"])
        y = int(data["window_y"])
        return x, y
    except Exception as exc:  # noqa: BLE001 —— 损坏/缺失/权限异常一律回退默认位置
        print(f"[鲸鱼娘] 【警告】config.json 读取失败，使用默认右下角位置（{exc}）")
        return None


def save_window_position(x: int, y: int) -> bool:
    """把窗口坐标写入 config.json（覆盖式，运行时生成；异常捕获不崩溃）。"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump({"window_x": int(x), "window_y": int(y)}, fh,
                      ensure_ascii=False, indent=2)
        print(f"[鲸鱼娘] 【位置保存】config.json <- ({int(x)}, {int(y)})")
        return True
    except Exception as exc:  # noqa: BLE001 —— 权限不足等只警告
        print(f"[鲸鱼娘] 【警告】config.json 写入失败：{exc}")
        return False


def clamp_position_to_screen(x: int, y: int, w: int, h: int):
    """
    坐标有效性兜底：把 (x,y) 钳制到主屏可用区域内（至少保留 60px 可见），
    防止 config.json 里的旧坐标把桌宠带到屏幕外再也找不到。
    """
    screen = QApplication.primaryScreen()
    if screen is None:
        return int(x), int(y)
    geo = screen.availableGeometry()
    nx = max(geo.left() - w + 60, min(int(x), geo.right() - 60))
    ny = max(geo.top(), min(int(y), geo.bottom() - 60))
    return nx, ny


class _RedQuitLabel(QLabel):
    """红色【退出】菜单项内容：点击即触发所属 action。"""

    def __init__(self, action, parent=None):
        super().__init__("退出", parent)
        self._action = action
        self.setStyleSheet("color:#e00000; background:transparent;"
                           "padding:5px 26px 5px 24px;")
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._action.trigger()
        super().mouseReleaseEvent(event)


class RedQuitAction(QWidgetAction):
    """红色字体【退出】菜单项：在 QMenu 中由 createWidget 生成红字内容。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText("退出")

    def createWidget(self, parent):  # noqa: N802 —— QMenu 需要
        return _RedQuitLabel(self, parent)


def make_red_quit_action(menu: QMenu, slot):
    """构造红色字体的【退出】菜单项并绑定点击回调。"""
    act = RedQuitAction(menu)
    act.triggered.connect(slot)
    return act


def make_tray_icon() -> QIcon:
    """
    托盘图标：优先使用桌宠自身素材 nothing.png（程序自身图标）；
    素材缺失/损坏时回退到程序化绘制的“鲸”字蓝圆标，保证不崩溃。
    """
    path = os.path.join(ASSET_ROOT, IMAGE_RELS["nothing"])
    pm = QPixmap(path)
    if not pm.isNull():
        return QIcon(pm)

    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(52, 160, 235))
    painter.drawEllipse(2, 2, 60, 60)
    font = QFont("Microsoft YaHei", 30)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(QRectF(0, 2, 64, 60), Qt.AlignCenter, "鲸")
    painter.end()
    return QIcon(pm)


class PetTrayIcon(QSystemTrayIcon):
    """
    Windows 右下角系统托盘（程序启动即注册，常驻后台，出现在隐藏图标列表）：
      - 双击托盘图标 → 显示/还原被隐藏的桌宠主窗口；
      - 仅【退出】执行完整退出流程（aboutToQuit → pet._cleanup 统一清理）。
    """

    def __init__(self, pet: "DesktopPet"):
        super().__init__(pet)
        self._pet = pet
        self.setIcon(make_tray_icon())          # 程序自身图标
        self.setToolTip("Mori Companion · 鲸鱼娘\n右键：聊天 / 工具状态 / 资料 / 隐藏 / 退出")
        # 右键菜单与桌宠右键菜单保持一致（顺序严格一致，含分隔线与红色【退出】）
        # QSystemTrayIcon 非 QWidget，菜单不设其父，仅用 self._menu 强引用保活
        menu = QMenu()
        act_chat = QAction("聊聊天", menu)
        act_status = QAction("工具状态", menu)
        act_skin = QAction("角色与动作", menu)
        act_add = QAction("添加资料文件", menu)
        act_view = QAction("查看已有资料", menu)
        act_reset = QAction("重置位置", menu)
        self._hide_act = QAction("隐藏鲸鱼娘", menu)   # 文本按显示/隐藏状态动态切换
        act_chat.triggered.connect(pet.open_chat_window)
        act_status.triggered.connect(pet.open_status_window)
        act_skin.triggered.connect(pet.open_skin_window)
        act_add.triggered.connect(pet.start_add_documents)
        act_view.triggered.connect(pet.start_view_files)
        act_reset.triggered.connect(pet.reset_position)
        self._hide_act.triggered.connect(pet.toggle_hide_whale)
        for act in (act_chat, act_status, act_skin, act_add, act_view, act_reset):
            menu.addAction(act)
        pet._add_interaction_menu(menu)
        menu.addSeparator()                       # 「重置位置」与「隐藏鲸鱼娘」之间的分隔线
        menu.addAction(self._hide_act)
        menu.addAction(make_red_quit_action(menu, pet.quit_app))   # 红色【退出】
        menu.aboutToShow.connect(self._sync_menu_labels)
        self.setContextMenu(menu)
        self._menu = menu                        # 强引用保活

        self.activated.connect(self._on_activated)
        self.show()

    def _sync_menu_labels(self) -> None:
        """弹出托盘菜单前同步【隐藏/显示鲸鱼娘】文案。"""
        self._hide_act.setText("显示鲸鱼娘" if self._pet.isHidden()
                               else "隐藏鲸鱼娘")

    def _on_activated(self, reason) -> None:  # noqa: N802
        """双击托盘图标：恢复显示桌宠并弹固定气泡“我鲸鱼娘又回来了！”。"""
        if reason == QSystemTrayIcon.DoubleClick:
            pet = self._pet
            if pet.isHidden():
                pet.show_whale(greet=True)
            else:
                pet.raise_()
                pet.activateWindow()


# ================================================================================
# 五、桌宠主窗口（状态机核心）
# ================================================================================

class DesktopPet(QWidget):
    """
    桌宠窗口：无边框 + 背景透明 + 置顶 Tool。
    状态机：idle(nothing/eat/think) / touch / pickup(拖拽) / landing(落地) /
           file_import(资料导入反馈) / view_files(查看资料)。
    优先级：pickup > touch / landing > file_import > view_files > idle；
    高优先级打断/暂停低优先级，结束后回到 idle 并接续之前计时进度
    （不重置 5 分钟周期）。
    """

    def __init__(self, character=None):
        super().__init__()
        self.preferences = InteractionPreferences()
        self.character = character or default_character(ASSET_ROOT)
        self.setWindowTitle("Mori Companion · " + self.character.definition.name)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        # ---------------- 当前画面（GIF 流式解码，不缓存全部动作帧） ----------------
        self._cur_key: str = "nothing"
        self._pixmap: QPixmap | None = None
        self._facing = "right"
        self._interaction_facing = "right"
        self._visible_base_frame = None
        self._visible_base_reversed = False
        self._facing_frames = FacingFrames()
        self._speech_anchor_key = None
        self._speech_anchor_point = QPoint()

        # ---------------- 系统托盘 / 隐藏状态 ----------------
        self._tray = None           # 由 main() 注册的 QSystemTrayIcon（不支持则为 None）
        self._hidden = True         # 首次 showEvent 前不启动待机计时
        self._quitting = False      # 完整退出中：允许窗口真正关闭，避免关闭事件阻断退出

        # ---------------- 状态机 ----------------
        self._state: str = "idle"            # idle / touch / pickup / landing
        self._stage_idx: int = 0             # idle 阶段下标（IDLE_ORDER）
        self._stage_remaining: int = IDLE_MS["nothing"]   # 当前阶段剩余毫秒
        self._idle_paused: bool = False      # 被高优先级状态暂停

        # ---------------- 鼠标交互状态 ----------------
        self._pressing = False
        self._drag_active = False            # 是否已判定为拖拽
        self._press_global = QPoint()
        self._drag_offset = None
        self._suppress_next_release = False  # 双击第二下释放（消费掉）
        self._touch_region = "body"
        self._touch_progression = TouchProgression()
        self._context_menu: QMenu | None = None

        # ---------------- RAG 文档管理 ----------------
        self.rag_text: str = ""                       # 已加载资料纯文本（本地搜索用）
        self.rag_loaded: int = 0                      # 已加载字数（截断后）
        self.rag_combined_truncated: bool = False     # 合并总文本是否超限截断
        self.rag_doc_truncated: bool = False          # 是否存在单文档超长截断
        self._view_open = False                       # view_files：资源管理器窗口已出现
        self._view_poll_count = 0                     # 等待窗口出现的轮询计数
        ensure_rag_dir()                              # 启动即检测/创建 RAG 文件夹
        self.setAcceptDrops(True)                     # 允许外部文件拖拽导入（与 PickUp 互不干扰）

        # ---------------- 弹窗 / 聊天 ----------------
        self._bubble: SpeechBubble | None = None
        self._speech_bubble_cache: SpeechBubble | None = None
        self._chat_window: ChatWindow | None = None
        self._status_window: StatusWindow | None = None
        self._skin_window = None
        self._settings_window = None
        self._tool_reaction = PetReaction()
        self._pending_tool_speech = ""
        self._ambient_dialogue = AmbientDialogue()
        self.tool_summary_text = "尚未收到工具事件。"
        self._history_lines = []

        # ---------------- 计时器（全部由 _cleanup 统一停止） ----------------
        self._stage_timer = QTimer(self)     # idle 阶段倒计时（单发）
        self._stage_timer.setSingleShot(True)
        self._stage_timer.setTimerType(Qt.PreciseTimer)
        self._stage_timer.timeout.connect(self._on_stage_timeout)

        self._dialogue_timer = QTimer(self)   # 独立于待机阶段和工作动作的日常台词
        self._dialogue_timer.setInterval(IDLE_TALK_MS)
        self._dialogue_timer.setTimerType(Qt.PreciseTimer)
        self._dialogue_timer.timeout.connect(self._pop_ambient_line)

        self._touch_timer = QTimer(self)     # touch 3 秒倒计时（单发）
        self._touch_timer.setSingleShot(True)
        self._touch_timer.setInterval(TOUCH_MS)
        self._touch_timer.timeout.connect(self._on_touch_timeout)

        self._bubble_timer = QTimer(self)    # 气泡 3 秒自动销毁（单发）
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.setInterval(BUBBLE_MS)
        self._bubble_timer.timeout.connect(self._kill_bubble)

        self._click_timer = QTimer(self)     # 单击/双击判定延迟（单发）
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(QApplication.doubleClickInterval())
        self._click_timer.timeout.connect(self._do_touch)

        self._file_import_timer = QTimer(self)   # 按收纳／安慰时长计时（单发）
        self._file_import_timer.setSingleShot(True)
        self._file_import_timer.setInterval(FILE_IMPORT_MS)
        self._file_import_timer.timeout.connect(self._on_file_import_timeout)

        self._shake_timer = QTimer(self)     # 导入失败窗口抖动（重复触发）
        self._shake_timer.setInterval(SHAKE_STEP_MS)
        self._shake_timer.timeout.connect(self._on_shake_tick)
        self._shake_base = QPoint()          # 抖动起始位置
        self._shake_tick = 0                 # 抖动步数
        self._shake_active = False           # 是否正在抖动

        self._view_timer = QTimer(self)      # view_files：轮询监听资源管理器窗口关闭
        self._view_timer.setInterval(VIEW_POLL_MS)
        self._view_timer.timeout.connect(self._on_view_poll)

        self._cheerup_timer = QTimer(self)   # 完整登场庆祝（单发）
        self._cheerup_timer.setSingleShot(True)
        self._cheerup_timer.setTimerType(Qt.PreciseTimer)
        self._cheerup_timer.setInterval(CHEERUP_MS)
        self._cheerup_timer.timeout.connect(self._on_cheerup_timeout)

        # ---------------- 登场 / 待机启动控制 ----------------
        # 待机计时器严禁提前启动：cheerup_show 3 秒结束后（或素材缺失直接跳过）才开启。
        self._idle_started = False    # idle 状态机计时是否已启动
        self._transitions = FrameTransition(self)
        self._renderer = create_renderer(self.character.definition.renderer,
                                         self.character.assets, self._display_pixmap,
                                         self._make_blank_placeholder, self)
        self._set_image("nothing")    # 仅先确定窗口尺寸，不启动任何待机计时
        # 初始定位（config.json 覆盖逻辑在 main() 中处理）
        self._home_rect = self._compute_center_geometry()
        from .interactions import InteractionDirector

        self.interactions = InteractionDirector(self, ASSET_ROOT, self.preferences)
        self.interactions.message.connect(self._show_speech)
        from .living import LivingDirector

        self.living = LivingDirector(self, ASSET_ROOT)
        self._touch_poses = TouchPosePlayer(self, ASSET_ROOT)
        self._drag_poses = DragPosePlayer(self, ASSET_ROOT)

    # ================= 素材加载 =================

    def _make_blank_placeholder(self, key: str) -> QPixmap:
        """素材缺失/损坏时的空白占位（半透明淡色块 + 细边框，保证可点击不崩溃）。"""
        w, h = 240, 300
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(120, 140, 170, 120), 2))
        p.setBrush(QColor(205, 220, 240, 90))
        p.drawRoundedRect(QRectF(2, 2, w - 4, h - 4), 12, 12)
        p.end()
        return pm

    def _set_image(self, key: str) -> None:
        """Switch the renderer without restarting an unchanged animation."""
        self._cur_key = key
        reaction = self._tool_reaction
        animation = (reaction.animation if reaction.status != "idle" and
                     reaction.image == key and reaction.animation is not None
                     else self.character.animations.for_interaction(key))
        self._renderer.set_animation(animation)

    def _display_pixmap(self, pm: QPixmap) -> None:
        """A frame changes the drawing, never the desktop window position."""
        self._pixmap = pm
        # 窗口跟随图片原生尺寸自动变化（图片宽高不一致时也会自动适配）
        if self.width() != pm.width() or self.height() != pm.height():
            self.resize(pm.width(), pm.height())
        self.update()

    def speech_anchor(self):
        """Hold the head position within a pose; breathing must not shake text."""
        director = getattr(self, "interactions", None)
        frame, origin, clip = self._visible_base_frame or self._pixmap, QPoint(), None
        key = ("base", self._cur_key, self.inward_facing(), self.width(), self.height())
        living = getattr(self, "living", None)
        if living and living.active and living.frame is not None:
            facing = self.inward_facing() if self.character.definition.id == "whale-girl" else "right"
            frame = living.library.facing.get(living.frame, facing)
            key = ("life", living.scene.id, facing, self.width(), self.height())
        notices = getattr(self, "task_notices", None)
        if (director and director.displayed_frame is not None and
                (director.active or director.attention.frame is not None)):
            frame, origin = director.displayed_frame, director.displayed_origin
            clip = director._clip_rect
            key = ("scene", director.scene, director.index, director.active,
                   director.displayed_reversed, self.width(), self.height())
        if notices and notices.feedback.frame is not None:
            key = ("task-notice", notices.feedback.kind, self.inward_facing(), self.width(), self.height())
            if key != self._speech_anchor_key:
                frame = notices.feedback.frame.scaled(self.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                frame = self._facing_frames.get(frame, self.inward_facing()
                    if self.character.definition.id == "whale-girl" else "right")
            origin, clip = QPoint(), None
        if key != self._speech_anchor_key:
            self._speech_anchor_key = key
            self._speech_anchor_point = (head_anchor(frame, origin, self.rect(), clip)
                                         if frame is not None and not frame.isNull()
                                         else QPoint(self.width() // 2, 0))
        return QPoint(self._speech_anchor_point)

    # ================= 头顶思考气泡（单例 / 3 秒 / 稳定跟随） =================

    def _show_speech(self, text: str, automatic: bool = False) -> None:
        """
        复用一个不接受焦点的提示气泡，更新内容并显示 3 秒。
        窗口隐藏期间不弹任何气泡（后台定时器/事件桥 照常运行，画面静默）。
        """
        if (self._hidden or (automatic and not self.preferences.proactive_dialogue)
                or (getattr(self, "task_notices", None) and self.task_notices.bubble.isVisible())):
            return
        self._bubble_timer.stop()
        if self._speech_bubble_cache is None:
            self._speech_bubble_cache = SpeechBubble(text, self)
        else:
            self._speech_bubble_cache.set_text(text)
        bubble = self._speech_bubble_cache
        bubble.place_above(self)
        self._bubble = bubble
        if not bubble.isVisible():
            bubble.show()
        self._bubble_timer.start()

    def _kill_bubble(self) -> None:
        """Hide speech but retain its one reusable tooltip surface."""
        self._bubble_timer.stop()
        if self._bubble is not None:
            b, self._bubble = self._bubble, None
            b.hide()

    def moveEvent(self, event) -> None:  # noqa: N802 —— 气泡跟随桌宠移动
        super().moveEvent(event)
        if self._bubble is not None and self.isVisible():
            self._bubble.place_above(self)

    # ================= idle 待机状态机 =================

    def _apply_idle_stage(self) -> None:
        if self._hidden:
            return
        active = self._tool_reaction.status != "idle"
        image = (self._tool_reaction.image if active else "nothing"
                 if (getattr(self, "_codex_working", False) or getattr(self, "_legacy_codex_working", False)) else IDLE_ORDER[self._stage_idx])
        if not active and image == "eat" and self._ambient_dialogue.memory.recent("ate"):
            image = "nothing"
        self._set_image(image)
        if hasattr(self, "interactions"):
            self.interactions.attention.sync()

    def _flush_tool_speech(self) -> None:
        """Deliver event speech after direct interactions, independently of poses."""
        if self._hidden or self._state != "idle":
            return
        if self._pending_tool_speech:
            self._show_speech(self._pending_tool_speech, automatic=True)
            self._pending_tool_speech = ""

    def _start_stage_timer(self, ms: int) -> None:
        self._stage_remaining = ms
        self._stage_timer.setInterval(ms)
        if not self._hidden:
            self._stage_timer.start()

    def _start_dialogue_timer(self) -> None:
        if not self._hidden and self.preferences.proactive_dialogue:
            self._dialogue_timer.start()

    def _stop_dialogue_timer(self) -> None:
        self._dialogue_timer.stop()

    def _idle_start(self) -> None:
        """启动 idle 待机：周期重置从 nothing 开始（240s），开启 45s 待机台词计时。
        幂等：仅允许从“尚未启动”状态启动一次（cheerup 3 秒结束/素材缺失后调用）。"""
        if self._idle_started:
            return
        self._idle_started = True
        self._state = "idle"
        self._stage_idx = 0
        self._idle_paused = False
        self._apply_idle_stage()             # 切回 idle nothing.png
        self._flush_tool_speech()
        self._start_stage_timer(IDLE_MS["nothing"])
        self._start_dialogue_timer()
        print("[鲸鱼娘] 【待机开始】周期已重置：nothing(240s) → eat(30s) → think(30s)")

    def _pause_idle(self) -> None:
        """离开 idle 进入高优先级状态时：暂停 idle 阶段计时与 45s 待机台词计时。"""
        if self._state != "idle":
            return
        if hasattr(self, "interactions"):
            self.interactions.attention.suspend()
        r = self._stage_timer.remainingTime()
        if r is not None and r >= 0:
            self._stage_remaining = r
        self._stage_timer.stop()
        self._stop_dialogue_timer()
        self._idle_paused = True

    def _back_to_idle(self) -> None:
        """
        回到 idle：接续之前计时进度（不重置 5 分钟周期）。
        若 idle 待机尚未启动（cheerup_show 未播完即被打断等），则此刻全新启动待机循环。
        """
        self._touch_poses.stop()
        self._drag_poses.stop()
        self._state = "idle"
        if not self._hidden:
            self._renderer.resume()
        if not self._idle_started:
            self._idle_start()               # 待机计时此刻才允许启动
            return
        if self._idle_paused:
            self._idle_paused = False
            self._start_stage_timer(self._stage_remaining)
            self._start_dialogue_timer()
        self._apply_idle_stage()
        self._flush_tool_speech()

    # ================= 登场动画 cheerup_show =================

    def _cheerup_asset_available(self) -> bool:
        """登场素材是否存在且可解码（缺失/损坏 → 警告并跳过登场效果）。"""
        try:
            path = str(self.character.assets.resolve("cheerup"))
        except ValueError:
            return False
        if not os.path.exists(path):
            print(f"[鲸鱼娘] 【警告】登场素材缺失，跳过登场效果：{path}")
            return False
        try:
            pm = QPixmap(path)
        except Exception as exc:  # noqa: BLE001 —— 解码异常视为损坏
            print(f"[鲸鱼娘] 【警告】登场素材损坏，跳过登场效果：{path}（{exc}）")
            return False
        if pm.isNull():
            print(f"[鲸鱼娘] 【警告】登场素材损坏，跳过登场效果：{path}")
            return False
        return True

    def play_entrance(self) -> bool:
        """
        登场动画（启动 / 隐藏恢复）：
          1) 素材可用：切 cheerup.png + 弹固定气泡「鲸鱼娘登场！」；
             完整播放登场庆祝，再切回 idle nothing 并启动/恢复待机计时。
          2) 素材缺失/损坏 → 警告并直接跳过，立刻进入待机（绝不卡死）。
          3) 已处于 cheerup_show → 幂等返回。
        """
        if self._state == "cheerup_show":
            return True
        if not self._cheerup_asset_available():
            # 容错：跳过登场图片与气泡，立刻启动待机（不阻塞流程、不崩溃）
            print("[鲸鱼娘] 【登场】素材缺失，跳过登场效果，直接进入待机。")
            if not self._idle_started:
                self._idle_start()
            return False
        if self._click_timer.isActive():
            self._click_timer.stop()
        # 若待机已在运行（隐藏恢复场景）→ 先暂停保存进度
        if self._idle_started and self._state == "idle":
            self._pause_idle()
        self._touch_timer.stop()
        self._touch_poses.stop()
        self._drag_poses.stop()
        self._renderer.resume()
        self._state = "cheerup_show"
        self._set_image("cheerup")
        self._show_speech(self._ambient_dialogue.line("bound.entrance") or ENTRANCE_SPEECH, automatic=True)
        self._cheerup_timer.start()
        print("[鲸鱼娘] 【登场】庆祝结束后进入待机…")
        return True

    def _cancel_cheerup(self) -> None:
        """打断 cheerup_show（更高优先级状态切入时调用）。"""
        self._cheerup_timer.stop()

    def _on_cheerup_timeout(self) -> None:
        """登场庆祝结束：切回 idle nothing，此刻才启动/恢复待机计时。"""
        if self._state != "cheerup_show":
            return
        print("[鲸鱼娘] 【登场结束】切回 idle nothing，开始待机循环。")
        self._back_to_idle()

    def _on_stage_timeout(self) -> None:
        """idle 当前阶段耗尽 → 按固定顺序切下一阶段；eat/think 入场弹固定台词。"""
        if self._state != "idle" or self._hidden or self._idle_paused:
            return
        if IDLE_ORDER[self._stage_idx] == "eat" and self._cur_key == "eat" and self._tool_reaction.status == "idle":
            self._ambient_dialogue.memory.record("ate")
        self._stage_idx = (self._stage_idx + 1) % len(IDLE_ORDER)
        stage = IDLE_ORDER[self._stage_idx]
        if self._tool_reaction.status != "idle":
            self._apply_idle_stage()
            self._start_stage_timer(IDLE_MS[stage])
            return
        self._apply_idle_stage()
        # Explicit entry cues remain bound; ambient speech keeps its own clock.
        speech = IDLE_ANIMATION_SPEECH.get(stage) if self._cur_key == stage else ""
        if speech:
            self._show_speech(speech, automatic=True)
        print(f"[鲸鱼娘] 【待机轮转】→ {stage}")
        self._start_stage_timer(IDLE_MS[stage])

    def _pop_ambient_line(self) -> None:
        """工作或待机时独立碎碎念，避让动作台词与任务提醒。"""
        if (self._state != "idle" or self._idle_paused or self._hidden
                or self._pressing or not self.preferences.proactive_dialogue
                or self._bubble is not None or self._pending_tool_speech):
            return
        notices = getattr(self, "task_notices", None)
        if notices and (notices.bubble.isVisible() or notices.feedback.kind or notices.feedback.queue):
            return
        if any(window is not None and window.isVisible() for window in (
                self._chat_window, self._status_window, self._skin_window,
                self._settings_window, self._context_menu,
                getattr(notices, "panel", None))):
            return
        activity = self._tool_reaction.status
        if activity == "idle" and (getattr(self, "_codex_working", False)
                                   or getattr(self, "_legacy_codex_working", False)):
            activity = "generating"
        pool = load_talk_pool(TALK_NOTHING_REL, NOTHING_FALLBACK) if activity == "idle" else ()
        line = self._ambient_dialogue.choose(activity, pool)
        if line:
            self._show_speech(line, automatic=True)

    # ================= touch（左键单击交互） =================

    def inward_facing(self, x=None):
        bounds = self.interactions._bounds()
        x = self.geometry().center().x() if x is None else x
        self._facing = inward_facing(x, bounds.left(), bounds.right(), self._facing)
        return self._facing

    def _hit_touch_region(self, point):
        """Use the visible frame, including reversed walking, for local hits.

        Distinguish the upper head, two cheeks and physical tail.
        """
        pm = self._visible_base_frame or self._pixmap
        reversed_view = self._visible_base_reversed
        if self.interactions.displayed_frame is not None:
            if self.interactions._clip_rect is not None and not self.interactions._clip_rect.contains(point):
                return "body"
            pm = self.interactions.displayed_frame
            point = point - self.interactions.displayed_origin
            reversed_view = self.interactions.displayed_reversed
        if pm is None or not pm.rect().contains(point):
            return "body"
        image = pm.toImage()
        if image.pixelColor(point).alpha() < 32:
            return "body"
        if (self.interactions.active and
                self.interactions.steps[self.interactions.index].pose == "window-peek-top"):
            if reversed_view:
                point = QPoint(pm.width() - 1 - point.x(), point.y())
            # The prone head is lower and farther left than the standing head.
            if QRect(82, 104, 116, 69).contains(point):
                return "head"
            if QRect(112, 173, 73, 29).contains(point):
                return "cheek-left" if point.x() < 145 else "cheek-right"
            if point.x() < 112 and 32 < point.y() < 190:
                return "tail"
            return "body"
        x, y = point.x() / image.width(), point.y() / image.height()
        if reversed_view:
            x = 1 - x
        if x >= .48 and y < .43:
            return "head"
        if .55 <= x <= .89 and .43 <= y < .60:
            return "cheek-left" if x < .69 else "cheek-right"
        if x < .46 and .12 < y < .78:
            return "tail"
        return "body"

    def _do_touch(self) -> None:
        """单击后播放对应部位的反应，再恢复工作表情。"""
        self._interaction_facing = self.inward_facing()
        self.interactions.cancel(restore_position=False)
        if self._state in ("pickup", "cheerup_show"):
            return      # cheerup_show 3 秒内屏蔽 touch
        # 高优先级 touch 打断低优先级 file_import / view_files
        if self._state == "file_import":
            self._file_import_timer.stop()
        elif self._state == "view_files":
            self._stop_view_watch()          # 交互即结束查看状态（降级方案）
        if self._state == "idle":
            self._pause_idle()
        # 若上次 touch 的 3 秒倒计时未结束 → 清除旧计时，重新开始 3 秒
        if self._touch_timer.isActive():
            self._touch_timer.stop()
        self._state = "touch"
        self._set_image("touch")
        if not self._hidden:
            self._renderer.resume()
        self._touch_poses.stop()
        region = self._touch_region
        animated = False
        if self.character.definition.id == "whale-girl" and region in (
                "head", "tail", "cheek-left", "cheek-right"):
            clip = self._touch_progression.next(region, time.monotonic())
            animated = self._touch_poses.play(clip)
            if animated:
                self._renderer.pause()
        self._ambient_dialogue.memory.record("touched")
        line = self._ambient_dialogue.line("touch." + region) if animated else self._ambient_dialogue.line("touch.body")
        if not line:
            line = random.choice(load_talk_pool(TALK_TOUCH_REL, TOUCH_FALLBACK))
        print(f"[鲸鱼娘] 【点击交互】{line}")
        self._show_speech(line)
        duration = sum(self._touch_poses.library.clip["durations"]) if animated else TOUCH_MS
        self._touch_timer.start(duration)

    def _on_touch_timeout(self) -> None:
        """点击或落地动作结束，恢复工作表情与待机计时。"""
        if self._state not in ("touch", "landing"):
            return
        print("[鲸鱼娘] 【互动结束】恢复待机。")
        self._back_to_idle()

    # ================= pickup（拖拽交互） =================

    def _start_pickup(self) -> None:
        """拖拽开始时切换悬空姿势，并暂停待机计时。"""
        self._interaction_facing = self.inward_facing()
        self.interactions.cancel(restore_position=False)
        if self._state in ("pickup", "cheerup_show"):
            return      # cheerup_show 3 秒内屏蔽 PickUp 拖拽
        if self._click_timer.isActive():
            self._click_timer.stop()
        if self._touch_timer.isActive():
            self._touch_timer.stop()          # 拖拽打断 touch
        if self._state == "file_import":
            self._file_import_timer.stop()    # 拖拽打断 file_import 反馈
        if self._state == "view_files":
            self._stop_view_watch()           # 拖拽即结束查看状态（降级方案）
        if self._state == "idle":
            self._pause_idle()
        self._state = "pickup"
        self._touch_poses.stop()
        self._set_image("pickup")
        if not self._hidden:
            self._renderer.resume()
        if self.character.definition.id == "whale-girl":
            pivot = self._drag_offset if self._drag_offset is not None else QPoint(185, 65)
            if self._interaction_facing == "left":
                pivot = QPoint(self.width() - pivot.x(), pivot.y())
            if self._drag_poses.start(pivot, self._press_global, mirrored=self._interaction_facing == "left"):
                self._renderer.pause()
        speech = self._ambient_dialogue.line("bound.pickup") or PICKUP_SPEECH
        print(f"[鲸鱼娘] 【拖拽开始】{speech}")
        self._show_speech(speech)

    def _end_pickup(self) -> None:
        """Release at the chosen position, cushion the landing, then recover."""
        if self._state != "pickup":
            return
        animated = self._drag_poses.active
        self._drag_poses.stop()
        if animated and not self._hidden and self._touch_poses.play("landing"):
            self._state = "landing"
            self._set_image("nothing")
            self._renderer.pause()
            self._touch_timer.start(sum(self._touch_poses.library.clip["durations"]))
            self._show_speech(self._ambient_dialogue.line("bound.landing"))
        else:
            self._back_to_idle()
        # 用户手动拖拽后记住窗口位置 → config.json（下次启动恢复）
        save_window_position(self.x(), self.y())


    # ================= RAG：知识库刷新 / 文件导入 =================

    def refresh_rag(self) -> None:
        """刷新 RAG 知识库文本（打开聊天窗口时、文件导入成功后调用）。"""
        info = load_rag_text()
        self.rag_text = info["text"]
        self.rag_loaded = info["loaded"]
        self.rag_combined_truncated = info["combined_truncated"]
        self.rag_doc_truncated = info["doc_truncated"]
        if self._chat_window is not None:
            self._chat_window.update_rag_label()
        print(f"[鲸鱼娘] 【知识库刷新】已加载 {self.rag_loaded}/{RAG_MAX_TOTAL} 字，"
              f"跳过损坏文档 {info['skipped']} 个。")

    def _ensure_visible_for_action(self) -> None:
        """托盘触发文件/查看动作时，若主窗口被隐藏则先还原（便于看到反馈）。"""
        self.interactions.cancel()
        if self.isHidden():
            self.show()
            self.raise_()
            self.activateWindow()

    def start_add_documents(self) -> None:
        """右键菜单【添加资料文件】：弹出系统文件对话框，多选 .txt/.docx 导入。"""
        if self._state in ("pickup", "touch", "landing"):
            print("[鲸鱼娘] 【添加资料】处于高优先级状态，本次命令已忽略。")
            return
        self._ensure_visible_for_action()
        ensure_rag_dir()
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要添加的资料文件（支持 .txt / .docx）", "",
            "资料文件 (*.txt *.docx);;所有文件 (*.*)")
        if paths:
            self._handle_import_paths(paths)

    def _handle_import_paths(self, paths: list) -> None:
        """
        统一导入入口（菜单多选 / 外部文件拖拽共用）：
          - 高优先级状态（pickup/touch）下直接忽略，不触发任何反馈；
          - 全部成功 → 成功反馈；存在失败 → 失败反馈（抖动 + 错误气泡）。
        """
        if self._state in ("pickup", "touch", "landing"):
            print("[鲸鱼娘] 【添加资料】高优先级状态中，文件拖拽导入不触发反馈。")
            return
        ok_all = True
        any_ok = False
        for path in paths:
            moved = self._import_document(path)
            ok_all = ok_all and moved
            any_ok = any_ok or moved
        if any_ok:
            self.refresh_rag()      # 文件导入成功后自动刷新知识库（无需重启）
        self._enter_file_import(ok_all)

    def _import_document(self, path: str) -> bool:
        """把单个文档复制到 RAG：仅 .txt/.docx；重名自动追加序号不覆盖；异常 → False。"""
        try:
            if not os.path.isfile(path):
                print(f"[鲸鱼娘] 【导入失败】文件不存在：{path}")
                return False
            if not is_supported_document(path):
                print(f"[鲸鱼娘] 【导入失败】不支持的格式（仅 .txt/.docx）：{path}")
                return False
            if os.path.getsize(path) > DOCUMENT_MAX_BYTES:
                print("[鲸鱼娘] 【导入失败】资料文件超过 2 MiB。")
                return False
            ensure_rag_dir()
            stem, ext = os.path.splitext(os.path.basename(path))
            target = os.path.join(RAG_DIR, stem + ext)
            counter = 1
            while os.path.exists(target):        # 重名自动加序号，绝不覆盖
                target = os.path.join(RAG_DIR, f"{stem}({counter}){ext}")
                counter += 1
            shutil.copy2(path, target)            # 复制到 RAG，保留原文件
            print(f"[鲸鱼娘] 【导入成功】{os.path.basename(path)} → {target}")
            return True
        except Exception as exc:  # noqa: BLE001 —— 权限不足/被占用/其他异常
            print(f"[鲸鱼娘] 【导入失败】复制文件异常（{type(exc).__name__}）：{exc}")
            return False

    # ================= file_import（按成功／失败含义播放反馈） =================

    def _enter_file_import(self, ok: bool) -> None:
        """进入收纳反馈：成功抱稳资料，失败安慰；自定义皮肤保留抖动回退。"""
        self.interactions.cancel()
        if self._state in ("pickup", "touch", "landing"):
            return
        if self._click_timer.isActive():
            self._click_timer.stop()
        if self._state == "file_import":
            self._file_import_timer.stop()     # 重新开始 3 秒倒计时
        if self._state == "view_files":
            self._stop_view_watch()
        if self._state == "cheerup_show":
            self._cancel_cheerup()          # file_import（更高优先级）打断 cheerup_show
        if self._state == "idle":
            self._pause_idle()
        self._state = "file_import"
        if ok:
            self._set_image("true")
            self._show_speech(self._ambient_dialogue.line("bound.file_ok") or FILE_OK_SPEECH)
        else:
            self._set_image("wrong")
            self._show_speech(self._ambient_dialogue.line("bound.file_failed") or FILE_FAIL_SPEECH)
            if self.character.definition.id != "whale-girl":
                self._start_shake()
        print(f"[鲸鱼娘] 【导入反馈】file_import：{'成功' if ok else '失败'}（完整反馈后恢复待机）")
        self._file_import_timer.start(self.character.animations.for_interaction("true" if ok else "wrong").duration_ms)

    def _on_file_import_timeout(self) -> None:
        """导入反馈结束：切回 idle nothing，恢复待机计时（接续原进度）。"""
        if self._state != "file_import":
            return
        print("[鲸鱼娘] 【导入反馈结束】恢复待机。")
        self._back_to_idle()

    def _start_shake(self) -> None:
        """窗口抖动 0.3 秒：左右小幅晃动后回到原位（导入失败反馈）。"""
        self._shake_base = self.pos()
        self._shake_tick = 0
        self._shake_active = True
        self._shake_timer.start()

    def _stop_shake(self) -> None:
        if not self._shake_active:
            return
        self._shake_active = False
        self._shake_timer.stop()
        if self.pos() != self._shake_base:
            self.move(self._shake_base)

    def _on_shake_tick(self) -> None:
        self._shake_tick += 1
        total = max(1, int(SHAKE_MS / SHAKE_STEP_MS))
        if self._shake_tick > total:
            self._stop_shake()
            return
        dx = 10 if (self._shake_tick % 2 == 1) else -10
        base = self._shake_base
        if base:
            self.move(QPoint(base.x() + dx, base.y()))

    # ================= view_files（查看资料状态） =================

    def start_view_files(self) -> None:
        """右键菜单【查看已有资料】：打开 RAG 文件夹，进入 view_files 状态。"""
        if self._state in ("pickup", "touch", "landing", "file_import",
                           "cheerup_show"):
            print("[鲸鱼娘] 【查看资料】处于更高优先级状态，命令已忽略（不切换图片）。")
            return
        if self._state == "view_files":
            print("[鲸鱼娘] 【查看资料】已在查看状态中。")
            return
        self._ensure_visible_for_action()
        if self._click_timer.isActive():
            self._click_timer.stop()
        if self._state == "idle":
            self._pause_idle()
        self._state = "view_files"
        self._set_image("think")                       # 复用 think.png
        count = count_rag_documents()                  # 实时统计 .txt+.docx
        self._show_speech(VIEW_COUNT_SPEECH.format(count=count))
        print(f"[鲸鱼娘] 【查看资料】view_files 状态，当前已收纳 {count} 个资料。")

        self._view_open = False
        self._view_poll_count = 0
        if sys.platform == "win32":
            self._view_timer.start()                       # 轮询监听文件夹窗口关闭

        # RAG 文件夹不存在则先创建再打开；打开失败给气泡提示，不崩溃
        ensure_rag_dir()
        if not open_folder_in_explorer(RAG_DIR):
            print(f"[鲸鱼娘] 【警告】打开 RAG 文件夹失败：{RAG_DIR}")
            self._show_speech(VIEW_OPEN_FAIL_SPEECH)   # 覆盖计数气泡（全局唯一气泡）
            self._view_poll_count = VIEW_OPEN_TRIES + 1  # 不再空等窗口，靠交互降级退出

    def _on_view_poll(self) -> None:
        """
        轮询监听资源管理器窗口：
         - 窗口尚未出现 → 持续等待（超过尝试次数即停表，靠用户交互降级退出）；
         - 窗口曾出现 → 一旦消失立即自动结束 view_files 并恢复待机。
        """
        if self._state != "view_files":
            self._view_timer.stop()
            return
        if not self._view_open:
            self._view_poll_count += 1
            if find_folder_window(RAG_DIR):
                self._view_open = True
                print("[鲸鱼娘] 【查看资料】已检测到 RAG 文件夹窗口，开始监听关闭。")
            elif self._view_poll_count > VIEW_OPEN_TRIES:
                # 无法精准监听（explorer 复用/标题不同等）→ 停表，改由交互降级
                print("[鲸鱼娘] 【查看资料】未检测到独立窗口，改用“任意交互退出”降级方案。")
                self._view_timer.stop()
            return
        if not find_folder_window(RAG_DIR):
            print("[鲸鱼娘] 【查看资料】RAG 文件夹窗口已关闭，自动恢复待机。")
            self._stop_view_watch()
            self._back_to_idle()

    def _stop_view_watch(self) -> None:
        """停止文件夹窗口监听（交互结束 / 窗口关闭 / 被更高优先级状态打断时）。"""
        self._view_timer.stop()

    # ================= 外部文件拖拽导入（与 PickUp 严格区分） =================

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        """外部文件拖入桌宠窗口：接受文件拖拽（拖拽移动桌宠走 mouse 事件，互不干扰）。"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        """外部文件松开：收集本地文件路径并走统一导入流程（自动校验后缀）。"""
        event.acceptProposedAction()
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile()]
        if not paths:
            return
        print(f"[鲸鱼娘] 【文件拖入】收到 {len(paths)} 个文件，开始导入校验…")
        self._handle_import_paths(paths)

    # ================= 鼠标事件 =================

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._touch_region = self._hit_touch_region(event.position().toPoint())
        if hasattr(self, "interactions"):
            self.interactions.cancel(restore_position=False)
        if event.button() == Qt.RightButton:
            self._open_context_menu(event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            # 新一次按下即新手势：清除可能残留的“双击第二下释放”标记
            self._suppress_next_release = False
            if self._state == "cheerup_show":
                # cheerup_show：3 秒内屏蔽触摸/拖拽
                return
            self._pressing = True
            self._drag_active = False
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = self._press_global - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._state == "cheerup_show":
            return                              # cheerup_show 屏蔽 PickUp 拖拽
        if (event.buttons() & Qt.LeftButton) and self._pressing:
            gpos = event.globalPosition().toPoint()
            if not self._drag_active:
                # 按下且移动达到阈值才算拖拽；单纯按下不动只算点击
                if (gpos - self._press_global).manhattanLength() >= DRAG_THRESHOLD_PX:
                    self._drag_active = True
                    self._start_pickup()
            if self._drag_active and self._state == "pickup":
                self.move(self._clamp_pos(gpos - self._drag_offset))
                self._drag_poses.sample(gpos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.RightButton:
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            if self._suppress_next_release:     # 双击第二下的释放 → 无动作
                self._suppress_next_release = False
                self._pressing = False
                self._drag_offset = None
                return
            if self._pressing:
                was_drag = self._drag_active
                self._pressing = False
                self._drag_active = False
                self._drag_offset = None
                if was_drag:
                    self._end_pickup()          # 拖拽结束
                else:
                    # 单击延迟到双击判定窗口之后，确保“双击无动作”
                    self._click_timer.start()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """左键双击：无任何动作（不再弹聊天框）；消费掉双击相关的单击/释放。"""
        if self._click_timer.isActive():
            self._click_timer.stop()
        self._suppress_next_release = True   # 双击第二下的释放不触发任何动作
        self._pressing = False
        self._drag_offset = None
        super().mouseDoubleClickEvent(event)

    def _build_context_menu(self) -> QMenu:
        """
        右键菜单（顺序严格 + UI 样式）：
        —— 分隔线 ——  隐藏鲸鱼娘（隐藏时显示为“显示鲸鱼娘”） / 退出（红色字体）
        """
        menu = QMenu(self)
        act_chat = QAction("聊聊天", menu)
        act_status = QAction("工具状态", menu)
        act_skin = QAction("角色与动作", menu)
        act_add = QAction("添加资料文件", menu)
        act_view = QAction("查看已有资料", menu)
        act_reset = QAction("重置位置", menu)
        hide_text = "显示鲸鱼娘" if self.isHidden() else "隐藏鲸鱼娘"
        act_hide = QAction(hide_text, menu)
        act_chat.triggered.connect(self.open_chat_window)
        act_status.triggered.connect(self.open_status_window)
        if getattr(self, "task_notices", None):
            act_tasks = QAction("Codex 任务与原文", menu)
            act_tasks.triggered.connect(self.task_notices.open_panel)
            menu.addAction(act_tasks)
        act_skin.triggered.connect(self.open_skin_window)
        act_add.triggered.connect(self.start_add_documents)
        act_view.triggered.connect(self.start_view_files)
        act_reset.triggered.connect(self.reset_position)
        act_hide.triggered.connect(self.toggle_hide_whale)
        for act in (act_chat, act_status, act_skin, act_add, act_view, act_reset):
            menu.addAction(act)
        self._add_interaction_menu(menu)
        menu.addSeparator()                       # 「重置位置」与「隐藏鲸鱼娘」之间
        menu.addAction(act_hide)
        menu.addAction(make_red_quit_action(menu, self.quit_app))   # 红色【退出】
        return menu

    def _add_interaction_menu(self, menu):
        modes = menu.addMenu("陪伴模式")
        group = QActionGroup(modes)
        for key, label in (("focus", "专注模式"), ("entertainment", "娱乐模式")):
            action = modes.addAction(label)
            action.setCheckable(True)
            group.addAction(action)
            action.setChecked(self.preferences.mode == key)
            action.triggered.connect(lambda _checked=False, key=key: self.set_mode(key))
            modes.aboutToShow.connect(lambda action=action, key=key: action.setChecked(self.preferences.mode == key))
        settings = menu.addAction("互动设置")
        settings.triggered.connect(self.open_settings_window)
        stop = menu.addAction("停止当前剧情")
        stop.triggered.connect(lambda: self.interactions.cancel())
        restore = menu.addAction("恢复被 Mori 最小化的窗口")
        restore.triggered.connect(self.interactions.restore_window)
        menu.aboutToShow.connect(lambda: stop.setEnabled(self.interactions.active))
        menu.aboutToShow.connect(lambda: restore.setEnabled(bool(self.interactions.restore_identity)))

    def set_mode(self, mode):
        if mode in ("focus", "entertainment"):
            self.interactions.apply(replace(self.preferences, mode=mode))

    def open_settings_window(self):
        from .settings_panel import SettingsWindow

        self.interactions.cancel()
        if self._settings_window is None:
            self._settings_window = SettingsWindow(self)
        self._settings_window.refresh()
        self._settings_window.show()
        self._settings_window.raise_()
        self._settings_window.activateWindow()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 —— 右键：光标处弹菜单
        self._open_context_menu(event.globalPos())
        event.accept()

    def _open_context_menu(self, position: QPoint) -> None:
        """Handle physical right clicks and keyboard context-menu requests.

        Reuse one nonblocking popup so native contextMenuEvent delivery cannot
        create a second menu or accumulate hidden menus on repeated clicks.
        """
        if self._context_menu is None:
            self._context_menu = self._build_context_menu()
        if not self._context_menu.isVisible():
            self._context_menu.popup(position)

    def _clamp_pos(self, pos: QPoint) -> QPoint:
        """屏幕边界钳制：宠物至少留一部分在屏幕内（防拖丢）。"""
        screen = self.screen() if self.screen() else QApplication.primaryScreen()
        if screen is None:
            return pos
        geo = screen.availableGeometry()
        x = max(geo.left() - self.width() + 60,
                min(pos.x(), geo.right() - 60))
        y = max(geo.top(), min(pos.y(), geo.bottom() - 60))
        return QPoint(int(x), int(y))

    # ================= 聊聊天（右键菜单） =================

    def apply_tool_reaction(self, reaction, session_lines, history_lines):
        """Only neutral presentation data crosses the tool/UI boundary."""
        speech_changed = (reaction.status, reaction.speech) != (
            self._tool_reaction.status, self._tool_reaction.speech)
        self._tool_reaction = reaction
        if hasattr(self, "interactions") and self.interactions.active and reaction.status != "idle":
            self.interactions.cancel(restore_position=False)
        self._renderer.set_emotion(reaction.emotion)
        self.tool_summary_text = "\n".join(session_lines) or "尚未收到工具事件。"
        self.tool_summary_text += (f"\n情绪：{reaction.emotion.name} · "
                                   f"强度 {reaction.emotion.intensity:.0%} · 动作：{reaction.image}")
        self._history_lines = history_lines
        if speech_changed:
            self._pending_tool_speech = reaction.speech
        self.living.activity_changed()
        if self._state == "idle":
            self._apply_idle_stage()
            self._flush_tool_speech()
        if self._tray is not None:
            self._tray.setToolTip("Mori Companion\n" + self.tool_summary_text[:500])
        if self._status_window is not None:
            self._status_window.update_status(self.tool_summary_text, history_lines)

    def open_status_window(self):
        self.interactions.cancel()
        if self._status_window is None:
            self._status_window = StatusWindow(self)
        self._status_window.update_status(self.tool_summary_text, self._history_lines)
        self._status_window.show()
        self._status_window.raise_()
        self._status_window.activateWindow()

    def open_skin_window(self):
        self.living.cancel()
        from .skin_panel import SkinWindow

        if self._skin_window is None:
            self._skin_window = SkinWindow(self, ASSET_ROOT)
        self._skin_window.refresh()
        self._skin_window.show()
        self._skin_window.raise_()
        self._skin_window.activateWindow()

    def set_character(self, character):
        if getattr(self, "task_notices", None):
            self.task_notices.feedback.reset_assets()
        self._transitions.clear()
        self._facing_frames.clear()
        self._visible_base_frame = None
        self._touch_poses.stop()
        self._drag_poses.stop()
        self._touch_progression.clear()
        self._touch_region = "body"
        self.interactions.clear_assets()
        reaction = character.react(self._tool_reaction.status)
        renderer = create_renderer(character.definition.renderer, character.assets,
                                   self._display_pixmap, self._make_blank_placeholder, self)
        old = self._renderer
        old.dispose()
        old.deleteLater()
        self.character = character
        self._renderer = renderer
        self._tool_reaction = reaction
        renderer.set_emotion(self._tool_reaction.emotion)
        self.setWindowTitle("Mori Companion · " + character.definition.name)
        self._set_image(self._cur_key)
        if not self._hidden:
            renderer.resume()

    def open_chat_window(self) -> None:
        """弹出聊天子窗口（就近放在宠物右侧）；打开聊天窗口即刷新 RAG 知识库。
        边界规则：窗口隐藏时先自动恢复显示再执行。"""
        self.living.cancel()
        self._ensure_visible_for_action()
        if self._chat_window is None:
            self._chat_window = ChatWindow(pet=self)
            app = QApplication.instance()
            if app is not None:
                app._chat_window = self._chat_window
        else:
            self.refresh_rag()          # 每次打开都刷新（含再次打开）
            self._chat_window.update_rag_label()
        self._chat_window.show_and_activate(self.frameGeometry())
        print("[鲸鱼娘] 【聊聊天】已弹出聊天窗口。")


    # ================= 退出 / 清理 / 托盘 =================

    def attach_tray(self, tray) -> None:
        """由 main() 注册系统托盘（不支持托盘时为 None，走关窗即退出回退逻辑）。"""
        self._tray = tray

    def quit_app(self) -> None:
        """【退出】：先保存当前窗口位置到 config.json，再完全结束进程。
        aboutToQuit → _cleanup 统一停止定时器/销毁气泡/销毁托盘对象。"""
        print("[鲸鱼娘] 【退出】正在清理并完全退出程序…")
        save_window_position(self.x(), self.y())   # 退出前保存当前位置（五.6）
        self._quitting = True       # 允许桌宠/聊天窗口真正关闭（不阻断退出）
        if self._chat_window is not None and self._chat_window.isVisible():
            self._chat_window.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()          # aboutToQuit → _cleanup 统一清理

    def _cleanup(self) -> None:
        """停止全部定时器、销毁气泡组件、销毁托盘对象，杜绝资源残留。"""
        self.interactions.stop()
        self.living.stop()
        if getattr(self, "task_notices", None):
            self.task_notices.close()
        self._touch_poses.stop()
        self._drag_poses.stop()
        if self._context_menu is not None:
            self._context_menu.close()
        self._transitions.clear()
        self._facing_frames.clear()
        self._visible_base_frame = None
        self._renderer.dispose()
        for timer in (self._stage_timer, self._dialogue_timer, self._touch_timer,
                      self._bubble_timer, self._click_timer,
                      self._file_import_timer, self._shake_timer,
                      self._view_timer, self._cheerup_timer):
            try:
                timer.stop()
            except RuntimeError:
                pass
        self._stop_shake()
        self._kill_bubble()
        if self._speech_bubble_cache is not None:
            self._speech_bubble_cache.deleteLater()
            self._speech_bubble_cache = None
        for window in (self._chat_window, self._status_window, self._skin_window, self._settings_window):
            if window is not None:
                window.hide()
        if self._tray is not None:
            tray, self._tray = self._tray, None
            try:
                tray.hide()
                tray.deleteLater()
            except RuntimeError:
                pass
            print("[鲸鱼娘] 【退出】已销毁系统托盘图标。")
        print("[鲸鱼娘] 【退出完成】已停止全部定时器并销毁气泡组件。")

    # ================= 隐藏鲸鱼娘 / 显示还原（隐藏期间后台继续运行） =================

    def hide_whale(self) -> None:
        """【隐藏鲸鱼娘】：主窗口隐藏，托盘图标常驻；定时器 / 事件桥 后台继续运行。"""
        if not self.isHidden():
            print("[鲸鱼娘] 【隐藏】鲸鱼娘暂时藏起来了（双击托盘图标可唤出）。")
            self.hide()

    def show_whale(self, greet: bool = True) -> None:
        """恢复显示主窗口；greet=True 时执行登场动画（cheerup.png + 登场气泡 3 秒）。"""
        if self.isHidden():
            self.show()
        self.raise_()
        self.activateWindow()
        if greet:
            print("[鲸鱼娘] 【显示】鲸鱼娘回来了，播放登场动画。")
            self.play_entrance()

    def toggle_hide_whale(self) -> None:
        """右键菜单【隐藏鲸鱼娘】/【显示鲸鱼娘】切换。"""
        if self.isHidden():
            self.show_whale(greet=True)
        else:
            self.hide_whale()

    def closeEvent(self, event) -> None:  # noqa: N802
        """
        窗口关闭按钮/Alt+F4：
          - 完整退出中(_quitting) → 直接接受，配合 app.quit 正常结束；
          - 有托盘 → 只隐藏主窗口，驻留后台托盘运行；
          - 无托盘（系统不支持等）→ 回退为关窗即完全退出。
        """
        if self._quitting:
            event.accept()
            return
        if self._tray is not None:
            print("[鲸鱼娘] 【窗口关闭】隐藏到系统托盘继续运行（托盘右键【退出】可结束）。")
            self.hide()
            event.ignore()
        else:
            self.quit_app()
            event.accept()

    # ================= 初始定位（默认右下角；config.json 有效时启动/退出恢复该位置） =================

    def _compute_center_geometry(self) -> QRect:
        """
        计算“默认位置”：桌面右下角、任务栏上方。
        使用桌面可用区域（已排除任务栏），让窗口右下角与可用区域右下角对齐。
        当 config.json 缺失/无效时用作启动位置；【重置位置】也会重新计算此坐标。
        """
        screen = QApplication.primaryScreen()
        if screen is None:
            return QRect(0, 0, self.width(), self.height())
        geo = screen.availableGeometry()          # 可用区域：排除任务栏/停靠栏
        x = geo.left() + geo.width() - self.width()
        y = geo.top() + geo.height() - self.height()
        print(f"[鲸鱼娘] 【启动定位】可用区域 {geo.width()}x{geo.height()}，"
              f"窗口 {self.width()}x{self.height()} → 右下角 ({x}, {y})")
        return QRect(int(x), int(y), self.width(), self.height())

    def home_geometry(self) -> QRect:
        """默认（右下角）启动几何；config.json 优先逻辑在 main() 中处理。"""
        return self._home_rect

    def reset_position(self) -> None:
        """右键【重置位置】：立刻回到桌面右下角（任务栏上方），并同步 config.json。"""
        self.interactions.cancel()
        self._ensure_visible_for_action()        # 隐藏状态下先恢复显示
        rect = self._compute_center_geometry()   # 按当前分辨率实时重算右下角
        self._home_rect = rect
        self.setGeometry(rect)
        self.raise_()
        save_window_position(self.x(), self.y())
        print(f"[鲸鱼娘] 【重置位置】已移动到右下角 ({self.x()}, {self.y()})。")

    # ================= 绘制 =================

    def paintEvent(self, event) -> None:  # noqa: N802
        desired = QImage(self.size(), QImage.Format_ARGB32_Premultiplied)
        desired.fill(0)
        frame_painter = QPainter(desired)
        frame_painter.setRenderHint(QPainter.Antialiasing)
        pm = getattr(self, "_pixmap", None)
        facing = self._interaction_facing if self._state in ("touch", "pickup", "landing") else self.inward_facing()
        if self.character.definition.id != "whale-girl":
            facing = "right"
        key, duration = ("base", self._cur_key, pm.cacheKey() if pm else 0, facing), .12
        notices = getattr(self, "task_notices", None)
        if notices and notices.feedback.draw(frame_painter):
            key = ("task-notice", notices.feedback.kind, notices.feedback.frame.cacheKey())
            duration = .08
        elif self.living.draw(frame_painter):
            key = ("life", self.living.scene.id, self.living.frame.cacheKey(), facing)
        elif hasattr(self, "interactions") and self.interactions.draw(frame_painter):
            visible = self.interactions.displayed_frame
            key = ("scene", self.interactions.scene, self.interactions.index,
                   visible.cacheKey() if visible else 0, self.interactions.displayed_reversed)
            duration = (GAITS[self.interactions.steps[self.interactions.index].gait].transition
                        if self.interactions.active and self.interactions.steps[self.interactions.index].pose == "walk" else .12)
        elif self._drag_poses.active:
            frame_painter.save()
            if facing == "left":
                frame_painter.translate(self.width(), 0)
                frame_painter.scale(-1, 1)
            self._drag_poses.draw(frame_painter)
            frame_painter.restore()
            key, duration = ("drag", self._drag_poses.pose_index, facing), .08
            self._visible_base_frame = self._facing_frames.get(self._drag_poses.frame, facing)
            self._visible_base_reversed = facing == "left"
        elif self._touch_poses.active and self._touch_poses.frame is not None:
            self._visible_base_frame = self._facing_frames.get(self._touch_poses.frame, facing)
            self._visible_base_reversed = facing == "left"
            frame_painter.drawPixmap(0, 0, self._visible_base_frame)
            key = ("touch", self._touch_poses.library._name, self._touch_poses.frame.cacheKey(), facing)
        elif pm is not None and not pm.isNull():
            self._visible_base_frame = self._facing_frames.get(pm, facing)
            self._visible_base_reversed = facing == "left"
            frame_painter.drawPixmap(0, 0, self._visible_base_frame)
            if self._cur_key == "nothing":
                duration = .045
        else:
            frame_painter.fillRect(self.rect(), QColor(255, 255, 255, 200))
        frame_painter.end()
        displayed = self._transitions.present(desired, key, duration)
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if self.interactions._clip_rect is not None:
            painter.setClipRect(self.interactions._clip_rect)
        painter.drawImage(0, 0, displayed)
        painter.end()
        if self._bubble is not None:
            self._bubble.follow(self)
        if notices:
            notices.bubble.follow(self)

    def showEvent(self, event) -> None:  # noqa: N802 —— 显示时清除隐藏标记
        super().showEvent(event)
        self._hidden = False
        self._transitions.resume()
        if self._state == "idle" and self._idle_started:
            self._back_to_idle()
        self._renderer.resume()
        if self._touch_poses.active:
            self._renderer.pause()
            self._touch_poses.resume()
            self._touch_timer.start(max(1, getattr(self, "_touch_remaining", TOUCH_MS)))
        if hasattr(self, "interactions"):
            self.interactions.visibility_changed(True)
        if hasattr(self, "living"):
            self.living.schedule()

    def hideEvent(self, event) -> None:  # noqa: N802 —— 隐藏时标记，隐藏期间不弹气泡、后台继续
        self._transitions.pause()
        if hasattr(self, "interactions"):
            self.interactions.visibility_changed(False)
        self._renderer.pause()
        if self._touch_poses.active:
            self._touch_remaining = max(1, self._touch_timer.remainingTime())
            self._touch_timer.stop()
            self._touch_poses.pause()
        if self._state == "idle":
            self._pause_idle()
        self._hidden = True
        if hasattr(self, "living"):
            self.living.stop()
        if getattr(self, "task_notices", None):
            self.task_notices.bubble.hide()
        if self._state == "pickup":
            self._drag_poses.stop()
            self._pressing = self._drag_active = False
            self._drag_offset = None
            if not self._quitting:
                self._back_to_idle()
        self._kill_bubble()
        self._click_timer.stop()
        self._shake_timer.stop()
        super().hideEvent(event)

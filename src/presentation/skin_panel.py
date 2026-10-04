"""Manual GIF/PNG skin management. No image-generation service or network."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from character.animation import ANIMATION_KEYS
from character.defaults import default_character
from character.skins import SkinRepository
from companion.paths import ensure_state_dir

LABELS = {"nothing": "待机", "eat": "工作 / 进食", "think": "思考",
          "attention": "等待确认", "cheerup": "完成 / 登场", "wrong": "失败",
          "touch": "触摸", "pickup": "拖拽", "true": "收纳成功"}


def validate_image(path):
    """Bound file, dimensions, frame count and playback rate before activation."""
    path = Path(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("图片不能超过 2 MiB")
    reader = QImageReader(str(path))
    size = reader.size()
    if (bytes(reader.format()).lower() not in (b"png", b"gif") or
            not 1 <= size.width() <= 512 or not 1 <= size.height() <= 512):
        raise ValueError("需要 512×512 以内的 PNG / GIF")
    count = reader.imageCount()
    if count > 120:
        raise ValueError("GIF 不能超过 120 帧")
    frames = 0
    canvas = None
    while reader.canRead():
        image = reader.read()
        if image.isNull():
            raise ValueError("图片解码失败")
        frames += 1
        if frames > 120:
            raise ValueError("GIF 不能超过 120 帧")
        if canvas is None:
            canvas = image.size()
        elif image.size() != canvas:
            raise ValueError("GIF 各帧画布尺寸必须一致")
        if bytes(reader.format()).lower() == b"gif" and reader.nextImageDelay() < 80:
            raise ValueError("GIF 帧间隔至少 80ms，以保持低资源占用")
        if not reader.supportsAnimation():
            break
    if not frames or (count > 0 and count != frames):
        raise ValueError("图片帧不完整")


class SkinWindow(QWidget):
    def __init__(self, pet, asset_root):
        super().__init__()
        self.pet = pet
        self.default = default_character(asset_root)
        self.repository = SkinRepository(ensure_state_dir())
        self.setWindowTitle("Mori Companion · 角色与动作")
        self.resize(470, 440)
        layout = QVBoxLayout(self)
        self.skins = QListWidget(self)
        layout.addWidget(self.skins)
        row = QHBoxLayout()
        activate = QPushButton("使用选中角色", self)
        delete = QPushButton("移除选中皮肤", self)
        row.addWidget(activate)
        row.addWidget(delete)
        layout.addLayout(row)
        import_skin = QPushButton("导入皮肤 character.json", self)
        layout.addWidget(import_skin)
        self.name = QLineEdit(self)
        self.name.setPlaceholderText("新皮肤名称（导入单个动作时使用）")
        layout.addWidget(self.name)
        row = QHBoxLayout()
        self.action = QComboBox(self)
        for key in ANIMATION_KEYS:
            self.action.addItem(LABELS[key], key)
        import_action = QPushButton("导入 PNG / GIF 动作", self)
        row.addWidget(self.action)
        row.addWidget(import_action)
        layout.addLayout(row)
        self.message = QLabel("导入动作会复制当前皮肤并替换所选动作，可随时切回原角色。", self)
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        layout.addWidget(self.message)
        activate.clicked.connect(self.activate)
        delete.clicked.connect(self.remove)
        import_skin.clicked.connect(self.import_skin)
        import_action.clicked.connect(self.import_action)
        self.refresh()

    def refresh(self):
        self.skins.clear()
        for definition in [self.default.definition, *self.repository.list()]:
            current = definition.id == self.pet.character.definition.id
            item = QListWidgetItem(definition.name + ("（当前）" if current else ""))
            item.setData(Qt.UserRole, definition.id)
            self.skins.addItem(item)
            if current:
                self.skins.setCurrentItem(item)

    def _apply(self, character):
        # Interaction pose is kept; the new renderer starts with that same key.
        for key in ANIMATION_KEYS:
            validate_image(character.assets.resolve(key))
        self.pet.set_character(character)
        self.repository.select(character.definition.id)
        self.refresh()
        self.message.setText("已切换角色：" + character.definition.name)

    def activate(self):
        item = self.skins.currentItem()
        if item is None:
            return
        try:
            identity = item.data(Qt.UserRole)
            self._apply(self.default if identity == self.default.definition.id
                        else self.repository.get(identity))
        except (OSError, ValueError, TypeError) as error:
            self.message.setText(str(error))

    def remove(self):
        item = self.skins.currentItem()
        if item is None:
            return
        identity = item.data(Qt.UserRole)
        if identity in (self.default.definition.id, self.pet.character.definition.id):
            self.message.setText("请先切换到其他角色；默认角色不能移除。")
            return
        try:
            self.repository.remove(identity)
            self.refresh()
            self.message.setText("已移除选中皮肤。")
        except (OSError, ValueError) as error:
            self.message.setText(str(error))

    def import_skin(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择皮肤配置", "", "character.json (character.json)")
        if path:
            try:
                self._apply(self.repository.import_manifest(path, validate_image))
            except (OSError, ValueError, TypeError) as error:
                self.message.setText(str(error))

    def import_action(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择动作素材", "", "动作图片 (*.gif *.png)")
        if path:
            try:
                files = {key: self.pet.character.assets.resolve(key) for key in ANIMATION_KEYS}
                files[self.action.currentData()] = Path(path)
                name = self.name.text().strip() or self.pet.character.definition.name + " · 自定义"
                self._apply(self.repository.install(name, files, validate_image))
            except (OSError, ValueError, TypeError) as error:
                self.message.setText(str(error))

    def closeEvent(self, event):
        event.ignore()
        self.hide()

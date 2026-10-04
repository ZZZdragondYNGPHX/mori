"""Manual mode switch, per-story controls, application exclusions and previews."""
from dataclasses import replace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from companion.preferences import MODULES, OPTIONAL_MODULES, InteractionPreferences

from .interactions import SCENE_MODULES, SCENES
from .system_actions import SYSTEM_SCENES


class SettingsWindow(QWidget):
    def __init__(self, pet):
        super().__init__()
        self.pet = pet
        self.setWindowTitle("Mori Companion · 互动设置")
        self.resize(660, 800)
        self._refreshing = False
        layout = QVBoxLayout(self)
        intro = QLabel("基本动画始终保留。其他模块分别保存娱乐／专注的选择，切换模式时自动应用。\n"
                       "默认专注只开基本动画；你也可以为专注模式开启喜欢的模块。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.mode = QComboBox()
        self.mode.addItem("专注模式", "focus")
        self.mode.addItem("娱乐模式", "entertainment")
        mode_form = QFormLayout()
        mode_form.addRow("当前模式", self.mode)
        layout.addLayout(mode_form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        content = QVBoxLayout(body)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        modules = QGroupBox("按模式保存模块开关")
        grid = QGridLayout(modules)
        grid.addWidget(QLabel("模块"), 0, 0)
        grid.addWidget(QLabel("娱乐"), 0, 1)
        grid.addWidget(QLabel("专注"), 0, 2)
        self.module_checks = {}
        for row, (key, (name, description)) in enumerate(MODULES.items(), 1):
            label = QLabel(name + "\n" + description)
            label.setWordWrap(True)
            grid.addWidget(label, row, 0)
            for column, mode in enumerate(("entertainment", "focus"), 1):
                check = QCheckBox("开启")
                check.setAccessibleName(("娱乐" if mode == "entertainment" else "专注") + "模式：" + name)
                check.setToolTip(description)
                check.setEnabled(key != "basic")
                self.module_checks[mode, key] = check
                grid.addWidget(check, row, column)
        content.addWidget(modules)
        defaults = QPushButton("当前模式恢复默认模块")
        defaults.clicked.connect(self.reset_modules)
        content.addWidget(defaults)
        system = QGroupBox("系统互动")
        system_form = QFormLayout(system)
        system_note = QLabel("上方系统互动模块统一控制以下全部功能，无需逐项开启。\n"
                             "娱乐模式可自主拉动或最小化窗口；滚动、涂鸦和标记由你发起。")
        system_note.setWordWrap(True)
        system_form.addRow(system_note)
        self.system_action = QComboBox()
        for key, label in SYSTEM_SCENES.items():
            self.system_action.addItem(label, key)
        system_form.addRow("操作", self.system_action)
        self.doodle_kind = QComboBox()
        for key, label in (("star", "星星"), ("circle", "圈圈"), ("arrow", "箭头"),
                           ("whale", "小鲸鱼"), ("text", "短句")):
            self.doodle_kind.addItem(label, key)
        system_form.addRow("涂鸦内容", self.doodle_kind)
        self.doodle_text = QLineEdit()
        self.doodle_text.setMaxLength(32)
        self.doodle_text.setPlaceholderText("写一句短句，画完后会自动消失")
        system_form.addRow("短句", self.doodle_text)
        self.system_execute = QPushButton("让 Mori 操作")
        self.system_execute.clicked.connect(self.execute_system)
        system_form.addRow(self.system_execute)
        hint = QLabel("涂鸦／滚动会让你点击选择位置，右键或 Esc 取消。\n"
                      "字迹完成后停留 6 秒，再淡出；窗口标记跟随窗口移动和缩放。")
        hint.setWordWrap(True)
        system_form.addRow(hint)
        self.minimize_interval = QSpinBox()
        self.minimize_interval.setRange(60, 3600)
        self.minimize_interval.setSuffix(" 秒")
        system_form.addRow("最小化的最短间隔", self.minimize_interval)
        restore = QPushButton("恢复上次被 Mori 最小化的窗口")
        restore.clicked.connect(pet.interactions.restore_window)
        self.restore = restore
        system_form.addRow(restore)
        content.addWidget(system)
        speech = QGroupBox("气泡")
        speech_form = QFormLayout(speech)
        self.bubble_opacity = QSpinBox()
        self.bubble_opacity.setRange(20, 100)
        self.bubble_opacity.setSuffix(" %")
        self.bubble_opacity.setAccessibleName("气泡不透明度")
        speech_form.addRow("不透明度", self.bubble_opacity)
        speech_note = QLabel("气泡默认允许鼠标穿透。Codex 原文气泡悬停满 3 秒后可点击；\n"
                            "移出圆气泡立即恢复穿透。日常台词始终不挡点击。")
        speech_note.setWordWrap(True)
        speech_form.addRow(speech_note)
        content.addWidget(speech)
        details = QGroupBox("节奏设置")
        form = QFormLayout(details)
        self.checks = {}
        for key, label in (("dialogue", "主动表达模块中允许台词"),):
            check = QCheckBox(label)
            self.checks[key] = check
            form.addRow(check)
        self.pace = QComboBox()
        for key, label in (("auto", "按距离选择"), ("walk", "散步"), ("trot", "小跑"), ("run", "快跑")):
            self.pace.addItem(label, key)
        form.addRow("移动步速", self.pace)
        self.rest_time = QSpinBox()
        self.rest_time.setRange(10, 300)
        self.rest_time.setSuffix(" 秒")
        form.addRow("打盹停留时间", self.rest_time)
        self.interval = QSpinBox()
        self.interval.setRange(5, 300)
        self.interval.setSuffix(" 秒")
        self.interval.setToolTip("实际安静间隔在这个值上增加 15–35%；不延迟主动点击和工作反馈。")
        form.addRow("主动剧情基础间隔", self.interval)
        self.life_idle = QSpinBox()
        self.life_idle.setRange(30, 300)
        self.life_idle.setSuffix(" 秒")
        self.life_idle.setAccessibleName("空闲生活剧情间隔")
        self.life_idle.setToolTip("随机间隔为此值的 0.5–1.5 倍；默认约 30–90 秒。")
        form.addRow("空闲生活剧情间隔", self.life_idle)
        self.life_work = QSpinBox()
        self.life_work.setRange(60, 600)
        self.life_work.setSuffix(" 秒")
        self.life_work.setAccessibleName("工作小动作间隔")
        self.life_work.setToolTip("随机间隔为此值的 0.5–1.5 倍；默认约 1–3 分钟。")
        form.addRow("工作小动作间隔", self.life_work)
        life_note = QLabel("工作时偶尔发呆、揉眼或张望，几秒后恢复工作表情。\n"
                           "空闲时展开生活小剧情，并记得近期吃过、歇过或与你互动的小事。")
        life_note.setWordWrap(True)
        form.addRow(life_note)
        self.weights = {}
        for key, label in (("top_weight", "置顶前台权重"), ("foreground_weight", "前台权重"),
                           ("background_weight", "后台权重")):
            spin = QSpinBox()
            spin.setRange(0, 100)
            self.weights[key] = spin
            form.addRow(label, spin)
        content.addWidget(details)
        priority = QLabel("置顶前台：最上层普通窗口，优先识别显式置顶；前台：其他未最小化窗口；"
                          "后台：最小化运行的程序。默认权重 8:3:1，0 表示不选择该层。\n"
                          "每段剧情锁定一个目标，结束后再选择。后台问候不会打开窗口。")
        priority.setWordWrap(True)
        content.addWidget(priority)
        content.addWidget(QLabel("排除的软件（每行一个应用标识或关键词，如 konsole、firefox）："))
        self.excluded = QTextEdit()
        self.excluded.setAcceptRichText(False)
        self.excluded.setMaximumHeight(90)
        content.addWidget(self.excluded)
        row = QHBoxLayout()
        self.scene = QComboBox()
        for key, label in SCENES.items():
            if key not in SYSTEM_SCENES:
                self.scene.addItem(label, key)
        preview = QPushButton("立即体验")
        row.addWidget(self.scene)
        row.addWidget(preview)
        layout.addLayout(row)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setTextFormat(Qt.PlainText)
        layout.addWidget(self.feedback)
        self.saved = QLabel("选择即时生效，并保存到本机；娱乐与专注分别记忆。")
        self.saved.setWordWrap(True)
        layout.addWidget(self.saved)
        self.refresh()
        self.mode.currentIndexChanged.connect(self.apply)
        for check in self.checks.values():
            check.toggled.connect(self.apply)
        for (mode, key), check in self.module_checks.items():
            if key != "basic":
                check.toggled.connect(self.apply)
        self.pace.currentIndexChanged.connect(self.apply)
        self.rest_time.valueChanged.connect(self.apply)
        self.interval.valueChanged.connect(self.apply)
        self.life_idle.valueChanged.connect(self.apply)
        self.life_work.valueChanged.connect(self.apply)
        self.minimize_interval.valueChanged.connect(self.apply)
        self.bubble_opacity.valueChanged.connect(self.apply)
        for spin in self.weights.values():
            spin.valueChanged.connect(self.apply)
        self.excluded.textChanged.connect(self.apply)
        preview.clicked.connect(self.preview)
        self.system_action.currentIndexChanged.connect(self.refresh_system_fields)
        self.doodle_kind.currentIndexChanged.connect(self.refresh_system_fields)
        self.refresh_system_fields()
        pet.interactions.message.connect(self.feedback.setText)
        pet.interactions.changed.connect(self.refresh)

    def refresh(self):
        self._refreshing = True
        preferences = self.pet.preferences
        self.mode.setCurrentIndex(self.mode.findData(preferences.mode))
        for key, check in self.checks.items():
            check.setChecked(getattr(preferences, key))
        for (mode, key), check in self.module_checks.items():
            check.setChecked(preferences.module_enabled(key, mode))
        self.pace.setCurrentIndex(self.pace.findData(preferences.movement_pace))
        self.rest_time.setValue(preferences.rest_seconds)
        self.interval.setValue(preferences.interval_seconds)
        self.life_idle.setValue(preferences.life_idle_seconds)
        self.life_work.setValue(preferences.life_work_seconds)
        self.minimize_interval.setValue(preferences.minimize_interval_seconds)
        self.bubble_opacity.setValue(preferences.bubble_opacity)
        for key, spin in self.weights.items():
            spin.setValue(getattr(preferences, key))
        apps = "\n".join(preferences.excluded_apps)
        if not self.excluded.hasFocus() and self.excluded.toPlainText() != apps:
            self.excluded.setPlainText(apps)
        self.restore.setEnabled(bool(self.pet.interactions.restore_identity))
        self.system_execute.setEnabled(preferences.module_enabled("system"))
        self.feedback.setText(self.pet.interactions.desktop.reason)
        self._refreshing = False

    def apply(self, *_args):
        if self._refreshing:
            return
        preferences = replace(self.pet.preferences, mode=self.mode.currentData(),
            module_profiles={mode: {key: self.module_checks[mode, key].isChecked() for key in OPTIONAL_MODULES}
                             for mode in ("entertainment", "focus")},
            movement_pace=self.pace.currentData(), rest_seconds=self.rest_time.value(),
            interval_seconds=self.interval.value(), minimize_interval_seconds=self.minimize_interval.value(),
            life_idle_seconds=self.life_idle.value(), life_work_seconds=self.life_work.value(),
            bubble_opacity=self.bubble_opacity.value(),
            excluded_apps=[item.strip()[:160] for item in self.excluded.toPlainText().splitlines()
                           if item.strip()][:64], **{key: check.isChecked() for key, check in self.checks.items()},
            **{key: spin.value() for key, spin in self.weights.items()})
        try:
            self.pet.interactions.apply(preferences)
            self.saved.setText("已保存：模块开关、互动节奏和气泡不透明度。")
        except OSError as error:
            self.feedback.setText("设置保存失败：" + str(error))

    def preview(self):
        scene = self.scene.currentData()
        if not self.pet.preferences.module_enabled(SCENE_MODULES[scene]):
            self.feedback.setText("请先开启当前模式对应的模块。")
            return
        self.hide()
        # Let the previous ordinary application regain focus before selecting a target.
        QTimer.singleShot(600, lambda: self.pet.interactions.start(scene))

    def refresh_system_fields(self, *_args):
        drawing = self.system_action.currentData().startswith("doodle-")
        self.doodle_kind.setEnabled(drawing)
        self.doodle_text.setEnabled(drawing and self.doodle_kind.currentData() == "text")

    def execute_system(self):
        if not self.pet.preferences.module_enabled("system"):
            self.feedback.setText("请开启当前模式的系统互动模块。")
            return
        scene, kind, text = self.system_action.currentData(), self.doodle_kind.currentData(), self.doodle_text.text()
        if scene.startswith("doodle-") and kind == "text" and not text.strip():
            self.feedback.setText("请先填写短句。")
            return
        self.hide()
        QTimer.singleShot(600, lambda: self.pet.interactions.system.request(scene, kind, text))

    def reset_modules(self):
        mode = self.pet.preferences.mode
        defaults = InteractionPreferences(mode=mode)
        preferences = self.pet.preferences
        for key in OPTIONAL_MODULES:
            preferences = preferences.with_module(key, defaults.module_enabled(key))
        try:
            self.pet.interactions.apply(preferences)
            self.saved.setText("已恢复当前模式的默认模块，另一个模式的选择保持原样。")
        except OSError as error:
            self.feedback.setText("设置保存失败：" + str(error))

    def closeEvent(self, event):
        event.ignore()
        self.hide()

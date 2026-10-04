# Mori Companion 基础架构

核心处理通用事件，来源仅用于标识会话与显示。任何来源的同一种事件都走同一套聚合、情绪、动画与表现逻辑。Codex CLI 的专用状态名称、Hook 输入及启动细节完全位于适配层。

## 事件协议 v1

`POST /v1/events`，`Content-Type: application/json`，`Authorization: Bearer <token>`。客户端从个人数据目录的 `bridge.json` 读取本次运行的 URL 与 token；不要硬编码端口。请求大小最多 64 KiB。不接受浏览器 Origin、代理转发或远程地址。

```json
{
  "schema_version": 1,
  "source": "my-tool",
  "session_id": "workspace-session",
  "task_id": "task-42",
  "event_id": "unique-id-for-deduplication",
  "timestamp": 1791000000.0,
  "type": "thinking",
  "summary": "正在处理任务"
}
```

必需字段：`source`、`session_id`、`type`。其余字段可省略；本机客户端自动生成 event_id 和当前时间。`task_id` 用于隔离新旧任务，建议每次任务都提供。timestamp 为 Unix 秒；event_id 跨重试保持一致。字符串限制：标识 160 字符，summary 240 字符，拒绝控制字符及未定义字段。

事件：

| 事件 | 含义 | 默认表现 |
| --- | --- | --- |
| task_started | 新任务开始 | 思考 |
| thinking | Agent 正在推理或处理结果 | 思考 |
| generating | 生成内容或执行工具/构建等工作 | 吃饭动作 |
| waiting_input | 等待用户输入或权限确认 | 提醒动作 |
| task_completed | 当前任务/轮次结束 | 欢呼 |
| task_failed | 明确的任务失败 | 错误反馈 |
| idle | 当前来源待命 | 原待机循环 |

`generating` 是通用工作状态，不要求是模型生成文字。核心不知道 Hook、终端、模型、工具命令或 CI Job 的具体含义。失败只有在适配器有明确失败证据时发送。

桥接层只将规范化事件排入队列。首个入队事件发出一次 Qt 唤醒信号，后续事件合并等待主线程批量处理。角色更新、窗口操作和计时器操作全部在 Qt 主线程。队列满返回 429；不自动无限重试。坏插件返回通用 500 错误，后续合法请求继续处理。

同一 `(source, session_id)` 中时间更旧的事件、重复 event_id、已退休 task_id、已结束任务的迟到工作事件会被忽略。会话优先级：等待输入 > 明确失败 > 工作中 > 思考/任务开始 > 完成 > 待命。用户正在点击、拖拽、导入或登场时，最新工具反应等待交互结束再呈现；不会覆盖交互姿态。隐藏时保留最新状态但不弹气泡。

## 扩展 Agent Adapter

路径一：工具自身直接发送规范化事件，不需要向 Mori 安装 Python 插件。

```python
from companion.client import send_event
from companion.events import CompanionEvent

send_event(CompanionEvent(
    source="git", session_id="repo-session", task_id="commit-1",
    type="task_completed", summary="提交已完成",
).to_dict())
```

跨语言实现也可以读取 `bridge.json` 后向 `/v1/events` 发相同 HTTP 请求。例如 VS Code 扩展可以在任务开始与结束回调中发送事件，CI 本地代理可以转译远端构建状态。远端 CI 不直接访问本机桥；需要本地适配器明确接入。

路径二：实现 `ToolAdapter`，把来源格式转换为事件。返回 `None` 表示不相关事件。

```python
from companion.events import CompanionEvent

class MyAdapter:
    id = "my-tool"

    def normalize(self, payload):
        return CompanionEvent(
            source=self.id,
            session_id=payload["session"],
            task_id=payload.get("task", ""),
            type={"busy": "generating", "done": "task_completed"}[payload["state"]],
        )
```

插件 `pyproject.toml`：

```toml
[project.entry-points."mori_companion.adapters"]
my-tool = "my_plugin:MyAdapter"
```

安装插件后，在 Mori 个人数据目录创建 `settings.json`：

```json
{"enabled_adapters": ["codex", "my-tool"]}
```

重启后插件接收 `POST /v1/adapters/my-tool` 原始 payload。默认不加载未启用的 entry point。插件代码在进程内执行，因此适合个人可信插件；当前阶段没有沙箱、后台插件进程、动态热更新或插件市场。

## 角色进化接口

`character/contracts.py` 中预留了以下边界：

| 接口 | 职责 |
| --- | --- |
| CharacterDefinition / CharacterSystem | 角色身份、素材映射及系统组合 |
| EmotionSystem | 通用工作状态转换为情绪及强度 |
| AnimationStateMachine | 工作状态与情绪转换为动画 key、循环标记及持续时间 |
| AssetManager | 根据动画 key 解析角色素材 |
| CharacterRenderer | 动画切换、情绪更新、暂停、恢复、释放的渲染后端生命周期 |

默认使用 `ActivityEmotionSystem` 与 `ActivityAnimationStateMachine`。前者只在通用状态变化时设定情绪，待机时根据单调时钟衰减，重复轮询不会重复触发情绪；后者将七种工作状态和原鼠标互动映射为动画 key、循环标记与持续时间。角色系统不读取事件来源。

`character/dialogue.py` 的 `AmbientDialogue` 独立选择日常台词，只接收工作状态，不读取动画 key。表现层使用独立低频计时器调度气泡，工作时也可发呆想吃饭；播放台词不改动画和任务状态。待机进食／思考等专属入场台词通过 `IDLE_ANIMATION_SPEECH` 显式绑定，剧情保留 `Step.speech`。工具事件台词单独延迟到直接互动结束后派发，单纯更换动画不重复播报。日常台词优先避让动作台词和任务提醒，遵循当前模式的主动表达开关。

`character/life.py` 定义 36 段生活动作及有时效的 `LifeMemory`，`presentation/living.py` 独立调度工作短段和空闲完整段。台词来自打包的 `talk/life.txt`，按六类主题、工作／空闲和前情分组；工作事件台词在重复轮询期间保持稳定。工作短段不改事件状态，结束后恢复最新工作表情，重要提醒和用户互动可立即中止。默认角色消费新姿势，自定义角色使用自己的画面回退。运行时仅缓存当前段至多六张小 PNG，80ms 绘制计时器只在生活动作期间运行；记忆仅保存在当前进程中并自然过期。具体数量、间隔和素材来源见 [生活语料与动画](living-companion.md)。

`PngAssetManager` 解析 PNG/GIF 路径并限制素材目录，默认角色优先选 GIF，缺失时回退对应 PNG。九种默认 PNG/GIF 均为 280×230 画布，动作切换与静态回退保持窗口尺寸一致。`presentation/renderers.py` 的 `RasterRenderer` 实现 `CharacterRenderer`，使用一个 `QMovie.CacheNone` 解码器，无全部帧缓存。情绪强度在原速度的 80%–100% 范围内调整 GIF 速度，不超过制作帧率；相同 AnimationState 不会重启；一次性动作的限时计时器与 GIF 均在隐藏时暂停，恢复后继续剩余时间。画面只改变窗口内部绘制，固定 GIF 画布避免逐帧改变窗口尺寸与位置。

渲染后端按 CharacterDefinition.renderer 显式选择，未来插件可注册 `mori_companion.renderers` entry point。工厂接收 assets、display(QPixmap 回调)、placeholder、parent，并返回实现 set_animation / set_emotion / pause / resume / dispose 的后端。Live2D、Spine、Rive SDK 目前均未加载；模型资产描述与参数绑定在未来后端及专用资源加载器中扩展，通用事件层无需修改。

`character/skins.py` 管理用户目录的皮肤、持久化选择和原子导入。`presentation/skin_panel.py` 解码验证 PNG/GIF 后复制到新皮肤，不移动源文件。GIF 只允许 ≤512×512、≤120 帧、每帧 ≥80ms；素材 ≤2 MiB，最多 32 个皮肤。启动验证自定义皮肤，损坏时使用原角色。

皮肤配置示例（实际配置应包含九种动作）：

```json
{
  "schema_version": 1,
  "id": "my-character",
  "name": "我的角色",
  "renderer": "raster",
  "animations": {
    "nothing": "nothing.gif", "eat": "eat.gif", "think": "think.gif",
    "attention": "attention.gif", "cheerup": "cheerup.gif", "wrong": "wrong.gif",
    "touch": "touch.gif", "pickup": "pickup.gif", "true": "true.gif"
  }
}
```

目录内资源可为 PNG 或 GIF，禁止越出皮肤目录的相对路径或符号链接。导入后使用新的本地 id，避免与原皮肤冲突。用户选择保存在 `character-selection.json`，皮肤位于 `characters/`；恢复默认不会删除个人皮肤。

运行时不调用生图服务。九张默认动作原画在开发期用内置 image_gen 重绘，对齐已确认的 interaction/peek.png 角色设计；完整提示词与画布参数记录在 move/prompts.json。高分辨率生成源图与旧素材分别存于 output/character-refresh/sources/ 和 originals/move/。`scripts/prepare_character_assets.py` 仅裁掉空白透明边距、等比缩小并放入统一画布，保留生成 alpha；`scripts/build_gifs.py` 只遍历默认九种动作映射，以形变与位移复现 GIF，不会递归处理互动原画或存档。Pillow 仅是 assets 可选构建依赖。`asset_library/interaction/` 四张 PNG 同样由内置生图生成，提示词与角色锁定词记录在该目录的 prompts.json。

## 娱乐与专注模式

`companion/preferences.py` 负责纯数据偏好与持久化，读取／更新 `settings.json` 的 `interactions` 字段并保留适配器及其他配置。首次使用专注模式，最小化默认关闭。模式只由用户在设置／托盘／右键菜单切换，工作事件不自动切换模式。

`presentation/interactions.py` 将互动组织为有时限的剧情步骤。每段动作包含姿势、目的地、台词和可选的最小化操作；步间衔接，全部窗口操作在 Qt 主线程。绘制使用四张缩小后的 PNG 和运行时平移／旋转，25 FPS 计时器仅在剧情期间运行，结束后释放；不会缓存所有动画帧。自定义角色使用自己的原动作回退，避免突然换成鲸鱼娘。

探头的遮挡使用 `QPainter.setClipRect()`，不修改系统窗口形状。每帧先用 `CompositionMode_Source` 清空透明背景，再绘制当前姿势；离开探头步骤立即解除绘制裁切，避免透明画布和桌面合成器保留上一个姿势。跟随目标更新裁切后请求重绘。

专注模式继续播放工作 GIF，所有主动台词、走动及剧情关闭。主动消息和用户触摸、拖拽等消息分别处理；模式切换清除待播主动台词，后续不补播。剧情期间优先让用户操作打断；隐藏、换角色、输入等待及失败事件均可中断。正常结束后恢复最新工作姿态和待机计时，保留新的落点；模式切换等取消操作仍恢复剧情前的位置，触摸／拖拽打断保留当前位置。

散步在当前屏幕可用区域内随机选择落点，速度与停留时间分别随机，下一段从上一落点出发。窗口剧情的离开步骤使用随机落点，鼠标互动在目标附近结束。坐姿使用缩放原画中的臀部支撑锚点计算窗口位置，并绕锚点摆动；屏幕顶部约束作用于坐点，允许上半身越过屏幕边缘，避免把坐姿推入窗口内容区。

`presentation/desktop_interaction.py` 通过 KWin 6 临时 JavaScript 和本机 session D-Bus 获取活动窗口及几何变化。窗口管理器的信号提供更新，不轮询进程／窗口内容，不读取窗口标题，也不更改全局 KWin 设置。退出或进入专注模式时卸载所有本次脚本。未支持的桌面仍提供不依赖窗口 API 的剧情。

最小化分为台词倒计时、伸手动作与执行三步。执行前由 Qt 再确认模式、独立开关、排除名单、原窗口身份和活动状态；KWin 操作时再次验证窗口仍为可最小化的普通非全屏窗口。切换目标、移动窗口、隐藏或改变模式会取消待执行操作，禁止通过模拟鼠标点击按钮。恢复仅作用于最后一次由 Mori 最小化的同一窗口标识，不会改动当前新窗口。设置面板提供软件排除关键词、两个频率和逐项体验入口。

## 生命周期与资源边界

一个个人数据目录对应一个宠物实例，第二次启动请求显示已有实例。用户退出时停止所有角色计时器与 HTTP 服务、关闭气泡/面板和托盘，并删除自身的桥描述文件。单独关闭 Codex CLI 不关闭宠物，关闭宠物也不结束 Codex CLI。

当前窗口位置与文档位于个人数据目录；不写回源码目录，不读取上游 API 密钥。保留角色台词池，旧原素材存档于项目 output/character-refresh/originals/。资料复制，避免拖入后删除开发项目原文件。

## Codex CLI 专用边界

`codex.py` 负责官方 Hook → 七种通用事件，`codex_cli.py` 负责启动终端 CLI 和会话级 Hook 配置，`catalog.py` 负责内置适配器注册。核心、角色系统和宠物窗口不判断 source 是否等于 codex。

`mori-codex` 使用真实 CLI 可执行程序，继承当前工作目录、标准输入输出和用户 CLI 参数，设置 `MORI_CODEX_CLI=1`。Hooks 首先检查此标记；未标记进程不发事件。独立 CLI 后端通过 `--no-daemon` 避免与 GUI 共享常驻后端环境。`--remote` 不受支持；已有共享会话、未通过入口启动的会话不自动发现。

当前不会抓取终端屏幕、递归读取 Codex 日志、读取 transcript、访问 GUI 进程、自动信任 Hooks、启动模型请求或改变审批决定。`Stop` 的通用 task_completed 仅指轮次结束。官方接口依据：[Hooks](https://learn.chatgpt.com/docs/hooks)、[CLI 命令](https://learn.chatgpt.com/docs/developer-commands)、[更新说明](https://learn.chatgpt.com/docs/changelog)。

## 普通 codex 终端集成

`companionctl enable-codex` 在用户选定的交互 Shell 中定义同名 `codex` 函数，转到 CLI Adapter 的启动入口。Fish 使用独立 `conf.d/mori-companion.fish`；Bash/Zsh 使用独立脚本与启动文件的一条 source 引用，修改现有启动文件前先备份。非交互 Shell 不定义函数，原 Codex 可执行文件、全局配置及 GUI 启动方式不改动。用户不需要每次记住不同命令。

该入口保留参数、终端标准输入输出与退出码，并在独立 CLI 后端注入稳定的监控 Hook 定义。首次通过 Codex `/hooks` 审查并信任，后续对话复用按定义 hash 保存的信任；不会自动修改信任存储、绕过审查或审批工具操作。已有 CLI 进程不热注入，需要重启。

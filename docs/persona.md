# Mori 人设与 LLM 系统提示词

## 文件与来源

- `src/character/prompts/mori.md`：Mori 默认人设，UTF-8 纯 Markdown 正文，可整体作为 LLM 的系统提示词。
- `src/character/prompts/upstream/qq-bridge-whale.md`：用户指定的 [qq-bridge 小鲸鱼角色卡](https://github.com/Derpyu520/qq-bridge/blob/main/roles/%E5%B0%8F%E9%B2%B8%E9%B1%BC.md)原文，逐字节保留，供追溯和对照。
- `src/character/prompts/upstream/LICENSE.txt`：该角色卡上游 MIT 许可及版权声明，随资源打包。

导入日期：2026-10-04。固定来源提交：[`b21a8ccce9b516455ee454245b1553f0630c895b`](https://github.com/Derpyu520/qq-bridge/blob/b21a8ccce9b516455ee454245b1553f0630c895b/roles/%E5%B0%8F%E9%B2%B8%E9%B1%BC.md)。修改 Mori 版本时保留上游原文；来源与维护说明放在本文，不拼入模型提示词。

## Mori 适配

保留原角色卡的十部分结构、傲娇嘴硬但心软的性格、短句接梗、日常节奏及回复示例。角色身份改为 Mori／茉伴／小鲸鱼；外观与现有桌宠的深蓝长发、圆蓝眼、女仆装和超大鲸鱼尾巴一致，角色身份独立于底层模型厂商。

默认场景改为桌面一对一陪伴。多人聊天、引用和 @ 规则只在宿主明确提供相应上下文时适用；直接提问应回应。QQ 桥接与专用搜索工具名称替换为宿主实际提供的能力，分条发送、静默和主动发言由宿主负责，模型调用内不等待。认真求助与事实问题应可靠回应，不把接梗、价格调侃或工具操作当作真实事实。

## 后续 LLM 接入

安装项目后可调用公开入口，读取不依赖当前工作目录，也不需要 Qt 或 AI SDK：

```python
from character import load_system_prompt

messages = [
    {"role": "system", "content": load_system_prompt()},
    {"role": "user", "content": "今天好累"},
]
```

将 `messages` 交给后续模型适配器；若接口使用独立的 `system` 参数，将 `load_system_prompt()` 的返回值传入该参数。源码模式需把 `src` 加入 Python 模块搜索路径。

动态状态、用户输入和历史消息由调用层单独传入；可用工具及操作许可由宿主定义。人设加载函数只读取本地文本，不发送模型请求，不写入聊天记录，也不改写 Codex 的系统提示词。本地台词库已按 Mori 的短句朋友腔重写，固定交互和聊天回应也从台词本读取；远程 LLM 对话尚未接入。维护入口及本地 TTS 导出见 [台词本说明](dialogue-scriptbook.md)。

资源已通过 `pyproject.toml` 的 `character` package-data 声明打包。日后调整人设只需编辑 `mori.md`；加载函数每次读取文件，后续调用层应在创建会话时加载一次，并按需要在新会话中重新加载。

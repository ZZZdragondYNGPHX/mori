# Mori 台词本与本地 TTS

人设依据是 [mori.md](../src/character/prompts/mori.md)。台词以熟悉的朋友口吻说话：短句、随口吐槽、轻微嘴硬，心软只偶尔露一点；不用“主人”“本小姐”，不反复强调陪伴、可靠、加油。闲聊多数一句在十字左右，操作反馈以说清楚为先。

## 编辑入口

| 文件 | 内容 | 维护方式 |
| --- | --- | --- |
| [life.txt](../src/asset_library/talk/life.txt) | 636 条场景台词，53 个分组 | 保留分组名，每句一行 |
| [fixed.txt](../src/asset_library/talk/fixed.txt) | 动画、鼠标、窗口、系统操作、本地聊天及操作提示；另含动态模板 | 固定台词每组一条，代码通过分组名读取 |
| [nothing.txt](../src/asset_library/talk/nothing.txt) | 8 条兼容待机台词 | 纯正文，每句一行 |
| [touch.txt](../src/asset_library/talk/touch.txt) | 8 条兼容触摸台词 | 纯正文，每句一行 |

台词文本是显示及朗读的正文，不写外层引号、表情、括号动作、情绪标签或配音说明。“？”改为“干嘛？”“啊？”等可读的口语；停顿用逗号，疑问和抗议用相应标点。低概率的“用户”称呼可以保留，避免每句叫人。

生活台词沿用原有触发条件：`common` 不要求当前在做某动作，`work` 不声称替用户干活，`idle` 只在空闲使用，`memory` 依赖实际发生的近期前情。`scene.*.start/end` 同时服务工作短段和完整剧情，例如困倦收尾只说清醒了一点，不统一声称睡过。文件收纳反馈和任务反馈按实际事件表达，不把一轮结束说成所有问题都已解决。

## 导出

在仓库根目录运行：

```bash
python scripts/export_dialogue.py
```

默认写入 `output/tts-scriptbook/`；也可用 `--output-dir /你的导出目录` 指定位置。只读取本地打包台词并写出文本，不加载 TTS 模型或生成音频。

当前清单包含 **718 条去重后的固定台词和 4 条动态模板**。兼容常量与兼容皮肤台词也包含在内；不是只导出生活碎碎念。

| 导出文件 | 用途 |
| --- | --- |
| `manifest.json` | 完整台词本、整体声音指导、固定台词及独立的动态模板 |
| `lines.jsonl` | 批量合成输入，一行一个 JSON 对象，只有可直接朗读的固定台词 |
| `sentences.txt` | 与 JSONL 同顺序的纯台词，方便通读及只接受文本的 TTS 工具 |

每个 JSONL 对象提供：

- `id`：`mori_` 加正文的 SHA-256 前 16 位；插入、删除或重排其他台词不影响已有编号，修改正文会产生新编号。
- `text`、`tts_text`：气泡正文及实际合成文本，目前一致。
- `groups`：所有使用这句台词的场景；重复正文只合成一次。
- `language`：`Chinese`。
- `instruct`：整体声音方向加场景语气，只作为配音指导，不拼到合成文本里。
- `audio_file`：建议输出路径 `voices/<id>.wav`，相对于导出目录。

遍历 `lines.jsonl`，将 `tts_text` 交给你的本地 TTS，并按 `audio_file` 保存。支持语气指令的模型可以另传 `instruct`；不支持时只用正文。先选几条触摸、困倦和任务提示试听，确定同一个声音后再全量合成。不同声音或合成参数建议分目录保存；编号只识别文本，不识别音色或参数变化。

## 动态内容

`template.*` 不进入 JSONL 和纯台词文件：资料数量 `{count}`、桌面输入诊断 `{error}` 需要运行时填充，不能把占位符原样读出来。它们保留在 `manifest.json` 的 `templates` 中。后续可用本地 TTS 在内容确定后合成，或只显示文本。

真实工具状态、资料搜索结果、Codex 原文和未来 LLM 回复随运行时变化，不属于有限的预录台词本；需要语音时另走运行时合成。后台窗口问候改为不含应用名的固定句，方便预录，也避免把“在后台”说成已经确认软件正在工作。

这次提供台词及音频映射清单；桌宠的音频播放尚未接入。人设提示词和原有动作调度规则沿用当前内容。

## 本地验证

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_dialogue.py'
PYTHONPATH=src python -m unittest discover -s tests -p 'test_scriptbook.py'
```

修改台词后重新导出，再按正文编号复用或补做音频。新增固定场景时，在 `fixed.txt` 增加分组，通过 `fixed_line("分组后缀")` 引用，避免继续往界面代码里写台词。

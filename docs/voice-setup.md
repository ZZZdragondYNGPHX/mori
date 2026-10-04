# Mori 声音选择

重要事件提示使用 Qwen VoiceDesign 的 **B：清亮灵动，偏少女感**。
完成提示直接沿用用户选中的 B 文件，保存在
`output/voice-selected-qwen/previews/task_completed.wav`。
播放顺序为系统提示音、180ms 间隔、角色语音；日常点缀动画保持安静。

等待选择和任务失败的台词分别是“这儿得你选一下”“出错了，过来看下”。
两条生成请求已写入 `output/voice-selected-qwen/pending-generation.json`。
用户要求 Qwen 补全留到新对话，本次不继续生成。它们沿用 B 的声线描述，
但 VoiceDesign 在不同文本间仍可能改变音色，生成后需要试听。

后续新对话需要补生成时，可在能访问 NVIDIA GPU 的本机终端执行：

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 \
  /home/henry/.cache/mori-tts/qwen3-1.7b/venv/bin/python \
  scripts/build_qwen_voice_previews.py \
  --model-dir /home/henry/.cache/mori-tts/qwen3-voicedesign-1.7b/model \
  --manifest output/voice-selected-qwen/pending-generation.json
```

实时对话的 Kokoro 原声线及两条替代声线均未通过用户试听：偏成熟、朗读慢，
此前还有逐字感。保留下载文件，后续优先试免注册、免 Key 的在线语音。

快速对话 TTS 的听感候选选定为 [edge-tts](https://github.com/rany2/edge-tts)
的晓伊女声，它调用 Edge 在线朗读服务。
这与需要账号、Key 的 Azure Speech 云 API 是两种接入方式。
它支持语速、音高、音量调整，无法通过人设提示词控制情绪。
最终试听在 `output/edge-dialogue-study/selected.wav`：“搞定”采用用户认可的
清楚开头，“还得是我”采用用户偏好的更清亮、更快版。该短句基于缓存音频
处理，不代表长对话统一加快。在线脚本对长对话先使用原速与 `+10Hz` 音高，
这两个 API 参数仍待实际试听，不能直接等同于缓存试听的处理参数。

本次本地环境导入检查通过，但在线请求未在 15 秒内完成；没有新生成的
在线试听文件，也未验证响应速度。试验记录见同目录 `trial.json`。
旧试听已清理，最终试听、重建所需缓存源音频和用户选择记录保留。

最小本地检查通过：最终音频为有效的 24kHz 单声道 PCM16 WAV，非静音、
无满幅削波；开头与用户确认清楚的版本逐字节一致，后半句采用所选快版参数。
Qwen B 保留文件与原文件逐字节一致；后续 Qwen 生成的提示音源已收至同目录。

无需联网即可重建最终试听：

```bash
python scripts/build_selected_edge_preview.py
```

在可以联网的本机终端生成试听：

```bash
/tmp/mori-voice-preview-venv/bin/python scripts/synthesize_edge.py
```

生成结果为 `output/edge-dialogue-study/xiaoyi.wav`；同名 JSON 记录首个音频块
到达时间和整句合成耗时。脚本整句提交文本，不逐字请求；音频块可以用于后续
流式播放，但目前脚本只保存文件，桌面应用尚未接入音频播放。

如需比较晓晓，使用：

```bash
/tmp/mori-voice-preview-venv/bin/python scripts/synthesize_edge.py \
  --voice zh-CN-XiaoxiaoNeural --output output/edge-dialogue-study/xiaoxiao.wav
```

偏好记录保存在 `output/voice-preferences.json`。输出清理明细见
`output/cleanup-report.json`；动画构建仍引用的源目录保留。
桌面音频播放尚未接入，Qwen 补全按用户安排留到新对话。

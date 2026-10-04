# 第六批：系统互动与临时笔迹

新增 `system-pen`、`system-grip`、`system-reach` 三组四张关键姿势，使用内置 imagegen 工具生成透明原画。运行时只读取本地 PNG，不调用生图服务。

- 最终原画：[sources/system-poses-v2.png](sources/system-poses-v2.png)
- 初稿与提示：[prompt.txt](prompt.txt)、[sources/system-poses.png](sources/system-poses.png)
- 运行素材：`src/asset_library/motion/system-{pen,grip,reach}-{0,1,2,3}.png`
- 导出：`.venv/bin/python scripts/build_system_assets.py`，仅裁剪、缩放与对齐，不重绘角色。
- 清单累计 132 张 PNG、60 段动作，之前五批资源保留。

最终修正提示：保持八张完整角色，精确四列两行；每格缩小角色并保留透明留白；拿笔的中间两张向右伸臂书写，笔尖斜向右下；收手姿势不持笔。保留原角色脸、蓝色渐变长发、女仆装和完整鲸鱼尾巴。无文字、窗口或背景，真实 alpha 透明。

屏幕笔迹由 Qt 原生路径逐段绘制，不写入其他应用。拿笔和抓握姿势的笔尖／手掌锚点保存在 `clips.json`。窗口位移使用真实几何回报，笔迹使用共享轨迹。字体涂鸦描绘字形轮廓，汉字不按真实笔顺绘制。

向上触碰原画：[sources/system-reach.png](sources/system-reach.png)。最终修正将第三张的指尖抬到最高发梢上方，用于屏幕顶沿按钮，保留全身轮廓。

操作区预览：[settings-panel.png](settings-panel.png)。模块开关仍在上方的娱乐／专注模块表中，操作区不提供各功能独立开关。

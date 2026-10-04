# 来源与素材说明

Mori Companion 基于 kmjs1915 的 [CeTaPet-AI-DeskTopPet-DeepSeek](https://github.com/kmjs1915/CeTaPet-AI-DeskTopPet-DeepSeek) 重构，保留仓库历史、上游 MIT LICENSE 与作者版权声明。参考上游提交可以通过 `git log` 查看。

上游鲸鱼娘 PNG 与 touch / nothing 台词最初随重构迁入；台词仍位于 `src/asset_library/talk/`。九张旧 PNG 与基于它们制作的旧 GIF 已原样存档于 `output/character-refresh/originals/move/`。存档中的 attention/attention.png 对应上游 query/nomoney.png，仅改变路径。桌宠交互窗口代码从原 main.py 拆分并修改。

当前 `src/asset_library/move/` 的九张 PNG 为内置 image_gen 工具生成的新原画，以已确认的 `src/asset_library/interaction/peek.png` 为角色与画风参考、以上游动作原图为动作参考。保留角色设定与动作含义，统一新版表情、配色与超大鲸鱼尾巴。九个新 GIF 基于这些新原画制作，通过轻微形变、摇摆和位移组成动画，可通过 scripts/build_gifs.py 重建。生成提示词与处理参数见 move/prompts.json；高分辨率生成源图见 output/character-refresh/sources/。

四张 `src/asset_library/interaction/` 原画同样由内置 image_gen 工具生成，相关提示词见 interaction/prompts.json。新素材与上游存档分别记录，未将生成图标记为上游原图。

上游 README 将角色素材描述为 ChatGPT AI 生成素材，并另附个人学习使用说明。本项目保留来源记录；当前只面向用户请求的个人桌面陪伴用途。

Mori Companion 是此重构版本的新名称，与上游原项目名称及任何 AI 厂商品牌分开。

## 小鲸鱼人设提示词

`src/character/prompts/upstream/qq-bridge-whale.md` 复制自 Derpyu520 的 [qq-bridge / roles/小鲸鱼.md](https://github.com/Derpyu520/qq-bridge/blob/b21a8ccce9b516455ee454245b1553f0630c895b/roles/%E5%B0%8F%E9%B2%B8%E9%B1%BC.md)，固定提交为 `b21a8ccce9b516455ee454245b1553f0630c895b`；`src/character/prompts/mori.md` 是用于 Mori 桌面陪伴的人设适配版本。Copyright (c) 2026 Derpyu520 and qq-bridge / QSH contributors。上游 MIT 许可全文保留在 `src/character/prompts/upstream/LICENSE.txt`，随提示词资源分发；具体适配说明见 [Mori 人设](docs/persona.md)。

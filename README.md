# SandEvo（EvoLab｜演化岔路）

SandEvo 是面向 DGX Spark 黑客松构建的交互式演化实验。用户会连续经历三轮选择：先看当前生物和环境，再选择环境变化、偶发事件与演化方向；系统继承上一代谱系，生成下一阶段图片，并在恰当的位置解释对应的生物学知识。

> 这不是科研级演化预测。项目输出是以自然选择、环境压力和性状权衡为约束的模拟假说与艺术生成结果。

## 核心体验

```text
当前生物与环境图
  → 选择环境变化、偶发事件和演化方向
  → Step 3.7 Flash 生成严格结构化的下一代状态
  → 本地规则检查前置条件、收益、代价与证据边界
  → DGX Spark 上的 ComfyUI + FLUX 生成下一代图片
  → 命中知识卡，或明确说明这是受约束的推演
  → 把新状态带入下一轮，最终形成四格谱系胶片
```

项目的核心区别不是“让 AI 随机画怪兽”，而是让生成过程显式呈现：

```text
环境 → 选择压力 → 性状变化 → 适应收益 → 生存代价
```

## 当前状态

- [x] 在 DGX Spark 上原样复现官方 OpenClaw + Ollama + ComfyUI Workshop
- [x] 验证本地 Qwen3.6 35B、ComfyUI、FLUX 与 Skill 图片生成链路
- [x] 完成 `step-3.7-flash + high + strict JSON Schema` 真实规划链路
- [x] 完成 Evolution Skill、固定阶段契约、路线槽位和冲突规则
- [x] 生成三张连续阶段图与 1920×1400 演化故事板
- [x] 完成 OpenClaw 自然语言触发 → `MEDIA:` 的端到端演示
- [x] 完成三轮有状态 HTTP API、分支兼容过滤和失败恢复
- [x] 完成可交互 Demo UI、真实开场图、等待态、谱系胶片与知识弹层
- [x] 完成轻量演化知识图谱、知识卡、权威来源与未来压力边界
- [x] 完成 DGX Spark 三轮真实 Step + FLUX 串联验收
- [x] FLUX.2 Klein 4B 完成四世界 8 组盲评、真实三轮和 FLUX.1 回退验收
- [x] HunyuanVideo-1.5 在 DGX Spark 生成并保留一段 3.375 秒浅海 I2V 成片

比赛主入口是三轮网页体验。Step 3.7 Flash 负责云端严格结构化规划；DGX Spark 本地运行会话 API、规则校验、知识检索和 ComfyUI + FLUX。OpenClaw 故事板与 CLI 链路继续保留为已验证回退。

## 项目结构

```text
skills/evolution/       Evolution Skill、单轮交互引擎、严格 Schema、目录与测试
knowledge/              演化知识图谱、权威来源与确定性适配器
demo-ui/                无构建依赖的交互页面与 Python HTTP 服务
demo-assets/interactive/ 正式开场环境图
demo-assets/video/      已验收的浅海视频、海报与生成记录
demo-assets/fixtures/   旧故事板链路的冻结 Manifest
notebooks/              官方 Workshop 安全副本与说明
```

## 本地验证

```bash
python3 -m unittest discover -s skills/evolution/tests -v
python3 -m unittest discover -s knowledge/tests -v
python3 -m py_compile demo-ui/server.py skills/evolution/interactive_engine.py knowledge/knowledge_adapter.py
bash -n skills/evolution/run_helper.sh
node --check demo-ui/app.js
```

## 图像渲染与视频留档

FLUX.1 和 FLUX.2 Klein 使用两套独立工作流。FLUX.1 仍是没有额外配置时的兼容基线；DGX 比赛实例可以显式优先使用已经通过门禁的 Klein，并在失败时回到 FLUX.1：

```bash
export EVOLAB_IMAGE_RENDERER=flux2-klein-4b
export EVOLAB_IMAGE_FALLBACK=flux1
```

门禁不是只看一张“好看的图”。固定测试覆盖四个世界、每个世界两个种子，再检查盲评结果、浅海连续三轮和故障回退：

```bash
python3 skills/evolution/renderer_ab.py \
  --output-root output/renderer-ab \
  --comfy-url http://127.0.0.1:7000 \
  --comfy-output /path/to/ComfyUI/output
```

浅海世界的真实视频放在 [demo-assets/video/](demo-assets/video/)。它由 HunyuanVideo-1.5 480p I2V Step Distilled FP8 在 DGX Spark 上生成，规格为 848 × 480、81 帧、24 fps、8 步，冷运行耗时 242.453 秒。页面只把它当作同一场景的动态留档，并明确说明运动画面不是新的科学证据；视频生成不在现场三轮交互的必经路径上。

模型安装和视频生成均先检查文件、节点、编码器与输出边界：

```bash
python3 skills/evolution/install_hunyuan_video_models.py --help
python3 skills/evolution/video_generation.py --help
```

可复核的参数、输入哈希、运动提示、耗时、峰值内存和逐帧检查结果记录在 [视频清单](demo-assets/video/tidal-symbiosis-dgx.json)。

离线交互验收：

```bash
python3 demo-ui/server.py --host 127.0.0.1 --port 8088 --dry-run
# 浏览器打开 http://127.0.0.1:8088/demo-ui/
```

真实模式由服务端读取环境变量中的 Step Key，浏览器不会接触 Key。HTTP 静态目录只开放 `demo-ui/` 与 `demo-assets/`；`.env`、源码、运行记录和内部文档均被阻断。详见 [交互 Demo 说明](demo-ui/README.md)。

## 官方基线代码

仓库包含一份比赛组织方 Workshop Notebook 的公开安全副本：

- [notebooks/workshop-official-sanitized.ipynb](notebooks/workshop-official-sanitized.ipynb)
- [运行与安全说明](notebooks/README.md)
- [.env.example](.env.example)：Step Plan 配置模板，不包含真实密钥

该副本保留 26 个原始代码单元，移除了全部执行输出、机器地址和运行时 Token。它用于证明官方基线可复现，不代表比赛作品必须继续使用 OpenClaw。

## 技术栈

- NVIDIA DGX Spark
- 本地 Qwen3.6 35B
- ComfyUI + FLUX.1 / FLUX.2 Klein 4B
- HunyuanVideo-1.5 480p I2V Step Distilled FP8
- Agent Skill
- Step 3.7 Flash（结构化路线规划与多模态复核）
- OpenClaw
- Python + Pillow

## 开源协议

[MIT License](LICENSE)

# SandEvo（EvoLab｜演化岔路）

> 在 DGX Spark 上，把“环境改变了什么、谱系继承了什么、获得能力又付出什么代价”变成一段可以亲手推进的三轮演化实验。

[90 秒有声演示](https://github.com/boblank/DGXSparkSandEvo/raw/refs/heads/main/demo-assets/submission/evolab-90s-introduction.mp4) · [固定四阶段回放](demo-assets/submission/evolab-fixed-lineage-replay.mp4) · [最终提交说明](docs/submission.md) · [目标与提交审查](docs/final-goal-audit.md) · [“十日谈”开发征文](docs/hackathon-ten-days.md)

**最终提交版本：`v1.0.1-submission`**。免登录在线演示是上面的 90 秒成片；完整的三轮有状态交互在本地或 DGX Spark 上运行，两者展示的是同一套七世界、规则、模型路由与回退边界。

![EvoLab 七世界入口](docs/assets/evolab-final-desktop.png)

```mermaid
flowchart LR
    U["七世界与三轮选择"] --> S["场景包与谱系状态"]
    S --> P["Step 3.7 Flash<br/>严格结构规划"]
    P --> A["科学审查 Agent<br/>通过 / 修订一次 / 阻断"]
    A --> G["本地规则与证据门禁"]
    G --> R["DGX Spark<br/>Klein → FLUX.1"]
    R --> V["图像连续性审查<br/>Step 多模态 + 技术门禁"]
    V --> O["阶段图、知识卡与四阶段回放"]
```

最快运行方式：

```bash
git clone https://github.com/boblank/DGXSparkSandEvo.git
cd DGXSparkSandEvo
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python demo-ui/server.py --host 127.0.0.1 --port 8088 --dry-run
# 浏览器打开 http://127.0.0.1:8088/demo-ui/
```

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
- [x] 完成规划 Agent → 科学审查 Agent 的一次修订闭环与绘图前阻断
- [x] 完成 Step 多模态图像连续性审查，技术降级不会冒充语义审查
- [x] 完成 DGX Spark 三轮真实 Step + FLUX 串联验收
- [x] FLUX.2 Klein 4B 完成四世界 8 组盲评、真实三轮和 FLUX.1 回退验收
- [x] HunyuanVideo-1.5 在 DGX Spark 生成并保留一段 3.375 秒浅海 I2V 成片
- [x] 每个完成会话可把起点与三次改变整理成带中文选择字幕的四阶段回放

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
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
PYTHON_BIN=.venv/bin/python scripts/verify_release.sh
```

`requirements.txt` 是网页、规则、图片和测试的必需依赖。需要直接走 PyAV 媒体路径时，再安装 `requirements-media.txt`；只生成四阶段回放也可以使用系统 `ffmpeg` 回退。

## 图像渲染与视频留档

FLUX.1 和 FLUX.2 Klein 使用两套独立工作流。FLUX.1 仍是没有额外配置时的兼容基线；DGX 比赛实例可以显式优先使用已经通过门禁的 Klein，并在失败时回到 FLUX.1：

```bash
export EVOLAB_IMAGE_RENDERER=flux2-klein-4b
export EVOLAB_IMAGE_FALLBACK=flux1
```

第二、三轮的回退不会丢掉父图。Klein 失败后，FLUX.1 会把同一张父图编码为 latent，再以 `denoise < 1` 生成后代；如果参考工作流或上传步骤不可用，这一轮会失败并保留原阶段，不会静默退成无参考文生图。

规划结果在绘图前还要经过独立科学审查。审查可以放行、要求一次修订或阻断；第二次仍未通过时不会调用渲染器。`/api/health` 只表示进程存活，`/api/readiness` 才会实际核对 Step、ComfyUI、工作流节点和当前渲染链模型。

门禁不是只看一张“好看的图”。固定 A/B 资产仍保留原四世界、每个世界两个种子的可比基线；交互测试现已覆盖七个世界，并额外检查历史关键性状连续、父分支前置条件、未来证据标签、浅海连续三轮和故障回退：

```bash
python3 skills/evolution/renderer_ab.py \
  --output-root output/renderer-ab \
  --comfy-url http://127.0.0.1:7000 \
  --comfy-output /path/to/ComfyUI/output
```

视频分成两类，不能混为一谈：

- **会话四阶段回放**：完成三轮后读取本次会话的 `stage_00..03`。完整环境和选择写入清单，画面只保留轮次与一句变化；每个阶段都有缓慢镜头运动，相邻阶段连续衔接。输出为 H.264 MP4，页面可直接恢复会话并播放。它是选择记录，不增加新的科学结论。
- **第三轮不会突然结束**：提交前页面会先说明后面还有约 10 秒回放；提交后按“生成最终阶段 → 制作四阶段回放”显示两步进度，手机端会自动把用户带到播放器。
- **Hunyuan 场景动态留档**：浅海世界的真实成片放在 [demo-assets/video/](demo-assets/video/)。它由 HunyuanVideo-1.5 480p I2V Step Distilled FP8 从一张已验收的最终阶段图生成，规格为 848 × 480、81 帧、24 fps、8 步，冷运行耗时 242.453 秒。它只证明 DGX 上的 I2V 链路已经跑通，不冒充当前会话的多阶段变化。

HunyuanVideo-1.5 I2V 的输入仍是一张起始图，不能直接承担“上一阶段到下一阶段”的四段叙事。因此比赛主页面使用可复核的多图回放；单图 Hunyuan 成片继续保留为独立的设备生成证据，二者都不进入前三轮图片生成的阻断路径。

模型安装和视频生成均先检查文件、节点、编码器与输出边界：

```bash
python3 skills/evolution/install_hunyuan_video_models.py --help
python3 skills/evolution/video_generation.py --help
python3 skills/evolution/lineage_video.py --help
```

可复核的参数、输入哈希、运动提示、耗时、峰值内存和逐帧检查结果记录在 [视频清单](demo-assets/video/tidal-symbiosis-dgx.json)。

## DGX Spark 不是一句运行环境

- **本地图像主路径**：FLUX.2 Klein 4B 在真实三轮中生成 1024×1024 图片，热运行分别为 5.015、5.014、5.013 秒。
- **失败回退**：注入 Klein 提交失败后，独立 FLUX.1 工作流在 30.023 秒完成合格图片，并留下 `fallback_from=flux2-klein-4b`。
- **本地视频证据**：HunyuanVideo-1.5 在 DGX Spark 上生成 848×480、81 帧、24 fps 的 3.375 秒 I2V，耗时 242.453 秒。
- **统一内存实测**：该 Hunyuan 任务记录的峰值进程 RSS 为 33,440,780,288 字节，进程 HWM 为 44,792,082,432 字节，系统已用峰值为 83,387,592,704 字节。数值来自公开清单，不拿其他显卡的参考值冒充 Spark 实测。
- **现场可恢复**：图片与视频串行调度；Klein 失败回 FLUX.1；Hunyuan 只生成独立资产，不阻断三轮交互；断网时可使用同一 API 与页面的确定性预演模式。

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

| 归属 | 实际使用 |
|---|---|
| NVIDIA | DGX Spark、CUDA 运行环境、统一内存；本地承载 ComfyUI、会话服务和媒体任务 |
| Stepfun | Step 3.7 Flash：严格结构规划；StepAudio 2.5：90 秒提交视频旁白 |
| Agent 协作 | Step 规划 Agent、独立科学审查 Agent、Step 多模态图像连续性审查；会话协调与证据绑定保持为确定性 Module |
| 本地语言模型基线 | Ollama + Qwen3.6 35B，用于官方 Workshop 复现与 CLI 回退验证 |
| 图像 | ComfyUI、FLUX.2 Klein 4B Distilled FP8、FLUX.1 dev FP8 |
| 视频 | HunyuanVideo-1.5 480p I2V Step Distilled FP8；PyAV / ffmpeg；H.264 |
| 应用 | Evolution Skill、Python、Pillow、原生 HTML / CSS / JavaScript |

FLUX.1、FLUX.2 Klein 与 HunyuanVideo 都不是 NVIDIA 模型。本项目没有使用 NIM、TensorRT-LLM 或 NeMo，也不会把未使用的 SDK 写进技术栈。

## 开源协议

[MIT License](LICENSE)

# SandEvo（EvoLab｜演化岔路）

SandEvo 是面向 DGX Spark 黑客松构建的生成式演化实验：用户描述环境变化，Agent 基于进化规则提出多条具有不同收益与代价的演化路线，审查其中的冲突，再生成最合理路线的未来生物图鉴。

> 这不是科研级演化预测。项目输出是以自然选择、环境压力和性状权衡为约束的模拟假说与艺术生成结果。

## 核心体验

```text
环境变化
  → 生成三条差异化演化路线
  → 审查生物学冲突与适应代价
  → 选择并修订最合理路线
  → ComfyUI + FLUX 生成未来生物
  → 输出演化分岔成果卡
```

项目的核心区别不是“让 AI 随机画怪兽”，而是让生成过程显式呈现：

```text
环境 → 选择压力 → 性状变化 → 适应收益 → 生存代价
```

## 当前状态

- [x] 在 DGX Spark 上原样复现官方 OpenClaw + Ollama + ComfyUI Workshop
- [x] 验证本地 Qwen3.6 35B、ComfyUI、FLUX 与 Skill 图片生成链路
- [ ] 完成 Evolution Skill 的规划、审查与规则校验
- [ ] 生成演化分岔图与最终成果卡
- [ ] 确定比赛版本 Agent 运行时并完成端到端演示

官方 OpenClaw Workshop 是已验证的基线和回退方案。比赛主线优先验证 Hermes + Step Plan；OpenClaw 是否继续使用取决于剩余时间和集成成本，NemoClaw 只在核心链路稳定后评估。

## 官方基线代码

仓库包含一份比赛组织方 Workshop Notebook 的公开安全副本：

- [notebooks/workshop-official-sanitized.ipynb](notebooks/workshop-official-sanitized.ipynb)
- [运行与安全说明](notebooks/README.md)
- [.env.example](.env.example)：Step Plan 配置模板，不包含真实密钥

该副本保留 26 个原始代码单元，移除了全部执行输出、机器地址和运行时 Token。它用于证明官方基线可复现，不代表比赛作品必须继续使用 OpenClaw。

## 技术栈

- NVIDIA DGX Spark
- 本地 Qwen3.6 35B
- ComfyUI + FLUX
- Agent Skill
- Step 3.7 Flash（结构化路线规划与多模态复核）
- Hermes 候选运行时；OpenClaw 已验证回退
- Python + Pillow

## 开源协议

[MIT License](LICENSE)

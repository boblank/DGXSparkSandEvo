---
name: evolution
description: "Generate a scientifically bounded, three-stage evolution storyboard from a Chinese environment prompt. Use for 生物进化、演化路线、未来生物、低氧适应、演化岔路 and EvoLab requests."
metadata: { "openclaw": { "emoji": "🧬", "requires": { "bins": ["python3", "bash"] } } }
---

# EvoLab Evolution Storyboard

收到生物演化或环境改变请求时，调用本目录的 run_helper.sh。不要自行编造输出文件路径。

## Run

    "$OPENCLAW_HOME/.openclaw/skills/evolution/run_helper.sh" \
      --scenario "<用户的原始中文场景>"

固定演示场景：

    让一种生活在浅海的微型祖先，经历真核化和简单多细胞化后，在氧气持续下降、捕食压力上升的未来环境中继续演化。生成三条不同路线，并选出最合理的一条。

## Output

helper 成功时在 stdout 最后一行打印：

    MEDIA:<absolute_path_to_evolution_storyboard.png>

把整行原样放进最终回复。不要把 manifest、日志、API Key、Authorization header 或模型 reasoning 放进回复。

## Rules

- 单次请求只运行一次 helper。
- 三阶段图片按 Stage 1 → Stage 2 → Stage 3 顺序生成。
- 只为最终选中路线生成未来生物图；未选路线只展示文字。
- helper 失败时报告“演化故事板生成失败”，不要伪造图片。
- 输出是受约束的演化情景推演和艺术表达，不是科研预测。

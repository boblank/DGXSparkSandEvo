# EvoLab 开放世界交互 Demo

首页是一张从深时延伸到未来的世界图谱。体验者可以进入热液喷口、潮池共生、埃迪卡拉海床、泥盆纪河口、智人的来路、鸟类飞行或太空世代；七个世界都有独立起点、三轮选择、知识边界和画面锚点：

```text
选择世界
→ 看当前生物、环境或前生命化学系统
→ 选择环境变化
→ 选择偶发事件
→ 选择更容易延续的方向
→ 规划下一阶段，由独立科学审查决定是否放行
→ 审查通过后生成图片与解释
→ 在上一阶段基础上继续
```

历史重建与未来推演走两套约束。历史世界会保存关键性状账本：已经获得的承重附肢、羽毛或双足运动组合，只有在场景包声明了有来源的结构转化时才允许替换；分支前置条件不满足时，选项根本不会出现。未来世界允许多种答案，但每轮都标成情景推演，并区分个体可塑性、技术补偿与跨世代遗传变化。

潮池世界第三轮仍可进入海水变暖、海平面上升、海洋低氧或酸化情景。热液世界在生命门槛之前，只使用“化学系统、结构、循环和下一阶段”，不会提前写出后代或物种。人类起源不使用“从猿到人”的阶梯，鸟类世界不把羽毛和飞行压成同一步；太空世代不把航天生理反应冒充新人类证据。

## 本地界面验收

离线模式不会调用 Step 或 ComfyUI，会生成轻量 SVG 作为阶段图：

```bash
cd <repo-root>
python3 demo-ui/server.py --host 127.0.0.1 --port 8088 --dry-run
```

打开：

```text
http://127.0.0.1:8088/demo-ui/
```

## DGX Spark 真实运行

```bash
export STEP_API_KEY='从安全环境注入，不要写进仓库或日志'
export COMFYUI_URL='http://127.0.0.1:7000'
python3 demo-ui/server.py --host 127.0.0.1 --port 8088
```

服务端每轮调用：

- `step-3.7-flash`
- `reasoning_effort=high`
- `response_format.type=json_schema`
- `strict=true`
- 独立科学审查；最多要求规划器修订一次
- DGX Spark 本地 ComfyUI / FLUX
- 有父图时先做结构门禁，再用多模态模型核对身份、关键性状和禁画项

`EVOLAB_REVIEW_MODE=required` 会在审查超时、无效 JSON 或第二次未通过时阻断绘图。`optional` 允许审查服务不可用时改走规则门禁，记录会明确写成 `rules_only`，页面不会把它说成双 Agent。图像审查也有独立的 `EVOLAB_VISUAL_REVIEW_MODE`；语义审查没有运行时，只能报告结构门禁结果。

## HTTP 契约

```text
GET  /api/health
GET  /api/readiness
GET  /api/scenarios
POST /api/sessions
GET  /api/sessions/{session_id}
POST /api/sessions/{session_id}/evolve
GET  /api/assets/{session_id}/{filename}
```

演化请求：

```json
{
  "environment_id": "oxygen_pulses",
  "contingency_id": "stable_engulfment",
  "direction_id": "endosymbiotic_cell",
  "expected_round": 1
}
```

创建指定世界：

```json
{
  "scenario_id": "ediacaran_seafloor"
}
```

空请求仍进入 `tidal_symbiosis`，用于兼容原有浅海演示。

`/api/health` 只回答 HTTP 进程是否存活；`/api/readiness` 会实际核对 Step 严格 JSON、ComfyUI、当前渲染链工作流、参考图节点和模型登记。任一依赖未就绪时返回 `503`。

服务端会保存 `lineage_parent`、`inherited_traits`、`protected_traits`、本轮选择、收益、代价、知识命中、图片地址和私有 `review_trace`。公开阶段只带脱敏裁决摘要；原始模型响应、隐藏推理、完整提示词和密钥不会进入浏览器。轮次冲突、重复提交、未知选项和不满足父分支前置条件的方向会被拒绝，不会让谱系悄悄跳步。

## 安全边界

- 浏览器永远拿不到 API Key。
- HTTP 静态目录只开放 `demo-ui/` 与 `demo-assets/`。
- `.env`、`skills/`、`runs/`、`internal-docs/` 和路径穿越请求都会被阻断。
- 下游错误只返回经过整理的中文提示，不回显响应正文、请求头或密钥。
- 失败的一轮不会覆盖已经完成的谱系，可直接重试。

## 验证

```bash
node --check demo-ui/app.js
python3 -m py_compile demo-ui/server.py skills/evolution/interactive_engine.py
python3 -m unittest discover -s skills/evolution/tests -v
python3 -m unittest discover -s knowledge/tests -v
```

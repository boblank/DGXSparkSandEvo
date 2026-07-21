# EvoLab 三轮交互 Demo

这不是 Manifest 查看器。页面会创建一条有状态的生命谱系，让体验者连续完成三轮选择：

```text
看当前生物与环境
→ 选择环境变化
→ 选择偶发事件
→ 选择演化方向
→ 生成下一阶段图片与解释
→ 在上一阶段基础上继续
```

第三轮进入未来情景，可选海水变暖、海平面上升、海洋低氧或酸化。历史节点命中知识库时展示知识卡和来源；具体未来形态与未知组合会明确标成情景推演，不冒充已发现物种。

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
- DGX Spark 本地 ComfyUI / FLUX

## HTTP 契约

```text
GET  /api/health
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

服务端会保存 `lineage_parent`、`inherited_traits`、本轮选择、收益、代价、知识命中和图片地址。轮次冲突、重复提交和未知选项会被拒绝，不会让谱系悄悄跳步。

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

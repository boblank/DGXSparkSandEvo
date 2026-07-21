# EvoLab knowledge adapter

这是一个可整体关闭的知识来源旁路。它只读取人工筛选的来源、固定知识卡和轻量图谱，不调用模型、不生成引用，也不修改核心规则校验结果。

当前白名单与图谱覆盖：

- 历史转变：`M02` 原核到真核、`M03` 质体内共生、`M04` 遗传重组、`M05` 多细胞化、`M07` 登陆、`M08` 飞行、`M09` 神经系统与脑、`M10` 社会协作、`M11` 微生物协作群落；
- 未来压力：`GLOBAL_WARMING`、`SEA_LEVEL_RISE`、`OCEAN_DEOXYGENATION`、`OCEAN_ACIDIFICATION`；
- `M12` 只把已知压力与前序谱系约束组合为情景外推，不把结果写成预测。

每个图谱节点、边和交互知识卡都包含：

- `evidence_class`：`known_mechanism`、`teaching_simplification` 或 `scenario_extrapolation`；
- `prerequisites`、`mechanisms`、`tradeoffs`；
- `source_ids` 与 `boundary`。

## 查询

从仓库根目录运行：

```bash
python3 knowledge/knowledge_adapter.py --knowledge-card-id ENDOSYMBIOSIS
python3 knowledge/knowledge_adapter.py --transition-id M05
python3 knowledge/knowledge_adapter.py --pressure-id OCEAN_ACIDIFICATION
```

成功命中时输出：

```json
{
  "status": "ok",
  "query": {"transition_id": "M05"},
  "count": 6,
  "sources": []
}
```

示例中的 `sources` 为省略后的展示；实际输出包含完整来源卡。检索 ID 不存在时仍以退出码 `0` 返回明确空结果，不猜测或补写引用：

```json
{
  "status": "empty",
  "query": {"transition_id": "M99"},
  "count": 0,
  "sources": [],
  "message": "无可用来源"
}
```

目录或 JSON 损坏属于适配器错误，退出码为 `2`，错误 JSON 写入标准错误。调用方应降级为本地固定知识卡，而不是让模型补造来源。

加入 `--explain` 后，适配器会返回命中的图谱节点、转变边、交互知识卡和来源：

```bash
python3 knowledge/knowledge_adapter.py --transition-id M02 --explain
python3 knowledge/knowledge_adapter.py --pressure-id GLOBAL_WARMING --explain
```

图谱没有某个转变时，返回 `status: "no_match"`，并保持 `nodes`、`edges`、`knowledge_cards` 和 `sources` 为空。这个结果用于界面明确告诉用户“没有命中已知历史节点”，不能触发模型补写来源。

## 数据边界

每个来源都包含 `supports` 和 `boundary`。前者只说明它能支撑的最小知识点，后者明确不能从该来源推出什么。未来情景来源用于约束“可能的选择压力、收益与代价”，不把图片或路线包装成确定预测。

`skills/evolution/knowledge_cards.json` 中的 `cards` 保留给旧版一次性 Manifest，避免主流程突然多出无关知识卡；新版三轮体验读取 `interactive_cards`，按 transition 或 pressure 精确命中。

新增或修改来源时必须人工核对论文标题、链接、支持范围和边界。不要把模型生成的引用直接写入白名单。

## 测试

```bash
python3 -m unittest discover -s knowledge/tests -v
```

# EvoLab knowledge adapter

这是一个可整体关闭的知识来源旁路。它只读取人工筛选的来源、固定知识卡和轻量图谱，不调用模型，也不生成引用。历史场景现在会在规划前查询真实类群；匹配结果同时交给生成器和科学审查门禁。

当前白名单与图谱覆盖：

- 历史转变：`M02` 原核到真核、`M03` 质体内共生、`M04` 遗传重组、`M05` 多细胞化、`M07` 登陆、`M08` 飞行、`M09` 神经系统与脑、`M10` 社会协作、`M11` 微生物协作群落；
- 未来压力：`GLOBAL_WARMING`、`SEA_LEVEL_RISE`、`OCEAN_DEOXYGENATION`、`OCEAN_ACIDIFICATION`；
- `M12` 只把已知压力与前序谱系约束组合为情景外推，不把结果写成预测。

真实类群种子库目前收录提塔利克鱼、青山藻、始祖鸟、阿法南方古猿和狄更逊水母。它们覆盖现有泥盆纪、元古宙、埃迪卡拉纪、鸟类飞行和人类起源场景的首批高价值路径。种子库不是完整的古生物名录；没有匹配时，系统必须返回知识缺口。

每个图谱节点、边和交互知识卡都包含：

- `evidence_class`：`known_mechanism`、`teaching_simplification` 或 `scenario_extrapolation`；
- `prerequisites`、`mechanisms`、`tradeoffs`；
- `source_ids` 与 `boundary`。

每个 `historical_taxon` 还记录年代范围、环境、生态角色、外部性状、内部性状和来源边界。匹配分数由四个可复查部分组成：场景 0.30、转变机制 0.25、用户方向 0.25、环境 0.20。系统不用向量相似度补猜关系。

匹配结果只有三种：

- `historical_reference`：四类条件高度相似，而且外部、内部性状都有来源。生成器必须保留两类锚点；
- `partial_reference`：只有部分条件相似，只能用作背景；
- `bounded_inference`：找不到足够接近的真实类群，只能有限推测。

## 查询

从仓库根目录运行：

```bash
python3 knowledge/knowledge_adapter.py --knowledge-card-id ENDOSYMBIOSIS
python3 knowledge/knowledge_adapter.py --transition-id M05
python3 knowledge/knowledge_adapter.py --pressure-id OCEAN_ACIDIFICATION
```

按一次用户选择查询真实历史类群：

```bash
python3 knowledge/knowledge_adapter.py \
  --transition-id M07 \
  --match-historical \
  --scenario-id devonian_estuary \
  --direction-id bottom_support \
  --environment-id weedy_shallows
```

输出包含候选类群、分数组成、必须保留的内外性状、来源编号和证据边界。主生成链只把允许公开的部分写入阶段产物，审查留痕仍保留来源编号。

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

每个来源都包含 `supports` 和 `boundary`。前者只说明它能支撑的最小知识点，后者明确不能从该来源推出什么。化石保存不到的软组织、行为和亲缘位置不会被自动补全。未来情景来源只约束可能的选择压力、收益与代价，不把图片或路线包装成确定预测。

`skills/evolution/knowledge_cards.json` 中的 `cards` 保留给旧版一次性 Manifest，避免主流程突然多出无关知识卡；新版三轮体验读取 `interactive_cards`，按 transition 或 pressure 精确命中。

新增或修改来源时必须人工核对论文标题、链接、支持范围和边界。不要把模型生成的引用直接写入白名单。

## 测试

```bash
python3 -m unittest discover -s knowledge/tests -v
```

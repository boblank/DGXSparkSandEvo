# 官方 Workshop Notebook

`workshop-official-sanitized.ipynb` 是 DGX Spark 黑客松组织方 Workshop Notebook 的公开安全副本，用于复现官方的 OpenClaw + Ollama/Qwen + ComfyUI/FLUX 基线。

## 与现场执行版本的差异

- 公网主机替换为 `<PUBLIC_HOST>`；
- 节点编号替换为 `XX`；
- 清空全部 26 个代码单元的输出和 execution count；
- 规范化 Jupyter kernel metadata；
- 增加公开使用与设备认证安全提示；
- 未加入任何 API Key、Gateway Token、个人照片或生成缓存。

公开 Notebook SHA-256：

```text
936f8039bbdcae54f5659e5555775b04c8e4b4a4b1f7b591ceb79e957c0e6993
```

## 使用

1. 在 DGX Spark 的 JupyterLab 中打开 Notebook。
2. 将 `NODE_SUFFIX="XX"` 和 `<PUBLIC_HOST>` 替换为比赛环境提供的值。
3. 按顺序执行全部单元。
4. 不要提交执行后的 Notebook；执行输出可能包含临时 Gateway Token 和机器地址。

该 Notebook 依赖 DGX Spark、CUDA、组织方 Workshop Bundle、Ollama、ComfyUI 和 OpenClaw，无法在普通 macOS 环境完成端到端执行。对应现场基线已经在目标 DGX Spark 上完成 26/26 代码单元执行，Jupyter error output 为 0。

## 安全说明

Notebook 中的 `allowInsecureAuth` 与 `dangerouslyDisableDeviceAuth` 只用于隔离、可信、短时 Workshop 网络。不得用于生产或长期公网部署。公开部署应启用 HTTPS、设备认证和最小化网络暴露。

## 来源与许可

Notebook 来源于比赛组织方提供的官方 Workshop。原 Notebook 未声明独立开源许可，因此本仓库的 MIT License 不自动覆盖该第三方材料；原作者和组织方保留其相应权利。仓库仅保留脱敏副本用于比赛复现、审查和归档。

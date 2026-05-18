# PROJECT_PROMPT.md

本文件是当前仓库的宿主中立项目级 prompt 真源。

## 作用定位

- 这里放长期稳定、希望每次新会话都默认生效的项目规则
- 不放还在验证中的临时经验或一次性排障过程
- `expcap` 负责发现、验证、证明经验；稳定后再考虑晋升到这里

## 宿主兼容策略

- 本文件是宿主中立的项目规则真源
- `AGENTS.md`、`CLAUDE.md`、以及其他主流 agent 宿主的项目提示词文件，应优先桥接这里，而不是各自维护一套分叉规则
- 某个宿主不原生识别 `AGENTS.md` 时，也应优先复用这里的内容

## 当前稳定规则

- `AGENTS.md` 负责项目主入口和规则总览
- `AGENTS.expcap.md` 只负责动态经验 `get/save` 集成，不替代项目静态规则
- `expcap` 的默认定位是 experience governance / asset governance，不是不断自动压缩历史的长期记忆池
- 原始 trace 是一等证据；总结先生成 candidate，不直接当成真理
- 召回结果是带 provenance 和 risk 的候选，不直接支配执行
- 稳定、短小、反复证明有效的规则，优先收敛到这里
- 仍在试验、需要 feedback/proof 的内容，继续留在 `expcap` 资产层

## Maintainer Notes

- 保持短、硬、稳定
- 规则一旦失效，优先直接修改这里
- 不要把长篇背景材料、一次性教训或环境噪声沉进这里

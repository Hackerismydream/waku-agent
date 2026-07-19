# DeepSeek V4-Pro 候选评测证据

评测日期：2026-07-19

这是一份 Waku 内部候选任务集的真实运行证据，不是人工批准的冻结
baseline。它用于建立当前行为基线、发现失败模式，并给简历中的 Eval
指标提供可追溯口径。

## 结论

| 指标 | 结果 |
|---|---:|
| 候选任务 | 40 条（开发集 24，留出集 16） |
| 重复次数 | 每条 3 次，共 120 次 |
| 执行错误 | 0 |
| 原始端到端通过 | 112/120（93.3%） |
| 审计后端到端通过 | 114/120（95.0%） |
| 3/3 稳定通过任务 | 37/40（92.5%） |
| P50 / P95 Agent 延迟 | 9.217 / 21.539 秒 |
| Agent 输入 / 输出 token | 493,063 / 65,928 |
| Agent 估算成本 | $0.271838 |
| Judge 估算成本 | $0.028572 |
| 总评测估算成本 | $0.300410 |
| 单个确定性成功执行的 Agent 成本 | $0.002385 |

“端到端通过”要求确定性断言通过且 Judge 分数不低于 7；“3/3
稳定通过”要求同一任务的三次尝试全部端到端通过。延迟只统计 Agent
turn，不含沙箱准备和 Judge。

## 运行配置与版本

- 执行模型：`deepseek-v4-pro`
- Judge：`deepseek-v4-flash`
- 本地 artifact：`.waku/evals/20260719T160057.956392Z-18fff31f`
- 源运行 commit：`f8dfef16e3a7821a3bf08011e49165b63c52a406`
- 源运行 dataset SHA-256：`18fff31facbd8b915cbe97239db39b6305933bd2302ffeff4eae7c17bb45233b`
- 审计合同 commit：`835368f7dbc40c0a7c53a30abde850556d6ef2f3`
- 审计合同 dataset SHA-256：`1ba9ae83c181a338771970eeac3e711231dc456d09e8f2d51d3e205a91bf7e29`
- Prompt-source SHA-256：`ccfb8f4509cbee47332d003f6a3961c5a62df91df95f1e56ee824730e731f4a2`
- Python：3.13.12；OpenAI SDK：2.46.0
- 相关 runtime / eval source 在运行时为 clean worktree

费率按 DeepSeek 公布的标准价格估算：V4-Pro 输入/输出分别为
$0.435/$0.87 每百万 token，V4-Flash 为 $0.14/$0.28；未推断缓存、批量
或协议折扣。参见 [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing)。

## 为什么同时报告 93.3% 和 95.0%

源运行完整保存了 120 条 reply、工具轨迹、沙箱终态和 Judge 结果。逐条审计
8 个原始 failure 后，发现其中两条是合同假阴性：

1. Weekly Reset 草稿包含完整 checklist，但没有字面单词 `plan`；
2. Trip Prep 将本地用户写作 `You`，而合同只接受 `Me`。

`835368f` 只增加这两组等价表达，没有修改 Agent Prompt、Loop、Skill 或
模型输出。使用当前确定性 scorer 对同一批 immutable receipts 重放后，
两条转为通过，得到 114/120；原始 summary 和 failures 仍保留在源 artifact
中。这个变化是 evaluator 校正，不是模型能力提升。

## 剩余真实失败

| 任务 | 次数 | 失败证据 |
|---|---:|---|
| `multi-list-and-message` | 2 | 一次草稿遗漏 16:00；一次先生成错误草稿再补写，留下两个 outbox 文件 |
| `qna-summarize-request` | 3 | 要求不超过十个字，但回复追加 Markdown、字数说明或多个备选答案 |
| `recovery-reopen-history` | 1 | 已从会话历史正确回答 Harbor Hall，却额外写入长期记忆 |

最终 6 个失败分布为：`tool_args` 1、`routing` 1、`response` 3、
`recovery` 1。确定性层还抓到了 Judge 的盲点：三条超长回复均被 Judge
误评为 10/10；重复草稿也被 Judge 评为 10/10，但执行轨迹和沙箱状态明确
显示副作用不正确。

## 按任务类别

| 类别 | 审计后通过 |
|---|---:|
| memory | 21/21 |
| messaging | 3/3 |
| multi_tool | 16/18 |
| recovery | 11/12 |
| safety | 15/15 |
| scheduling | 24/24 |
| simple_qa | 9/12 |
| skill | 15/15 |

这些是尝试次数，不代表更大总体上的能力估计；小类别尤其不能据此声称
“100% 可靠”。

## 证据边界

- `evals/dataset_manifest.json` 仍是 `pending_author_review`，所以不能写成
  “40 条人工黄金任务”或“冻结 baseline”。
- 只有一个候选模型完成 3 次重复，不能写成“3 模型 × 3 次”。
- Judge 与候选模型来自同一供应商，但模型 ID 不同；可称“独立模型
  Judge”，不能称“跨供应商独立 Judge”。
- 成本是 token 乘公开费率的估算，不是账单实扣金额。
- 这组数据是隔离沙箱中的 Eval，不是 ECS 连续运行的线上 SLA 或真实用户
  任务指标。

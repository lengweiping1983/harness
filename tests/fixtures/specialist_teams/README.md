# 专业团队在线验收案例

这些小型构造案例验证有限的业务边界，不是团队能力排名。`expected.json` 不发送给模型。

| 案例目录 | 默认团队标识 | 检查内容 |
|---|---|---|
| `security` | `team-security-custom` | 路径字符串前缀与路径段边界；代码风险不等于生产事件 |
| `performance` | `team-performance-custom` | 描述性算术、因果推断、测量终点及未知取证成本 |
| `product` | `team-product-custom` | 操作次数减少不等于转化率提升；未执行的无障碍验收不得称作完成 |
| `evidence` | `team-research-custom` | 重复报道不等于独立测量；厂商主张不等于已核实效果 |

先在团队页从对应起点创建并保存团队。案例使用上表默认标识；重复创建、手动修改标识或使用其他团队时，在提交前明确修改对应 `task.json`。使用已登录且当前选定的模型，或显式指定当前账号可用的模型，例如：

```sh
.venv/bin/python scripts/evaluate_research_team.py --run --case tests/fixtures/specialist_teams/security --model chatgpt/gpt-6.1-terra
.venv/bin/python scripts/evaluate_research_team.py --run --case tests/fixtures/specialist_teams/performance --model chatgpt/gpt-6.1-terra
.venv/bin/python scripts/evaluate_research_team.py --run --case tests/fixtures/specialist_teams/product --model chatgpt/gpt-6.1-terra
.venv/bin/python scripts/evaluate_research_team.py --run --case tests/fixtures/specialist_teams/evidence --model chatgpt/gpt-6.1-terra
```

每条命令只提交一次，输出本次 `evaluation.json` 的绝对路径。之后用同一路径及原案例收集，不重新运行 `--run`：

```sh
.venv/bin/python scripts/evaluate_research_team.py --collect /绝对路径/evaluation.json --case tests/fixtures/specialist_teams/security
```

运行中返回当前状态；终态后核对分类、原文字面引文、真实成功成员身份、允许的读取调用和原始资料哈希。声明了 `member_read_paths` 的案例还要求每个直接派工成员至少有一次归属本轮、参数完整、路径合规且状态为 `done` 的 `read` 调用，并在 `successful_reads` 中列出成员、调用与文件身份。仅成员成功结束、只发生失败读取或借用其他轮次读取均不通过；失败后本轮正常读取允许恢复，越界工具或路径仍保留违规。

该检查不证明读取覆盖全部资料、成员的每条结论都来自其读取结果或全部理由均被引文语义支持，仍须人工复核；工具调用边界审计不等于操作系统沙箱。案例只读取隔离副本，不执行攻击或压测；显式运行才消耗在线订阅，不进入默认离线测试发现。生成的对话和临时资料保留，不自动清理用户运行数据。

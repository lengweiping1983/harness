# 显式研究回归样例

这些构造案例仅用于开发验证，不是真实产品事实，也不作为团队运行时的统一验收格式。业务提示词仍决定研究、决策和头脑风暴的表达及收敛方式。

```sh
.venv/bin/python scripts/evaluate_research_team.py --run --case tests/fixtures/research_decision
.venv/bin/python scripts/evaluate_research_team.py --run --case tests/fixtures/research_conflicts
```

每次显式运行提交一次并输出 manifest 路径；收集同一次运行：

```sh
.venv/bin/python scripts/evaluate_research_team.py --collect .run/research-eval-目录/evaluation.json --case tests/fixtures/research_conflicts
```

默认访问本地 5273 服务并使用服务端当前默认模型；可用 `--model` 显式覆盖。先安装任务文件指定的团队及成员。不要因运行仍在进行或观察超时重新提交；收集返回的 running 表示继续读取原运行。源码变化导致后台重启时，该次中断结果保留，不能计作通过。

- `research_decision`：模块名称误读、环境约束、未知恢复需求、无性能证据与真实成员身份。
- `research_conflicts`：历史与当前版本、局部观察外推、恢复保证、重复来源与缺失用户偏好。

每个目录的 `task.json` 定义本题提示及成员要求；Markdown 是发给团队的资料；`expected.json` 仅供收集器使用，不复制到任务工作区。manifest 保存资料和任务定义哈希，不能修改标准后重新评分旧运行。来源哈希按文件原始字节核对，CRLF、LF 或混合换行不会因文本读取而被误报；实际改变换行字节仍视为来源变化。引文匹配使用同一字节快照的 UTF-8 解码文本。

自动检查只覆盖分类、字面引文和宿主成员身份。人工复核仍须逐条检查引文是否支持主张、资料适用范围以及遗漏；正确标签配无关引文不能据自动通过认定研究正确。多次相同任务的独立试验也有价值；当前不提供稳定性比例或跨框架排名。

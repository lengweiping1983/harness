# 参与 Harness

所有变更先通过 GitHub Issue 记录问题、范围与验收条件，再从最新默认分支创建 `codex/<主题>` 分支，提交关联 Issue 的 Draft PR。缺陷提供最小复现与脱敏环境；功能先讨论用户问题和替代方案。安全漏洞请使用仓库 Security 页的私密报告入口（如已启用），不要公开凭据或可利用细节。

当前私有仓库已暂停 GitHub Actions，保留工作流供后续恢复。PR 完成本地适用验证与审查后直接合并 main，不等待远端检查；合并后清理已合并的远端及本地来源分支。当前状态与恢复步骤见 [GitHub 协作配置](docs/github-workflow.md)。

## 准备环境

修改前阅读 [开发与测试](docs/development.md)、[项目协作约束](docs/project-contract.md)和[代码生成规则](docs/code-generation.md)。首次贡献准备 Python 3.14+、Node.js 22+、npm 和 Git；CI 使用 Python 3.14 与 Node.js 22。在独立检出目录的仓库根执行：

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
npm --prefix model-runtime ci
npm --prefix web ci
```

已有 Python 3.14+ 环境可复用；旧版本环境需显式另建，不覆盖或迁移。Windows 将 Python 路径换为 `.venv\Scripts\python.exe`。桌面与浏览器专项只在明确要求时准备并运行；常规验证无需安装桌面依赖或浏览器；模型凭据不属于离线回归的环境准备。

## 完成一次贡献

| 步骤 | 操作与完成条件 |
|---|---|
| Issue | 选择缺陷、功能或工程任务表单，写清问题、本次范围和可复核的完成条件；补充材料按需提供 |
| Draft PR | 从最新 `origin/main` 创建 `codex/<主题>` 分支；提交可审查的变更后开 Draft PR，正文使用 `Closes #编号` 关联真实 Issue |
| 开发与本地验证 | 实现变更、保留业务断言，同步受影响的文档与生成源；按下文核对整个交付范围 |
| Ready 与审查 | 填写实际命令、结果、环境和未执行项；转 Ready 后核对最新代码的本地验证及 PR 正文，按 Issue 条件审查行为并解决讨论 |
| 合并与核对 | 本地适用验证与审查完成后直接 squash 合并；回查 main 提交、Issue 自动关闭和必要的实际行为，清理已合并的远端与本地分支。合并不等于发布安装包 |

日常未提交修改先运行 `make verify-plan PYTHON=.venv/bin/python`，再运行 `make verify PYTHON=.venv/bin/python`。默认基线为 HEAD；分支已有提交且还有工作区修改时，默认计划不能代替整个 PR 的范围。最终交付先更新远端引用，再覆盖分支已提交和未提交的变更：

```sh
git fetch origin main
.venv/bin/python scripts/verify.py --base origin/main --explain
.venv/bin/python scripts/verify.py --base origin/main
```

修改验证基础设施或无法确定影响时，`make verify` 保守选择全部常规领域。验证始终包含完整生产构建和生成同步；运行期间不改源码或 HEAD。检查退出码及 `output/verification.json` 中的 `passed`、`inputs_unchanged`、`build_unchanged`，再报告通过。`output/` 仅保留本地，在 PR 正文填写脱敏证据。

浏览器回归由 Actions 的 `Browser regression` 手动工作流或本地显式 `make test-browser PYTHON=.venv/bin/python` 运行，不属于常规 PR 门禁。手动准备配套 Chromium 和系统依赖见开发文档；普通 Chrome／Edge 能启动不代表扩展场景可运行。常规验证通过时仍需明确浏览器未执行，不能宣称浏览器验收通过。

PR 模板中的勾选清单只是提醒；“变更与验收”“验证证据”“风险与回退”仍需填写实际说明。机器检查正文结构和 Issue 身份，不能证明验证已执行或业务已完成。大任务拆成可独立验收的子 Issue，每个 PR 用关闭关键词关联本次完成的子 Issue，父 Issue 仅引用，避免部分交付提前关闭整个任务。新提交需要重新验证；回归通过新 Issue 和回退 PR 跟踪，不能通过重置用户运行数据回退代码。

## 恢复 CI 后的失败处理

| 状态 | 下一步 |
|---|---|
| 测试或构建失败 | 阅读失败步骤和日志，先定位，再修复并重新验证；不删除断言或扩大跳过范围 |
| 作业没有启动 | 阅读 Annotations，区分账户／额度、权限及平台故障；记录阻塞，不能宣称代码回归已执行 |
| 等待检查或分支落后 | 核对工作流触发事件和检查名称；按合并框提示更新分支，并等待新检查 |
| 运行中、取消或超时 | 结果仍未通过；核对旧运行是否被新提交替代或触及作业时限，保留原因，不盲目重跑 |

依赖更新 PR 同样必须审查和完成适用的本地验证，不因停用 CI 而自动批量升级或发布。具体作业、PR 检查和门禁边界见 [GitHub 协作配置](docs/github-workflow.md)。

恢复 CI 时，维护者需按 [GitHub 协作配置](docs/github-workflow.md) 核对远端规则。仓库中的模板与 Actions 文件只有进入默认分支后才会按 GitHub 规则生效。

提交与 PR 标题使用 `type: 简短说明`，例如 `fix: preserve drafts after retry`。常用类型为 `feat`、`fix`、`docs`、`test`、`ci`、`build`、`refactor`；标题描述最终变更，不使用临时状态或验证结论充当标题。问题类型按缺陷报告、功能提案、工程任务选择，优先复用已有 Issue。

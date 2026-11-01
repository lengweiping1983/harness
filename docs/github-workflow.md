# GitHub 协作配置

仓库的持续回归、PR 正文检查和桌面安装包构建由 `.github/workflows/` 管理。本地验证入口见 [开发与测试](development.md)，安装包准备与签名见 [桌面文档](../desktop/README.md)。

## 持续回归

日常贡献从 [贡献指南](../CONTRIBUTING.md)进入，按 Issue、Draft PR、本地验证、Ready、审查、squash 合并及 main 核对完成闭环。本页维护实际工作流与远端配置的边界。

`ci.yml` 在 `main` 推送、PR、`merge_group` 和手动触发时运行，没有文件路径过滤。其他分支推送不会单独启动该工作流。默认权限为 `contents: read`；每个回归作业限时 45 分钟，汇总作业限时 5 分钟。同一 PR 的新运行取消旧运行，其他触发不自动取消。

| 作业／检查 | 执行内容 |
|---|---|
| `Python tests` | Ubuntu、Python 3.14、Node.js 22；安装 Python 测试依赖及浏览器语言资源验证所需的 Web 锁文件依赖，构建 wheel，隔离验证发行资源，运行后端测试发现、协议与语言生成同步、编码和 lint 检查 |
| `Web and desktop tests` | Ubuntu、Python 3.14、Node.js 22；运行 PR 策略回归，安装带哈希的桌面 Python 依赖与各 npm 锁文件依赖，执行前端类型检查、完整 Web／桥接运行时单测、生产构建及桌面测试发现 |
| `CI required` | 等待 Python 和 Web／桌面两个作业，仅当两者结果均为 `success` 时通过；失败、取消或跳过均不能通过汇总检查 |

常规 CI 不启动浏览器。上述桌面回归运行在 Ubuntu，原生安装包由独立工作流构建。

## 手动浏览器回归

`browser-tests.yml` 仅由 `workflow_dispatch` 手动触发，不在 PR、推送或合并组事件上运行，也不属于 `CI required`。在 Actions 中选择 `Browser regression` 和目标分支后显式启动。浏览器检查未执行或失败时，不能以常规门禁通过宣称界面验收完成；已知环境与夹具问题保留在 [Issue #21](https://github.com/lengweiping1983/harness/issues/21)。

工作流使用四个独立 Ubuntu 作业，分别准备 Python 3.14、Node.js 22、配套 Chromium 和系统依赖，并完成生产构建。按排序后的场景位置取模分组，所有发现的完整场景恰好进入一组，不拆分场景内部的语言、主题或尺寸断言；各组独立拥有后端、端口和临时数据，`fail-fast: false` 保留其他组结果，每组限时 45 分钟。

本地显式 `make test-full` 包含完整浏览器套件，也可用 `npm --prefix web run test:browser -- --shard=1/4` 诊断单组。单组通过不代表完整套件通过；分组不接受额外场景选择，非法编号或会产生空组的数量明确失败。浏览器准备见开发文档。

## PR 正文检查

`pr-policy.yml` 在 `pull_request_target` 的 opened、edited、synchronize、reopened 和 ready_for_review 事件运行 `PR policy`。它只执行可信工作流中的元数据脚本，不检出或执行 PR 分支代码；内容、Issue 和 PR 权限均为只读。

正文检查先移除 HTML 注释，然后要求：

- 使用 `Closes #123`、`Fixes #123` 或 `Resolves #123` 等关闭语法关联本仓库真实 Issue；关联 PR 编号会失败。
- “变更与验收”“验证证据”“风险与回退”三个二级章节各含至少 10 个字符的非空说明。

`dependabot[bot]` 使用自动生成的正文，免除上述格式检查；其他机器人没有该豁免，业务 CI 仍独立运行。正文模板位于 `.github/PULL_REQUEST_TEMPLATE.md`。格式检查核对正文结构和 Issue 身份，验收证据仍由审查者核实。

该工作流没有 `merge_group` 触发配置；CI 的合并组回归与 PR 正文检查是两个独立检查入口。

## 规则模板与问题表单

`.github/rulesets/default-branch.json` 是供导入的规则模板，仓库文件本身不会应用远端设置。模板针对默认分支，禁止删除和非快进更新，要求 PR、已解决的审查讨论、squash 合并及严格的 `CI required`／`PR policy` 检查；独立批准数量为 0，未配置 bypass。远端是否已启用须读取实际规则确认。

`.github/ISSUE_TEMPLATE/` 提供缺陷报告、功能提案和工程任务表单，禁用空白 Issue。各表单分别收集复现与实际行为、用户问题与验收标准、工程范围与验证方案；日志和示例不得包含凭据或用户数据。

Dependabot 每周检查 GitHub Actions、`web` npm、`desktop` npm 和根目录 pip 依赖，每组最多保留 3 个更新 PR。桌面带哈希的 Python 锁文件仍按发行流程维护。

## 安装包与构建产物

`desktop-build.yml` 在手动触发或推送 `desktop-v*` 标签时运行，构建矩阵为 macOS ARM64、macOS x64 和 Windows x64。各作业使用 Node.js 22，安装 npm 依赖并执行前端类型检查、桌面回归，再调用桌面打包入口准备运行时与安装包；签名凭据通过对应平台的仓库 secrets 提供。

各平台安装包及更新元数据上传为 `Harness-<平台>-<架构>` artifact。全部平台构建成功后，Ubuntu 汇总作业调用 `desktop/release-assets.mjs` 整理为 `Harness-release-assets`；两类 artifact 均保留 14 天，缺少产物时失败。该工作流上传构建产物，不创建 GitHub Release。

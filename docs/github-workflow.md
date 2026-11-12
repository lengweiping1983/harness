# GitHub 协作配置

仓库的持续回归、PR 正文检查和桌面安装包构建由 `.github/workflows/` 管理。本地验证入口见 [开发与测试](development.md)，安装包准备与签名见 [桌面文档](../desktop/README.md)。

当前仓库为私有，按维护者要求在 Settings → Actions → General 选择 Disable actions，暂停所有 GitHub Actions，不使用托管 CI，也不等待远端 CI 或 PR 正文检查。全部工作流、测试和规则模板保留，以下作业说明描述重新启用后的配置，不表示当前正在执行。不购买套餐或启用付费 runner。

当前贡献流程为 Issue → 分支与 PR → 本地适用验证和审查 → 直接 squash 合并 main → 核对提交与 Issue → 清理已合并来源分支。仓库已开启 Automatically delete head branches；本地切回 main，拉取最新提交，确认 PR 已合并且内容保留后删除对应本地分支，并执行 `git fetch --prune origin`。squash 合并的来源提交可能不是 main 的祖先，不能仅凭祖先关系判断是否可删除；未合并的 PR 和独有工作必须保留。

原默认分支规则仍保存，但 GitHub 明确提示当前私有仓库套餐不强制执行规则集，因此不依赖它阻挡合并。恢复 CI 时，先核对套餐与免费额度，取得用户明确授权，再启用 Actions、核对必需检查与实际规则执行能力，运行目标提交并确认结果；未恢复前不报告远端门禁通过。安装包签名和产物存储属于独立发行范围。

## 持续回归

日常贡献从 [贡献指南](../CONTRIBUTING.md)进入，按 Issue、Draft PR、本地验证、Ready、审查、squash 合并及 main 核对完成闭环。本页维护实际工作流与远端配置的边界。

Actions 重新启用后，`ci.yml` 在 `main` 推送、PR、`merge_group` 和手动触发时运行，没有文件路径过滤。其他分支推送不会单独启动该工作流。默认权限为 `contents: read`；每个回归作业限时 45 分钟，汇总作业限时 5 分钟。同一 PR 的新运行取消旧运行，其他触发不自动取消。

| 作业／检查 | 执行内容 |
|---|---|
| `Python tests` | Ubuntu、Python 3.14、Node.js 22；安装 Python 测试依赖及浏览器语言资源验证所需的 Web 锁文件依赖，构建 wheel，隔离验证发行资源，运行后端测试发现、协议与语言生成同步、编码和 lint 检查 |
| `Web tests` | Ubuntu、Python 3.14、Node.js 22；运行 PR 策略回归，安装锁定依赖，执行前端类型检查、完整 Web／桥接运行时单测及生产构建 |
| `CI required` | 等待 Python 和 Web 两个作业，仅当两者结果均为 `success` 时通过；失败、取消或跳过均不能通过汇总检查 |

常规 CI 不启动桌面或浏览器回归。`desktop-tests.yml` 的 `Desktop regression` 仅允许手动触发，在 Windows 和 macOS 标准 runner 执行桌面套件；不属于 `CI required`。只有用户明确要求对应专项时才触发桌面或浏览器工作流。

## 手动浏览器回归

`browser-tests.yml` 仅由 `workflow_dispatch` 手动触发，不在 PR、推送或合并组事件上运行，也不属于 `CI required`。在 Actions 中选择 `Browser regression` 和目标分支后显式启动。浏览器检查未执行或失败时，不能以常规门禁通过宣称界面验收完成；发现问题时按实际失败日志建立 Issue，不使用未经核实的问题链接。

工作流使用四个独立 Ubuntu 作业，分别准备 Python 3.14、Node.js 22、配套 Chromium 和系统依赖，并完成生产构建。课程执行场景另安装 bubblewrap 与发行版 AppArmor 配置，加载仅针对 bwrap 的 `bwrap-userns-restrict` 配置后，实际预检文件、网络和进程命名空间；不关闭全局 AppArmor 限制，预检失败立即停止，不降级为无隔离执行。按排序后的场景位置取模分组，所有发现的完整场景恰好进入一组，不拆分场景内部的语言、主题或尺寸断言；各组独立拥有后端、端口和临时数据，`fail-fast: false` 保留其他组结果，每组限时 45 分钟。

本地显式 `make test-browser` 运行完整浏览器套件；`make test-full` 仅运行后端与 Web 常规门禁。浏览器单组诊断可用 `npm --prefix web run test:browser -- --shard=1/4`。单组通过不代表完整套件通过；分组不接受额外场景选择，非法编号或会产生空组的数量明确失败。浏览器准备见开发文档。

## PR 正文检查

`pr-policy.yml` 在 `pull_request_target` 的 opened、edited、synchronize、reopened 和 ready_for_review 事件运行 `PR policy`。它只执行可信工作流中的元数据脚本，不检出或执行 PR 分支代码；内容、Issue 和 PR 权限均为只读。

正文检查先移除 HTML 注释，然后要求：

- 使用 `Closes #123`、`Fixes #123` 或 `Resolves #123` 等关闭语法关联本仓库真实 Issue；关联 PR 编号会失败。
- “变更与验收”“验证证据”“风险与回退”三个二级章节各含至少 10 个字符的非空说明。

`dependabot[bot]` 使用自动生成的正文，免除上述格式检查；其他机器人没有该豁免，业务 CI 仍独立运行。豁免分两层：作业级 `if` 对 Dependabot 触发的运行直接跳过，不启动 runner；脚本内的作者判断继续覆盖维护者在 Dependabot 分支上推送、`github.actor` 变为维护者的情形。必需检查允许 `skipped` 状态，因此跳过的 `PR policy` 不阻塞这些 PR 在业务 CI 通过后合并。正文模板位于 `.github/PULL_REQUEST_TEMPLATE.md`。格式检查核对正文结构和 Issue 身份，验收证据仍由审查者核实。

该工作流没有 `merge_group` 触发配置；CI 的合并组回归与 PR 正文检查是两个独立检查入口。

## 规则模板与问题表单

`.github/rulesets/default-branch.json` 是供导入的规则模板，仓库文件本身不会应用远端设置。模板针对默认分支，禁止删除和非快进更新，要求 PR、已解决的审查讨论、squash 合并及严格的 `CI required`／`PR policy` 检查；独立批准数量为 0，未配置 bypass。远端是否已启用须读取实际规则确认。

维护者在仓库 Settings → Rules → Rulesets 导入该 JSON，核对目标为默认分支、状态为 Active、两项必需检查名称与 Actions 完全一致，并保存后回查。连接器不能读取传统分支保护或管理规则时，需要已登录的维护者网页会话；权限错误不代表规则存在或已生效。普通贡献 PR 仍须实际通过 `PR policy`，不能用 Dependabot 的跳过结果代替验证。

`.github/ISSUE_TEMPLATE/` 提供缺陷报告、功能提案和工程任务表单，禁用空白 Issue。各表单分别收集复现与实际行为、用户问题与验收标准、工程范围与验证方案；日志和示例不得包含凭据或用户数据。

Dependabot 每周检查 GitHub Actions、`web` npm、`desktop` npm 和根目录 pip 依赖，每组最多保留 3 个更新 PR。GitHub Actions 的全部更新合为一个分组；npm 与 pip 只把 minor 和 patch 合入分组，主版本仍单独成 PR，保留逐个审查升级风险的机会。分组只决定 PR 的聚合方式，不改变锁文件和版本范围本身，实际升级结果仍按业务 CI 验证。桌面带哈希的 Python 锁文件仍按发行流程维护。

## 安装包与构建产物

`desktop-build.yml` 仅在明确要求构建桌面安装包后手动触发，标签推送不自动启动，构建矩阵为 macOS ARM64、macOS x64 和 Windows x64。各作业使用 Node.js 22，安装 npm 依赖并执行前端类型检查、桌面回归，再调用桌面打包入口准备运行时与安装包；签名凭据通过对应平台的仓库 secrets 提供。

各平台安装包及更新元数据上传为 `Harness-<平台>-<架构>` artifact。全部平台构建成功后，Ubuntu 汇总作业调用 `desktop/release-assets.mjs` 整理为 `Harness-release-assets`；两类 artifact 均保留 14 天，缺少产物时失败。该工作流上传构建产物，不创建 GitHub Release。

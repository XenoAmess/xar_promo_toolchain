# Contributing to XAR Promo Toolchain / 为 XAR Promo Toolchain 贡献

Thank you for contributing. This repository is a standalone promotional-media toolchain with explicit package, evidence, audit, human-review, and release boundaries. Changes must preserve the contract and verification boundary of the component they touch.

感谢参与贡献。本仓库是独立的宣传媒体工具链，具有明确的软件包、证据、审计、人工复核与发布边界；修改必须遵守所涉及组件的合同与验证边界。

## Before opening a pull request / 提交拉取请求前

1. Read `AGENTS.md`, the root README, and the authoritative documentation linked for the component you intend to change.
2. Run the required repository bootstrap in `AGENTS.md` before editing.
3. Keep the change scoped. Do not mix unrelated package, schema, example, generated output, or media changes in one pull request.
4. Do not hand-edit generated schemas or release metadata; change their source and run the documented generator.
5. Run the smallest relevant Python/package checks and record the exact commands and results in the pull request.
6. Identify third-party code, data, media, generated material, and model output together with their license or authorization.

对应中文：先阅读 `AGENTS.md`、根 README 与目标组件的权威文档并执行规定的环境前置；保持改动单一；生成文件通过来源和生成器更新；运行与风险相称的最小测试；第三方、生成或模型产出材料必须说明来源、许可与必要署名。

## Contributor License Agreement / 贡献者许可协议

External contributions require acceptance of [CLA.md](CLA.md). The CLA is a
license, not a copyright assignment: contributors keep ownership of their work.

外部贡献必须接受 [CLA.md](CLA.md)。CLA 只授予许可，不转让贡献者的著作权。

The repository uses a small, repository-owned GitHub Actions checker. It has no
external service, database, GitHub App, personal access token, or secret beyond
the job-scoped `GITHUB_TOKEN`. Signatures are recorded as public pull-request
comments and therefore apply per pull request.

本仓库使用自有的轻量 GitHub Actions 检查器，不依赖外部服务、数据库、GitHub App、个人访问令牌或额外密钥。
签署记录就是公开的 PR 评论，因此按 PR 生效。

Every account reported by the checker must post this exact comment on the pull
request:

```text
I have read and agree to the Contributor License Agreement (CLA), version 1.2, and I confirm that I have authority to grant the rights for my contribution.
```

The checker covers the pull-request author and GitHub-linked commit authors. If
a commit author cannot be mapped to a GitHub account, the check stops for manual
review. Each additional contributor may sign by posting the same statement from
their own account.

检查器会覆盖 PR 作者以及能够关联到 GitHub 账号的提交作者。无法映射的提交作者会触发人工复核；多人共同贡献时，
每位被列出的贡献者都应使用自己的账号发表同一声明。

Maintainers may apply the `cla:manual` label only after separately verifying an
entity CLA, offline signature, unmapped author, or equivalent evidence. The
label is an auditable override, not a way for contributors to bypass the CLA.

维护者只有在核验企业 CLA、线下签名、无法映射的作者或等效证据后，才可添加 `cla:manual` 标签。该标签是可审计的人工覆盖，
不是贡献者绕过 CLA 的入口。

## Pull-request expectations / PR 要求

- Explain the user-visible or developer-visible outcome.
- Link the governing package, schema, review, or release contract.
- List tests actually run; distinguish unit/package checks from live media and named human-review evidence.
- Keep artifacts, audits, sign-offs, release claims, and publication channels honest and separate.
- Expect the required commit status `CLA / signed` before merge.

If you are contributing on behalf of an employer or another legal entity, make
sure you are authorized to bind it before signing. If that requires a separate
entity agreement, contact the maintainer before the contribution is merged.

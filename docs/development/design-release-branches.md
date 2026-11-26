# developとmasterのbranch・リリース設計の設計記録

[設計記録の索引](design.md)

## developとmasterのbranch・リリース設計

`develop`を開発用branch、`master`をリリース用branchおよびGitHubのdefault branchとする。
`master`への更新はPRのマージコミットだけに限定し、直接pushを許可しない。
PRの作成は、条件が成立する`agent-toolkit:atk-mode`の作業`process-wi`と、恒常的な認可または明示指定により公開まで扱う協調モードではエージェントが実施する。
公開範囲の明示指定を優先し、それ以外では手動で行う。
PR作成、必須check、レビュー、マージ後同期の固定費を抑えつつmasterへの反映を遅らせすぎないよう、通常変更は時間でまとめる。
リリースの実施可否と周期は[release-dotfiles「developとmasterのリリース運用」](../../.claude/skills/release-dotfiles/SKILL.md#developとmasterのリリース運用)が定める。
head branchの機械的な限定は設けない。

repository設定ではマージコミットを有効にし、squash merge、rebase mergeおよびauto-mergeを無効にする。
マージ後のbranch自動削除も無効にし、エージェントが同期状態を検収した後にだけ次の工程へ進める。
GitHubのdefault branchは`master`のまま維持する。

`master-release-pr`という固定名のactiveなbranch rulesetを1件だけ使用する。
rulesetのbypass主体は空にし、PR経由の更新、会話threadの解決、最新の`master`を含む次の9必須check、削除禁止およびforce push禁止を設定する。

- `test-linux`
- `test-windows`
- `python-lint (3.15)`
- `pytest (3.15)`
- `pytest-toolkit (3.15)`
- `rust-lint`
- `statusline-version`
- `browser-e2e`
- `update-dotfiles-upgrade (windows)`

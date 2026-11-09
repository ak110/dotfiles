# Claude Code・Codex Hook実装ガイドライン：matcher設定

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## matcher設定

ツール名に基づいて`matcher`を評価するイベントは、`PreToolUse`、`PostToolUse`、`PostToolUseFailure`、`PermissionRequest`、`PermissionDenied`の5つである。
`"*"`、空文字列、およびキー自体の省略はすべてのツールに一致する。
英数字、`_`、`-`、空白、`,`、`|`のみで構成される文字列は、`|`または`,`で区切られたツール名の完全一致として評価される。
それ以外の文字を含む文字列は、先頭と末尾を固定しないJavaScriptの正規表現として評価される。
すべてのツールに一致させる新規登録では明示的に`"*"`を記述し（努力目標）、ツール名で評価しないイベントでは`matcher`キーを省略する。
一次資料は<https://code.claude.com/docs/en/hooks.md>の`Matcher patterns`節である。
監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks.md：matcher設定：2026年9月4日」にある。

- `matcher`の対象を広く設定した場合は、スクリプト側で`tool_name`を確認して対象外のツールを早期リターンする。
- Codexに対しては、ツール名を明示して対象を絞り込んだmatcherを提供する。
  Claude Code向けの全ツール一致のmatcherをそのまま渡すと、入力仕様を確認していないツールでもハンドラーが意図せず起動してしまうためである。

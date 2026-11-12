# dotfilesの公開前の検証

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 公開前の検証を実行する前 | `agent-basics:check-execution/SKILL.md` |
| 公開前に実行する検証を選定する前 | `agent-toolkit:commit/references/publish.md` |
| 公開前の統合差分に`rust/claude-statusline/`の変更がある時、statusline採番規定を確認する前 | `../../release-dotfiles/SKILL.md` |

## 公開前の検証

- 公開前に実行する検証は、`agent-toolkit:commit`の`references/publish.md`「検証とCI」で選定する。
  ローカル全体検証が必要と判定された場合は、`make sync`で同期した後に`uv run --frozen pyfltr run --no-fix --pytest-args='--maxfail=10'`を用いる。
  失敗の打ち切り件数は`verification-values-execution.md`「検証コマンドの実行」に従う。
  `agent-basics:check-execution`を起動し、標準出力と標準エラー出力を保存して結果を確認できる手段（例: `agents_server`の`start`ツールで`shell`を指定）で実行する。
  - 公開担当は統合差分の配布対象ごとに採番要否を確定し、レーンからの引渡しと比較する。
    `rust/claude-statusline/`に変更があれば、`release-dotfiles`の採番規定をpush前に適用し、最終検証のpyfltrへ`--enable=statusline-version`を指定する。
    同チェックの既定無効は維持し、他の全体検証の成功と同チェックの成功を区別する。

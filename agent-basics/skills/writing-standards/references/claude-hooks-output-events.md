# Hookの出力フィールド：PermissionRequest

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## PermissionRequest

確認ダイアログの表示時に発火し、ユーザーに代わってツールの実行許可や拒否を判定する。
PreToolUseとは異なり、`hookSpecificOutput`の直下に`decision`オブジェクトを配置し、`hookEventName`には`"PermissionRequest"`を指定する。

組み込みのdenyルールやaskルールは、hookの戻り値にかかわらず評価される。
たとえば`.claude/`配下への書き込み確認といった組み込みのaskルールは、PreToolUseで`allow`を返しても上書きできないが、PermissionRequestで`decision.behavior: "allow"`を返すことでダイアログの表示を抑制できる。
なお、組み込みのdenyルールは`allow`を返しても強制的に適用される。
`updatedInput`は入力値を変更するだけで確認ダイアログ自体は残るため、確認ダイアログが表示されるような入力を拒否したい場合はブロックとして処理する。

`Read`ツールのdeny規則でディレクトリの指定がないglobパターンを使うと、gitignore構文に従って任意の深さで一致するため、ディレクトリを走査する読み取りコマンドが一律で確認ダイアログの対象になる。
このダイアログはPermissionRequestの`allow`でも抑制できないため、保護対象のファイルは走査対象全体を巻き込まないように具体的なパスでリポジトリの設定に記述する。

`matcher`はツール名（`Bash`、`Edit|Write`など）で評価する。
入力payloadには`tool_name`や`tool_input`に加えて`permission_suggestions`配列が含まれる。

## UserPromptSubmit

エージェントがそのターンの応答を生成する前に、`hookSpecificOutput.additionalContext`を用いてユーザー発話の先頭にコンテキストを前置注入する。
Claude Codeでは`decision`とは独立して注入でき、標準出力（stdout）へのテキスト出力もコンテキストに追加される。
Codexでは`hookSpecificOutput.hookEventName`に`UserPromptSubmit`を指定して`additionalContext`を返す。

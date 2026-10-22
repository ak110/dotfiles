# Hookの出力フィールド

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/claude-hooks-output.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-output.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-output.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |

## 出力フィールドの使い分け

イベントごとの出力契約は`agent-toolkit/agent_toolkit/_testing/hook_output_contract.py`が公式のHooksリファレンスをJSON Schemaで写して定める。`agent-toolkit/agent_toolkit/_hooks/output_contract_test.py`が登録済みの全hookの出力がその契約に合うか確かめる。hookを追加または変更する場合は、契約とテストを同じ変更単位で更新する。

永続ログは`_hooks.stop_session.append_stop_log`等の専用APIに集約する。

## UserPromptSubmit

Claude CodeのUserPromptSubmit payloadは現在名`session_title`を持つ。
Pythonのコマンドhookは`hookSpecificOutput.sessionTitle`で名前を設定し、注記を同じオブジェクトの`additionalContext`へ返す。
現在名が空か前回自動設定した名前と同じ場合だけ更新し、手動で付けた名前を保つ。
命名用の種別はPostToolUse(Skill)と手動起動時のUserPromptSubmitが通常状態へ記録し、process-loopの環境印と計画名から名前を選ぶ。
Codexでは名前を設定しない。

応答契約を注入する注記は、本文の種別で頻度を分ける。

- 開始・`clear`・会話圧縮で`agent-toolkit:user-confirmation-and-report`の内容が文脈から失われた後の起動案内は、実ユーザー発話へ同スキルの起動と、不具合の有無・原因・直し方を回答や確認へ含める場合の診断前の`agent-toolkit:bugfix`起動を示す。説明の補足にも同じ起動契機を適用する。Claude Codeは`agent-toolkit:user-confirmation-and-report`の起動で解除するまで、Codexは文脈1つにつき1回だけ返し、機械注入では消費しない
- エージェント向け文書の読込だけを求める注記は、基準文書の所在だけを示す1文とし、経過時間によらず通常発話ごとに返す（介入のたびに規範の所在を想起させ、文脈へ載る量を一定に保つ）
- 判定手順を本文へ持つ注記は、直前の通常発話からの経過時間が閾値以上の通常発話にだけ返す。初回の通常発話には返さないが、基準の時刻は初回から毎回更新する

対象の通常発話はユーザーが入力したスラッシュコマンドを含み、ハーネスが挿入した通知と機械注入のターンは対象外とする。成立した注記は1つの`additionalContext`へ結合し、Codex payloadでも同じく返す。

ユーザーが書いた文は、Claude Codeの`AskUserQuestion`の回答（提示した`label`と一致しない`answers`の値、`response`、`annotations`の`notes`）としてPostToolUseにも届く。発話の内容を判定材料とする注記は両hookで同じ定義の本文を使い、`AskUserQuestion`の自由記述へは、1回の質問につき1回しか生じないため閾値を適用せず毎回返す。

## 組織向け設定

利用上限の`rate_limit`はStopFailureのコマンドhookが記録し、`turn.complete`が消費して既存の再開予約へ接続する。

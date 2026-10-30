# Hookの出力フィールド

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/claude-hooks-output.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-output.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-output.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `session-state-and-flags.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/session-state-and-flags.md` |

## 出力フィールドの使い分け

イベントごとの出力契約は、`agent-toolkit/agent_toolkit/_testing/hook_output_contract.py`が公式のHooksリファレンスをJSON Schemaで定義している。また、`agent-toolkit/agent_toolkit/_hooks/output_contract_test.py`が登録済みの全hookの出力がその契約を満たしているかを検証する。hookを追加または変更する場合は、契約定義とテストを同一の変更単位で更新する。

永続ログの出力は、`_hooks.stop_session.append_stop_log`などの専用APIに集約する。

## UserPromptSubmit

Claude Codeの`UserPromptSubmit` payloadは、セッションの現在名を表す`session_title`を保持する。
Pythonのコマンドhookは`hookSpecificOutput.sessionTitle`でタイトルを設定し、注記は同じオブジェクトの`additionalContext`に含めて返す。
現在名が空であるか、前回自動設定した名前と一致する場合にのみ更新し、ユーザーが手動で設定したタイトルを維持する。
命名用の種別は`PostToolUse(Skill)`および手動起動時の`UserPromptSubmit`が通常状態に記録し、`process-loop`の環境印と計画名からタイトルを決定する。
なお、セッションタイトルの設定はClaude Codeに限定する。

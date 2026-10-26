# Hookの出力フィールド

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/claude-hooks-output.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkitが所有する運用条件である。

## 名指し参照の読込方法

本文の各参照における適用条件を守り、選択した資料を読み込み、指定箇所に注目してから、その基準に基づく具体的な判断へ進む。起動時の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-output.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-output.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |

## 出力フィールドの使い分け

イベントごとの出力契約は、`agent-toolkit/agent_toolkit/_testing/hook_output_contract.py`が公式のHooksリファレンスをJSON Schemaで定義している。また、`agent-toolkit/agent_toolkit/_hooks/output_contract_test.py`が登録済みの全hookの出力がその契約を満たしているかを検証する。hookを追加または変更する場合は、契約定義とテストを同一の変更単位で更新する。

永続ログの出力は、`_hooks.stop_session.append_stop_log`などの専用APIに集約する。

## UserPromptSubmit

Claude Codeの`UserPromptSubmit` payloadは、セッションの現在名を表す`session_title`を保持する。
Pythonのコマンドhookは`hookSpecificOutput.sessionTitle`でタイトルを設定し、注記は同じオブジェクトの`additionalContext`に含めて返す。
現在名が空であるか、前回自動設定した名前と一致する場合にのみ更新し、ユーザーが手動で設定したタイトルを維持する。
命名用の種別は`PostToolUse(Skill)`および手動起動時の`UserPromptSubmit`が通常状態に記録し、`process-loop`の環境印と計画名からタイトルを決定する。
なお、セッションタイトルの設定はClaude Codeに限定する。

セッション開始時、`clear`実行後、および会話圧縮後の最初の実ユーザー発話に対して、`agent-toolkit:user-confirmation-and-report`の起動を案内する。また、不具合の有無・原因・修正方針を回答や確認に含める場合は、診断前に`agent-toolkit:bugfix`を起動するよう案内する。両ホストにおいて会話文脈ごとに1回配送し、機械的な注入処理では配送待ち状態を消費しない。発話内容と現物の突き合わせ作業は、同スキルの「原文の保持と取り込み」および「確認要否の判定」に従う。時間の経過のみを理由とした固定手順の再注入は行わない。

案内の対象となる通常発話には、ユーザーが入力したスラッシュコマンドを含める。ハーネスが挿入した通知や機械注入によるターンは対象外とする。生成された注記は単一の`additionalContext`に統合し、Codex payloadにおいても同様に返却する。

ユーザーが入力した文章は、Claude Codeの`AskUserQuestion`の回答（提示した`label`と一致しない`answers`の値、`response`、`annotations`の`notes`）として`PostToolUse`にも届く。発話内容を判定材料とする注記は両hookで同一の定義本文を用い、原因・対象・対処の変化に応じた共通の新規性判定に基づいて配送する。

## 組織向け設定

利用上限（`rate_limit`）は`StopFailure`のコマンドhookが記録し、`turn.complete`がそれを取得して既存の再開予約処理に接続する。

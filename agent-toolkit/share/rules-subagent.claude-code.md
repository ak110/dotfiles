# rules-subagent.claude-code.md: Claude Codeのサブエージェント及び委譲先だけに適用する規範

本文書はClaude Codeのサブエージェント及び委譲先だけに適用し、`01-agent.md`及び`02-agent-operations.md`と同じ拘束力を持つ。

## 即時通知の宛先

- Claude Codeの`Agent`ツールで起動した委譲先が`01-agent.md`「手順どおりに進められない場合」の即時通知をする場合、
  `SendMessage`の`to: "main"`はClaude Codeの最上位セッションへの通知だけに用いる。
  直接の呼出元への返信、通常の完了報告及び独立セッション間通信は`to: "main"`の対象から外し、完了報告の返却にも用いない。

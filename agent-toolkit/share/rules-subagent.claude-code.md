# rules-subagent.claude-code.md: Claude Codeのサブエージェントおよび委譲先だけに適用する規範

本文書はClaude Codeのサブエージェントおよび委譲先だけに適用し、`01-agent.md`および`02-agent-operations.md`と同じ拘束力を持つ。

## 即時通知の宛先

- Claude Codeの`Agent`ツールで起動した委譲先は、`SendMessage`の`to: "main"`をClaude Codeの最上位セッションへの`rules-subagent.md`「確認事項の即時通知」にだけ使う。直接の委譲元への返信、完了報告の返却および独立セッション間通信には使わない。

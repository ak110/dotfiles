# rules-subagent.claude-code.md: Claude Codeのサブエージェントおよび委譲先向け規範

この文書はClaude Codeのサブエージェントおよび委譲先に適用し、`01-agent.md`および`02-agent-operations.md`と同等の拘束力を持つ。

## 即時通知の宛先

- Claude Codeの`Agent`ツールで起動された委譲先は、`SendMessage`の`to: "main"`を、Claude Codeの最上位セッションに対する`rules-subagent.md`「確認事項の即時通知」に限定して使用する。直接の委譲元への返信、完了報告の返却、独立したセッション間の通信には使用しない。

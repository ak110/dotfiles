# 監査記録: プロジェクト指示

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## プロジェクト指示のAGENTS.md対応：2026年9月20日

2026年9月20日、Claude Code v2.1.277の公式リリースノート<https://github.com/anthropics/claude-code/releases/tag/v2.1.277>で、`CLAUDE.md`が無いプロジェクトで`AGENTS.md`を読む機能が追加されたことを確認した。
再検証は同バージョンのリリースノートを読み、`AGENTS.md`対応の記載を確認する。

## プロジェクト指示のCLAUDE.mdアダプター：2026年9月26日

2026年9月26日、Claude Codeの公式文書<https://code.claude.com/docs/en/memory.md>で、設定を変えていない状態では`CLAUDE.md`・`.claude/CLAUDE.md`・`CLAUDE.local.md`のいずれかがあると`AGENTS.md`を読まないことを確認した。
該当する記載は次のとおりである。

```text
Because `CLAUDE.local.md` counts, adding one to keep your own uncommitted instructions in a project that relies on `AGENTS.md` stops Claude from reading `AGENTS.md` for you.
```

同日、Claude Code 2.1.283の`claude -p --model haiku`で観測した。
合言葉を書いた`AGENTS.md`と`CLAUDE.local.md`だけを置いたディレクトリでは、合言葉を答えなかった。
`claudize`が置く`# CLAUDE.md`と`@AGENTS.md`の2行のアダプターを加えると、合言葉を答えた。
再検証は同じ構成の一時ディレクトリで、アダプターの有無ごとに`claude -p`へ合言葉を尋ねる。

# 縮退誘発フレーズ集（隔離リファレンス）

`agent-toolkit/rules/agent.md`「セッション分割・別計画化は禁止する」節と、
`agent-toolkit/scripts/pretooluse.py`の`AskUserQuestion`向け縮退誘発検出フックが対象とする禁止フレーズの典拠。

`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節に従い、
機械チェック用辞書の検出語そのものは本ファイルへ隔離する。
メインエージェントは本ファイルを読み込まず、`Explore`サブエージェントまたは`plan-implementer`経由でのみ参照する。
pyfltr機械チェックの対象からも除外する（`pyproject.toml`の`extend-exclude`へ登録する）。

## 対象カテゴリと代表フレーズ

各カテゴリは`agent-toolkit/scripts/pretooluse.py`の`_SCOPE_ESCALATION_PHRASES`定数の正規表現と対応する。
フックはカテゴリ識別子をstderrへ出力し、検出フレーズ本文は転記しない。

| 識別子 | 対象観念 | 代表フレーズ |
| --- | --- | --- |
| `workload` | 作業量過多を根拠とした打診 | 「N件すべてで〜は作業量的に困難」 |
| `single-session` | 1セッション完遂困難を根拠とした打診 | 「1セッションで完遂するのは難しい」 |
| `approach-confirm` | 進め方の確認・相談 | 「進め方を確認したい」 |
| `split-execution` | 分割実行・分割対応の提案 | 「分割して進めたい」 |
| `context-shortage` | 残コンテキスト見積りを根拠とした打診 | 「残コンテキストが不足する見込みのため」 |
| `defer-onset` | 着手延期・別途対応の提案 | 「対応を後回しにしたい」 |
| `priority-consult` | 優先順位の相談 | 「優先順位を相談したい」 |
| `scope-volume` | 対象件数・範囲の多さを根拠とした打診 | 「対象件数が多いため進め方を相談したい」 |

# 監査記録: 規範の文体と配置

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-basics/skills/writing-standards/references/agent-documents-basics.md：責務と構成：2026年10月6日

配送範囲表のAntigravityの2列、作業ディレクトリのプロジェクト規範の行、agent-toolkitのスキルの行、Codexの列の`~/.codex/AGENTS.md`の配置条件の根拠である。

2026年10月6日、`agents_server`の`start`でツールを使わせない委譲先を起動し、初期コンテキストにある文書を答えさせて、各見出しの直後の1文の逐語引用を現物と比べた。
版はClaude Code 2.1.291（claude-agent-sdk 0.2.163）、codex-cli 0.160.1、agy 1.3.0である。
Claudeの`explore`は`rules/`配下だけが届き、`rules-subagent.md`、作業ディレクトリの`AGENTS.md`、スキル（`skills=[]`）は届かなかった。
Claudeの`delegate`は`rules/`配下、`rules-subagent.md`と`rules-subagent.claude-code.md`、作業ディレクトリの`AGENTS.md`、スキルが届いた。
Codexの`explore`は`~/.codex/AGENTS.md`経由で`rules/`配下と`rules-main.codex.md`、およびスキルが届き、`rules-subagent.md`と作業ディレクトリの`AGENTS.md`は届かなかった。
`delegate`はこれらに加えて`rules-subagent.md`と作業ディレクトリの`AGENTS.md`が届いた。
Antigravityの`explore`と`delegate`はどちらも作業ディレクトリの`AGENTS.md`が届き、`rules/`配下は届かなかった。
`rules-subagent.md`は`delegate`だけに届き、`delegate`のスキル一覧は作業ディレクトリの`.agents/skills`配下などだけでagent-toolkitのスキルを含まなかった。
`explore`のスキルは確かめていない。
実装の根拠は次の3つである。
`_agents_server/claude.py`の`_build_options`は軽量起動で`setting_sources=["user"]`と`skills=[]`を渡す。
`codex.py`の`_base_thread_config`は軽量起動で`project_doc_max_bytes=0`を渡す。
`antigravity.py`の`_system_prompt`は起動区分ごとの指示を本文の先頭へ置くだけで、読込範囲を変えない。
`~/.codex/AGENTS.md`は`scripts/sync_codex_agents.py`が生成し、Codex単体のインストーラーは配置しない（`docs/guide/codex-guide.md`冒頭）。
`Agent`ツールのサブエージェントとCodexの組み込み委譲先のスキルの列は観測していないため「未確認」とした。

再検証では`start`の`model_type`へ`codex:<model>`、`agy:<model>`、`claude:<model>`を指定し、ツールを使わない指示とともに同じ問いを渡す。
問いは初期コンテキストの文書一覧と、次の各見出しの直後の1文である。
見出しは`01-agent.md`「QCDと3段判定」、`02-agent-operations.md`「ツール・コマンド運用」、`rules-subagent.md`「確認事項の即時通知」である。
ほかに`rules-main.codex.md`「メインエージェント向け規範」と作業ディレクトリの`AGENTS.md`「記録の所在」を使う。

## agent-basics/skills/writing-standards/references/agent-documents-basics.md：語調と現行規則の表現：2026年10月31日

2026年10月31日、[Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)をHTTP 200で取得した。
対象版はClaude Opus 5.5であり、実モデルの比較実験は行っていない。
同ガイドの「Calibrate effort」はeffortを主な調整手段とし、`xhigh`・`max`は品質向上を測定した作業へ留め、思考を減らす場合はeffortを先に下げることを推奨する。
「Thinking instructions in chat system prompts」は熟考を促す行の削除を検討するよう案内する。
「Safeguard refusals」は内部推論の再現要求が`reasoning_extraction`で拒否されうる一方、短い説明や実施内容の要約は求められると説明する。
費用面の推奨を共通規範の努力目標へ採ることは技術判断であり、全モデルの効果を実証した記録ではない。
再検証は同じURLの対象モデルと上記3節を取得し、effortとプロンプト指示の関係、最上位effortの選定条件、内部推論と短い説明の区別を確認する。
モデル更新時の点検では、[Claudeの共通ガイド](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)や[OpenAIのガイド](https://developers.openai.com/api/docs/guides/prompt-engineering)から使用モデルの資料へ進む。
両URLも同日にHTTP 200を確認し、後者は旧platform URLから上記URLへの転送を確認した。

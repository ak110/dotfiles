# dotfilesの検証で使う値：agent-doc-tone

## agent-doc-tone

- `agent-doc-tone`は`make test`とcommit時のpre-commit（stageした対象ファイル）で動き、単独では`uv run --frozen pyfltr run --commands=agent-doc-tone`で実行する。
- 対象はエージェントが実行時に読むMarkdown（`AGENTS.md`、`agent-toolkit/`配下のrules・skills・share、`.chezmoi-source/dot_claude/`、`.claude/skills/`）である。
  `agent-toolkit/`配下ではコードのコメント、docstring、表示メッセージ、プロンプト注入文も語彙の判定に含む。
- 指標と閾値は`scripts/check_agent_doc_tone.py`のdocstringにある。
  同スクリプトの`_DENIED_PATTERNS`に一致した説明文は、ファイル名と行番号を表示して終了コード1以上で止まる（pre-commitではcommitが中断される）。
  `_CAUTION_PATTERNS`は文脈で対象や動作が曖昧になりやすい表現で、警告だけを出して終了コード0で終わる。
  正確な専門用語と合意済みの正式名称はそのまま保ち、引用した原文、検出用の定義データ、保存形式の名称も用途を区別してそのまま保持する。
- 密度の超過では、診断の1行目が超過した指標の比率、件数、閾値以下へ抑える最小の書換え件数を示し、続く詳細が否定形で終わる文と「当該」を行番号と抜粋付きで全て挙げる。
  引用ブロックの段落は本文として数え、コードブロックと検出用データは密度の計算から除く。
  否定形終端率は文の数、「当該」の出現率は出現の回数で数えるため、同じ文に複数回出た場合は出現ごとに書き換える。
- 挙がった箇所から必要な件数を選び、否定形の宣言は肯定形へ、法令調の指示語は指す対象が具体的に伝わる表現へ、文全体の構成を見直して書き直す（語彙の言い換えは`agent-basics:writing-standards`のtextlintの指摘の直し方に従う）。

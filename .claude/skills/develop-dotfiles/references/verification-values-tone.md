# dotfilesの検証で使う値：agent-doc-tone

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| テストとCIの所要基準を使う前 | `../../../../agent-basics/skills/writing-standards/references/testing-speed.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing-speed.md` |
| テストとCIの所要基準を使う前 | `agent-basics:check-execution/references/diagnostics-results.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/diagnostics-results.md` |
| 共有実行枠のコマンドをシェルから起動する前 | `agent-toolkit:/share/check-execution-operations-launch.md` | 判断 | `read:agent-toolkit:*/share/check-execution-operations-launch.md` |

## agent-doc-tone

- `make test`およびコミット時のpre-commit（ステージされた対象ファイル向け）によって`agent-doc-tone`が実行される。
  単独で実行する場合は`uv run --frozen pyfltr run --commands=agent-doc-tone`を使用する。
- チェックの対象は、エージェントが実行時に参照するMarkdownファイル（`AGENTS.md`、`agent-toolkit/`配下のrules・skills・share、`.chezmoi-source/dot_claude/`、`.claude/skills/`）である。
  なお`agent-toolkit/`配下では、コード内のコメント、docstring、表示メッセージ、プロンプト注入文も語彙判定の対象に含まれる。
- 文体の密度が規定の閾値を超えたファイルは、測定指標とともに出力される（指標と閾値の詳細は`scripts/check_agent_doc_tone.py`のdocstringに記載されている）。
  同スクリプトの`_DENIED_PATTERNS`に定義された禁止パターンに説明文が一致した場合は、該当ファイル名と行番号を表示して終了コード1以上で停止する（pre-commitではコミットが中断される）。
- 文脈によって対象や動作が曖昧になりやすい表現（同スクリプトの`_CAUTION_PATTERNS`）については、警告メッセージのみを出力して終了コード0で終了する。
  これは文脈上意味が正しく伝わるかを確認するための補助機能であり、正確な専門用語や合意済みの正式名称はそのまま維持する。
  自身で作成した適切な用例や説明は通常のチェックへ通し、引用した原文、検出用の定義データ、保存形式の名称などは用途を区別してそのまま保持する。
- 密度が閾値を超過した場合、診断結果の1行目に超過した各指標の比率、該当件数、および閾値以下へ抑えるために必要な最小書き換え件数が表示される。
- 続く詳細診断では、本文中から検出された否定形で終わる文や「当該」の使用箇所が、行番号および該当箇所の抜粋とともにすべて列挙される。
  引用ブロック内の段落も本文としてカウントされるが、コードブロックや検出用データは密度の計算対象（分子・分母）から除外される。
  否定形終端率は文の数を基準とし、「当該」の出現率は単語の出現回数を基準としてカウントするため、同一の文に複数回出現した場合は出現ごとに書き換える。
- 列挙された検出箇所の中から必要な件数を選び、否定形による宣言文は肯定形の表現へ、法令調の指示語は指示対象が具体的に伝わる表現へ書き直す。
  語彙の再利用を避ける際は、`agent-basics:writing-standards`におけるtextlint指摘の修正方針に準拠し、文全体の構成を見直して書き直す。

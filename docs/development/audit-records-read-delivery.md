# 監査記録: Codexの資料取得と出力予算

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/skills/atk-mode/playbooks/host-codex.md：Codex固有の入出力：2026年10月30日

省略時の回復の現行契約と、撤去した取得量の推定の経緯を指す。
以下の9月29日の測定は、撤去済みの容量警告の判断根拠として残すものであり、48KiBを現行の読み取り可否の基準にはしない。

2026年9月29日、tiktoken 0.14.0のo200k_baseで`agent-toolkit/skills/`・`share/`・`rules/`配下のMarkdownを数えた。
分割前の最大の文書`agent-toolkit/skills/atk-mode/playbooks/wi-drafting.md`（60,005バイト）は16,945トークンだった。
分割後の137件では、最大が13,176トークン（`skills/atk-mode/references/plan-format.md`、45,749バイト）だった。
1トークンあたりバイト数の最小は3.10（`skills/atk-mode/references/plan-legacy-formats.md`）だった。
配布設定`share/codex_config.toml`（当時は`scripts/codex_config.toml`）の`tool_output_token_limit`は20000である。
20,000トークンと3.10バイトの積は約62,000バイトであり、実行セルが本文へ付加する分の余裕を取って閾値を48KiB（49,152バイト）とした。
48KiBの文書は最悪の比率でも約15,900トークンで、上限に対して約4,000トークンの余裕が残る。
再検証では同じ集合をo200k_baseで数えて1トークンあたりバイト数の最小値を測り直し、配布設定の`tool_output_token_limit`との積が閾値と付加分の余裕を上回るか確かめる。

### 補完読取と出力予算の契約更新（2026年10月30日）

前述の48KiBは当時の大量読取を案内する閾値であり、個々の出力が省略されない保証値ではない。
当時は文章主体の内側`max_output_tokens`をUTF-8バイト数÷3の切上げ以上、外側セルを内側予算の合計以上としていた。
nativeの`original token count`が設定予算を超えた場合は、その値以上で1回再取得し、文脈容量を超える本文は分割していた。
この見積りは全資料・全モデルへ一般化した実測値ではなく、現行の取得量や読取方式の判定には使わない。

2026年10月30日から31日まで、PostToolUseが省略された読取を元turnのsandbox読み取り権限の範囲で再取得し、本文を追加コンテキストへ配送していた（`codex_read_delivery.py`）。
この補完読み取りと、それに合わせてPreの容量案内を省く判定は、2026年10月31日に本文配送とともに撤去した。
当時は`codex_read_observation.py`が省略の無い成功読取だけを読了としていた。
読了の記録は2026年11月20日に撤去した。
現在のPostToolUseは、`agent-toolkit/agent_toolkit/_hooks/codex_truncation.py`で省略の包絡（`Warning: truncated output (original token count: <数>)`）を識別する。
省略を識別した結果へは、元の量と回収の操作を示す通知を返す。
再検証では省略の包絡を含む結果へ通知が付き、本文を返さないことを公開PostToolUseで確かめる。
取得量の予測による容量警告は撤去し、実際の省略を`codex_truncation_test.py`で確かめる。
本節は確定契約と実装の参照先を記録するものであり、文書更新担当によるテストの再実行結果ではない。

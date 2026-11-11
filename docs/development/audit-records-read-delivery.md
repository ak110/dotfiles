# 監査記録: 資料取得と読込表

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/agent_toolkit/_hooks/pretooluse/large_reads.py：全文取得の閾値：2026年9月29日

2026年9月29日、tiktoken 0.14.0のo200k_baseで`agent-toolkit/skills/`・`share/`・`rules/`配下のMarkdownを数えた。
分割前の最大の文書`agent-toolkit/skills/wi-standards/SKILL.md`（60,005バイト）は16,945トークンだった。
分割後の137件では、最大が13,176トークン（`skills/write-plan/references/plan-file-standards.md`、45,749バイト）だった。
1トークンあたりバイト数の最小は3.10（`skills/write-plan/references/legacy-plan-file-standards.md`）だった。
配布設定`share/codex_config.toml`（当時は`scripts/codex_config.toml`）の`tool_output_token_limit`は20000である。
20,000トークンと3.10バイトの積は約62,000バイトであり、実行セルが本文へ付加する分の余裕を取って閾値を48KiB（49,152バイト）とした。
48KiBの文書は最悪の比率でも約15,900トークンで、上限に対して約4,000トークンの余裕が残る。
再検証では同じ集合をo200k_baseで数えて1トークンあたりバイト数の最小値を測り直し、配布設定の`tool_output_token_limit`との積が閾値と付加分の余裕を上回るか確かめる。

### 補完読取と出力予算の契約更新（2026年10月30日）

前述の48KiBは大量読取を案内する閾値であり、個々の出力が省略されない保証値ではない。
文章主体の内側`max_output_tokens`はUTF-8バイト数÷3の切上げ以上、外側セルは内側予算の合計以上とする。
nativeの`original token count`が設定予算を超えた場合は、その値以上で1回再取得し、文脈容量を超える本文は分割する。
この値は取得予算の最低値であり、前述の測定を新たな全資料・全モデルへ一般化した実測値ではない。

2026年10月30日から31日まで、PostToolUseが省略された読取を元turnのsandbox読取権限の範囲で再取得し、本文を追加コンテキストへ配送していた（`codex_read_delivery.py`）。
この補完読取と、それに合わせてPreの容量案内を省く判定は、2026年10月31日に本文配送とともに撤去した。
当時は`codex_read_observation.py`が省略の無い成功読取だけを読了としていた。
現行の取得成功契約と構造は[design-hooks.md](design-hooks-read-declarations.md#宣言型読込表による操作前配送)にある。
再検証では`codex_read_observation_test.py`と`pretooluse/large_reads_test.py`で、省略の有無ごとの読了判定、本文を返さないこと、および単一・複数ファイルの予算案内を比較する。
本節は確定契約と実装の参照先を記録するものであり、文書更新担当によるテストの再実行結果ではない。

## agent-toolkit/agent_toolkit/_hooks/pretooluse/read_delivery.py：並行Readの公開入力：2026年10月30日

独立した観測担当の記録によると、Claude Code 2.1.314、Sonnet 5.5、effort mediumで、Readだけを公開した隔離セッションへ2ファイルの同時取得を指示した。
各ReadのPreToolUseを一時settingsのhookで捕捉し、プロセスは終了0、標準エラーは空、最終出力は両方の本文を報告した。
捕捉した2入力では、それぞれのtranscriptにassistant型のレコードは0件で、payloadのtool_use_idとtool_inputも現在の1件だけを示した。
この観測を根拠に、Pre時点のtranscriptだけでは同じ応答の並行相手の取得先を確定できないと判断した。
原記録はリポジトリ外に保存されている。

確定した案内方式は、純粋Read・静的Bash読取・Skill起動では依存案内を保留し、次の非取得操作で残る未取得資料を案内するものである。
当時の案内は本文一致と全文被覆の取得判定、および案内済み資料を未取得のまま再書込する場合の遮断を維持していた。
現行契約は前節の設計資料を参照する。
この観測は変更後の案内方式の動作検証ではない。
再検証では同じ版条件とReadのみの許可を使い、一時settingsで各Pre入力とtranscriptを保存し、現在のtool_useと並行相手の可視性を比べる。
変更後の案内は`read_delivery_test.py`と`read_delivery_dispatch_test.py`で、純粋取得後の成功・失敗・省略と、次の非取得操作・再書込・全文取得後の解除を比較する。

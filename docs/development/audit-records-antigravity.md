# 監査記録: Antigravity

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/agent_toolkit/_atk/config.py：Antigravity CLIのモデル指定：2026年9月18日

2026年9月18日、Antigravity CLI 1.2.5（`/home/aki/.local/bin/agy`）で確認した。
`agy models`は終了コード0で14件を返し、各行は推論の深さを含む完全スラッグと表示名をタブで区切る。
完全スラッグは`gemini-3.8-flash-high`、`gemini-3.8-flash-medium`、`gemini-3.8-flash-low`のように、ベース名へ`-high`・`-medium`・`-low`を付けた形である。
`gemini-3.1-pro`は`-high`と`-low`だけを持つ。
一方、`claude-sonnet-4-6`・`claude-opus-4-6-thinking`・`gpt-oss-120b-medium`には推論の深さごとに接尾辞を変える分岐が無い。
`--model`は推論の深さを含まないベース名も受理する。
`agy -p 'reply with OK only' --model gemini-3.8-flash`は終了コード1で終わった。
標準エラーの本文は次のとおりである。

```text
error: invalid model selection (--model "gemini-3.8-flash" --effort ""): --model gemini-3.8-flash requires --effort (available: low, medium, high)
```

同じ呼び出しへ`--effort low`を加えると終了コード0で終わり、標準出力へ`OK`だけを書いた。
この検証により、`--model`へベース名を渡し`--effort`を別に渡す`agent_toolkit/_agents_server/antigravity.py`の`build_command`の形が、現行版で成立することを確認した。
再検証は`agy models`の出力から完全スラッグの接尾辞の有無を確認し、`agy -p 'reply with OK only' --model <ベース名>`を`--effort`の有無で1回ずつ実行して終了コードと標準エラーを比べる。

2026年9月23日、Antigravity CLI 1.2.9で非対話実行の時間指定を確認した。
`agy --help`の`--print-timeout`は指定しない場合の値を`0s`と示す。
`--print-timeout 3600`は単位不足として拒否された。
次のコマンドは終了コード0となり、`init`、`step_update`、`result`のイベントを返した。

```sh
agy -p 'reply with OK only' --model gemini-3.8-flash --effort medium --output-format stream-json --print-timeout 3600s
```

2026年9月24日の同版の出力では、`result`イベントの`status`と`response`は内側の`result`オブジェクトにあり、状態値は`SUCCESS`、本文は`OK`であった。
作業ツリーの`start_custom`で同じ候補を起動した結果もagyのsessionが`completed`となり、待機応答の本文に`OK`を受け取った。
同日、同版へ「検証完了」とだけ答える指示を与えた。
`--output-format stream-json --print-timeout 120s --model gemini-3.8-flash --effort low --mode plan --disable-slash-commands`で実行すると、終了コード0で5行のJSONLを返した。
イベントは`init`が1行、`step_update`が3行、`result`が1行で、最後の`result.response`は`検証完了`だった。
`step_update`の2行には`text_delta`があり、公開ストリームに表示用の本文が含まれることを確認した。
標準エラーには、slash command expansionを無効にすると`--mode plan`が適用されないという説明が1行出た。
再検証は`agy --version`で版数を記録し、同じ指示で`--print-timeout`を`3600`と`3600s`へ変えて終了コード、標準エラーおよびイベント種別を確認する。

## pytools/_internal/antigravity_plugin.py：常時規範の容量：2026年11月12日

Antigravity CLI 1.5.6で、共通常時規範の末尾が新規セッションへ届くことを確認した。
`agy -p`へ、ツールを使わず起動時の`02-agent-operations.md`の最終文だけを答える入力を与えた。
モデルと実行モードは`--model gemini-3.8-flash --effort low --mode plan`を指定した。
`--settings '{"toolPermission":"strict"}' --output-format stream-json --print-timeout 120s`も指定した。
終了コードは0で、回答は生成した2つ目の規範の最終文と一致した。
出力のイベントにツール実行は無かった。
この観測時の生成物は23,888バイトと6,157バイトであり、本文の移設は行っていない。

`usage.input_tokens`の22,225は、ツール定義なども含むセッション全体の入力である。
規範単体の実トークン数として扱わない。
公開CLIのヘルプと[対話用のcontextパネル](https://antigravity.google/docs/cli/commands/context/)からは
規範単体をモデルの方式で数える非対話操作を確定できなかった。
[Gemini APIの計数](https://ai.google.dev/gemini-api/docs/tokens)にはモデル別の`count_tokens`があるが、
確認した環境ではAPIキーが設定されておらず、単体の実数は未測定である。
したがって、実数が小さいという仮定で換算を緩めず、既存の2 bytes/tokenを保つ。

[公式のRules資料](https://antigravity.google/docs/rules/)の容量の節は、1ファイル24,000バイトを超えた場合の切り捨てと、
常時規範の合計20,000トークンを超えた場合に大きいファイルから参照形式へ変える動作を説明している。
生成器はこれらの上限と超過時の失敗検査を維持する。
整理後の目安は上限の85%である17,000トークンとし、エージェントが定めた値とする。
比較の出発点80〜85%のうち、必要な移設量を抑える85%を選んだ。
超過是正ごとに3,000トークン（既存換算で約6,000バイト）の追記余裕を戻す。
過去に1文の置換で推定値が約54トークン増えた報告があり、複数回の追記を吸収する余裕と判断した。
この報告は通常の追記量の分布の実測ではない。
17,000は生成を拒否する第2の上限にはせず、20,000以下では容量の追加測定・記録・削減を求めない。

再確認は同じモデルと版で新規セッションへ最終文を答えさせ、生成物と逐語比較する。
規範単体のモデル計数を取得できる環境では、生成した全規範を`count_tokens`で数え、換算の安全幅を再評価する。
生成の境界と全文保持は`test_only_exceeding_budget_requires_reorganization`、
`test_size_failure_keeps_previous_distribution`と`antigravity_rules_invariant_test.py`で確認する。
上限以下の入力は整理後水準を超えても生成でき、超過時だけ水準以下への是正を案内する。

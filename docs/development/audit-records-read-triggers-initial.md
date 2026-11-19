# 読込表の監査記録: 分類、本文配送の撤去と全文取得の観測

## agent-toolkit/share/read-trigger-bootstrap.md：操作を契機とする共有資料：2026年10月27日

本節は、読込表の宣言と表外の資料参照を分類し、取得を案内する経路と対応付けた記録である。
比較基準は2026年10月26日の`c31bea2b6`で、対象は`agent-toolkit/skills/`、`agent-toolkit/share/`、`.claude/skills/`、`agent-basics/skills/`の373 Markdownだった。
原本の保存先はprivate-notesの`plans/2026/10/27-0550_process-wi_レーン05.read-delivery/`で、次の表のファイル名を使う。
初回保存版はprivate-notesのcommit `0168b0a8e`にある。
初回原本には取得時のdotfiles HEADの記録が無いため、各行の`現行sourceのSHA256`で取得対象の版を特定する。

| 本節の呼称 | 原本ファイル | 主な列 |
| --- | --- | --- |
| 全宣言表 | `read-delivery-20261027-routes.tsv` | `種別`、`照合状態`、変更属性、`宣言位置`、`基準位置`、`現行sourceのSHA256` |
| 表外指示の探索表 | `read-delivery-20261027-search.tsv` | `検索語`、`位置`、`形態分類` |
| 参照経路表 | `read-delivery-20261027-references.tsv` | `検索語`、`位置`、`参照先`、`結果` |

表外指示の検索語は`全文読`、`に従う`、`が定める`、`を参照`である。
取得時は語ごとに`git grep -n -F -e '<検索語>' -- agent-toolkit/skills/ agent-toolkit/share/ .claude/skills/ agent-basics/skills/`を実行した。
再取得では、4対象根のMarkdownを`agent_toolkit._common.read_triggers.parse_tables`で解析し、sourceと行番号で重複を除く。
宣言の契機・条件・対象・配送操作を同じsourceの比較基準版と対応付け、各属性を別々に数える。
前掲の4語を各々の固定文字列検索で取得し、表の行・本文の規定参照・説明・コードフェンス・所在参照を分ける。
本文の名指し資料は`validate_reference_routes`で、本文内とsidecarの宣言、常時規範、名指し参照の短節と対応させる。
`read_triggers_invariant_test.py`で宣言・参照先・見出し・先行経路を確認する。

廃止前の代表実機はClaude Code 2.1.311の新規会話で、Pythonの可変デフォルト引数の不具合調査と原因分析節を持つ短い報告書の新規作成を自然な入力で求めた。
資料取得やhookの案内を探す指示は含めなかった。
WriteのPreToolUseは原因分析資料の全文32,697文字を返し、ホストは追加文脈をtool-resultsの保存ファイルへ移した。
保存本文は末尾改行を除きhook出力と一致した。
順序はWrite入力の生成、hookの本文出力、Write成功、最終応答で、後続Readは観測されなかった。
モデルが判断前に本文を利用したかは未検証である。
この観測は廃止前の方式に限り、新方式の達成根拠にはしない。

本文をhookで代替配送する方式は同月19日の既存判断に反した。
宣言型読込表の導入時に再導入された本文配送と、その後の宣言順変更は、後続の全文取得方式で撤去した。
40,000文字の内部配送予算とホスト保存先のRead分割取得案内を撤去した。
案内方式へ戻した作業途中の版で、同じ自然入力を使った新規会話を観測した。
この途中版は公開履歴の版として再取得する根拠に使わない。
Claude Code 2.1.311では、`agent-basics:bugfix`のSkill起動をhookが取得済みと記録した。
詳細資料へのRead案内も届いたが、会話全体にそのReadはなく、原因分析を含むWrite入力がWriteのhook案内より先に生成された。
全11hookとプロセスの終了コードは0だったが、詳細資料の取得と判断前の利用は確認できなかった。
この各1回の観測から、別会話での取得や全対象の判断前利用を保証しない。

以下は、2026年11月11日の分割で見出しを失った本節の続き（分類の再取得、全文取得方式の観測、再検証の手順）の要約である。
数値の全量と中間版のSHA256はGitの履歴にある。

分類は同じ4対象根と比較基準で取り直した。
全文取得方式で最後に取得した分類は、宣言1790行（判断1741、操作34、起動時15）、4語の検索1616行、参照分類1313行で、未確定と照合不可は0行だった。
宣言を含む249 sourceのSHA256は、分類の取得時の作業ツリーの本文と一致した。
この参照分類は4語の候補に限り、候補外を含む宣言経路は`read_triggers_invariant_test.py`のstrict検査で確かめる。
指定節は注目箇所であり、取得済みの判定単位はファイル全文とする。
原本の節指定列は宣言の内容を表し、取得済み状態や実機での成功を示さない。
2回目と中間取得時の原本は保存先のGit履歴に無く、その件数とSHA256は参考値で、その版は再取得できない。
全文取得方式の原本は次の取得時SHA256で特定し、行末はLFである。
この分類を再取得する場合は、比較基準`c31bea2b6`と、保存したroutesのsource SHAが一致する公開履歴の版を選び、前掲の4語の独立検索と宣言・参照の対応付けを行う。

| 原本ファイル | 全文取得方式の取得時SHA256 |
| --- | --- |
| `read-delivery-20261027-routes.tsv` | `42fbc98017b58b4a2db9965ba46714e23562803c9fba8d4a6d64b95a8723b7d6` |
| `read-delivery-20261027-search.tsv` | `40c41efeadf4e63e72b1bb901306feb4724695b0fd2571787a4a1cfcc2992cde` |
| `read-delivery-20261027-references.tsv` | `96a36680f1a74e5cad6656e7ae059cc0084f25ffa31ab65cc3e3deec7e0bbbc5` |

全文取得方式の代表観測はClaude Code 2.1.311で、`b28807ac95b2e7b2059bb6a670f4daf1ce098f7f`に未commitの変更を加えた版で行った。
自然会話では、Write後の案内、Read、再WriteとEditを観測したが、180秒上限で終わり最終応答はなかった。
制御会話では同じPython資料の1〜50行、52行以降、51行の順にReadし、途中に未取得範囲がある間は全文取得済みにならず、51行の取得後に全文取得済みとなった。
後続の自然会話は終了0で最終応答を得たが、basics側の原因分析資料は前半のRead出力が省略され、後半だけが記録されて全文取得済みにはならなかった。
このときは`--plugin-dir`で指定したagent-toolkit 2.228.0と、インストール済みの別の場所のagent-basics 1.4.0を読み込み、登録元とRead先の本文の同一性は確かめていない。
登録元とRead先の一致は、1つのworktreeの2 pluginを明示し、許可ツールをReadとSkill、上限を120秒とした別の制御観測で確かめた。
この制御観測の版はClaude Code 2.1.311、agent-toolkit 2.228.0、agent-basics 1.4.0で、終了0・result successだった。
`agent-basics:bugfix`のSkillと`agent-basics/skills/bugfix/SKILL.md`のReadを比べ、Readは121行中121行で省略が無かった。
Read本文のSHA256は`301587105fd4b30d287e954ef3d263007c7787d7cf78d44c383c3cffcc10d494`で、Skill本文とRead本文は同じworktreeの資料と一致した。
この制御観測の3 sourceは再検証で選ぶ版と一致した。
自然会話と、同名pluginが複数登録された環境での優先順位は、この制御結果の対象外である。
観測した宣言は原因分析の操作と判断の2宣言とPython資料の1宣言に限り、他の宣言の個別の取得順と、公開イベントのないモデル内部の判断や利用は観測していない。

現行方式の再観測では、起動前に対象worktreeのcommit OIDと未commit差分、agent-toolkitとagent-basicsのmanifestのversion、後掲の3 sourceのSHA256を記録する。
同時に2 plugin配下の資料ファイルの絶対パスとSHA256を記録し、実際にReadした資料ごとに取得時のSHA256と比べる。
起動は次のコマンドで行い、`<入力>`はPythonの可変デフォルト引数の不具合を調べて原因分析節を持つ短い報告書を新規に作る依頼とする。

```sh
timeout 180 claude -p --no-session-persistence --plugin-dir <worktree>/agent-toolkit --plugin-dir <worktree>/agent-basics --allowedTools Write,Read --output-format stream-json --verbose --include-hook-events '<入力>'
```

streamのplugin登録のpathとversion、SkillのBase directory、Readのpath・本文・hashが対象worktreeと一致することを確かめる。
初回Write、PreToolUseの案内、SkillとRead、再WriteとEditをイベントの順序で確かめ、PostToolUseのRead結果の範囲・省略の有無と、同じ主体・資料hashの取得済み状態を記録する。
未取得範囲、最終応答、終了状態も記録し、公開イベントからモデル内部の利用を推論しない。
出力先は取得前に存在しない作業用パスを使う。
Read本文は取得範囲を同じファイルの対応箇所と比べ、事前に記録していない資料は版の一致を確かめられないものとして扱う。
自然会話とは別に、再検証で選ぶ版（後掲の3 sourceのSHA256が一致する公開版）の`read_delivery.py`と`read_delivery_test.py`で、正常・省略・失敗・誤metadata・分割取得の全文被覆を比べる。
登録元とRead先の一致は、前掲の制御観測と同じく1つのworktreeの2 pluginを明示し、ReadとSkillだけを許可した会話で確かめる。

再検証で選ぶ版は、次の3 sourceのSHA256で識別する。
代表観測と後続自然観測の版は公開履歴からの再現を確認できず、歴史的な参考として扱う。
後続自然観測の後に例外案内と不在資料向け案内を変えたため、その版は再検証で選ぶ版と同一ではない。
正常資料の案内と全文被覆は変えておらず、不在の分岐は公開hook入力を使う別の試験で確かめた。

| 実装source | 再検証で選ぶ版のSHA256 |
| --- | --- |
| `agent-toolkit/agent_toolkit/_hooks/pretooluse/read_delivery.py` | `541dfa9f284eed60737344a2c860ceab721b898fd287f77efd4c7578d9a81153` |
| `agent-toolkit/agent_toolkit/_common/read_triggers.py` | `e89e28d67bde492d15516738cc347b174dd57440450627ab19cbd23a84cd0969` |
| `agent-toolkit/agent_toolkit/_hooks/rules_context.py` | `50361034a30c66546ad75331ec5b9667923147d634f7f55fee22678f6bedb72c` |

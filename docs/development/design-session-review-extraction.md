# 証拠抽出スクリプトの構成の設計記録

[設計記録の索引](design.md)と[振り返り・知見集約の設計記録](design-session-review.md)から参照する主題別の記録である。

## review-sessionの構成と候補抽出

### 証拠抽出スクリプトの構成

`--summary-only`は既存の照会結果から件数・判定・保存先を生成側で選ぶ。
grep・fixed-string・context-at・tool-calls・warn・bundleと統計を対象にし、未解決・エラーの診断と終了コード、結果が参照する記録の由来を保つ。
警告は通常表示と同じ抽出・重複除去後のイベント数を返し、0件も明示する。
統計は総量・親・Claudeサブエージェント・agent threadの要約の順序と全フィールドを保つ。
追加指定を省いた照会とbundleの保存ファイルは全量を使う。
準備文書は前掲の表示予算へ縮約し、必要な全量へ接続する。
表示選択を同じ公開指定へ集約し、消費側ごとの一時選別と集計定義の複製を省く。
既知のCodex詳細は既存のdetailイベントへ入力構造、結果本文、コマンドと終了状態を分けて返し、構造調査はrecord-schemaへ分担する。
候補証拠とprepareも同じ詳細生成を使い、本文のJSON再解析を消費側へ重ねない。
`--reported-improvements`は既存抽出の改善点を出所付きで投影し、同じイベント内の行順と時系列を保つ。
同文の別出所も保持し、0件と未解決記録を区別する。
候補判定の新設や別台帳ではなく、保存全量から必要な本文へ直接到達する照会として追加する。

単一記録とカタログの人間入力検索は、`session_evidence_user_events.py`の抽出と検索を共有する。
カタログは既存の親選定とworkflow・cwdの条件を適用し、選定した親だけを共有の抽出へ渡す。
人間が入力した値への検索と、同じ記録の直前のアシスタント発話の添付を分けることで、質問・選択肢を一致件数へ混ぜず文脈を取得できる。
期間は元記録行の時刻で判定し、時刻なし入力と読取不能記録を未確認範囲として返す。
独自の記録走査を行う別コマンドは、生成本文の判定と2つの実行系の解釈を二重に維持するため採らない。

単一記録の照会は結果の`kind`による区別可能性で併用を判定し、固定順に全照会を評価する。
共通の由来と未解決イベントは一度だけ出力し、終了コードは各照会の最大値とする。
`--grep`・`--hook-notices`・`--user-events`は`summary`を共有し、既存の単独出力との互換性を保つため複数を含む組を拒否する。
ファイル保存型の`--bundle`と入力集合が異なるカタログ・保存済み発話の排他条件は保持する。

`atk run-script session-review-evidence`の起動スクリプト`agent-toolkit/skills/review-session/scripts/session_review_evidence.py`はCLIの引数の解釈と照会モードの振り分けだけを持つ。
記録から時系列イベントへの変換と照会モードが共有する処理は同じディレクトリの`session_evidence_extract.py`、対象記録の解決と付随記録の収集は`session_evidence_records.py`に置く。
照会モードごとの処理は`session_evidence_<照会モード>.py`（`warn`・`detail`・`stats`・`hook_notices`・`user_events`・`tool_calls`・`context`・`catalog`・`bundle`と、`--bundle`が使う問題候補の`candidates`）に置く。
テストは各モジュールの隣の`*_test.py`に置き、複数のテストが使う記録の組み立ては`agent-toolkit/agent_toolkit/_testing/session_evidence_support.py`に置く。

分けたモジュールは、同じディレクトリの兄弟モジュールとして`import`で読む。
`agent-toolkit/agent_toolkit/_atk/run_script.py`はスクリプトを`runpy.run_path`で実行する前にスクリプトのディレクトリを`sys.path`の先頭へ加えるため、起動の形を変えずに読める。
照会モードを追加する場合は、そのモードのモジュールを同じディレクトリへ置き、起動スクリプトの引数と振り分けへ加える。
記録の保存先の規約と行の解釈は`agent-toolkit/agent_toolkit/_atk/session_record_format.py`が持つ。
委譲先の記録の探索は`agent-toolkit/agent_toolkit/_agents_server/record_paths.py`が持ち、本スクリプトは独自の探索を持たない。
照会モードの処理を`agent_toolkit`のパッケージへ置く案は、振り返りのスキルだけが使う処理で配布物の共通層を広げるため採らない。

準備は`--work-dir`省略時に対象セッションのmanaged-tempへ実行ごとの領域を確保し、返したJSONの`work_dir`と文書の絶対パスから後続が読む。
生成物は終了後も保持し、セッションのmanaged-tempの7日保持へ委ねる。
明示した領域の保持・回収は呼出側が担う。

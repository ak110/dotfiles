# レビュー修正の履歴統合の設計記録

[設計記録の索引](design.md)と[計画と実行レビューへの受渡しの設計記録](design-planning.md)から参照する主題別の記録である。

## レビュー修正の履歴統合

通常の実装モードのレビュー修正では、レビュー修正専用commitを作成せず実装commitへ統合する。
統合先の選び方と、履歴を書き換えられないため続行できない理由を返す条件は`agent-toolkit:commit/references/history-rewrite-selection.md`「修正方法の選択」が定める。
履歴書換えを開始した後に失敗した場合は新規commitへ切り替えず、同書の`## 失敗時の扱い`に従う。
条件を同書へ集約するのは、同じ判断を設計記録と方針記録が別々の文面で持つと、一部だけを改めたときに古い判断が残るためである（2026年10月7日に、新規commitへ退避する規定と方針記録の不一致を観測した）。
続行できない理由は、指摘の採否・認可範囲の判断が必要な場合に加え、履歴を書き換えられない場合と履歴書換え開始後の技術的失敗で返す。
技術的失敗へ添える観測項目は同節に従う。
過去単位を含む場合は、fixup作成前に最古fixup対象と履歴書換え前の元HEADを保持する。
実装担当は各fixup作成前、autosquash直前およびamend直前の各phaseで`agent-toolkit/skills/commit/references/history-rewrite.md`が定める汎用のプッシュ済み判定を再実行する。
同判定は`git fetch --all --prune`と`git for-each-ref --contains=<対象sha> refs/remotes/`を使う。
未pushかつ単一の実装担当が所有するworktreeの履歴書換え保護は同判定へ一本化し、remote広告refの直積証跡・replace ref・graft・
shallow複製への追加防御は、対応する観測事象を得るまで導入しない（確認への回答に由来）。
範囲内のfirst-parent全OIDの公開済み判定をfixup作成前に完了する。
fixupの作成条件は、対象コミット件名が範囲内で一意であり、範囲にmergeを含まないこととする。
この事前判定後も、autosquash直前の再判定をTOCTOU対策として実行する。
各fixup作成後は、対象OIDから得た統合先件名と形式に応じた制御件名（`fixup!`または`amend!`）の完全一致を`git log -1 --format=%s`で確認する。
autosquash成功後は実装担当が`git rev-parse --short=7 HEAD`で取得した書換え後HEADの一意な短縮OIDへautosquash成功後の2回目のpush済み判定対象を置換する。
実装担当は履歴書換え前後の7文字以上の一意な短縮OID対応とphaseごとの`rewrite_guard`反復証跡をレビュー指摘管理表へ保存し、統合前に現行Git実体と比べる。
プッシュ済み判定と制御件名の確認を含む各phaseの失敗は、`history-rewrite.md`の`## 失敗時の扱い`に従う。
詳細な操作手順（fixup・autosquash・amendの順序、phase名、判定コマンド）は`history-rewrite.md`が定め、本書へ転記しない。

通常の実装モードでレビュー修正を委譲する場合は、メインが元の実装入力、レビュー指摘管理表および対象worktreeの絶対パスを修正担当へ渡す。
修正担当は公開契約と元の要求や確認回答で認可された変更を基準文書から確認し、レビュー指摘管理表の指摘を採否判断して、採用指摘を実装単位commitの7文字以上の一意な短縮OIDへ対応付ける。
計画が衝突する場合と認可上限を超える場合は、履歴と作業ツリーを変更せず続行できない理由を返す。
成立する場合は未pushの実装単位履歴へ修正を統合し、詳細をレビュー指摘管理表と成果物へ記録して工程完了だけを返す。
最終単位だけは修正・変更範囲の検証・stage後に`amend` phaseで再判定し、成功後にamendする。
過去単位を含む場合は最終単位も含めた全ての統合先を履歴順に1単位ずつ修正・変更範囲の検証・stageしてfixupを作成し、全fixupの後に1回だけautosquashする。
各fixup作成前とautosquash直前にプッシュ済みかを判定する。
対応付けができない修正などの統合先と、履歴を書き換えられない場合の扱いは`agent-toolkit:commit/references/history-rewrite-selection.md`「修正方法の選択」が定める。
履歴書換えを開始した後に失敗した場合は新規commitへ切り替えず、`agent-toolkit/skills/commit/references/history-rewrite.md`の`## 失敗時の扱い`に従う。
複数の過去単位では、各fixup作成後のclean確認で次の過去単位へ進み、全過去単位のfixup作成後に1回だけautosquashを実行する。
`rewrite_guard`は`phase`・`target_oids`・`published_decision`・各Gitコマンドの終了コード・エラー要約へ縮小する。
専用の`pre_fixup` phaseを先頭に、各再判定phase（`fixup:<単位順>`、`autosquash`、`amend`）を独立した反復配列要素として記録する。
`rewrite_guard`のphaseは通常の計画実行モードのレビュー修正だけに記録し、それ以外では`not_applicable`とする。
実装担当は履歴書換え前の7文字以上の一意な短縮OIDと`rewrite_guard`をレビュー指摘管理表の対象指摘へ保存し、履歴統合後に変更前後OID対応と全phaseの結果へ更新する。
同じ担当の会話履歴が欠落した場合は、レビュー指摘管理表の完了内容が現行Git実体と一致するか確かめて回復する。
準備中の証拠しかない場合または実体を確定できない場合は続行できない理由を返し、無指定reflogから旧OIDを復元しない。
メインへの中間受渡しと成果物・Git・検証結果の再検収は設けない。
初回実装担当のrouteと実効`engine`・`model`・`effort`を保持し、レビュー修正の起動直前に解決した今回routeの実効3値と組み合わせて引継ぎを確定する。
同じ担当へ同じタスクの未完了作業・指摘への対応・再レビューを返し、実効3値がすべて一致する場合だけ元の実装担当threadを継続する。
いずれかの実効値が異なる場合を含むそれ以外は旧担当の終了確認後に今回routeで新しい実装担当を起動し、元の実装入力と基準文書の絶対パスを開始前に1回だけ渡す。
開始後は同じ実装担当が再判定からamendまでを完結する。

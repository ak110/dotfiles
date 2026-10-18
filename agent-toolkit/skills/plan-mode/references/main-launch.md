# メインによる起動の実装と終端

本書は`agent-toolkit:plan-mode`をメインが起動した場合の、計画作成後の実装、実行レビュー、保存と報告を定める。
メインが計画の起草から実装、実行レビューの反映、保存までを所有し、専用worktreeを作成せず要求を受けた時点の作業ツリーで自ら実装する。
実行レビューが収束し受領確認を終えた完成条件証拠の絶対パスは、計画の進捗ログへ`受理済みの完成条件証拠`として保存し、後続の計画基準レビューへ同名の任意入力で渡す（読めない場合、省略時と行が無い場合は`なし`）。参照の適用条件は`agent-toolkit:review-standards`の`references/exec-review-recording.md`「受理済み判定への参照」に従う。

1. 計画の`## 要件・外部仕様`に従って実装する（`agent-toolkit:writing-standards`の該当資料を読む）
   - 実装前に受入シナリオの公開入口・入力と初期状態・有効な期待結果・検出する失敗を具体化し、同スキルの`references/testing.md`「検証単位の選定」で試験を選ぶ。レビュー修正にも適用し、不良は該当入力で再現して是正する
   - Git管理外のファイルを変える場合は最初の変更より前に変更前複製を作成し、証拠要求ありでは実装完了前に未判定検証記録JSONを作成して、複製の対とJSONの絶対パスを`## 進捗ログ`へ残す
   - `### 受入シナリオ`の各行に対応する公開された呼び出し手段を通る結合・E2Eテストを実装に含め、変更範囲の検証で実行して自己検収する。入口・入力・期待結果と実結果の対応、シナリオ別のテスト名と合否、未実施と条件変更の判断を未判定検証記録へ残し、自身が所有する残工程の担当と再開先を記録して実行レビューへ渡す。手動観測とテスト出力は`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`「操作の直前に判定する前提」の保存手順で、実行した版とともにmanaged-tempへ保存する。取得後に版が変わった記録の再利用は`agent-toolkit:review-standards`の`references/exec-review-recording.md`「根拠の書き方」に従う
   - 最後の実装commitの前に、差分を`agent-toolkit:writing-standards`の`references/design-heuristics.md`「設計品質の劣化確認」で評価し、劣化を是正して観点ごとの結論を検証結果へ残す
   - `## 検証`を実行し、`agent-toolkit:commit`に従ってcommitする
2. `${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って実行レビューを起動・受領する。完成条件証拠は担当が初回に生成する出力先として渡し、雛形を事前生成しない。画面差分があれば同じ対象HEADのユーザビリティレビューを並列に起動し、修正後も並列に再判定する。指摘はメインが修正し、修正commitの履歴統合は`agent-toolkit:commit`の`references/history-rewrite.md`「修正方法の選択」に従う
3. 終端前に、実行レビューの未解決が0件であること（画面差分ではユーザビリティレビューの`合格`か`未確定`も）を確かめる。返却の`想定外事象:`が伝える差分外の既存不良も同parentの手順で処置へ渡す。全体の自動チェックとCIは公開工程で判定する
4. `atk run-script plan-progress --`で完了判定を記録し、`atk plans commit <計画ファイル名>`で保存する。確認済みの公開範囲まで`agent-toolkit:commit`の`references/publish.md`に従って公開し、`agent-toolkit:completion-report`で報告する

---
name: release-dotfiles
user-invocable: false
description: >
  dotfilesリポジトリで`develop`から`master`へのリリースPRを作成・マージするとき、
  日次リリースの判定と実施を`agent-toolkit:atk-mode`の作業`process-wi`または公開まで認可された協調モードで行うとき、
  主checkoutをfast-forwardした後に常時稼働する`atk serve`へ反映するとき、
  `rust/claude-statusline/`の変更に伴う`Cargo.toml`のバージョン更新を判定するときに起動する。
---

# dotfilesのリリース運用

`develop`で開発した変更を、`master`へのリリースPRで公開する手順書である。
`master`は必須CIを全て通ったマージコミットだけで更新し、マージ、再試行、ロールバックはいずれも手順の中で明示して1回ずつ行う（auto-mergeと自動の再試行・ロールバックは使わない）。
公開範囲について明示の指示があれば、それを最優先とする。
日次リリースの経緯は`docs/development/concepts-workflows.md`「developとmasterのリリース運用」、branchの初期化と保護設定は`docs/development/design-release-branches.md`にある。

## developとmasterのリリース運用

対象は、`agent-toolkit:atk-mode`の作業`process-wi`の作業と、`operate-ak110-projects`「リリース運用」の恒常的な認可か明示の指定で公開まで担当する協調モードの作業である。
メインエージェントが次の手順で日次リリースの要否を判定して実施する。
この範囲に当たらない作業では、リリースPRを手で作り、マージの後は`merge-pr`の手順でブランチの同期、CI、必要なリリースを検収する。

1. `develop`へpushする前に、`rust/claude-statusline/`配下の差分（試験と試験入力だけ、`Cargo.lock`だけの変更を含む）があれば、`rust/claude-statusline/Cargo.toml`の`version`を上げる。
   `release-statusline.yaml`は差分のあるマージごとに`statusline-v<version>`タグを作るため、据え置くと既存のタグと衝突する。
   レーン担当は更新の根拠を計画か引き継ぎ記録へ残し、公開担当はその根拠が統合差分にも当てはまることと、版数と`Cargo.lock`の整合を確かめる。
   最終の検証は`.claude/skills/develop-dotfiles/references/verification.md`「公開前の検証」のとおりpyfltrへ`--enable=statusline-version`を渡し、レーンの途中のcommitでは無効のまま保つ。
2. pushとCIの成功の後、公開する差分を判定する。
   `agent-toolkit:atk-mode/references/git-identifier.md`で解決した`origin/develop`と`origin/master`が同じcommitなら、その旨を報告してPRの作成を見送る。
   差分があれば、WIキューの消化状態や変更を使う主体によらず次へ進む。
3. 同じ最上位セッションでリリースPRを1件マージ済みなら、追加分は`develop`へのpushとCIの成功まで確かめる。
   長い継続中は`agent-toolkit:atk-mode/references/orchestrator-runtime.md`「継続中の公開と更新」の期限で追加のリリースPRを作り、セッションの終了を待たない。
   期限より前にセッションを終える場合は、追加分を次のセッションのPRへまとめる。
   マージ済みの`master`の不具合の修正を`develop`へ公開した場合は、不具合を残さないため、同じセッションでも追加のリリースPRを作ってマージする（ユーザー指示、2026年10月28日）。
4. 同じheadとbaseを持つopenなPRを確かめる。
   1件なら再利用し、複数なら候補の番号とURLを報告して停止する。
   0件なら、`agent-basics:writing-standards/references/writing-genres.md`「判断と行動を求める記述」の変更説明の基準でタイトルと本文を書き、一時領域へ保存する。
   - 本文は1分で読める要約にし、詳しい証拠は記録（計画、WI、CIのrun）の所在で示す。
   - エンドユーザーに影響する変更を先頭の章に置き、変更の種類ごとに章を分け、変更を1件ずつ項目にして、エンドユーザーが読んで分かる粒度で書く。
   - 版数は末尾の章にまとめる。
     章の名前と数はその回の変更に合わせ、変更が1件だけのPRは章を設けずに書く。
   - 手本は#209である（エンドユーザーに影響する変更、エージェントの作業手順の変更、不具合の修正と試験、版数の4章。タイトルは「リポジトリごとの編集制約と推論量の途中変更、一括処理の所要短縮」）。

   作成の直前に`agent-toolkit:atk-mode/playbooks/review-external-writes.md`を読み（タイトルも文面ファイルへ含める）、PRを作る。
5. PRの完全なURLを指定して`merge-pr`を起動し、マージ、ブランチの同期、CIの確認、必要なリリースの検収まで終える。
   ローカル同期は `merge-pr` が共有依存環境を準備した後に行う。
   先に手動でfast-forwardしない。
   共通CIを再利用した場合も、マージした完全OIDのCI結果を`merge-pr`の手順で確かめる。
6. 今回`master`へ反映した使い方の変更を採用記録から拾い、公開版のガイドを確かめる。
   `agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「使い方が変わる変更」に従って設定の要否を環境ごとに確かめ、専用の案内UWIを登録する。
   `develop`へのpushだけの段階では案内UWIを登録せず、後日のリリースでも同じ採用記録から再開する。
7. 主checkoutをfast-forwardした後は、稼働中の`atk-serve.service`を再起動する。
   反映と稼働の確認は`docs/development/operations-services.md`「fast-forward後のatk serveへの反映」に従い、反映したHEAD、再起動の終了コード、active/runningとHTTPの確認結果を完了報告へ残す。
8. PRの作成かマージに失敗した時点で停止し、外部の状態、失敗した工程、runのURL、再開に必要な情報を報告する。
   再開は報告した情報から手で行い、自動の再試行とロールバックは使わずに済ませる。

返す報告に含める項目: PRのURL、マージコミットの完全OID、CIとReleaseのrunの結果、反映したHEADと`atk serve`の確認結果、登録した案内UWI、停止した場合の外部の状態と再開に必要な情報。

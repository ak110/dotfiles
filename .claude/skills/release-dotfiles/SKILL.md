---
name: release-dotfiles
user-invocable: false
description: >
  dotfilesリポジトリで`develop`から`master`へのリリースPRを作成・マージするとき、
  日次リリースの判定と実施を`agent-toolkit:process-wi`または公開まで認可された協調モードで行うとき、
  主checkoutをfast-forwardした後に常時稼働する`atk serve`へ反映するとき、
  `rust/claude-statusline/`の変更に伴う`Cargo.toml`のバージョン更新を判定するときに起動する。
---

# dotfilesのリリース運用

このスキルは、本リポジトリにおける`develop`と`master`のリリース運用手順、および日次リリースの判定手順をまとめたものである。
公開前の検証手順は`develop-dotfiles`の`references/publishing.md`を参照する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 関連する記録

branch初期化、GitHubの保護設定、マージ後の詳細手順は、[developとmasterのリリース運用](../../../docs/development/concepts-workflows.md#developとmasterのリリース運用)と[branchとリリースの設計](../../../docs/development/design-release-branches.md#developとmasterのbranchリリース設計)に記録がある（所在参照）。
主checkoutのfast-forward後に常時稼働サーバーへ反映する手順は、[atk serveへの反映](../../../docs/development/operations-services.md#fast-forward後のatk-serveへの反映)にある（所在参照）。
いずれも規範以外の記録であり、必要な範囲を選んで読む。

## 常時稼働サーバーへの反映

主checkoutをfast-forwardした後は、稼働中の`atk-serve.service`を再起動する。
反映と稼働確認は[atk serveへの反映](../../../docs/development/operations-services.md#fast-forward後のatk-serveへの反映)に従う。
反映したHEAD、再起動の終了コード、active/runningとHTTPの確認結果を公開担当の完了報告へ残す。

## developとmasterのリリース運用

日常の開発作業は`develop`ブランチ上で行い、リリースは`master`に向けたPRを通じて実施する。
`master`ブランチは、必須CIをすべて通過したマージコミットのみによって更新する。
auto-mergeの有効化や、失敗時の自動再試行、自動ロールバックの導入は行わない。

`agent-toolkit:process-wi`による作業、および`operate-ak110-projects`「リリース運用」の恒常的な認可や明示的な指定によって公開まで担当する協調モードの作業を対象とする。
メインエージェントが以下の手順で日次リリースの要否を判定し、実施する（背景や経緯は[日次リリースの自動実施](../../../docs/development/operations-release.md#日次リリースの自動実施)を参照）。
なお、公開範囲について明示的な指示がある場合は、それを最優先とする。

1. 公開工程でのpushとCIの成功後、公開する差分を判定する。
   `agent-toolkit:commit`のGit識別子規定で解決した`origin/develop`と`origin/master`が同じcommitなら、その旨を報告してPR作成を見送る。
   差分があれば、WIキューの消化状態や変更を利用する主体によらず次へ進む。
2. 同一の最上位セッションでリリースPRを1件マージ済みなら、追加分は`develop`へのpushとCI成功まで確認し、次のセッションのPRへまとめる。
   ただし、マージ済みの`master`の不具合修正を`develop`へ公開した場合は、不具合を残さないため同じセッションでも追加のリリースPRを作成してマージする（ユーザー指示、2026年10月28日）。
3. 重複PRを防ぐため、同じheadとbaseを持つopenなPRを確認する。
   1件なら再利用し、複数なら候補の番号とURLを報告して停止する。
   0件なら、タイトルに今回の変更の主題を1文で記し、本文を`agent-basics:writing-standards/references/writing-common.md`「文章の共通規定」に沿ってmanaged-tempへ保存する。
   作成直前に`agent-toolkit:review-external-writes`を起動し、PRを作成する。
4. PRの完全なURLを指定して`merge-pr`を起動し、マージ、ブランチ同期、CI確認、および必要なリリースの検収まで完了させる。
5. PRの作成やマージに失敗した時点で停止し、外部状態、失敗工程、runのURLと再開に必要な情報を報告する。
   自動再試行とロールバックは行わない。

上記の公開範囲に該当しない作業では、リリースPRを手動で作成し、マージ完了後に`merge-pr`の手順に沿ってブランチ同期、CI、および必要なリリースを検収する。

statusline（`rust/claude-statusline/`配下）に変更を加えた場合は、どの手順でリリースする場合であっても、必ず`rust/claude-statusline/Cargo.toml`の`version`を更新する。
レーン担当者は更新の根拠を計画や引き継ぎ記録へ残す。
公開担当者はその根拠が統合差分にも当てはまるか確認し、`develop`へpushする前に版数と`Cargo.lock`の整合を確かめる。
対象は配下の全ファイルの差分であり、テストコード・テスト入力のみや`Cargo.lock`のみの変更も含む。
GitHub Actionsの`release-statusline.yaml`は差分があるマージごとに`statusline-v<version>`タグを生成するため、据え置くと既存タグと衝突する。
公開前の最終検証は`develop-dotfiles`の`references/publishing.md`「公開前の検証」に従い、pyfltrへ`--enable=statusline-version`を指定し、既存チェックの結果を確認する。
レーンの途中commitでは既定無効を維持し、採番の完了は公開担当が検収する。

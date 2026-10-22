---
name: dotfiles-release
user-invocable: false
description: >
  dotfilesリポジトリで`develop`から`master`へのリリースPRを作成・マージするとき、
  日次リリース条件の判定と実施を`agent-toolkit:process-wi`または公開まで認可された協調モードで行うとき、
  `rust/claude-statusline/`の変更に伴う`Cargo.toml`のversion更新を判定するときに起動する。
---

# dotfilesのリリース運用

本スキルは本リポジトリの`develop`と`master`のリリース運用と、日次リリースの判定手順を提供する。
テストや整形の手順は`dotfiles-development`が扱う。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| branch初期化、GitHubの保護設定またはマージ後の詳細手順が要る時 | [developとmasterのリリース運用](../../../docs/development/concepts-workflows.md#developとmasterのリリース運用)、[branchとリリースの設計](../../../docs/development/design-release.md#developとmasterのbranchリリース設計) |

## developとmasterのリリース運用

通常開発は`develop`で行い、リリースは`master`向けのPRで行う。`master`は必須CIを通過したマージコミットだけで更新する。auto-merge、失敗後の自動再試行、自動rollbackの導入は禁止する。

`agent-toolkit:process-wi`と、`ak110-projects-operations`「リリース運用」の恒常的認可または明示指定により公開まで扱う協調モードの作業では、メインが次の日次リリースを判定・実施する（経緯は[日次リリースの自動実施](../../../docs/development/operations.md#日次リリースの自動実施)）。公開範囲の明示指定があればそれを優先する。

- 条件: 公開工程のpushとCI成功を確認した後、`agent-toolkit:commit`のGit識別子規定に従って解決した`origin/develop`と`origin/master`のcommitが異なること。WIキューの状態や変更の消費主体（エージェント向けの変更だけでもよい）では限定しない。不成立なら両branchが同じcommitを指すことを報告し、リリースPRの作成対象から外す
- 同じ最上位セッションで`develop`から`master`へのリリースPRをすでに1件マージしている場合は、追加のPRを作成せず、`develop`へのpushとCI成功までで終える。累積差分は次のセッションの1件のリリースPRへ含める
- 同じheadとbaseのopen PRが1件ならそれを再利用し、0件なら作成する。複数件なら推測せず、候補の番号とURLを報告して停止する。作成する場合はPR本文をmanaged-tempへ保存し、投稿の直前に`agent-toolkit:external-write-review`を起動する。タイトルはそのセッションの変更の主題を1文で書き、本文は`agent-toolkit:writing-standards`の`references/writing.md`「文章の共通規定」に従う
- 続けてPRの完全なURLを指定して`merge-pr`を起動し、マージ、branch同期、CI、必要なReleaseの検収まで完遂する
- PRの作成またはマージが失敗した場合は、再試行もrollbackもせず、外部状態、失敗工程、run URL、再開点を報告する

前記の公開範囲に当たらない作業ではリリースPRを手動で作成し、マージ後は`merge-pr`の手順で同期、CI、必要なReleaseを検収する。

statusline（`rust/claude-statusline/`配下）を変更した場合は、どの手順でリリースする場合も`rust/claude-statusline/Cargo.toml`の`version`を更新する。レーン担当が版数更新の根拠を計画か引き継ぎ記録へ残し、終端担当が`develop`をpushする時点までに更新する。対象は同配下の全ファイルの差分であり、`src/*.rs`の`mod tests`内のテストとテスト入力だけの変更や`Cargo.lock`だけの変更も含む。`release-statusline.yaml`は差分のあるマージごとに`statusline-v<version>`タグを作成するため、据え置くと既存タグと衝突する。更新を忘れた場合は、公開前のローカル検証で明示的に有効化するpyfltrの`statusline-version`と、CIの`statusline-version` jobが検出する。

---
name: dotfiles-release
user-invocable: false
description: >
  dotfilesリポジトリで`develop`から`master`へのリリースPRを作成・マージするとき、
  日次リリース条件の判定と実施を`agent-toolkit:process-wi`または公開範囲を既存の判断基準どおりとした協調モードで行うとき、
  `rust/claude-statusline/`の変更に伴う`Cargo.toml`のversion更新を判定するときに起動する。
---

# dotfilesのリリース運用

本スキルは本リポジトリの`develop`と`master`のリリース運用と、日次リリースの判定手順を提供する。
テストや整形の手順は`dotfiles-development`が扱う。

## developとmasterのリリース運用

- 通常開発は`develop`で行い、リリースは`master`向けのPRで行う。`master`は必須CIを通過したマージコミットだけで更新する
  - `agent-toolkit:process-wi`と、公開範囲を「既存の判断基準どおり」と回答した協調モードの作業では、次の条件が成立する場合に`develop`から`master`へのリリースPRを作成し、マージまで実施する。判定と実施はメインが担う。導入の経緯と根拠は[日次リリースの自動実施](../../../docs/development/operations.md#日次リリースの自動実施)にある
    - 実施条件: 公開工程のpushとCI成功を確認した後、`agent-toolkit:commit`のGit識別子規定に従って`origin/develop`と`origin/master`を解決し、両者のcommitが異なる。変更の消費主体で限定せず、コーディングエージェント向けの変更だけでもリリースPRへ進める
    - 条件が成立しない場合は両branchが同じcommitを指していることを報告し、PRを作成しない
    - 実施する場合は、同じheadとbaseのopen PRを調べる。1件ならそのPRを再利用する。0件ならmanaged-tempの中へPR本文を保存し、投稿の直前に`agent-toolkit:external-write-review`を起動してから作成する。複数件の場合は対象を推測せず、候補の番号とURLを報告して停止する。タイトルにはそのセッションの変更の主題を1文で書く。本文は`agent-toolkit:writing-standards`の`references/writing.md`「人間向け文章の共通規定」に従う。`gh`の受理形式は操作直前のヘルプで確定する

    - 続けて、既存または新規PRの完全なURLを指定して`merge-pr`スキルを起動し、同スキルの手順でマージ、branch同期、CIおよび必要なReleaseの検収まで完遂する
    - PRの作成またはマージが失敗した場合は、自動再試行とrollbackを行わず、外部状態、失敗工程、run URLおよび再開点を報告する
  - 前項の公開範囲に当たらない作業では、リリースPRの作成を手動で行う。PRのマージ後は`merge-pr`の手順で同期、CIおよび必要なReleaseを検収する
  - statusline（`rust/claude-statusline/`配下）を変更した場合は、レーン担当が版数更新の根拠を計画または引き継ぎ記録へ残し、終端担当が`develop`をpushする時点までに`rust/claude-statusline/Cargo.toml`の`version`を更新する。この更新はどの手順でリリースする場合でも必要である。対象は`rust/claude-statusline/`配下の全ファイルの差分であり、`src/*.rs`の`mod tests`内のテストコードとテスト入力だけの変更や`Cargo.lock`だけの変更も含む。`release-statusline.yaml`は差分のあるマージに対して`statusline-v<version>`タグを作成するため、版数を据え置くと既存タグと衝突する。版数を更新し忘れた場合は、終端担当が公開前のローカル検証で明示的に有効化するpyfltrの`statusline-version`と、`develop`へのpushで実行されるCIの`statusline-version` jobの双方がそれを検出する
  - branch初期化、GitHubの保護設定およびマージ後の詳細手順は[developとmasterのリリース運用](../../../docs/development/concepts-workflows.md#developとmasterのリリース運用)、[branchとリリースの設計](../../../docs/development/design-release.md#developとmasterのbranchリリース設計)を参照する

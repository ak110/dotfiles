---
name: dotfiles-release
description: >
  dotfilesリポジトリで`develop`から`master`へのリリースPRを作成・マージするとき、
  日次リリース条件の判定と実施を`agent-toolkit:process-wi`のセッションで行うとき、
  `rust/claude-statusline/`の変更に伴う`Cargo.toml`のversion更新を判定するときに起動する。
---

# dotfilesのリリース運用

本スキルは、本リポジトリの`develop`と`master`のリリース運用と、日次リリースの判定手順を提供する。
検査と整形の手順は`dotfiles-development`が扱う。

## developとmasterのリリース運用

- 通常開発は`develop`で行い、リリースは`master`向けのPRで行う。`master`は必須CIを通過したマージコミットだけで更新する
  - `agent-toolkit:process-wi`では、次の条件が成立する場合に`develop`から`master`へのリリースPRを作成し、マージまで実施する。判定と実施はメインが担う。導入の経緯と根拠は[日次リリースの自動実施](../../../docs/development/operations.md#日次リリースの自動実施)にある
    - 実施条件: 公開工程のpushとCI成功を確認した後、`agent-toolkit:commit`のGit識別子規定に従って`origin/develop`と`origin/master`を解決し、両者のcommitが異なる
    - 条件が成立しない場合は両branchが同じcommitを指す旨を報告し、PRを作成しない
    - 実施する場合は、同じheadとbaseのopen PRを調べる。1件ならそのPRを再利用し、0件なら管理対象一時領域へPR本文を保存して作成する。複数件の場合は対象を推測せず、候補の番号とURLを報告して停止する。タイトルには当該セッションの変更の主題を1文で書く。本文は`agent-toolkit:writing-standards`「人間向け文章の共通規定」に従う。`gh`の受理形式は操作直前のヘルプで確定する

    - 続けて、既存又は新規PRの完全なURLを指定して`merge-pr`をSkill機能で起動し、同スキルの手順でマージ、branch同期、CI及び必要なReleaseの検収まで完遂する
    - PRの作成又はマージが失敗した場合は、自動再試行とrollbackを行わず、外部状態、失敗工程、run URL及び再開点を報告する
  - それ以外の経路では、リリースPRの作成を手動で行う。PRのマージ後は`merge-pr`の手順で同期、CI及び必要なReleaseを検収する
  - statusline（`rust/claude-statusline/`配下）を変更した場合は、`develop`をpushする時点までに`rust/claude-statusline/Cargo.toml`の`version`を更新する。この更新はリリース経路によらず必要であり、更新漏れは`develop`へのpushで実行されるCIの`statusline-version` jobが検出する
  - branch初期化、GitHubの保護設定及びマージ後の詳細手順は[developとmasterのリリース運用](../../../docs/development/concepts.md#developとmasterのリリース運用)、[branchとリリースの設計](../../../docs/development/design.md#developとmasterのbranchリリース設計)を参照する

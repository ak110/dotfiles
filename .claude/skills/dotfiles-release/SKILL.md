---
name: dotfiles-release
user-invocable: false
description: >
  dotfilesリポジトリで`develop`から`master`へのリリースPRを作成・マージするとき、
  日次リリースの判定と実施を`agent-toolkit:process-wi`または公開まで認可された協調モードで行うとき、
  主checkoutをfast-forwardした後に常時稼働する`atk serve`へ反映するとき、
  `rust/claude-statusline/`の変更に伴う`Cargo.toml`のバージョン更新を判定するときに起動する。
---

# dotfilesのリリース運用

このスキルは、本リポジトリにおける`develop`と`master`のリリース運用手順、および日次リリースの判定手順をまとめたものである。
テストやコード整形の詳細な手順については`dotfiles-development`を参照する。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../dotfiles-development/SKILL.md` | 判断 | `read:*/skills/dotfiles-development/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:commit/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/commit/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:external-write-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/external-write-review/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/writing.md#「文章の共通規定」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/writing.md#「文章の共通規定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../merge-pr/SKILL.md` | 判断 | `read:*/skills/merge-pr/SKILL.md` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| branch初期化、GitHubの保護設定またはマージ後の詳細手順が要る時 | [developとmasterのリリース運用](../../../docs/development/concepts-workflows.md#developとmasterのリリース運用)、[branchとリリースの設計](../../../docs/development/design-release.md#developとmasterのbranchリリース設計) | 判断 | `section:*/docs/development/concepts-workflows.md#developとmasterのリリース運用`、`section:*/docs/development/design-release.md#developとmasterのbranchリリース設計` | |
| 主checkoutのfast-forward後に常時稼働サーバーへ反映する時 | [atk serveへの反映](../../../docs/development/operations.md#fast-forward後のatk-serveへの反映) | 判断 | `section:*/docs/development/operations.md#fast-forward後のatk-serveへの反映` | |

## 常時稼働サーバーへの反映

主checkoutをfast-forwardした後は、稼働中の`atk-serve.service`を再起動する。
反映と稼働確認は[atk serveへの反映](../../../docs/development/operations.md#fast-forward後のatk-serveへの反映)に従う。
反映したHEAD、再起動の終了コード、active/runningとHTTPの確認結果を公開担当の完了報告へ残す。

## developとmasterのリリース運用

日常の開発作業は`develop`ブランチ上で行い、リリースは`master`に向けたPRを通じて実施する。`master`ブランチは、必須CIをすべて通過したマージコミットのみによって更新する。auto-mergeの有効化や、失敗時の自動再試行、自動ロールバックの導入は行わない。

`agent-toolkit:process-wi`による作業、および`ak110-projects-operations`「リリース運用」の恒常的な認可や明示的な指定によって公開まで担当する協調モードの作業を対象とする。メインエージェントが以下の手順で日次リリースの要否を判定し、実施する（背景や経緯は[日次リリースの自動実施](../../../docs/development/operations.md#日次リリースの自動実施)を参照）。なお、公開範囲について明示的な指示がある場合は、それを最優先とする。

- 判定条件: 公開工程でのpushとCIの成功を確認したあと、`agent-toolkit:commit`のGit識別子規定に沿って解決した`origin/develop`と`origin/master`のcommitが異なっていること。WIキューの消化状態や変更を利用する主体（エージェント向けの変更のみであっても対象となる）によって制限することはない。条件を満たさない場合は、両ブランチが同じcommitを指している旨を報告し、リリースPRの作成は見送る。
- 同一の最上位セッションですでに`develop`から`master`へのリリースPRを1件マージしている場合は、追加のPRを作成せず、`develop`へのpushとCI成功の確認をもって作業を終了する。それ以降に蓄積された差分は、次のセッションで作成するリリースPRにまとめて含める。ただし、マージ済みの`master`に不具合が判明し、その修正を`develop`へ公開した場合は、`master`を不具合のまま残さないため、同じセッションでも追加のリリースPRを作成してマージする（ユーザー指示、2026年10月28日）。
- 同じheadとbaseを持つopenなPRがすでに1件存在する場合はそれを再利用し、0件であれば新しく作成する。該当するPRが複数存在する場合は独自に判断せず、候補となるPRの番号とURLを報告して処理を停止する。新しくPRを作成する際は、PR本文をmanaged-temp配下に保存し、作成の直前に`agent-toolkit:external-write-review`を起動する。タイトルにはそのセッションで行った変更の主題を1文で簡潔に記述し、本文は`agent-basics:writing-standards`の`references/writing.md`「文章の共通規定」に沿って作成する。
- 続いてPRの完全なURLを指定して`merge-pr`を起動し、マージ、ブランチの同期、CIの確認、および必要なリリースの検収までを一続きに完了させる。
- PRの作成やマージが失敗した場合は、自動での再試行やロールバックを行わず、外部の状態、失敗した工程、runのURL、および再開に必要な情報を報告して停止する。

上記の公開範囲に該当しない作業では、リリースPRを手動で作成し、マージ完了後に`merge-pr`の手順に沿ってブランチ同期、CI、および必要なリリースを検収する。

statusline（`rust/claude-statusline/`配下）に変更を加えた場合は、どの手順でリリースする場合であっても、必ず`rust/claude-statusline/Cargo.toml`の`version`を更新する。レーン担当者がバージョン更新の根拠を計画書や引き継ぎ記録に明記し、公開担当者が`develop`へpushする前までに更新を完了させておく。バージョン更新の対象となるのは同ディレクトリ配下の全ファイルの差分であり、`src/*.rs`内の`mod tests`におけるテストコードやテスト入力のみの変更、あるいは`Cargo.lock`のみの変更であっても更新が必要である。GitHub Actionsの`release-statusline.yaml`は、差分が存在するマージごとに`statusline-v<version>`タグを自動生成するため、バージョン番号を据え置いたままだと既存のタグと衝突してしまう。更新漏れがある場合は、公開前のローカル検証時に明示的に有効化するpyfltrの`statusline-version`チェックや、CIの`statusline-version`ジョブがそれを検出する。

---
name: operate-ak110-projects
user-invocable: false
description: >
  作者個人のプロジェクト群の運用を扱う。姉妹プロジェクト間のツールチェイン（Makefile、mise、prek、
  GitHub Actionsなど）・ドキュメント構成の同期、gv・lcなど姉妹プロジェクト自体の編集、推奨ガイド・
  共有ファイルへの追従、個人プロジェクトのAWI処理で選定メインが横断同期・依存更新の要否を判定するとき、個人プロジェクトの性質・作業手順を変える
  要求のWI作成、リリース、lint設定・足回りファイルの変更時に使う。
  「他プロジェクトへの反映」「プロジェクト間の同期」などを受けたエージェントが起動する。
  判定済みの依存更新だけを受託するレーンはAWI着手の契機では起動せず、独立した足回り変更など他の契機は適用する。
  一覧の外のプロジェクトで、個人プロジェクトの足回り改善の適用を検討するとき（追従台帳）にも使う。
  プロジェクト固有のアプリケーションロジック変更は対象外
---

# 作者個人のプロジェクト運用

本スキルは作者個人のプロジェクト群について、ツールチェインとドキュメント構成の同期、追従作業、着手時の依存更新、リリース運用、および足回り改善を他のプロジェクトで検討するための追従台帳の手順を提供する。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 個人プロジェクトのAWI処理で依存更新の要否を判定する前、または依存の明示更新要求へ着手する前 | [references/dependency-update.md](references/dependency-update.md) |
| 同期対象への波及を判定する前 | [references/project-synchronization.md](references/project-synchronization.md) |
| 推奨ガイド・共有ファイルへ追従する前、同期対象を更新した時、または足回りファイルの推奨設定を判断する前 | [references/cross-project-followup.md](references/cross-project-followup.md) |
| ドキュメント構成を変更・同期する前 | [references/doc-structure.md](references/doc-structure.md) |
| 他プロジェクト向けの追従提案を複数リポジトリへ投入する前 | `agent-toolkit:wi-standards`の`references/cross-repository-submission.md` |
| lint違反への対応か推奨設定の緩和を判断する前 | `agent-basics:writing-standards`の`references/implementation-time.md`「lintと機械チェック」 |
| リリースの認可・要否・実施を判断する前 | [references/release-operations.md](references/release-operations.md) |
| 一覧の外のプロジェクトで足回り改善の適用を検討する前と、個人プロジェクトで追従台帳の記録対象に当たる変更を確定した時 | [references/followup-procedure.md](references/followup-procedure.md) |
| gv・lcをLinuxから編集・検証する前、prek・pyfltr・文書lintの呼び出しを変更する前、またはCI workflow（`.github/workflows/**`）を編集する前 | [references/ci-and-tooling.md](references/ci-and-tooling.md) |

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 前提

姉妹プロジェクト群は、共通化対象のツールチェインを同じコマンド名・設定キーで揃える。
ドキュメントの章構成は「ドキュメント章構成の統一」節が定める。
[意図的に維持している差異](references/project-synchronization.md#意図的に維持している差異)に該当しないコメント表現・項目順・未指定時に使われる設定値は、同一文面または同等の設定へ揃える。
1プロジェクトで変更した場合、[後述のマトリクス](references/project-synchronization.md#変更時の同期対象マトリクス)に基づいて他プロジェクトへの波及要否を確認し、必要ならユーザーへ提案する。

対象プロジェクトの一覧と絶対パスはセッション開始時にコンテキストへロードされるローカル指示から得る。
各プロジェクトの属性は次の表が定め、本スキルの各節はこの表を参照する。

| プロジェクト | 一括更新コマンド | リリース運用の対象 |
| --- | --- | --- |
| `dotfiles` | `make update` | 対象外（リリースworkflowなし。`develop`から`master`へのマージは`release-dotfiles`が扱う） |
| `pyfltr` | `make update` | 対象 |
| `pytilpack` | `make update` | 対象 |
| `smpr` | `make update` | 対象外（リリースworkflowなし） |
| `glatasks` | `make update` | 対象 |
| `gv` | `mise run update` | 対象 |
| `lc` | `mise run update` | 対象 |

個別プロジェクトのパスはそのコンテキスト経由で参照する。

対象リポジトリが本スキルの対象に該当するかは、そのリポジトリの絶対パスが前段のプロジェクト一覧に現れるかで判定する。
一覧に現れない対象は、「足回り改善の適用検討と台帳への追記」の適用検討を除き、本スキルの対象外とする。

明らかにプロジェクト固有の変更や、「ツールチェインやドキュメント構成など」以外の変更であれば確認不要。

## 判定手順

リリース専用依頼でも本スキルの起動は維持する。
現在の会話コンテキストと自身が実行した操作から、
次の条件を両方確認できる場合は、
姉妹プロジェクトへの波及調査を開始せず、受領した作業だけを実施する。

- 既存commitのPR作成・マージ・CI検収だけを行う
- 現行セッションで同期対象を変更・計画・レビューしていない

いずれかを確認できない場合は、以下を実施する。

- 変更内容の分類を特定する（例: prek設定、mise設定、CI workflow、README構成など）
- 「[変更時の同期対象マトリクス](references/project-synchronization.md#変更時の同期対象マトリクス)」で波及プロジェクトを決める
- 「[意図的に維持している差異](references/project-synchronization.md#意図的に維持している差異)」に該当しないか確認する
- 移植対象の記述が依拠する前提（配布形態・外部サービスの有効状態など）を列挙し、
  各前提が移植先で成立することを、移植先の現物を観測して確かめるか、移植先の明文化された方針と比べて確認する。
  成立しない前提に依拠する記述は移植先の実情に合わせて書き換えるか、移植対象から除く
- 該当プロジェクトに対して`agents_server`の`start`（`mode`は`explore`）で読み取り専用の並列調査を行い、差分を把握する
  （対象が複数リポジトリへまたがるため、委譲元のコンテキストで全文を読むと後続の判断へ回せる容量が残らない）
- 同期が必要なプロジェクトと推奨アクションをユーザーに報告する

個人プロジェクトの性質や作業手順（開発・検証できる環境、セットアップ手順、リリース運用など）を変える要求では、WIの反映先を確定する前に、その性質を記述する規範を変更後の性質と比べる。
比べる規範は、dotfilesが所有するプロジェクト一覧、本スキルの属性表と`references/ci-and-tooling.md`などである。
比べた結果から、追従の要否と反映先を同じ起草段階で確定する。
規範の所有者が対象プロジェクトと別であることは投入先を分ける根拠として扱い、必要な追従は`agent-toolkit:wi-standards`の通常の投入で所有者のリポジトリへ渡す（複数リポジトリでは読込表の`references/cross-repository-submission.md`）。
対象プロジェクトだけを編集する制約がある場合も、その制約を保ったまま所有者宛ての追従の扱いを確定する。
ユーザー確認の要否は`agent-toolkit:user-confirmation-and-report/references/user-confirmation-and-report-decisions.md`「確認要否の判定」に従う。
その規範の記述に影響しない変更は、この比較の対象から外す。

スコープに含むのは「ツールチェインやドキュメント構成」の範囲。
典型パスは以下。

- ビルド/タスク: `Makefile` / `mise.toml`
- Python設定: `pyproject.toml`
- Node.js設定: `package.json`
- lint/format: `.pre-commit-config.yaml`
- CI: `.github/workflows/**`
- ドキュメント: `README.md` / `AGENTS.md` / `docs/**/development.md` / `docs/**/security.md`

## リリース運用

公開とリリースの認可、実施時機、判定、実施の手順は[references/release-operations.md](references/release-operations.md)が定める。
協調モードの変更の公開範囲を判定する前、リリースを求められた時、pushとCI成功の後にリリース要否を判定する前に全文を読む。

## 足回り改善の適用検討と台帳への追記

個人プロジェクトで実施した足回りの改善のうち、他のプロジェクトでも作業が要るものは追従台帳へ記録する。
一覧の外のプロジェクトでユーザーから適用の検討を依頼された場合と、個人プロジェクトで台帳の記録対象に当たる変更を確定した場合は、[references/followup-procedure.md](references/followup-procedure.md)に従う。
追記の要否は、本スキルを起動している時点（「判定手順」と「[追従作業と複数リポジトリ横断投入](references/cross-project-followup.md#追従作業と複数リポジトリ横断投入)」）で判定する。

## ドキュメント章構成の統一

README.md・AGENTS.md・docs/development/development.mdの標準章構成・共通文面・記述基準・バッジ記法は
[references/doc-structure.md](references/doc-structure.md)が定める。

## 補足事項

### ドキュメント・運用方針

- 他プロジェクト作業中に`~/.claude/rules/agent-toolkit/*`や`/agent-toolkit:*`スキルの問題を
  発見したらdotfiles側を修正する（マスター）

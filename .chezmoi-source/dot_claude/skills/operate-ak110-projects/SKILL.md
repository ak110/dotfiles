---
name: operate-ak110-projects
user-invocable: false
description: >
  作者個人のプロジェクト群の運用を扱う。姉妹プロジェクト間のツールチェイン（Makefile、mise、prek、
  GitHub Actionsなど）・ドキュメント構成の同期、gv・lcなど姉妹プロジェクト自体の編集、推奨ガイド・
  共有ファイルへの追従、個人プロジェクトのAWI処理で選定メインが横断同期・依存更新の要否を判定するとき、個人プロジェクトの性質・作業手順を変える
  要求のWI作成、版数の提案・確認、リリース、lint設定・足回りファイルの変更時に使う。
  「他プロジェクトへの反映」「プロジェクト間の同期」などを受けたエージェントが起動する。
  判定済みの依存更新だけを受託するレーンはAWI着手の契機では起動せず、独立した足回り変更など他の契機は適用する。
  個人プロジェクトで、他のプロジェクトでも個別の作業が要るコード整理やテスト・検証・CIの改善を確定したときにも使う。
  プロジェクト固有のアプリケーションロジック変更は対象外
---

# 作者個人のプロジェクト運用

作者個人のプロジェクト群で、ツールチェインと文書構成をそろえ、依存の更新とリリースを決まった認可の範囲で行う。
行う作業に合う資料を次の表から1つ選び、その資料を上から実行する。

| 作業・条件 | 読む資料 |
| --- | --- |
| 足回り（ツールチェイン、lint設定、CI、文書構成）の変更や推奨ガイドの改訂を姉妹プロジェクトへ広げる。波及先の判定、追従提案の投入 | `references/project-synchronization.md` |
| 足回りファイルを編集する。追従提案のAWIの実施、lint違反と設定の緩和 | `references/cross-project-followup.md` |
| 依存を更新する。AWI処理での要否の判定、明示の更新要求 | `references/dependency-update.md` |
| 個人プロジェクトやdotfiles配布プラグインの版数を提案・確認する。リリースworkflowの有無を問わない | `references/release-operations.md`「実施」2 |
| 公開とリリース。公開範囲、リリースの要否と実施、GLATasksの配備 | `references/release-operations.md` |
| `README.md`、`AGENTS.md`、`docs/development/`の章構成を変える、そろえる | `references/doc-structure.md` |
| gv・lcをLinuxで編集・検証する。prek、pyfltr、文書lintの呼び出しやCI workflowを変える | `references/ci-and-tooling.md` |
| 他のプロジェクトでも個別の作業が要る改善を確定する。追従台帳への追記 | `references/project-synchronization.md`「台帳への追記」 |

## 前提

対象はセッションの開始時に読み込まれるローカル指示のプロジェクト一覧に絶対パスが載るリポジトリである。
台帳からの適用には、一覧内外を問わず手動スキル `apply-followup-ledger` を使う。
他のプロジェクトの作業中に`~/.claude/rules/agent-toolkit/`や`agent-toolkit:*`スキルの問題を見つけたら、原本のdotfiles側を直す。

| プロジェクト | 一括更新コマンド | リリース運用の対象 |
| --- | --- | --- |
| `dotfiles` | `make update` | 対象外（リリースworkflowなし。`develop`から`master`へのマージは`release-dotfiles`が扱う） |
| `pyfltr` | `make update` | 対象 |
| `pytilpack` | `make update` | 対象 |
| `smpr` | `make update` | 対象外（リリースworkflowなし） |
| `glatasks` | `make update` | 対象 |
| `gv` | `mise run update` | 対象 |
| `lc` | `mise run update` | 対象 |

## 判定手順

リリース専用の依頼でも本スキルを起動し、既存commitのPRの作成・マージ・CIの検収だけを行い、現行のセッションで同期の対象を変更・計画・レビューしていなければ、波及の調査を始めずに受けた作業だけを行う。
それ以外でツールチェインか文書構成を変えた時は、`references/project-synchronization.md`で波及を判定する（明らかにプロジェクト固有の変更は除く）。

個人プロジェクトの性質や作業手順（開発・検証できる環境、セットアップ手順、リリース運用など）を変える要求のWIでは、反映先を確定する前に、その性質を書いた規範を変更後の性質と比べる。
比べる規範はdotfilesのプロジェクト一覧、本書「前提」の属性表、`references/ci-and-tooling.md`などで、結果から追従の要否と反映先を同じ起草の段階で決める。
規範の所有者が対象プロジェクトと別なら、対象プロジェクトだけを編集する制約があっても、追従を所有者のリポジトリ宛てのAWIとして渡す。

## リリース運用

協調モードの変更で公開範囲の指定が無ければユーザーの恒常的な認可で公開まで進め、明示の指定があればその指定を優先し、認可の範囲と実施は`references/release-operations.md`に従う。

## 台帳への追記

足回り、コード整理、テスト・検証・CIの改善のうち、他のプロジェクトの既存成果物や設定に個別の作業が要るものは、改善を確定した時に追従台帳へ記録する（`references/project-synchronization.md`「台帳への追記」）。

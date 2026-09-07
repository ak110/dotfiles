---
name: fast-process-wi
description: >
  対象リポジトリのAWIを、計画ファイルを作成せずメインが直接実装して終端するときに起動する。
disable-model-invocation: true
---

# AWIの軽量処理

本スキルは、メインが対象リポジトリのAWIを自ら取得し、計画ファイルを作成せずに主作業ツリーで直接実装して終端する工程を提供する。
AWIとUWIの共通概念、由来、承認、状態及び投入は`../wi-standards/SKILL.md`を正本とし、本スキルへ複製しない。
選定、レーン分け、計画、終端を委譲で分担する工程は`../process-wi/SKILL.md`が担い、本スキルは当該工程を起動しない。

本スキルの実行中は自律モードとする。ユーザー判断が必要な事項は`agent-toolkit/rules/01-agent.md`「協調と自律」節に従う。

## 本スキルを選ぶ場面

処理対象の全項目が、既存の方針と実装から変更内容を一意に導ける場合に本スキルを選ぶ。
設計判断、既存契約の変更、複数の実装単位への分解又はバグ対応を要する項目が1件でもある場合は`../process-wi/SKILL.md`を選ぶ。
この区分は`${CLAUDE_PLUGIN_ROOT}/share/pick-wi.subagent.md`の「調査とレーン分け」が軽量な項目とそれ以外を分ける区分と同一とする。
起動後に当該区分へ当たらない項目を確認した場合は、本スキルの実装へ着手せず、当該項目を`../process-wi/SKILL.md`の処理対象として残す。

## 不変条件

- 作業ツリーへ書き込む主体をメインだけとし、実装を委譲しない
- 実行レビューは独立コンテキストの担当が行い、メインが自らレビューを兼ねない
- 人間由来の要求を全部又は一部不採用にする場合は、ユーザーの確認を得るまで当該項目のキュー終端へ進まない
- 1回の処理回で扱う対象は手順2で固定した集合とし、以降の手順で再取得しない
- 1回の処理回が持つレビュー指摘管理表は、手順3の起点OIDで一意に定まる1件だけとする

## 実行順

1. `atk wi list --status=processable --target-repo=<repo> --skip-pull`で候補を取得し、`atk wi show`で各本文を読む。「本スキルを選ぶ場面」の区分を全件へ適用する
2. 処理対象のファイル名を`atk wi start-processing <filename>... --target-repo=<repo>`で`processing`へ移し、当該集合を固定する。実行後に全件が`processing`へ配置されたことを確認する
3. `git -C <対象リポジトリの絶対パス> rev-parse HEAD`で処理開始時点の完全OIDを取得し、当該処理回の起点OIDとして保持する。以降の手順で取得し直さない
4. 主作業ツリーで全項目を直接実装する。専用worktree、計画ファイル、計画レビュー及び通常型AWIの計画型変換を作成しない
5. 対象リポジトリを検査し、commitする
6. `atk review-table init ~/.claude/plans/fastwi-<起点OID>.exec-review.tsv`でレビュー指摘管理表を1件だけ作成する。表の命名、保存の要否及び削除は`../plan-mode/references/plan-file-standards.md`を正本とする
7. `${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`を全文読み、`レビュー基準: AWI`で実行レビュー担当を1件起動する。同書が定める`review_contract`とともに、手順6の表の絶対パス、手順3の起点OID、及び対象AWIのファイル名、要求、完成条件、由来、採否の5項目のAWI記録を渡す。起動の前に、当該表が実在することと、表名が`fastwi-<起点OID>.exec-review.tsv`と完全一致することを確認する
8. 指摘への修正はメインが主作業ツリーで実施し、レビュー修正担当を起動しない。同じ処理回の再レビューでは手順6の表と手順3の起点OIDをそのまま継続し、新しい表を作成しない
9. レビューの収束後、`atk wi adopt`又は`atk wi reject`で各AWIを終端する。統合後の検証、push、CI確認及びAWI本文が指示する固有の終端工程もメインが実施する
10. `~/.claude/plans/fastwi-<起点OID>.exec-review.tsv`を削除し、`test ! -e`の終了コード0で不在を確認してから`agent-toolkit:completion-report`で報告する

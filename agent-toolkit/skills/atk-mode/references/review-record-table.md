# レビュー指摘管理表

## レビュー指摘管理表の共通操作

レビュー指摘管理表は、指摘の追加と応答の記録を全て`atk review-table`で行うヘッダーの無いTSVである（列と出力の形式は`atk review-table --help`）。
CI対応レビュー指摘管理表は、計画ファイルの無いCI失敗への対応でレビュー指摘を記録する表（`ci-<OID>.exec-review.tsv`）である。
`atk review-table init`直後の0バイトのファイルは指摘0件の表であり、ヘッダーや仮の行を足さない。
監査記録は`docs/development/audit-records-wi.md`の「agent-toolkit/skills/atk-mode/references/review-record-table.md：レビュー指摘管理表の共通操作：2026年10月8日」にある。

レビュー担当の指摘でない行を担当が自分で足す時は、指摘箇所を`担当の記録:`で書き始めて対象を続ける。
対象は、実装中の判断・解釈の記録、既存の指摘行が無い時の完成条件の変更・除外の判断、統合時の競合解消の記録であり、レビュー担当の指摘行への応答には使わない。

## レビュー指摘管理表のパス

1回の実行レビューでは1つの指摘管理表を使い、名前と置き場所は`agent-toolkit:atk-mode/references/plan-storage.md`「計画ファイルの保存と参照」の手順5に従う（複数の計画を含むレビュー単位では作成順で最初の計画のstem）。
レビュー担当は最初に読む前に表の在否を確かめ、初回のレビューで無い時だけ`atk review-table init <表の絶対パス>`で作る。
起動元は表を作らずにレビュー担当を起動する。
再レビューと引き継ぎ再レビューで表が無い時は、空の表で代えずに、無い原因と再開に要る記録を`続行できない理由:`で返す。

## レビュー指摘管理表の保存

表はレビュー基準に応じて次のとおり扱う。

- 基準が`WI`だけ: 最終化まで保持し、レーンの統合では`atk lane integrate`が`phase: finalized`を返した後、直接実装では受領確認と意味の検収を終えた後に、private-notesへ保存せずに削除する。
- 基準に`計画`を含む: 計画を管理する担当が、計画バンドルと一緒に`agent-toolkit:atk-mode/references/plan-storage.md`「計画ファイルの保存と参照」で保存する。
- 基準が`CI記録`だけ: `agent-toolkit:atk-mode/playbooks/process-wi-ci-fix.md`「修正系列」に従って修正系列の単位で引き継ぎ、同節の手順6で保存する（`process-wi`の公開工程では公開担当が行う）。

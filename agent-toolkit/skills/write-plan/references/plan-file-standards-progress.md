# 計画ファイル作成基準：進捗ログ

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 共通参照AWI読解前 | `../../wi-standards/references/cross-repository-submission.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/cross-repository-submission.md` |
| 参照基準の適用前 | `../../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| レーンの予測補正記録を書く前 | `../../process-wi/references/selection-format-prediction.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-format-prediction.md` |
| 本文で基準を使う資料を選択・取得する時 | `../../commit/references/commit-wi-records.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/commit-wi-records.md` |
| 実作業の経験の恒久化候補を選ぶ前 | `agent-basics:writing-standards/references/investigation-experience.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-experience.md` |

## 進捗ログ

`## 進捗ログ`は中断後に再開するための大まかな記録で、`日時`、`完了した工程`、`結果・特記事項`の3列の表を置く。
初回起草時はデータ行を入れない。

`結果・特記事項`列には、後続の工程や再開するエージェントが使う情報だけを書く。
途中の観測経過や後続で参照しない一時ファイルのパスは省き、コミットは一意に特定できる短縮OIDで書く。

- 追記は`atk run-script plan-progress -- <計画ファイル> --completed-step <完了した工程> --result <結果・特記事項>`で行う（日時はスクリプトが生成する）
- 実装開始後は、再開時に次の工程を判断できる情報（コミットの完了、変更範囲の検証結果、実行レビューの完了、全体の完了判定、専用worktreeの絶対パス、専用ブランチ名、回収するリソースなど）を順に追記する
- WI実装コミットの記録、取得、履歴改変後の引き継ぎは`../../commit/references/commit-wi-records.md`「WI実装commitの対応」に従う（対応記録ファイルに短縮OIDと対象AWI群を保存し、進捗ログには固定3列表の行だけを追記する）
- `agent-toolkit:process-wi`のレーン処理では、統合工程の行の`結果・特記事項`に`レーン稼働時間: <秒数>秒`を記録する。
  秒数は、メインエージェントが最初のレーン担当者を起動した直後に保存・伝達した`レーン開始時刻`から、`atk lane integrate`のGit統合が成功して最終化の最初の操作を始める直前までの総経過時間で、待機、レビュー、統合前の失敗と復旧を含み、担当セッションが交代しても最初の開始時刻を起点にする。
  秒数と完了時刻は同CLIが算出した値を最終化コマンドの引数参照から取り、手作業で仮の数値を入れない
- 同じ行に`../../process-wi/references/selection-format-prediction.md`「予測補正記録」の書式で選定値・工程別の実測と根拠を記録する
- 完了報告を作るエージェントは、完了条件ごとに達成の根拠か未達の理由を進捗ログの最終行に記録する

再開するエージェントは、進捗ログの最終行、`## 概要`の目的、`## 変更履歴`のユーザー発言、完了条件、レビュー指摘管理表の回答を確かめてから次の工程を決める。
計画ファイルと指摘管理表の内容が食い違う場合は、指摘管理表の回答を現在の正式な結論とする。

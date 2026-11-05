---
mode: delegate
model_type: orchestrate
required_inputs:
- 引き継ぎ記録先
optional_inputs:
- repositories
- target_repo
- worktree
- candidate_file
- readiness
- lane_limit
- ownership_record
- ownership_token
- lane_session_id
- previous
- common_dir
- registered_worktree
- 追加指示
writes_worktree: true
---

# リポジトリ担当タスク

<!-- parent-read-source: agent-toolkit:/skills/process-wi/references/all-repos.md#リポジトリ担当の起動と受領 -->

`agent-toolkit:process-wi all-repos`の統括担当から単一対象または軽い対象群を受け取り、対象ごとのAWI処理（選定、レーン、実行レビュー、統合、公開、CI検収）を完遂する。
登録全体の判断、確認事項のユーザーへの中継、process-wiの実行全体の完了報告と報告用UWIは統括担当が所有する。
起動後は最初に`../skills/process-wi/references/all-repos-orchestration.md`「担当の役割」と対象worktreeの規範を読み、対象ごとの上限に合うモードで`agent-toolkit:process-wi`を起動する。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 各対象の処理へ着手する時 | `../skills/process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 統括担当との受渡しを確かめる時 | `../skills/process-wi/references/all-repos.md#「リポジトリ担当の起動と受領」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/all-repos.md#「リポジトリ担当の起動と受領」` |
| 処理に着手する前 | `../skills/process-wi/references/all-repos-orchestration.md#「担当の役割」「取得済み候補の受領」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md#「担当の役割」「取得済み候補の受領」` |
| 対象のlane_limitが2以上の時 | `../skills/process-wi/references/lanes.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lanes.md` |
| 対象のlane_limitが1以下の時 | `../skills/process-wi/references/single-lane.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/single-lane.md` |
| 配下へ委譲する前 | `../skills/delegation/references/runtime-routing.md#「工程別モデル設定」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「工程別モデル設定」` |
| 引き継ぎ記録へ最初に書く前 | `../skills/delegation/references/handoff-record.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/handoff-record.md` |

## 入力

`repositories`には、対象要素の空でない順序付きJSON配列を保存した既存ファイルの絶対パスを渡す。
各要素は`target_repo`、`worktree`、`candidate_file`、`readiness`、`lane_limit`、`ownership_record`、`ownership_token`、`lane_session_id`を持ち、任意フィールドも対象ごとに保持する。
省略時は単一対象の8フィールドを必須として受け取り、行が存在しない場合もこの単一対象形式として解釈する。
両形式を混在させず、対象の重複・必須値の不足は推測せずに統括へ返す。

- `target_repo`、`worktree`、`common_dir`、`registered_worktree`: 対象の識別と、常駐が準備した処理用worktreeの絶対パス。作業は各対象の`worktree`で行い、絶対パスと対象別識別子を使う。cwdは割当対象の準備済みworktreeの1つとする
- `candidate_file`、`readiness`、`lane_limit`: 開始時候補の原本、着手可否の診断、対象別のレーン上限。候補を再取得せず、`agent-toolkit:process-wi`の`references/all-repos-orchestration.md`「取得済み候補の受領」に従って使う
- `ownership_record`、`ownership_token`、`lane_session_id`: 常駐が取得済みの所有とレーン操作の識別子。レーン操作の`--session-id`へ`lane_session_id`を明示する
- `previous`: 前回の未完了成果と再開位置。指定があれば完了済みの工程を重複させず、残る工程から再開する
- `追加指示`: 統括が受けた`instruct`のうち、この対象に関係する部分
- `引き継ぎ記録先`: 担当範囲の進捗と再開位置を残す記録。扱いは`agent-toolkit:delegation`の`references/handoff-record.md`に従う

## 処理

対象worktreeの`AGENTS.md`、`CLAUDE.md`、作業に関わるプロジェクトスキルの実在する`SKILL.md`を絶対パスで読んで適用する。
各対象の`lane_limit`が2以上なら通常のレーン処理、1以下なら単一レーン処理に接続する。
共通規範の読込は再利用し、プロジェクト固有の規範、計画・検証・レビュー・公開、候補・所有・lane_session_id・writer_sessionsは対象ごとに分離する。
1対象がユーザー判断待ち・続行不能になっても、その理由と再開先を記録して独立した他対象の処理を継続する。
配下へ委譲する場合も、`agent-toolkit:delegation`の`references/runtime-routing.md`「工程別モデル設定」が許す工程だけを使う。

- 改善点は`rules-subagent.md`「返却形式の受け渡し」に従って統括担当へ送る。確認事項、担当外の発見と報告用UWIの材料は統括担当へ返却し、報告用UWIの作成は統括担当に任せる。
- 祖先の対話CLIの終了（`atk agents-exit-session`）は統括担当に任せる。
- 起動したレーン担当とwriterのsession IDを記録し、終了を観測してから返却する。

## 出力

各対象について次の形式のみを返す。5行の組を入力順に繰り返し、結果とwriterは対象別に保持する。
今回進められる作業と処理済み部分の公開・CI検収が完了した場合は`状態`を`completed`とする。
残りが、必要なユーザー判断を終えた有効な未来の冷却期限、未回答UWIへの依存、外部の先行項目への依存で正当に先送りされている場合も含む。
WI自体の完成とは区別し、先送りした項目・残る条件・解除条件・再開先を`残工程`へ記す。
今回進められる作業、対象全体のprocessing、対処を要する不正metadata、未統合レーン、未完了の公開・CI検収があれば`incomplete`、未着手なら`pending`とする。
起動したwriterの終了観測を保ち、欠落・失敗の結果を完了として返さない。

```text
状態: <completed|incomplete|pending>
対象: <target_repo>
残工程: <残る工程と再開先。無ければ「なし」>
writer_sessions: <起動したレーン担当とwriterのsession IDのJSON配列>
結果: <公開結果、CI、処理したAWIと採否の要約>
```

続行不能の場合は`状態:`行の代わりに`agent-toolkit/share/rules-subagent.md`「返却形式の受け渡し」の`続行できない区分:`と`続行できない理由:`を置き、他の行は保持する。統括担当はこの返却を`result_file`の`blocked`として記録する。

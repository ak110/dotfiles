---
mode: delegate
model_type: orchestrate
result_heads:
- "状態: incomplete"
- "状態: pending"
required_inputs:
- 引き継ぎ記録先
optional_inputs:
- repositories
- target_repo
- worktree
- candidate_file
- snapshot_file
- readiness
- lane_limit
- lane_session_id
- common_dir
- registered_worktree
- target
- target_branch
- default_branch
- 追加指示
writes_worktree: true
---

# リポジトリ担当タスク

all-reposの統括から明示的な委譲で単一の対象か軽い対象群を受け取り、対象ごとのAWI処理（選定、レーン、実行レビュー、統合、公開、CI検収）を完遂する。
登録全体の判断、確認事項のユーザーへの中継、実行全体の完了報告と報告用UWIは統括が所有する。
本担当はreport-completionの起動、報告用UWI、ユーザーへの最終送達、祖先ホストの終了を担当しない。
この除外は本書で判断でき、除外の確認のためのスキル本体・termination-evidenceの取得や`omit-completion`の記録は要らない。
必要な常時稼働サーバーへの反映は`agent-toolkit:atk-mode/playbooks/report-completion.md`「常時稼働サーバーへの反映」を使い、結果を委譲元へ返す。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 配下へ委譲する前 | `agent-toolkit:atk-mode/references/model-settings.md` |

## 入力

`repositories`には、対象要素の空でない順序付きJSON配列を保存した既存ファイルの絶対パスを渡す。
各要素は`target_repo`、`worktree`、`candidate_file`、`readiness`、`lane_limit`、`lane_session_id`を持ち、任意の欄も対象ごとに持つ。
省略時は単一対象の同じ6欄を必須として受け取り、行が無い場合もこの単一対象の形式として読む。
両形式を混ぜず、対象の重複や必須値の不足は推測せずに統括へ返す。

- `target_repo`、`worktree`、`common_dir`、`registered_worktree`: 対象の識別と、統括が準備した処理用worktreeの絶対パス。
  作業は各対象の`worktree`で行い、cwdは割り当てられた対象の準備済みworktreeの1つとする。
- `target`、`target_branch`、`default_branch`: 結果を対応付ける対象の識別子と、系統を登録したリポジトリで処理する系統と、系統を省略したWIの宛先の系統。
  `target`が無い対象は`target_repo`を識別子とする。
- `candidate_file`、`readiness`、`lane_limit`: 開始時の候補の原本、着手可否の診断、対象別のレーン上限。
- `snapshot_file`: 同じ初回取得で保存した開始時集合の絶対パス。
  行がなければ、新着の判定前に既存の開始時集合を統括から取得する。
- `lane_session_id`: 対象ごとのレーン操作の識別子。
  レーン操作の`--session-id`へ`lane_session_id`を明示する。
- `追加指示`: 統括が受けた`instruct`のうち、この対象に関係する部分。
- `引き継ぎ記録先`: 担当範囲の進捗と再開位置を残す記録（扱いは`agent-toolkit:atk-mode/references/work-contract.md`）。

## 処理

1. 受け取った対象ごとに、スキル`agent-toolkit:atk-mode`を作業`process-wi`で起動し、`lane_limit`が2以上なら複数レーン、1以下ならsingle-laneで処理する。
   受け取った候補の扱いは`agent-toolkit:atk-mode/playbooks/process-wi-all-repos.md`「取得済み候補の受領」、担当の分担は同書「担当の役割」に従う。
2. 共通の規範の読込は再利用し、プロジェクト固有の規範、計画・検証・レビュー・公開、候補・所有・`lane_session_id`は対象ごとに分ける。
   1つの対象がユーザー判断待ちか続行不能になっても、理由と再開先を記録して独立した他の対象の処理を続ける。
3. 改善点は`agent-toolkit:atk-mode/playbooks/subagent.md`「返却形式の受け渡し」に従って統括へ送り、確認事項、担当外の発見、報告用UWIの材料は統括へ返す。
4. 祖先の対話CLIの終了（`atk agents-exit-session`）は統括に任せる。
5. 起動したレーン担当とwriterの終了を観測してから返す（session IDは`agents_server`の登録を原本とする）。
   Claude Codeで背景の `atk agents wait` の完了通知により同じセッションへ自動再開できる場合に限り、ターンを閉じる返却本文は `待機中: <実行識別子>` の1行だけにする。
   自動再開が成り立つかの確かめ方と、確かめられない場合の同じターンでの回収は、`agent-toolkit:atk-mode/playbooks/delegation-waiting.md`「完了通知を待ってターンを終える場合」に従う。
   再開後は配下の結果を回収して残作業を進め、最終結果は本書「出力」に従って返す。

## 出力

各対象について次の形式のみを返す。
4行の組を入力順に繰り返し、結果は対象別に保持する。
今回進められる作業と処理済み部分の公開・CI検収が完了した場合は`状態`を`completed`とする。
残りが、必要なユーザー判断を終えた有効な未来の冷却期限、未回答UWIへの依存、外部の先行項目への依存で正当に先送りされている場合も含む。
WI自体の完成とは区別し、先送りした項目・残る条件・解除条件・再開先を`残工程`へ記す。
今回進められる作業、対象全体のprocessing、対処を要する不正metadata、未統合レーン、未完了の公開・CI検収があれば`incomplete`、未着手なら`pending`とする。
起動したwriterの終了観測を保ち、欠落・失敗の結果を完了として返さない。

```text
状態: <completed|incomplete|pending>
対象: <target>
残工程: <残る工程と再開先。無ければ「なし」>
結果: <公開結果、CI、処理したAWIと採否の要約>
```

続行不能の場合は`状態:`行の代わりに`agent-toolkit:atk-mode/playbooks/subagent.md`「返却形式の受け渡し」の`続行できない区分:`と`続行できない理由:`を置き、他の行は保持する。
統括担当はこの返却を、その対象の`blocked`として完了報告に載せる。
`user_decision`・`authorization`・`inaccessible_input`の不足をユーザーだけが提供できる場合は、統括担当が同じprocess-wiの実行内で処置する。

処置は`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「採否の確定と終了区分」手順3に従う。
統括自身か割当先が、事前承認型UWIを保存して元AWIへ依存を登録し、`inbox`へ差し戻す。
統括は保存結果を検収する。

# 登録全体のAWI処理

本スキルを起動した統括担当は、最上位のメインとして登録した全対象をprocess-wiの実行として完遂する。
集合JSONを受領した統括担当は、そこにある候補・所有・準備先を使用する。
開始時候補を再取得せず、対象別の`candidate_file`を選定の原本とする。
巡回ごとに統括会話を1回起動し、次の巡回では新しい会話が残件を選定する。
明示resumeでは保存済みの会話と未完了成果を引き継ぐ。

対象別の選定・監査・公開には[共通の受渡し規範](all-repos-orchestration.md)を適用する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 共通の受渡し規範を選択して取得する時 | `all-repos-orchestration.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md` |
| リポジトリ担当の処理規範を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 統括がlane_limit 2以上の対象を直接処理する時 | `lanes.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lanes.md` |
| 統括がlane_limit 1以下の対象を直接処理する時 | `single-lane.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/single-lane.md` |
| リポジトリ担当の引き継ぎ記録先を指定する時 | `../../delegation/references/base-contract-handoff.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract-handoff.md` |
| 先行工程を次の巡回へ回す前 | `agent-toolkit:process-wi/references/lane-resources.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lane-resources.md` |

## リポジトリ担当の起動と受領

```text
起動対象: repository-process.subagent.md
```

### 起動

**4.** 定型のリポジトリ担当はagents_serverの`start`をtaskで呼ぶ。
`model_type`を指定せず、役割文書の`orchestrate`を使う。

| 引数 | 値 |
| --- | --- |
| `cwd` | 単一対象の準備済み`worktree`。まとめ担当ではグループ内の準備済みworktreeの1つ |
| `subagent_md_path` | `repository-process` |
| `extra_params` | 次の名前付き入力だけ |

- 単一対象では`target_repo`、`worktree`、`candidate_file`、`readiness`、`lane_limit`、`ownership_record`、`ownership_token`、`lane_session_id`に集合JSONの対象要素の同名の値を渡す
- まとめ担当では、割り当てる対象要素をそのまま順序付きJSON配列へ保存し、単一対象の各フィールドの代わりにその絶対パスを`repositories`へ渡す
- `previous`、`common_dir`、`registered_worktree`: 対象要素に値がある場合だけ渡す
- `追加指示`: `instruct`のうち対象に関係する部分がある場合だけ渡す
- `引き継ぎ記録先`: 指定の省略や継続時の扱いは`../../delegation/references/base-contract-handoff.md`「引き継ぎ記録先」に従う

### 受領

**5.** 実行結果は引数なしの`atk agents wait`で受け取り、本書「リポジトリ担当の返却契約」の固定形式を満たしていることを確認する。

返却の各`対象`を割当集合と突き合わせ、重複・欠落なく各対象の`状態`、`残工程`、`writer_sessions`を`all-repos-completion.md`「結果と終了」の`result_file`へ保存して読戻す。
続行不能はその対象だけ`blocked`として理由と再開先を残し、他対象の結果は保つ。

<!-- return-contract:repository-process:start -->
<!-- 生成元: share/repository-process.subagent.md「出力」。手動編集しない。 -->
### リポジトリ担当の返却契約

各対象について次の形式のみを返す。
5行の組を入力順に繰り返し、結果とwriterは対象別に保持する。
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

続行不能の場合は`状態:`行の代わりに`../../../share/rules/rules-subagent.md`「返却形式の受け渡し」の`続行できない区分:`と`続行できない理由:`を置き、他の行は保持する。
統括担当はこの返却を`result_file`の`blocked`として記録する。
<!-- return-contract:repository-process:end -->

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 開始と振り分けに関する判断・作業の前 | `all-repos-starting.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos-starting.md` |
| 結果と終了に関する判断・作業の前 | `all-repos-completion.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos-completion.md` |

# 登録全体のAWI処理

本スキルを起動した統括担当は、最上位のメインとして登録した全対象をprocess-wiの実行として完遂する。
集合JSONを受領した統括担当は、そこにある候補・所有・準備先を使用する。
開始時候補を再取得せず、対象別の`candidate_file`を選定の原本とする。
初回起動と継続を同じ統括会話で扱い、継続では未完了対象だけを処理する。

対象別の選定・監査・公開には[共通の受渡し規範](all-repos-orchestration.md)を適用する。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したら判断や操作の前に指定節と、その成立に必要な前提・例外を自分で読む。全体が判断に必要な場合は全文を読む。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 共通の受渡し規範を選択して取得する時 | `all-repos-orchestration.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md` |
| リポジトリ担当の処理規範を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 統括がlane_limit 2以上の対象を直接処理する時 | `lanes.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lanes.md` |
| 統括がlane_limit 1以下の対象を直接処理する時 | `single-lane.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/single-lane.md` |
| リポジトリ担当の引き継ぎ記録先を指定する時 | `../../delegation/references/base-contract.md#「引き継ぎ記録先」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/base-contract.md#「引き継ぎ記録先」` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 開始と振り分け

集合JSONを伴わない手動起動では、`atk wi process-loop --once`を実行する。
この公開操作が対象別の所有・候補ファイル・worktreeを準備し、登録全体を1回処理する統括会話を起動する。
起動元はCLIの終了まで観測し、返却結果をユーザーへ伝える。
対象の作業・完了報告・報告用UWIは起動された統括に任せる。
対象0件では待機せず終了し、未完了・所有待ち・準備失敗は非0で終了する。
以降は集合JSONを受領した統括担当へ適用する。
集合JSONの`repositories`を対象集合とする。
各対象の準備済み`worktree`を処理の起点とする。
`ownership_record`と`ownership_token`は常駐が取得済みの所有であり、配下の担当へそのまま渡す。
`lane_session_id`はリポジトリ単位で保持し、レーン操作の`--session-id`へ明示する。

対象worktreeの作業に入る前に、その対象の`AGENTS.md`、`CLAUDE.md`、作業に関わるプロジェクトスキルの実在する`SKILL.md`を絶対パスで読んで適用する。
ホームなどのプロジェクト外cwdでは、対象のプロジェクトスキルを明示的に読み込み、通常のプロジェクトhookが担う確認を実施する。
対象の規範と既存チェックを適用してhookの目的を満たす。
担当の振分け前に、`agent-toolkit/rules/01-agent.md`「行動と手順の目的」に従い、今回の対象集合で委譲が得る成果と直接処理との差を点検する。
WorktreeCreate/WorktreeRemoveは対象設定を読む既存atk操作で実行し、常駐が準備したworktreeを利用する。

今回の集合JSONの`repositories`が1件なら、`lane_limit`によらず統括自身が直接処理する。
対象が2件以上の場合は、候補数Nに対する`lane_limit=floor((N+9)/10)`が2以上の対象を「リポジトリ担当の起動と受領」に従って委譲し、それ以外を統括自身が処理する。
直接処理では`lane_limit`が2以上なら[通常のレーン処理](lanes.md)、1以下なら[単一レーン処理](single-lane.md)へ接続する。
両経路とも受領済み候補・readiness・対象worktree・対象別`lane_session_id`を使い、起動したレーン担当とwriterを対象別の`writer_sessions`へ記録する。
対象worktreeごとに計画・実行レビュー・公開を分ける。
対象別の開始時CI点検、監査、UWI、公開とCI検収を実施する。

## リポジトリ担当の起動と受領

```text
起動対象: repository-process.subagent.md
```

### 起動

リポジトリ担当の起動は定型作業であり、agents_serverの`start`をtaskで呼ぶ。
`model_type`は指定せず、役割文書が定める`orchestrate`を使う。

| 引数 | 値 |
| --- | --- |
| `cwd` | 対象の準備済み`worktree` |
| `subagent_md_path` | `repository-process` |
| `extra_params` | 次の名前付き入力だけ |

- `target_repo`、`worktree`、`candidate_file`、`readiness`、`lane_limit`、`ownership_record`、`ownership_token`、`lane_session_id`: 集合JSONの対象要素の同名の値
- `previous`、`common_dir`、`registered_worktree`: 対象要素に値がある場合だけ渡す
- `追加指示`: `instruct`のうち対象に関係する部分がある場合だけ渡す
- `引き継ぎ記録先`: 指定の省略や継続時の扱いは`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う

### 受領

実行結果は引数なしの`atk agents wait`で受け取り、本書「リポジトリ担当の返却契約」の固定形式を満たしていることを確認する。
返却の`状態`、`残工程`、`writer_sessions`を「結果と終了」の`result_file`へ転記する。
続行不能の返却は`blocked`として記録する。

<!-- return-contract:repository-process:start -->
<!-- 生成元: share/repository-process.subagent.md「出力」。手動編集しない。 -->
### リポジトリ担当の返却契約

次の形式のみを返す。
公開・CI検収まで終了した場合は`状態`を`completed`、残工程がある場合は`incomplete`、未着手の場合は`pending`とする。

```text
状態: <completed|incomplete|pending>
残工程: <残る工程と再開先。無ければ「なし」>
writer_sessions: <起動したレーン担当とwriterのsession IDのJSON配列>
結果: <公開結果、CI、処理したAWIと採否の要約>
```

続行不能の場合は`状態:`行の代わりに`../../../share/rules-subagent.md`「返却形式の受け渡し」の`続行できない区分:`と`続行できない理由:`を置き、他の行は保持する。統括担当はこの返却を`result_file`の`blocked`として記録する。
<!-- return-contract:repository-process:end -->

## 結果と終了

処理を開始した担当とwriterの実行識別子を対象別に記録し、終了を観測してから所有の解放と回収へ進む。
続行できない対象はその対象の理由と再開位置を残し、独立した他の対象は処理する。
常駐からの継続指示では残る対象の未完了工程を再開する。
process-wiの実行全体の完了報告と報告用UWIは統括が1回だけ作成する。
配下のリポジトリ担当は報告用UWIを独立に作らず、公開結果・CI・残工程を統括へ返す。

常駐起動では、会話を終了する前に集合JSONの`result_file`へ次のJSONをUTF-8で保存する。
`repositories`のキーは今回の集合JSONが渡した対象の`target_repo`に限る。継続指示で同じ会話を再開した場合も、前ラウンドで終えた対象は書かない。値の`status`は`completed`、`incomplete`、`blocked`、`pending`のいずれかとする。
`completed`は公開・CI検収まで終了、`incomplete`は残工程あり、`blocked`は続行不能、`pending`は未着手を意味する。
各対象に残工程・再開先・続行不能の理由と、起動した`writer_sessions`の配列を記録する。
リポジトリ担当を開始した直後にもその識別子を保存し、統括の中断後に生存writerを確認できるようにする。
同じ対象を複数の担当が書き込まないよう、配下担当の終了を確かめてから統括自身の作業へ切り替える。

```json
{"repositories":{"example.test/team/repository":{"status":"incomplete","remaining":"CIの検収","writer_sessions":[]}}}
```

結果の保存後は最上位セッションの終了手順に従い、統括CLIの終了を常駐へ返す。
常駐は結果の申告と実際のキュー・レーン登録が一致することを確認する。
起動済み担当やwriterが未終了の場合は、資源を保持して終了を待機する。

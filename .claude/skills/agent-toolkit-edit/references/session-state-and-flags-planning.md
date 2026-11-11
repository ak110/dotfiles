# セッション状態ファイルとフラグ：作業契約系

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 各状態の共通設計・生存期間・排他・継承を適用する前 | `session-state-and-flags-common.md` |
| 呼び出し主体の判別方法を確かめる前 | `agent-basics:writing-standards/references/claude-hooks.md` |
| run-skillの再開入力と寿命を変更・調査する前 | `agents-server-shared-state-run-skill.md` |

## 作業契約系

run-skillが保持する元の実行入力・会話・時間予算は、[共有状態資料のrun-skillの再開入力](agents-server-shared-state-run-skill.md)に従う。
通常のhook状態JSONへ複製しない。

- `whole_contract_notified`: 委譲先の主体ごとに、全体作業契約の再読案内を最終出力へ採用した内容ハッシュの一覧を保持する。
  PreToolUseと圧縮後のSessionStartが、起動時の正式な作業契約入力と既存の資料読了記録から未読版を判定する。
  本文は配送せず、同じ版は1回だけ非遮断で案内する。
  実際の全文読取後は案内を抑止し、compact時はこの通知記録を削除して所在を再案内する。
  セッション状態と同じ寿命で保持する。
- `work_contract_input_sequence`: UserPromptSubmitが実ユーザー入力ごとに進める整数。
  初期値は0で、機械注入の間は値を保つ。
  PreToolUseが未作成通知の入力区切りとして参照する。
  セッション状態と同じ寿命で保持する。
- `work_contract_notified_sequence`: PreToolUseが未作成のメインへ通知した入力連番。
  排他ロック下で比較・更新し、同じ入力では1回だけ通知する。
  SessionStartの`compact`時に削除して、圧縮後の最初の操作で再通知する。
  他はセッション状態と同じ寿命で保持する。

## plan系

- `plan_mode_skill_invoked`: write-planスキルの起動を記録し、計画ファイルの構造に対する自動チェックを適用するかどうかの判定に使用する
- `current_plan_file_path`: PostToolUseが、計画ファイルの編集時や公開された計画作成コマンドの出力からファイルパスを記録する。
  PreToolUse(Skill)がwrite-plan起動時にこの値を消去する。
  UserPromptSubmitのコマンドhookがセッション名を選択する際にこのパスを参照する
- `last_hook_session_title`: UserPromptSubmitのコマンドhookが設定したセッション名。
  現在のセッション名が空であるか、この値と一致している場合に限り、新しい種別や計画名への更新を許可する。
  同hookが通常状態ファイルへ排他ロック下で保存する。
  通常状態ファイルと同様の保持期限および`clear`のタイミングで消去される
- `session_title_kind`: PostToolUse(Skill)、およびスキルの手動起動を受け取ったUserPromptSubmitが記録する命名用の種別（`process-wi`、`single-lane`、`all-repos`、`add-awi`）。
  UserPromptSubmitのコマンドhookがセッション名を選択する際に参照する。
  種別が`all-repos`の場合と、process-loopの環境識別印と会話IDが一致している場合（記録された種別を問わない）は、計画名を連結しない統括名`process-wi all-repos`を使う。
  生存期間は通常状態ファイルと同一とする

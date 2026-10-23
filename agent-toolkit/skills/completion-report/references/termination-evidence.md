# 終了工程の判断の記録

本書はメインが、Stop hookの遮断を受けた時と、完了報告の省略、作業の中止・置換、待機または技術的不成立の判断を記録する時の手順を定める。
適用するプロジェクト規範で`agent-toolkit:completion-report`を省く場合は、本スキルを起動せず本書を直接読む。

Stop hookが同スキルの起動不足を示して遮断した場合、起動が必要な作業を完了して終えるなら同スキルを起動し（Codexは`SKILL.md`を読む）、同スキルの`SKILL.md`「工程」の残る手順へ進む。
報告本文の修正や全文の再送は遮断を解除しない。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「工程」` | 判断 | `section:agent-toolkit:*/skills/completion-report/SKILL.md#「工程」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/references/all-repos-orchestration.md#「担当の役割」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md#「担当の役割」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |

## プロジェクト規範による省略

メインは`agent-toolkit/rules/01-agent.md`「方針が衝突する場合の優先順位」から、対象作業で本スキルの起動を省くか判断する。
省略する場合は、次のJSONを一時領域へ保存し、`atk run-script termination-evidence -- --decision-file <判断JSONの絶対パス>`で記録して通常の報告を届ける。

```json
{
  "session_id": "現在の会話の識別子",
  "action": "omit-completion",
  "work_id": "観測済み作業の識別子",
  "reason": "規範の意味と優先順位から省略する理由",
  "policy_file": "/対象リポジトリ/CLAUDE.md",
  "target_repo": "/対象リポジトリ"
}
```

識別子はStopの通知から得る。
Claude Codeでは`atk run-script termination-evidence -- --context`からも現在の識別子を取得できる（取得結果の項目は次節）。
省略は人間入力を根拠とするため、取得結果の`input_id`が値を持つ会話でだけ記録できる。
`policy_file`は対象リポジトリ内の適用するプロジェクト規範の絶対パス、`target_repo`は観測した成果作業が属するGitリポジトリのrootの絶対パスとする。
複数のリポジトリの成果作業を持つ場合は、作業をリポジトリごとに区切って判断する。
記録処理は`policy_file`から規範全文を読んで保存し、観測済み成果作業の対象と所在、現在のメインと会話・人間入力の対応を確かめる。
省略した`input_id`は現在の会話の直近の人間入力へ対応付ける。
明示した`input_id`が現在の人間入力を指し、`quote`が規範全文と一致するか確かめる。
規範の意味と優先順位はメインが判断する。
規範の内容変更、次の判断、新しい人間入力の後の成果作業では、古い省略を流用せず改めて判断する。
省略は指定した作業の起動不足だけへ作用し、別作業の不足とprocess-loopの`atk agents-exit-session`の義務は残る。
all-reposのリポジトリ担当は`agent-toolkit:process-wi`の`references/all-repos-orchestration.md`「担当の役割」に従い、同コマンドの代わりに最終返却でターンを終える。
振り返りを起動するかは、適用するプロジェクト規範が振り返りについて定める内容に従う（起動しない条件は`SKILL.md`「工程」手順6の列挙）。

## 中止・置換・待機・技術的不成立

ホストの猶予通知が続行を許す間は残工程を続ける。ホストが再開を禁止した場合は、状況と再開に必要な操作を`agent-toolkit:user-confirmation-and-report`でユーザーへ伝える。
`wait`は実際に継続中の待機、`blocked`は観測した失敗で技術的に続行できない場合に記録する。

ユーザーが作業の中止、別作業への置換を指示した場合、確認の回答または起動した委譲先の結果を待つ場合、技術的に続行できない場合は、`atk run-script termination-evidence -- --decision-file <判断JSONの絶対パス>`で判断を記録する。
判断JSONは`session_id`、`action`（`cancel`・`replace`・`start`・`resume`・`wait`・`blocked`）、対象の`work_id`と`reason`を持つ。
中止・置換・開始・再開にはユーザーの原入力の`input_id`を、確認待ちには投入した未回答UWIの`uwi_file`を、委譲先の待機には`target_session_id`を、技術的不成立には失敗した呼び出しの`call_id`を渡す。
記録処理は識別子から本文や応答を読み、由来と対象を確かめる。
`quote`は省略でき、明示した場合は取得した実体の全文との一致を確かめる。
`session_id`、`work_id`と直近のユーザー入力の`input_id`は遮断の通知から得る。
Claude Codeでは`atk run-script termination-evidence -- --context`からも取得できる。
取得結果は`session_id`と現在の`work_id`を人間入力の有無によらず返し、`input_id`と原文の`quote`は人間入力を観測した場合だけ値を持ち、観測していなければ`null`とする。
委譲先の待機と技術的不成立は実行結果を根拠とするため、人間入力の無い自動起動の会話でも取得した`session_id`と`work_id`で記録できる。
中止・置換・開始・再開は人間の原入力を根拠とするため、`input_id`が値を持つ場合にだけ記録できる。
判断は指定した作業だけへ作用し、ユーザーの割り込みと直接関係しない他の作業の起動不足は残る。
生成されたStopの継続入力やhookの注記はユーザーの原入力として受理されず、完了の申告を記録する操作は無い。
証拠を取得できない場合、Stop hookは遮断せず、判定できなかったことを記録する。
自律モードの報告用UWIの保存検収と`atk agents-exit-session`はStop hookの判定と別に同スキルの手順8・9が定める。

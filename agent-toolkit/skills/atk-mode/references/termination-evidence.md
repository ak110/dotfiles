# 終了工程の判断の記録

作業を終える担当が終了の判断を記録し、ユーザーが報告の無い終了や未達を完了と誤認しないようにするための手順である。
作業の対象、本人の中止・置換、待機、実際の失敗と公開範囲の判断を記録する。
最後に元要求・成果・未回収対象・本人へ届ける結果を照合する。
完了の判定は手順書の全文取得の記録で代用しない。
完了報告の要否は`agent-toolkit:atk-mode/playbooks/report-completion.md`「適用の判定」、計画保存の契機は`agent-toolkit:atk-mode/references/plan-storage.md`に従う。

## 中止・置換・待機・技術的不成立

作業の中止や別作業への置換を指示された場合、確認への回答や起動した委譲先の結果を待つ場合、技術的に続けられない場合は、`atk run-script termination-evidence -- --decision-file <判断JSONの絶対パス>`でその判断を記録する。

- 判断JSONには`session_id`、`action`（`cancel`、`replace`、`start`、`resume`、`wait`、`blocked`のいずれか）、対象の`work_id`、`reason`を入れる。
- 根拠の識別子は、中止・置換・開始・再開ではユーザーの元の入力の`input_id`、確認待ちでは投入した未回答UWIの`uwi_file`、委譲先の待機では`target_session_id`、技術的不成立では失敗した呼び出しの`call_id`とする。
- `wait`は実際に続いている待機がある場合、`blocked`は観測した失敗で技術的に続けられない場合に記録する。
- `session_id`、`work_id`、直近のユーザー入力の`input_id`は、Claude Codeでは`atk run-script termination-evidence -- --context`で取れる。
  取得結果は人間入力の有無によらず`session_id`と現在の`work_id`を含み、`input_id`と原文の`quote`は人間入力を観測した場合だけ値を持つ（無ければ`null`）。
- 委譲先の待機と技術的不成立は実行結果を根拠とするため、人間入力の無い自動起動の会話でも記録できる。
  中止・置換・開始・再開は人間の元の入力を根拠とするため、`input_id`に値がある場合だけ記録できる。
- 記録処理は各識別子から本文や応答を読み、由来と対象を確かめる。
  `quote`は省略でき、指定した場合は取得した実体の全文との一致を確かめる。
- 記録は指定した作業にだけ作用し、ユーザーの割り込みと関係しない他の作業の対象や判断は変えない。
- Stop hookの継続入力や注記はユーザーの元の入力として受理されず、完了を自己申告する記録の操作は無い。
- 報告用UWIの保存検収は、`agent-toolkit:atk-mode/playbooks/report-completion.md`「工程」の手順6に従う。

ホストの猶予通知で続行が許されている間は、残りの工程を進める。
ホストが再開を禁じた場合は、現在の状況と再開に必要な操作を`agent-toolkit:atk-mode/playbooks/confirmation.md`に沿ってユーザーへ伝える。

## 受理する証拠

判断の記録が受理する証拠は次のとおりである。

- 技術的不成立の記録（`blocked`）: 失敗応答を返したツール呼び出しだけを受理する。
  別の`call_id`で再試行した呼び出しの結果は、元の失敗の証拠から外れる。
- `wait`: 所有する委譲先への待機が続いている場合だけを受理する。
  待機が終わっている場合は、`atk agents wait`で待機を始めるか、回収後の残工程へ戻る。

## 技術的不成立の判定

利用上限の猶予通知と、ホストによるバックグラウンドコマンドの停止は、技術的不成立に含めない。
猶予通知を受けた場合は、`agent-toolkit:atk-mode/references/usage-limit.md`に従って同じ工程を続ける。
ホストが再開を禁じた処理は、再開してよいかをユーザーへ確かめる。
確認の手段は、協調モードでは`AskUserQuestion`、自律モードではUWIの投入とし、UWIは`wait`の`uwi_file`で記録する。
ホストの通知による停止は、終了理由の記録の対象外である。

## 終了方法の分岐

終了の理由に応じて次の操作を選ぶ。

| 終了の理由 | 操作 | 判定条件 |
| --- | --- | --- |
| 作業を完了して終える | 元要求・成果・未回収対象と報告の送達を照合し、`agent-toolkit:atk-mode/playbooks/report-completion.md`の必要な工程を終える | 適用する規範と起動元が定める完了条件を満たす |
| ユーザーが中止・置換を指示した | `cancel`か`replace`を記録する | 元の人間入力の`input_id`がある |
| 確認の回答や委譲先の結果を待つ | `wait`を記録する | 確認待ちは未回答UWIの`uwi_file`、委譲先の待機は`target_session_id`を指定する |
| 技術的に続けられない | `blocked`を記録する | 失敗した呼び出しの`call_id`を指定する。判定は本書「技術的不成立の判定」に従う |

記録は、元の証拠と対象の`work_id`を判断JSONに入れ、`atk run-script termination-evidence -- --decision-file <判断JSONの絶対パス>`へ渡して行う。

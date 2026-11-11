# 定期実行からのスキル起動の設計記録

[設計記録の索引](design.md)から参照する、定期実行からのスキル起動の記録である。

## `atk run-skill`

`atk run-skill`はcronなど端末を持たない定期実行から、指定した対象リポジトリで指定したスキルを自律モードの最上位セッションとして1回実行する。
ユーザーは別環境のリポジトリに持つスキルを数日に1回、自律モードで実行したいと求めた。
確認への回答では、WIキューを経由しない単発実行コマンドと毎回の報告用UWIを選んだ（2026年10月6日）。
実装は`agent-toolkit/agent_toolkit/_atk/run_skill.py`が持つ。
モデル候補の解決、可用性判定、利用上限の待機と正常終了の判定は、`atk wi process-loop`と共通の`agent-toolkit/agent_toolkit/_atk/orchestrator.py`が持つ。
可用性判定を軽量工程のモデル・effort `low`・軽量起動で行う理由は[design-process-loop.md](design-process-loop.md)にある。

`atk run-skill`が指定スキルを実行する子セッションは非対話起動（Claudeは`claude -p`、Codexは`codex exec`）とする。
定期実行には操作する端末が無く、対話型のCLIは入力を待ったまま止まるためである。
Claudeでは最初の入力を`/goal`へ渡し、目標評価で報告用UWIの保存までターンを続けさせる。
Codexの非対話起動は`/goal`の行を通常の入力として残し目標として扱わないため（Codex 0.160.1で観測）、`atk-auto`要素の目的文だけを渡し、セッションはモデルの最終応答で終わる。
目的文は起動したセッション自身が担当するスキル名と認識したモードの工程の完遂を示し、起動元が子の終了後に行う検収と確定を含まないことを短く併記する。
スキル全体の完遂だけを示した目的文では、目標の評価が起動元の同期検収まで子の未達と解釈し、子の担当工程を終えた後の終了を繰り返し遮断した（2026年11月5日のbatch実行の観測）。
自由記述、scope、旧scope、実行ID、報告先、batch manifestは`AGENT_TOOLKIT_SESSION_START_INPUT`でSessionStartへ渡す。
機械生成の起動入力はユーザーの明示指示と区別し、委譲先には継承しない。
Claudeの出力上限を超える入力はmanaged-tempへ原本を保存して参照を配送し、短い入力は本文へ含める。
終了契約はanswer-transitions、report-completion、batchの既存節が所有する。
完遂と具体的な外部前提不足による未完了終了を区別し、回復可能な残工程がある間は続行する。
2026年10月30〜31日の実ホスト受入では、成功だけを終了条件にすると失敗報告後も停止が遮断された。
この終了分岐を保持しつつ、恒久的な細則はスキルへ集約し、目的文の反復評価と二重保守を抑える。
未完了で終えた実行は報告保存の同期確認だけを満たし、振り返りbatchの分析成功の検収（`run_skill_batch.py`）と`finish`の保留は従来どおりである。

子セッションへはprocess-loop用の環境印（`AGENT_TOOLKIT_PROCESS_LOOP_SESSION`と`AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID`）を渡さない。
印はStopで`atk agents-exit-session`の実行記録を求めるhookを有効にし、そのコマンドは対話型のCLI本体だけを停止対象とするため、最終応答で終わる非対話セッションが時間上限まで終われなくなる。
起動元がprocess-loopの子でも印を除く。

`atk run-skill`の指定スキルの実行中に利用上限へ達した場合は、ClaudeとCodexのどちらも解除後に同じ会話を再開する。
Claudeは常に構造化出力から利用枠情報を取得し、`--resume`で継続する。
Codexは常に`--json`の`thread.started`からthread IDを取得し、ロールアウト記録の上限判定に従って`codex exec resume`で継続する。
進捗表示の有無と再開に必要な出力の取得を分け、解除待ちの時間は`--timeout`から除く。
解除予定が不明な場合は300秒後に再確認し、待機の回数と総時間を打ち切らない。
継続指示は中断前の記録を確認し、完了済み操作を繰り返さず未完了部分を続けることを伝える。
候補判定時のCodex上限は次の候補を先に試し、全候補失敗かつ上限失敗がある場合だけ最も早い解除予定まで待って先頭から判定し直す。
上限判定は共通層、同じ会話の再開はrun-skill、候補判定はorchestratorが所有する。

報告用UWIの本文作成と内容の十分性の判断は、セッション内の`agent-toolkit:report-completion`が所有する。
CLIは子の正常終了に加え、同じ実行IDと`source=run-skill`・`scope=<スキル名>`・`run_skill_report=true`を持つUWI本文のcommitを確かめる。
upstreamがある場合は実remoteへのpush到達も確かめ、全て成立した場合だけ終了コード0を返す。
保存未達は終了コード1とし、標準エラーへ診断、ログの所在と次の操作を書く。
124は時間上限の到達だけに用いる。
回復時はログの実行IDと対応UWIを確認し、同じ会話を再開してreport-completionで保存する。
保存済みならWI保存先のcommit・pushを完了し、次回の実行を待つ。

過去の実行が投入したUWIは、次回の同じスキルの実行が`source`（`run-skill`）と`scope`（スキル名）で識別して扱う。
手順は`agent-toolkit/skills/user-confirmation-and-report/references/answer-transitions.md`「`atk run-skill`の過去の実行のUWI」にある。
process-wiは回答済みUWIの取得へ`--source=!run-skill`を付け、`calculate_readiness`もこれらのUWIをreadyへ含めない。
含めると、process-wiの選定が除くUWIのためにprocess-loopが子セッションの起動を繰り返す。
識別には既存の`source`と`scope`を使い、新しい状態や保存先は加えない。

実行ごとのログは状態ディレクトリの`run-skill/`へ置き、各実行の開始時に最終更新から30日を超えたものを削除する。
数日に1回の起動で同じ組の直近約10回分を調べられる長さとした。
多重起動は同じディレクトリの`locks/`のファイルロックで防ぎ、対象リポジトリかスキルが異なる実行は並行して起動できる。

標準エラーが端末の場合は、ログ作成直後に絶対パス、取得直後に子の識別子、受信ごとにassistantの発言とtool呼出名を標準エラーへ表示する。
標準出力だけをリダイレクトした起動でも表示する。
Claudeは`--output-format stream-json --verbose`を使い、起動前のUUIDを表示する。
Codexは`--json`を使い、`thread.started`の`thread_id`を表示する（[非対話実行の公式仕様](https://learn.chatgpt.com/docs/non-interactive-mode#make-output-machine-readable)）。
思考と生JSON全体は表示せず、表示した進捗と両streamの生診断を同じログへ残す。
非TTYの子CLIの出力形式、成功時のstdout一行、失敗時のstderrと終了コード、timeoutと出力待機上限は従来どおりである。

進捗の変換と表示は`run_skill_progress.py`が持つ。
`atk agents logs`の`logs_export.py`が使う`session_record_format.SessionEvent`とClaudeの変換を再利用し、Codex exec固有のitemイベントだけを同じ型へ変換する。
保存済みロールアウトを読むlogsと実行中のstdoutを読むrun-skillは入力形式と所有する資源が異なるため、ログファイルの追跡機構は共通化しない。

子の終了前の表示と端末条件は、[run_skill_test.py](../../agent-toolkit/agent_toolkit/_atk/run_skill_test.py)の`test_tty_progress_arrives_before_child_exits`が、両候補・別スキル・正常終了・異常終了・timeoutを入力に検証する。

却下した案は3つある。
cronからAWIを定期投入し`atk wi process-loop`に処理させる案は、ユーザーが単発実行コマンドを選んだため採らない。
対話型の`claude`をcronから起動する案は、端末が無いため採らない。
Claude Codeのクラウド定期実行は社内のサーバーへ届かない見込みのため採らない（届くかは確かめていない）。

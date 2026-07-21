# 06-monitoring.md

background subagent（name指定の有無を問わない）起動中は、進行停滞検知のため定期的に状況確認を実施する。

## 適用対象

- Agentツールで`run_in_background=true`で起動したサブエージェント全般。
  `name`指定の有無を問わない。`name`未指定のbackground起動、
  例えば`plan-file-creator`の委譲起動も対象に含める
- 複数起動時は全teammate横断で確認する
- 単発起動（1件のみ）の場合も対象に含める

## 確認内容

以下を1回の確認で実施する。

- `git log --oneline -10`と`git status --short`でHEAD・作業ツリー差分を観測
- `TaskList`で進行中タスク・依存タスクを確認
- 成果物ファイル（計画ファイル等）の`mtime`を観測し、更新有無を確認する
 （対象サブエージェントのトランスクリプトファイルパスを別途把握している場合は同ファイルの`mtime`も併せて観測する）
- `name`未指定起動はトランスクリプト喪失で完了通知を返せず異常終了する場合があるため、
  `git log`・`TaskList`・成果物ファイルのmtimeのいずれにも変化が無くても成果物ファイルのmtime観測を省略しない
- 前回観測時点との差分（HEAD不変・作業ツリー変化なし・TaskList状態変化なし・mtime変化なし）を判定

## 停滞検知時の対応

- HEAD不変かつ作業ツリー変化なしかつTaskList進捗なしを連続で観測した場合、対象teammateへ`SendMessage`で作業継続を催促する
- 催促後も進捗なしなら、`agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節の巻き取り規定（メイン側で検証・コミット、または縮減した新規サブエージェント起動）へ移行する
- `idle_notification(available)`受信は本規範の停滞検知とは独立に扱う。完了判定は`agent-toolkit/references/plan-impl/caller-reception.md`の手順（`git log`・作業ツリー等の観測事実優先）に従う

## 仕組みの選択

Claude Code環境では以下のいずれかで実装する。

- `ScheduleWakeup`: `/loop dynamic mode`下で使用する。現セッション内で指定間隔（10分程度が実用値）でプロンプトを発火する
- `CronCreate`: 定期scheduled agentとして別セッションで実行する（現セッションと独立）
- Bash background loop: `while true; do <check>; sleep <interval>; done`を`run_in_background`で起動し、`Monitor`で観察する

いずれの仕組みでも、background subagent（name指定の有無を問わない）の
全完了後（またはexit-session到達時）に停止・登録解除する。

## Bash background loop運用

`Bash background loop`方式で長時間待機ループを起動する場合、次を遵守する。

- 起動前に、待機条件（filter値・条件式）が実データへ1回以上ヒットすることを
  foreground単発実行で確認する
- 起動後、対象処理の想定所要時間（一般的な目安。例: CI通過確認なら10分程度）が
  経過した時点で、`BashOutput`または進捗ファイルの`Read`で必ず1回状態を確認する
- 想定所要時間の2倍以上が経過しても完了マーカーが出ない場合、単純に待ち続けず、
  原因（filter誤用・プロセス消失・条件式ミス等）を疑い能動的な状態調査
 （対象コマンドの単発再実行等）を実施する

## 撤去手順

本規範が不要になった場合、以下の手順で撤去する。

- 本ファイル`agent-toolkit/rules/06-monitoring.md`を削除する
- `agent-toolkit/rules/`配下から本ファイルへの参照（存在すれば）を除去する
- `ScheduleWakeup`は`stop: true`で解除する。`CronCreate`は`CronDelete`で削除する。Bash background loopは`TaskStop`で停止する

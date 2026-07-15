# 06-monitoring.md

named background subagent起動中は、進行停滞検知のため定期的に状況確認を実施する。

## 適用対象

- Agentツールで`run_in_background=true`かつ`name`指定で起動したサブエージェント
- 複数起動時は全teammate横断で確認する
- 単発起動（1件のみ）の場合も対象に含める

## 確認内容

以下を1回の確認で実施する。

- `git log --oneline -10`と`git status --short`でHEAD・作業ツリー差分を観測
- `TaskList`で進行中タスク・依存タスクを確認
- 前回観測時点との差分（HEAD不変・作業ツリー変化なし・TaskList状態変化なし）を判定

## 停滞検知時の対応

- HEAD不変かつ作業ツリー変化なしかつTaskList進捗なしを連続で観測した場合、対象teammateへ`SendMessage`で作業継続を催促する
- 催促後も進捗なしなら、`agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節の巻き取り規定（メイン側で検証・コミット、または縮減した新規サブエージェント起動）へ移行する
- `idle_notification(available)`受信は本規範の停滞検知とは独立に扱う。完了判定は`agent-toolkit/references/plan-impl/caller-reception.md`の手順（`git log`・作業ツリー等の観測事実優先）に従う

## 仕組みの選択

Claude Code環境では以下のいずれかで実装する。

- `ScheduleWakeup`: `/loop dynamic mode`下で使用する。現セッション内で指定間隔（10分程度が実用値）でプロンプトを発火する
- `CronCreate`: 定期scheduled agentとして別セッションで実行する（現セッションと独立）
- Bash background loop: `while true; do <check>; sleep <interval>; done`を`run_in_background`で起動し、`Monitor`で観察する

いずれの仕組みでも、named background subagentの全完了後（またはexit-session到達時）に停止・登録解除する。

## 撤去手順

本規範が不要になった場合、以下の手順で撤去する。

- 本ファイル`agent-toolkit/rules/06-monitoring.md`を削除する
- `agent-toolkit/rules/`配下から本ファイルへの参照（存在すれば）を除去する
- `ScheduleWakeup`は`stop: true`で解除する。`CronCreate`は`CronDelete`で削除する。Bash background loopは`TaskStop`で停止する

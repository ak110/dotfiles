# 引き継ぎ起動とCodexの再開の設計記録

[設計記録の索引](design.md)から参照する、引き継ぎ起動とCodexの再開の記録である。

起動後の可用性失敗で、同じ会話の待機・接続先移行から回復できず終了した場合は、終了時の結果の`next_action`が前任IDを指定する引き継ぎ起動を案内する。
委譲元が`start(handoff_from=<前任session_id>)`を1回呼び、終了した前任の起動区分・工程別model_type・cwd・label・起動本文を引き継ぐ。
可用性失敗では除外記録の保持期間によらず前任候補を外し、設定順の後続候補を選ぶ。
作業自体の失敗には既存の継続・巻き取りの案内を使う。
後任は引き継ぎ記録を最初に読む指示、会話の流れ、委譲元の追送と各turnの最終応答全文、詳細照会手段を受け取る。
会話の流れと詳細の取得は既存のreview-sessionの記録抽出を使い、CLIを持たない後任へも読めるファイルを渡す。
Antigravityの前任は会話の流れに代えて`atk agents logs`の出力を使う。
後任のsession IDを新しい待機対象とし、完了済み操作と成果物を現物で確かめて残工程を続ける。
引き継ぎ起動は全engine・modeと起動後の全turnを対象にし、実行中の前任は終了を待つか`kill`する案内で返す。
サーバーが後任を自動起動する案は採らない。
前任の子sessionの所有と不要になった作業を引き継ぐ判断は委譲元にあり、後任IDへの対応付けも委譲元が行うためである。
起動時の候補切替、同じ会話の接続先移行、Claudeの解除待ち、終了後の明示的な引き継ぎをそれぞれの境界で扱う。

Codexの利用上限判定は、rolloutの`task_complete.error.codex_error_info`が`usage_limit_exceeded`であることを使う。
解除予定は直前の`token_count.rate_limits`から取得する。
`atk run-skill`は解除後に同じ会話を`codex exec resume`で続ける。
候補判定は次候補を優先し、全候補失敗かつ利用上限がある場合だけ解除を待って先頭から判定し直す。
process-loopは自身が起動した子の上限停止を確認し、後続turnが無い場合にその子を終了して次の反復へ進める。
これらの判定は`agent-toolkit/agent_toolkit/_common/codex_usage_limit.py`が所有し、再開は各起動元が担う。
公開CLIでの同じ会話の再開は`agent-toolkit/agent_toolkit/_atk/run_skill_test.py`、候補判定は同ディレクトリの`orchestrator_test.py`で確認できる。
所有する子の終了と後続turnの保護は、同ディレクトリの`wi/process_loop_session_lifecycle_test.py`で確認できる。
いずれも偽のCLI・rollout・時計を用いる検証であり、実上限からの再開の実機観測とは区別する。

ホスト側の失敗時の終了とStop hookの条件は[turn.rs](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/core/src/session/turn.rs)にある。
goalの上限停止は[goalのextension](https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/ext/goal/src/extension.rs)で確認できる。
過去の観測条件と再検証手段は[audit-records-usage-limit.md](audit-records-usage-limit.md)の「docs/development/design-agents-runtime-handoff-resume.md：Codexの利用上限記録と再開：2026年10月14日」にある。
起草時の元session記録はリポジトリ外の私有記録であり、設計判断の確認には上記の公開ソースとリポジトリ内の実装・テストを使う。

最上位のCodex TUIの解除後の自動再開は実装せず、`/goal resume`か発話で続ける。
利用上限で失敗したturnはStop hookを呼ばずに終わる。
`/goal`の状態は`usage_limited`となり、自動継続を停止する。
検討した案は、agents_serverが起動元threadのrolloutを監視し、解除予定時刻の後に`codex queue --thread <thread-id>`で再開の指示を1回送る方式である。
Codex CLI 0.164.0の実機観測では、共有daemonへ接続したidleのTUIへ`codex queue`で積んだ指示は直ちにturnを始め、TUIの画面に表示された。
一方で次の2点から、監視側はTUIを閉じた会話への送信を避けられない。

- TUIが使うagents_serverのMCPプロセスはTUIの子ではなくdaemonの子として起動し、TUIの終了後も残る。
  daemonは最後の購読者が離れても、購読者とthreadの活動が無い状態が30分続くまでthreadを読込済みに保つ（[app-serverの公式資料](https://learn.chatgpt.com/docs/app-server)「Unsubscribe from a loaded thread」）。
  TUIの終了後に積んだ指示は、画面の無いままdaemonでturnを始めて完了した
- そのMCPプロセスの環境に`CODEX_THREAD_ID`は無く、監視側は起動元のthreadを特定できない。
  app-serverの公開要求にもthreadの購読者の有無を返すものが無く、TUIの終了を判別できない

閉じた会話を無人で再開すると、ユーザーが画面で確認できない作業が承認設定のまま進む。
このため送信の安全条件（TUIが開いている間だけ送る）を満たす手段が無く、監視を実装しない。
共有daemonへ接続しない起動（`-c`などを指定したTUI）は`codex queue`の宛先にもならない。
観測の条件と再検証手段は[audit-records-usage-limit.md](audit-records-usage-limit.md)の「docs/development/design-agents-runtime-handoff-resume.md：最上位のCodex TUIの解除後の再開：2026年10月24日」にある。
Codexがthreadの購読者を判別できる公開手段、またはMCPプロセスへ起動元のthreadを渡す手段を提供した版では、同じ方式を再評価できる。

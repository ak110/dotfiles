# フックの責務・通知・セッション状態の設計記録

本書は[設計記録の索引](design.md)から主題別に分割した記録であり、機構の目的、構造の理由、知識境界と却下した代替案を保持する。
実行時に適用する規範は、各節が参照する現行のルールファイルとスキルが定める。

- [フックの責務境界と判定の集約](design-hooks-responsibility.md)
- [Bashコマンドの解析とheredoc遮断](design-hooks-shell-parsing.md)
- [遮断・警告フックの存廃判定](design-hooks-eligibility.md)
- [Stopの共通ハンドラー](design-hooks-stop-handler.md)
- [Stopの通知と入力待ち判定](design-hooks-stop-notices.md)
- [終了工程の証拠](design-hooks-termination-evidence.md)
- [通知の整形と画面への写し](design-hooks-notices.md)
- [複数ホストの入力変換と出力契約](design-hooks-host-adapters.md)
- [ユーザー発話への注入と注記](design-hooks-user-prompt.md)
- [進捗のセルフチェック通知](design-hooks-progress.md)
- [commitと委譲手段の選択の支援](design-hooks-pretooluse.md)
- [読込宣言の解析と資料解決](design-hooks-read-declarations.md)
- [本文の取得観測と確認前の拒否](design-hooks-read-acquisition.md)
- [コマンド時間と退避出力の観測](design-hooks-posttooluse.md)
- [会話圧縮と利用上限後の再開予約](design-hooks-compaction-resume.md)
- [定期再確認の装着と回収](design-hooks-periodic-recheck.md)
- [ユーザーへの本文送達](design-hooks-send-to-user.md)
- [フックごとのホスト対応範囲](design-hooks-host-support.md)
- [セッション状態と記録解決](design-hooks-session-state.md)
- [通知仕様の追随と読込宣言の回復](design-hooks-declaration-recovery.md)
- [効率化の仕組みの棚卸し（2026年11月）](design-hooks-inventory-202611.md)

# 委譲先の稼働状態・起動診断・通知の設計記録

本書は[設計記録の索引](design.md)から主題別に分割した記録であり、機構の目的、構造の理由、知識境界と却下した代替案を保持する。
実行時に適用する規範は、各節が参照する現行のルールファイルとスキルが定める。

- [状態ファイルの構造と寿命](design-agents-runtime-state.md)
- [補助保存と表示の寿命](design-agents-runtime-persistence.md)
- [終了時の結果の回収と待機ロック](design-agents-runtime-results.md)
- [statuslineの表示](design-agents-runtime-statusline.md)
- [接続選択とAPI残高不足](design-agents-runtime-connections.md)
- [API失敗と利用上限の待機](design-agents-runtime-usage-wait.md)
- [引き継ぎ起動とCodexの再開](design-agents-runtime-handoff-resume.md)
- [起動失敗の観測と無人起動](design-agents-runtime-preflight.md)
- [agents_serverの上り通知](design-agents-runtime-notifications.md)

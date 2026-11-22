# process-loopのモジュール構成の設計記録

[設計記録の索引](design.md)

## process-loopのモジュール構成

モジュールの構成は[巡回の実装](../../agent-toolkit/agent_toolkit/_atk/wi/process_loop.py)と対応する[テスト](../../agent-toolkit/agent_toolkit/_atk/wi/process_loop_test.py)で確認する。
単発は対象作業ツリーの解決と取得・投入結果を報告して終了し、process-wiの1回の実行やDependabot監査を起動しない。

単発用に収集処理を複製する案は、本文・キー・取得範囲の一致を別々に保守するため採らない。
呼び出す時機は各環境が選び、`docs/guide/claude-code-workflows.md`「推奨ワークフロー」はその境界を示す。

CI失敗の本文には実行日時、対象commitのSHA、現在のブランチHEADとその比較結果を含める。
古い失敗だけが残る場合は、HEADで実行が作成されない理由と、失敗原因が現行HEADにも残るかを調査する。
原因が残らない場合は確認結果の記録で完了でき、追加変更や後続実行を求めない完成条件を渡す。

収集側と待機側は[共通のログ取得案内](../../agent-toolkit/agent_toolkit/_git/forge_cli.py)を使い、CLIの対話画面を必要とする手順の分散を防ぐ。

GitLabのリポジトリ識別子とAPI接続先は別に扱う。
SSH接続先はglabのホスト別設定を選ぶキーとし、API接続先はその設定と公開環境指定で解決する。
API側を明示上書きする場合はHTTP(S) URLをポート込みでrepositoryへ指定し、baseline作成・待機・再開で同じ指定を使う。

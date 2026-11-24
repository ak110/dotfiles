# dotfilesの画面とブラウザー

## 画面とブラウザー

画面の確認は、目的に合う既存のテストとfixtureを選び、`make test-browser`の準備・起動の条件で実行する。
期待値・セレクター・起動の実装とテスト名の一覧は、テスト側の定義を基準にする。

| 目的 | データ・代表操作・結果 | 既存テスト（`agent-toolkit/agent_toolkit/_atk/serve/`配下） |
| --- | --- | --- |
| WIの登録・回答・編集・状態変更と外部更新時の入力保持 | 隔離WIを開き本文を編集・保存し、表示と保存本文・状態を確かめる。画面制御だけの代用と製品更新・実保存の保証を区別する | `browser_wi_mutation_test.py`と`browser_support_test.py`、共有fixtureは`agent-toolkit/agent_toolkit/_testing/serve_browser_wi_support.py` |
| 計画の選択・本文・図・変更反映 | 一時計画を一覧から選択し、本文更新後の描画を確認する | `browser_plans_test.py` |
| 保存済み会話の一覧・検索・選択・発話とツール入力 | 両engineのfixtureを使い、会話を検索・選択して内容を確認する | `browser_sessions_test.py` |

- `atk serve`のブラウザーUI、ブラウザー経由でアクセスされるサーバー側の処理、静的ファイル、実ブラウザーテストを変えたら、`make test-browser`で動作を確かめる。
  代表操作は選んだテストの入力とassertionへ対応付け、表示だけでなくテストが観測する最終の成果を確かめる。
- `make setup-browser`（Chromiumとシステム依存パッケージ）と`make setup-pwsh`（Ubuntu/Debian向けPowerShell）は、ユーザーが自分の端末で実行する初期セットアップである。
  エージェントは`make test-browser`の前提の不足を検知したら、システム依存パッケージの導入をユーザーに任せ、不足する前提と未実施の検証項目を報告する。
- 一時データで`atk serve`を起動する時（手動の描画を含む）は、保存先を`AGENT_TOOLKIT_PRIVATE_NOTES=<一時領域の絶対パス>`のように設定する。
  起動の前に、本番の起動と同じCLIと環境変数で`atk config get private_notes`を実行し、出力の保存先が一時領域のパスと一致することを確かめる。
  一致しない場合と解決に失敗した場合は起動をやめ、設定を見直して一致を確かめ直す（変数名を誤ると、通常の保存先である実データへ接続するため）。
  手動で起動したプロセスは起動時の識別子で終了を確かめ、参照を終えた一時データを回収する。
- ブラウザーテストの失敗出力にある「ブラウザー失敗証拠」の保存先から`failure.txt`と`*-trace.zip`を取得し、`uv run --frozen playwright show-trace <trace.zipの絶対パス>`で標準のTrace Viewerを開く。
  CIではブラウザー失敗証拠のartifactから取得し、ローカルの証拠はmanaged-tempの保持期限内に確かめる。
- 描画結果の確認には、ブラウザー操作ツールのほかに、`pyproject.toml`の依存にあるPython版Playwright（`uv run --frozen python`経由）も使える。
  導入済みのブラウザーバイナリは`~/.cache/ms-playwright`で確かめる。

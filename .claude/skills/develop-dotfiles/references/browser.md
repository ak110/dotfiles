# dotfilesの画面とブラウザー

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 画面とブラウザー

準備済みの環境で、目的に合う既存試験とfixtureを選ぶ。
期待値・セレクター・起動実装は試験側を正本とし、全試験名を別管理しない。

| 目的 | データ・代表操作・結果 | 既存試験の入口（`agent-toolkit/agent_toolkit/_atk/serve/`配下） |
| --- | --- | --- |
| WIの登録・回答・編集・状態変更と外部更新時の入力保持 | 隔離WIを開き本文を編集・保存し、表示と保存本文・状態を確かめる。画面制御だけの代用と製品更新・実保存の保証を区別する | `browser_wi_mutation_test.py`と`browser_support_test.py`、共有fixtureは`agent-toolkit/agent_toolkit/_testing/serve_browser_wi_support.py` |
| 計画の選択・本文・図・変更反映 | 一時計画を一覧から選択し、本文更新後の描画を確認する | `browser_plans_test.py` |
| 保存済み会話の一覧・検索・選択・発話とツール入力 | 両engineのfixtureを使い、会話を検索・選択して内容を確認する | `browser_sessions_test.py` |

既存の`make test-browser`の準備・起動条件を再利用する。
代表操作は選んだ試験の入力とassertionへ対応付ける。
手動描画では下記の保存先確認を先に行い、通常データから分離した一時データだけを操作する。
実行後は表示だけでなく対象試験が観測する最終成果を確かめ、失敗証拠を保持する。
手動で起動した所有プロセスは起動時の識別子で終了を確認し、参照を終えた一時データを回収する。

- ブラウザー試験の失敗出力にある「ブラウザー失敗証拠」の保存先から`failure.txt`と`*-trace.zip`を取得する。
  `uv run --frozen playwright show-trace <trace.zipの絶対パス>`で標準Trace Viewerを開く。
  CIではブラウザー失敗証拠artifactから取得する。
  ローカルの証拠はmanaged-tempの保持期限内に確認する。

- `make setup-browser`（Chromiumとシステム依存パッケージ）および`make setup-pwsh`（Ubuntu/Debian向けPowerShell）は、ユーザーが自身の端末で直接実行する初期セットアップ用のコマンドである。
  エージェントが`make test-browser`の前提環境の不足を検知した場合は、システム依存パッケージのインストールを勝手に行わず、不足している前提条件と未実施の検証項目をユーザーに報告する。
- `atk serve`のブラウザーUI、ブラウザー経由でアクセスされるサーバー側処理、静的ファイル、または実ブラウザーテストを変更した場合は、`make test-browser`を実行して動作を確認する。
- レビュー目的で一時データを使って`atk serve`を起動する際は、保存先環境変数を`AGENT_TOOLKIT_PRIVATE_NOTES=<一時領域の絶対パス>`のように設定する。
  起動する前に、本番起動と同じCLIおよび同じ環境変数を用いて`atk config get private_notes`を実行し、出力された保存先が一時領域のパスと一致していることを確認する。
  パスが一致しない場合や解決に失敗した場合は起動を取りやめ、環境変数の設定を見直したうえで再度一致を確認する（誤った変数名を指定すると、既定の通常保存先である実データに接続してしまうためである）。
- 画面の描画結果を実際に確認する際は、ブラウザー操作ツールのほかに、`pyproject.toml`の依存関係に含まれるPython版Playwright（`uv run --frozen python`経由で使用可能）も利用できる。
  インストール済みのブラウザーバイナリは`~/.cache/ms-playwright`で確認できる。

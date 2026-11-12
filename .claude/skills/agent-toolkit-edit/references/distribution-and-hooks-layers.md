# agent-toolkitの実装と配布の規約：agent_toolkitパッケージの配置と層

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## agent_toolkitパッケージの配置と層

- `agent-toolkit/agent_toolkit/`の直下には、配布物の外部から絶対パスで解決される公開スクリプトのみを配置し、先頭にアンダースコア（`_`）を付けない命名にする。
  対象は`hook.py`、`atk.py`、`agents_server_mcp.py`、`wait_ci.py`である。
- リモートホスト上で実行する`atk_serve_plans_remote_helper.py`と`atk_serve_sessions_remote_helper.py`は、`agent-toolkit/scripts/`直下の公開スクリプトとして配置する。
  これらは同じチェックアウト環境の`agent_toolkit`を`sys.path`経由でインポートする。
  また、SSH接続先には標準ライブラリおよび`platformdirs`と`watchdog`のみが用意されているため、両ヘルパーがインポートするモジュールもこれらだけに依存させる。
- 実装モジュールは、`_common`、`_git`、`_plan`、`_atk`、`_agents_server`、`_hooks`の各サブパッケージに配置する。
  この順序が依存関係の階層を表しており、インポートは後続の層から先行する層への方向、または同じ層の内部に限定する。
  新規モジュールはそれを読み込む主体が属するサブパッケージに配置し、フック以外からも利用するモジュールは`_hooks`以外の適切な層に配置する。
- 直下の公開スクリプトおよび`agent-toolkit/skills/*/scripts/`配下のスクリプトは、起動スクリプトとしてすべての層をインポートできる。
  ただし、`_hooks`をインポートできるのは`hook.py`のみとする。
- テスト専用の共有ヘルパーは、依存階層の外側にある`_testing`に配置する。
  このモジュールをインポートできるのは、`*_test.py`、`conftest.py`、および`_testing`配下のコードのみとする。
- サブパッケージ内では絶対インポートを用いる。
  `scripts/check_script_imports.py`は相対インポートを解析対象外としているため、相対インポートを使うと階層チェックの対象から漏れてしまう。
  このスクリプトにより、階層違反や`hook.py`以外からの`_hooks`のインポート、許可されていない箇所からの`_testing`のインポートを検出して報告する。
- モジュール名には、所属を表す接頭辞を除いて、組み込みの名前や標準ライブラリのトップレベル名と重複しない名前を付ける。
- テストコードの配置は、`develop-dotfiles`の`references/testing.md`「テスト配置」の定めに従う。

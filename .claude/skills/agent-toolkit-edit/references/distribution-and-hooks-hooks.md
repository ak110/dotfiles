# agent-toolkitの実装と配布の規約：フック実装の配置先（個人フックと配布物）

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| hookの実装や登録を新設・変更する前 | `claude-hooks.md` |
| hookの遮断・警告の判定を新設・変更する前 | `claude-hooks-block-warn.md` |
| hookの出力フィールドを選択・変更する前 | `claude-hooks-output.md` |
| hook通知の本文（`fix`・次の操作を含む）またはformatterを新設・変更する前 | `claude-hooks-messages.md` |

## フック実装の配置先（個人フックと配布物）

PreToolUseをはじめとするフックは、配布先の構成や個人の命名規則などdotfiles固有の運用を前提とする場合は個人フックに配置し、そうした前提を持たない汎用的な機能であればプラグイン側に配置する。
類似のチェックがすでに存在する側に統合することを優先する（努力目標）。

- `pytools/claude_hook/pretooluse.py`（個人フック）: chezmoiを通じて自身のアカウントの`~/.claude/settings.json`にのみマージされる。
  新規のツール名を対象に含める場合は、`share/claude_settings_json_managed.posix.json`および`.win32.json`の`matcher`設定を確認する。
- `agent-toolkit/`（プラグイン）: `.claude-plugin/marketplace.json`を通じて他のユーザーにも広く配布されるため、変更を加える際は`version-bump-execution.md`「バージョン更新」の規定に従う。
- agent-toolkitのCLI、フック、MCPサーバーは、`uv run --no-config --no-project --python 3.15 python <plugin root>/scripts/launch.py <対象>`というコマンドで起動する。
  `launch.py`は標準ライブラリのみを用いてプラグインルートとデータ領域を解決し、準備済みの共有環境のPythonで対象を起動する。
  依存の準備には`uv run --no-config --no-project --python 3.15 python <plugin root>/scripts/launch.py --prepare agent_toolkit/atk.py`を実行する。

  `--prepare`の後ろのtargetは配布root内に存在する起動対象を確認する必須引数であり、共有環境の選択には影響しない。
  依存に作用するpyproject設定、`uv.lock`、選択したPythonの実行ファイルパス、OS・アーキテクチャの組み合わせが未準備の場合に実行する。
  同じ組み合わせなら別の配布rootやworktreeでも準備済み環境を共有する。
  別worktreeを指定する場合の`<plugin root>`は、そのworktree内の`agent-toolkit`の絶対パスに置き換える。
  rootは`launch.py`の配置または`--project`から解決され、cwdの変更だけでは切り替わらない。
- 登録済みの`skills/*/scripts/`配下のスクリプトは、`atk run-script`を用いて同じ環境内から実行する。
  SSH接続先で実行する`atk_serve_*_remote_helper.py`のみは、独立したPEP 723スクリプトとして起動するため、PEP 723形式の宣言を維持する。

- `agent-toolkit/pyproject.toml`の`dependencies`を変更した場合は、同じプロジェクト配下の`uv.lock`も併せて更新する。
- `hooks.json`や`share/claude_settings_json_managed.*.json`から参照されているスクリプトを改名・移動・削除する場合は、後方互換性を保つためのスクリプトを残してバージョン管理に含める。
  稼働中のセッションが古いパスを参照し続けるためである（詳細な規約は`agent-basics:writing-standards`のフック実装に関する規約を参照する）。
  古いスクリプトを完全に撤去できるのは、新しいエントリーポイントを含むバージョンを配布した後のバージョン更新以降であり、かつ古い定義を読み込んだセッションがすべて終了したことを確認できた場合に限る。
  なお、配布先環境でインストールエラーとなるような配置は新しい配布物に残さず、旧バージョンの実体を保持することや更新時の移行処理によって旧方式の呼び出しを維持する。
- 同一のイベントに対してフックを追加する場合は、既存のエントリーポイントへ処理を統合することを推奨する（登録エントリを増やすとツール呼び出しごとのプロセス起動回数が増えるため）。
  入力契約の差異などにより統合が難しい場合は、理由を明記した上でエントリを分けて定義してよい。
  マッチャーの条件が互いに重複しない登録であれば、この方針を満たしているとみなす。

agent-toolkit配下のファイルを編集する際は、`pytools/claude_hook/pretooluse.py`がdotfiles固有の名前の混入を監視して警告する。
個人プロジェクト名の固定リストは同スクリプト内に定義されており、OSSとして公開しているプロジェクト名については警告（warning）レベルに留める。
スキル名、pytoolsのコマンド名、`pytools/_internal/`配下の複合モジュール名、ならびに`scripts/`や`libexec/`配下のスクリプト名は、実行時にディレクトリから動的に取得される。
外部CLIの参照については、`_EXTERNAL_CLI_ALLOWED`に登録されている識別子に限り、`command -v`などによる存在確認を行った上で使用を許可する。

## agents_serverのツール呼び出しを判定するhook

`agents_server`は、指定された`engine`に応じて適切なバックエンドを選択する。
ユーザーの承認、対話入力、認証の更新、一覧の取得操作などは公開せず、実行中のターンの中断操作のみをセッション単位の`kill`として公開する。

- PreToolUseフックでは、`send_message`および`kill`における保存済みセッションの妥当性と、`<役割名>.subagent.md`で定義された実行命令を伴う起動であるかを確認する。
- `start`の`delegate`、`explore`、`write`モードに対して、引用枠の外側で`<役割名>.subagent.md`の手順に基づく実行命令を直接渡した場合は、タスク起動形式に修正するよう処理を遮断する。
  また、`Agent`や`Task`の実行命令においては、1行目に記載された正式な命令および事前に宣言された入力以外の行が含まれている場合に処理を遮断する。
- 文書の読解、引用、比較対象としてのパス参照や、引用内に記載された実行命令の例、参照のみでは用途が確定できないケースについてはフックを通過させ、会話の文脈を推測した過剰な遮断や警告は行わない。
- `start`における入力パラメーターの検証（`mode`ごとの必須項目の不足や不正な混在の検出）や、`send_message`および`kill`の入力形式の検証（`prompt`、`instruction`、`extra_params`、`session_id`の指定不備）は、実行基盤側のバリデーションに委ねる。
  エラーの原因と次に取るべき操作は、ツールのエラー応答を通じて伝達する。
  フック側にツールと同じ受理条件を持たせると、ツール側の入力形式が拡張された際に正当な入力に対して誤った警告を発してしまうためである。
  実行権限の値はそのままツールへ渡す。
  なお、`wait`は新しいターンを開始しないためチェック対象外とする。
- PostToolUseフックでは、成功した`start`呼び出し（旧名称で記録された開始処理を含むすべての`mode`が対象）のカレントワーキングディレクトリ（cwd）と、`wait`、`send_message`、`kill`実行後のセッション状態を記録する。

## 複数hook共存時の識別子

`atk-auto`タグの`source`属性には、agent-toolkit由来のメッセージであれば接頭辞を付けない生成元名を指定し、それ以外の生成元であれば`<所有者>/<生成元>`という形式を指定する。
XMLタグの境界や属性の記述規約については、`agent-basics:writing-standards/references/claude-hooks-messages.md`「エージェント宛てメッセージの標識」を参照する。

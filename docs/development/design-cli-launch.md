# 公開出力とworktreeの選択の設計記録

[設計記録の索引](design.md)から参照する、公開出力とworktreeの選択の記録である。

## 公開出力から導出できる項目の撤去

`atk agents list`と`atk agents show`、およびMCPの`list`は停滞の判定を表す`stalled`を返していた。
`stalled`の判定は同じ応答が返す`seconds_since_activity`と定数の閾値を比べるだけで再現できるため、公開出力から撤去した。
MCPの`list`は通常時の3項目の公開契約を保つため、`stalled`を`seconds_since_activity`へ置き換えた。
Claudeの合成assistantメッセージ・`api_retry`とCodexの`willRetry: true`の`error`通知による再試行中も、一次情報から算出した`api_error`を返す。
Claudeの`retry_at`は次の再試行予定時刻を、利用上限の`limit_type`・`resets_at`は種類と解除予定時刻を示し、list・show・waitで同じ射影を使う。

導出できる項目を公開出力へ置くと、同じ判定を定める箇所が生成側と呼び出し側の2つに分かれる。
閾値を変えた時点で両者が食い違い、どちらが現在の判定かを呼び出し元が確定できない。
公開出力は一次情報だけで構成し、判定は呼び出し元が行う。

公開項目は消費側の用途から選ぶ。
CLI一覧は識別・活動・結果保留を、詳細は実行条件と本文を返す。
通常の活動は`seconds_since_activity`にまとめ、絶対活動時刻はMCPの`show(verbose=True)`で調査する。
結果のturn番号・確定時刻、通知の送信時刻、API失敗の集計回数・初回時刻、初期化の受信総数は内部保持と診断処理に残して通常応答へ通さない。
内部辞書から用途別の項目集合へ射影することで、内部追加を公開契約の追加にしない。
保存物の一括変換や新しい台帳は不要であり、旧保存物も回収時に同じ射影へ通す。

## dotfilesの`bin/atk`によるworktreeの選択

dotfilesの`bin/atk`と`bin/atk.cmd`は同一リポジトリ内からの通常起動でcwdのworktreeを使う。
専用worktreeの`plan-check`などを改修しても、PATHのコマンドが複製元の版を起動すると、改修版の欠陥が検証を通過する。

スクリプト自身の配置からdotfilesのrootを求め、cwdのGit共通ディレクトリと比較する。
同じ場合はcwdのworktreeにある`agent-toolkit/scripts/atk`へ委譲し、Git外と別リポジトリでは配置先の従来版を使う。
子へ引数、cwd、標準出力、標準エラーと終了コードを渡し、子の失敗で別版へ戻さない。
対象worktreeの`agent-toolkit/scripts/atk`が無い場合は、不足と復元の操作を返す。
Windowsは`call`による引数の再展開を避け、選択したバッチへ処理を移す。

配布物の`agent-toolkit/scripts/atk`は自身の配置から依存と実装を選び、常駐時の再起動を担う。
dotfilesの`bin/atk`はworktreeの選択だけを担い、配布物の実装、PATH登録、hookとMCPの起動先を保つ。
各工程へ絶対パスの選択を複製する案は、通常起動の取り違えを残すため採らない。
プラグイン側で実装を自動で切り替える案は、dotfilesを使わない環境の実装選択まで変えるため採らない。

プラグインのCLIは、Chat・Coworkがインストールを拒否する直下の`bin/`を避けて`scripts/`へ同梱する。
`scripts/launch.py`は標準ライブラリだけで配布rootと書き込み可能なデータ領域を解決し、準備済みの共有環境のPythonで対象を起動する。
依存の準備には`uv run --no-config --no-project --python 3.15 python <plugin root>/scripts/launch.py --prepare agent_toolkit/atk.py`を実行する。

`--prepare`の後ろのtargetは配布root内に存在する起動対象を確認する必須引数であり、共有環境の選択には影響しない。
後述の組み合わせが未準備の場合に実行し、同じ組み合わせなら別の配布rootやworktreeでも準備済み環境を共有する。
別worktreeを指定する場合の`<plugin root>`は、そのworktree内の`agent-toolkit`の絶対パスに置き換える。
rootは`launch.py`の配置または`--project`から解決され、cwdの変更だけでは切り替わらない。

CLI・hook・MCPはこのスクリプトから起動し、登録済みスキルスクリプトはCLIと同じ環境内で実行する。
依存に作用するpyproject設定、`uv.lock`、選択したPythonの実行ファイルパス、OS・アーキテクチャの組み合わせから環境の保存先を分ける。
データ領域は`AGENT_TOOLKIT_RUNTIME_DATA`の明示指定を優先し、次にOSのユーザーデータ領域を使う。
ホストが渡す`CLAUDE_PLUGIN_DATA`と`PLUGIN_DATA`はユーザーデータ領域へ書き込めない場合だけ使う。
ホストごとのplugin dataを優先すると、導入後のウォームアップ・端末の`atk`・Claude Code・Codexが同じ版の環境を別々に構築し、初回のhookがウォームアップ済みの環境を使えないためである。
root・data・環境の導出は`agent_toolkit/_common/plugin_environment.py`が所有し、各SDKへ同じ判断を複写しない。

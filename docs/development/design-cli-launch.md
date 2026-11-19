# 公開出力とworktreeの選択の設計記録

[設計記録の索引](design.md)から参照する、公開出力とworktreeの選択の記録である。

## 公開出力から導出できる項目の撤去

`atk agents list`と`atk agents show`、およびMCPの`list`は停滞の判定を表す`stalled`を返していた。
`stalled`の判定は同じ応答が返す`seconds_since_activity`と定数の閾値を比べるだけで再現できるため、公開出力から撤去した。

導出できる項目を公開出力へ置くと、同じ判定を定める箇所が生成側と呼び出し側の2つに分かれる。
閾値を変えた時点で両者が食い違い、どちらが現在の判定かを呼び出し元が確定できない。
公開出力は一次情報だけで構成し、判定は呼び出し元が行う。

公開項目は消費側の用途から選ぶ。
内部辞書から用途別の項目集合へ射影することで、内部追加を公開契約の追加にしない。
保存物の一括変換や新しい台帳は不要であり、旧保存物も回収時に同じ射影へ通す。

## dotfilesの`bin/atk`によるworktreeの選択

dotfilesの`bin/atk`と`bin/atk.cmd`は同一リポジトリ内からの通常起動でcwdのworktreeを使う。
専用worktreeの`plan-check`などを改修しても、PATHのコマンドが複製元の版を起動すると、改修版の欠陥が検証を通過する。

配布物の`agent-toolkit/scripts/atk`は自身の配置から依存と実装を選び、常駐時の再起動を担う。
dotfilesの`bin/atk`はworktreeの選択だけを担い、配布物の実装、PATH登録、hookとMCPの起動先を保つ。
同じ場合はcwdのworktreeにある`agent-toolkit/scripts/atk`へ委譲し、Git外と別リポジトリでは配置先の従来版を使う。
子へ引数、cwd、標準出力、標準エラーと終了コードを渡し、子の失敗で別版へ戻さない。
各工程へ絶対パスの選択を複製する案は、通常起動の取り違えを残すため採らない。
プラグイン側で実装を自動で切り替える案は、dotfilesを使わない環境の実装選択まで変えるため採らない。

プラグインのCLIは、Chat・Coworkがインストールを拒否する直下の`bin/`を避けて`scripts/`へ同梱する。
`scripts/launch.py`は標準ライブラリだけで配布rootと書き込み可能なデータ領域を解決し、準備済みの共有環境のPythonで対象を起動する。

CLI・hook・MCPはこのスクリプトから起動し、登録済みスキルスクリプトはCLIと同じ環境内で実行する。
`launch.py`のインラインメタデータ（PEP 723）が宣言する依存と`launch.py.lock`の内容、選択したPythonの実行ファイルパス、OS・アーキテクチャの組み合わせから環境の保存先を分ける。
理由と開発環境の構成は[パッケージ構成と起動層の設計記録](design-packages-layout.md)の「agent-toolkitの依存の宣言と開発環境」に従う。
ホストごとのplugin dataを優先すると、導入後のウォームアップ・端末の`atk`・Claude Code・Codexが同じ版の環境を別々に構築し、初回のhookがウォームアップ済みの環境を使えないためである。
データ領域は`AGENT_TOOLKIT_RUNTIME_DATA`の明示指定を優先し、次にOSのユーザーデータ領域を使う。
ホストが渡す`CLAUDE_PLUGIN_DATA`と`PLUGIN_DATA`はユーザーデータ領域へ書き込めない場合だけ使う。
root・data・環境の導出は`agent_toolkit/_common/plugin_environment.py`が所有し、各SDKへ同じ判断を複写しない。

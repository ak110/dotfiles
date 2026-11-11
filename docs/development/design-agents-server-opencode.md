# OpenCode CLI backendの設計記録

[設計記録の索引](design.md)から参照する、OpenCode CLI backendの記録である。

## OpenCode CLI backend

OpenCode 1を一般の委譲先engineとして追加し、工程別の直接候補列へ参加させる。
ユーザーが求めたQwenなどの利用を、既存のClaude・Codexのモデル設定と独立して切り替えるためである。
既存5プリセットと初期値を保ち、OpenCodeは工程別モデルキーへ候補列を直接指定して選ぶ。
`orchestrate_model`は保存と環境変数の両方の指定方法でopencodeを拒否する。

`opencode.py`がコマンドとイベント解釈を所有し、Antigravityと共通のプロセス所有・継続・停止・ログ追記は`cli_turn.py`へ集約する。
同じライフサイクルを別実装へ複製する案は、停止・再開の修正を両方へ繰り返すため採らなかった。
engine別のJSONを共通実行処理へ埋め込む案も、イベント仕様の変更理由が異なるため採らない。
各backendのコマンド、開始ID、イベント反映、CLI終了の検収と規範配送だけを分ける。
Antigravityの既存試験を共通化の回帰試験として使う。

`opencode run`へ`--model provider/model`・`--format json`・`--auto`を渡し、effortを`--variant`、継続IDを`--session`へ渡す。
設定の`provider/model[@effort]`はモデル内の`/`を保持し、末尾の`@`でvariantを分ける。
既知のeffortを末尾の`/`へ書く形も受理する。
担当モデルのチェックは共通のモデル候補パーサーへ委ね、個別の正規表現は持たない。
OpenCode 1.19.3の[公開ソース](https://github.com/anomalyco/opencode/blob/v1.19.3/packages/opencode/src/cli/cmd/run.ts)で、各イベントの`sessionID`、本文・ツール・step使用量の`part`と、idleでCLIが終わる契約を確認した。
終了コード0とアシスタント本文で完了を確定し、errorイベント・非0終了・本文なしは失敗とする。
OpenCodeには実行時間上限の指定が無いため共通実行処理で3600秒を持ち、独自の上限を持つAntigravityには重ねない。

共通規範・委譲先規範・ユーザー規範を開始と再開の本文へ連結する。
スキルをホストへ自動登録する機構は追加せず、plugin rootから明示的に読めるようにする。
配送範囲は`delivery-scope.md`と`start`の説明へ同じ内容を載せる。
CLI自身によるプロジェクト規範の読込は未確認として扱う。

ログは`logs/opencode/<session-id>.jsonl`へ置き、Antigravityの既存配置を保ったままengineを一意に解決する。
ネイティブイベントはそのまま保持し、CLIが出力しないturn入力・cwd・要求モデルは`agent_toolkit_input`行で補う。
この要求モデルを実行先で観測したモデルとはみなさない。
`session_record_format.py`の共通表示モデルを通してログ表示と証拠抽出を接続し、使用量・ツール・失敗・返却を既存の集計へ渡す。
起動・継続の偽CLI試験と、親からのログ表示・証拠抽出・統計の公開された呼び出し手段試験で検収する。

導入は両OSで公式に提供するnpm手段を使い、既存の工程依存でNode.js準備と他CLIのnpm移行の後に実行する。
未導入時だけ導入し、失敗は他の取得工程と同じく警告へ留める。

# Claude Code・Codex Hook実装ガイドライン：Codexの編集ツール入力

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## Codexの編集ツール入力

Codexの`apply_patch`はmatcher上で`Edit`や`Write`の別名として一致する。
ただし、入力payloadの`tool_name`は`apply_patch`のままであり、変更内容は`tool_input.command`内に`*** Begin Patch`から`*** End Patch`までの構文で渡される。
1回の呼び出しの中に、`Add File`、`Update File`、`Delete File`、`Move to`、および`@@`区切りの差分（hunk）が複数ファイル分まとめて含まれる。

ホスト間の差異をチェック処理本体へ持ち込まないようにするため、編集入力は以下の2層に正規化して扱う。

- 操作記録: 入力データのみから操作種別、対象パス、順序付きの編集断片を抽出する。
  ファイルを読み取らないため、`PostToolUse`からも利用できる。
- 変更前後像: 対象ファイルの現在内容に対して操作記録を適用し、変更後の全体像を構築する。
  これは`PreToolUse`でのみ使用する。

相対パスはpayloadに含まれる`cwd`を起点として解決する。
パッチ構文を解釈できない場合はhook側で遮断せず、妥当性の判定を`apply_patch`本体の処理に委ねる。

ホストの判定は、Codexがターン単位のhookに付与する空でない文字列の`turn_id`、および`UserPromptSubmit`でCodexのみが渡す`prompt`と`model`の組み合わせに基づいて行い、ツール名から推測することは避ける。

複数ファイルや複数チェックによる警告は、1つの`hookSpecificOutput.additionalContext`に統合して返す。
標準出力全体が1つのJSONとして解析されるためである。

Codexのシェル実行および統合実行（`exec_command`）はmatcher上で`Bash`に一致し、`tool_input.command`に実行コマンド文字列が渡される。
一次資料は<https://learn.chatgpt.com/docs/hooks>のTool coverageとし、matcherに指定する名前は`Bash`に限定する。

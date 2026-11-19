# 監査記録: MCPサーバー

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-basics/skills/writing-standards/references/mcp-server-design.md：典拠：2026年10月2日

2026年10月2日、同書の典拠表の各資料を取得した。
Claude Code公式資料（Claude Code 2.1.286の時点）の「For MCP server authors」は、ツール説明とserver instructionsを各2,048文字に切り詰めると記す。
この値は`CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH`で変更できる。
「MCP output limits and warnings」は1万トークンの警告と2万5千トークンの上限を記し、上限を変える`MAX_MCP_OUTPUT_TOKENS`と`_meta["anthropic/maxResultSizeChars"]`を示す。
MCP 2025-11-25のSchema Referenceは`InitializeResult.instructions`をsystem promptへ加えてもよいヒントとし、`ToolAnnotations`の全項目をヒントとする。
再検証は各資料の該当節を取得し、切り詰めの文字数、出力上限、instructionsの扱いを比べる。
値が変わった場合は同書の「ホスト固有の条件」と、後述の説明長のテストを改める。

既存の2サーバーへ同書の観点を適用した。
判定は「適用済み」「任意の改善候補」「仕様上の是正」に分ける。
任意の推奨との相違だけでは不良と判定しない。

agents_server（`agent-toolkit/agent_toolkit/agents_server_mcp.py`、MCP Python SDK 1.30.0）の公開ツールは`start`、`send_message`、`kill`、`stop`、`list`、`show`の6件である。
`FastMCP.list_tools()`の取得結果と、各ツールの直接の応答生成（`_public_start_response`、`AgentsServerManager`の各公開メソッド、`_actionable_tool`）を読んだ。

- 適用済み: 起動操作は`start`の`mode`へ統合し、modeを列挙型でスキーマへ示し、modeごとの必須・禁止の入力を説明と`_validate_start_inputs`の双方に置く。
  応答は`session_id`と次の操作を返し、`list`は除いた件数を`omitted`へ、`show`は`verbose`で詳細を選ぶ。
  例外は`_actionable_tool`が`next_action`付きの`ToolError`へ変え、SDKがツール実行エラーとして返す。
  全ツールが構造化応答を返し、`outputSchema`は任意の項目を許すobjectである。
  stdio transportのログは標準エラーとファイルへ出力する
- 是正済み（同書の「単体での利用」とホスト固有の条件による）: 適用前の`start`の説明は2,932文字で、Claude Codeの設定を変えない状態では後半の結果受領・起動前の準備・応答の説明が切り詰められていた。
  結果受領を先頭へ移し、modeの選び方を`mode`の引数説明へ移して1,962文字とした。
  `send_message`・`kill`・`stop`・`show`の`session_id`、`send_message`の`prompt`、`list`の`include_terminated`、`show`の`verbose`は引数説明を持たなかったため説明を加えた。
  `_agents_server/tool_descriptions_test.py::test_tool_descriptions_fit_claude_code_truncation_and_describe_every_argument`が説明長と全引数の説明を確かめる。
  instructionsは690文字である
- 任意の改善候補: `outputSchema`は応答の項目を列挙していない。
  応答は`mode`や状態で項目が変わり、列挙の保守負担が大きいため、2026年10月29日の見直しでも列挙しない
- 適用済み（2026年10月29日）: 全ツールへ日本語の`title`と注釈を付けた。
  `list`と`show`は`readOnlyHint`、`start`と`send_message`は`destructiveHint: false`、`kill`と`stop`は`destructiveHint: true`である。
  `list`が内部で行う保持期限到来sessionの失効は、保持期限のタイマーが同じ時刻に行う失効を先に反映するだけで、呼び出しの有無と回数で観測できる状態が変わらないため、`readOnlyHint`を付けた。
  `_agents_server/tool_descriptions_test.py::test_tool_list_publishes_title_and_side_effect_annotations`が`tools/list`の応答の値を確かめる
- 仕様上の是正: 該当なし

pyfltrの公開ツールは`build_server`が登録する11件である。
対象は`/home/aki/pyfltr/pyfltr/cli/mcp_server.py`で、対応する変更は2026年10月1日「fix: 置換失敗時の復旧とMCPの応答性を改善し、公開前の動作確認を追加する」、`c5aa7e2`である。
観測環境のMCP Python SDKは2.2.0だった。
読み取り専用で`list_tools()`の結果と各ツールの直接の応答生成を読んだ。

- 適用済み: 全ツールの`inputSchema`はobjectで、構造化結果は戻り値の型で検証してから返す。
  想定内の誤りは`ToolError`からツール実行エラーとして返り、`show_run`などは有効な値を得るツールを案内する。
  `grep`は件数上限、要約の選択、省略件数と`guidance`を返す。
  MCPのstdoutへは書かず、ログを標準エラーへ向ける
- 任意の改善候補: 公開される説明は`description=`の文字列だけで、引数の意味、組合せ条件、省略時の動作はクライアントへ届かないdocstringにある（例: `grep`の`max_count`の0の意味、`replace`の`within`と文脈行数の組合せ、`config`の`action`ごとの必須引数）。
  全ツールの引数に説明が無い。
  `run`の`mode`、`replace_history`と`config`の`action`は文字列型で列挙値がスキーマに無い。
  `show_run_output`は出力ログの全文、`show_run_diagnostics`は指定コマンドの診断の全件を返し、範囲・件数の指定と省略の通知を持たない。
  annotationsは全ツールで未設定で、書込を伴う`replace_undo`と`config`の説明は書き換えを明示しない。
  説明の言語が日本語と英語で混在する。
  instructionsは未設定である
- 仕様上の是正: 確定した該当なし。
  未確定として、workerプロセスからの結果JSONの読取に例外処理が無く、外部ツールがファイル記述子1へ直接書いた場合は汎用のエラー本文になる可能性がある（プロトコル違反ではない）。
  子プロセスの標準出力が常に取り込まれるかは確認していない

## agent-basics/skills/writing-standards/references/mcp-server-design.md：ホスト固有の条件：2026年10月13日

2026年10月13日、Claude Codeの公式リリースノートを`gh release view <タグ> -R anthropics/claude-code`で取得した。
2つの版の変更項目は次のとおりである。

```text
v2.1.295: Changed MCP tool descriptions the model loads through tool search to be cut at 16,384 characters instead of 2,048
v2.1.296: Changed the default limit on MCP tool descriptions sent up front and on MCP server instructions from 2,048 to 4,096 characters
```

取得元は[v2.1.295](https://github.com/anthropics/claude-code/releases/tag/v2.1.295)と[v2.1.296](https://github.com/anthropics/claude-code/releases/tag/v2.1.296)である。
前掲の2026年10月2日の記録にある2,048文字は、Claude Code 2.1.286の時点の公式資料の観測であり、本記録はその値を置き換えずに新版の仕様を記録する。
2026年10月11日の時点の公式MCPガイドには2,048文字の記述が残っていたため、版を特定したリリースノートを根拠とする。
上限を変える`CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH`は2026年10月2日の公式資料の記述であり、新版での名称は再確認していない。
引数の説明への適用は両リリースノートとも記していない。

agents_serverは全説明とinstructionsを初期送信の4,096文字以内に保つ。
検索後の16,384文字も同時に満たすため、検索の有無で説明を変えない。
説明長とinstructions長は`agent-toolkit/agent_toolkit/_agents_server/tool_descriptions_test.py`の次の2件が確かめる。

- `test_tool_descriptions_fit_claude_code_truncation_and_describe_every_argument`
- `test_lightweight_selection_reaches_instructions_and_mode_schema`

説明の先頭4,096文字に`EXPLORE_CAPABILITIES`が届くことは次の2件が確かめる。

- `launch_prompts_test.py::test_explore_capabilities_are_shared_with_public_schema`
- `mcp_tools_test.py::test_public_start_schema_delivers_explore_capabilities`

再検証は同じコマンドで後続版のリリースノートからMCPの説明とinstructionsの上限の変更項目を探し、公式MCPガイドの「For MCP server authors」と比べる。
値が変わった場合は同書の「ホスト固有の条件」、`tool_descriptions.py`の`START_DESCRIPTION`直前のコメントと前記のテストを改める。

## agent-basics/skills/writing-standards/references/mcp-server-design.md：典拠：2026年10月29日

2026年10月29日、公式マーケットプレイス`anthropics/claude-plugins-official`の複製`~/.claude/plugins/marketplaces/claude-plugins-official/`から`mcp-server-dev`の資料を読んだ。
複製の版は、同ディレクトリの`.gcs-sha`が`f8c5e7a1dcb71b10cb74586224b5401f3de5db28`、`~/.claude/plugins/known_marketplaces.json`の`lastUpdated`が`2026-10-29T07:27:00.345Z`である。
`build-mcp-server/references/tool-design.md`の「Tool annotations」は、`readOnlyHint`・`destructiveHint`・`idempotentHint`・`openWorldHint`の意味とホストの扱いを表で示す。
同節は、全読取ツールへ`readOnlyHint: true`を付けるよう勧める。
同書冒頭の「Anthropic Directory hard requirements」は、Directoryへ提出するサーバーの合否条件として、全ツールの`readOnlyHint`・`destructiveHint`・`title`、読み書きのツール分割、説明文での行動指示の禁止などを挙げる。
`server-capabilities.md`は`instructions`、sampling、roots、logging、progress、cancellation、completionを扱う。

再検証は同じ複製かGitHubの同じパスから各資料を取得し、注釈の推奨とDirectoryの条件を同書の「ツール注釈」と典拠表の注記と比べる。
推奨が変わった場合は同書を改める。

## agent-basics/skills/writing-standards/references/mcp-server-design.md：ホスト固有の条件：2026年10月29日

2026年10月29日、Claude Code 2.1.312の本体（`~/.local/share/claude/versions/2.1.312`）を`rg -a -o`で検索した。
MCPツールの呼び出しで`mayHaveRun`を持つ接続断の例外が起きた場合、本体は実行前の失敗か、ツールの`readOnlyHint===!0`のときに再試行する分岐を持つ。
別の再試行経路は、実行前の失敗、`isConcurrencySafe`、`annotations?.idempotentHint===!0`のいずれかで再試行する。
MCPツールの`isConcurrencySafe`は`annotations?.readOnlyHint??!1`を返すため、`readOnlyHint: true`のツールは並行呼び出しの対象にもなる。
権限の自動許可への注釈の作用は確認していない。

再検証は後続版の本体を`develop-dotfiles`「ホスト本体のバイナリの検索」の手順で`mayHaveRun`と`isConcurrencySafe(){return`を検索し、再試行と並行実行の条件を比べる。
条件が変わった場合は同書の「ホスト固有の条件」を改める。

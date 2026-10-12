# MCPサーバーの設計指針

本書はMCPサーバーのツール、説明、応答を設計、実装、変更またはレビューするエージェントへ判断の基準を示す。
Function hooksなどホストが登録するモデル向けツールにも、説明・引数スキーマ・実行時の入力検証・エラー応答の指針を適用する（スキーマの宣言だけで入力の正しさを仮定せず、不正入力には修正して再試行できる応答を返す）。ホスト登録ツールの応答形式はそのホストの契約に従い、MCPのJSON-RPC・伝送路・サーバー通知の要求は適用しない。
ツールの説明、引数のスキーマおよび応答はモデルのコンテキストを消費する。目的に合う粒度と応答量を選び、完遂率と選択の精度を保ったままトークンと呼び出し回数を減らす。

MCP仕様の要求は硬い制約として守る。開発元の推奨は利用場面で優劣が変わるため努力目標とし、ホスト固有の機能と制限はそのホストを対象に含む場合の条件とする。

## 典拠

2026年10月2日に取得した次の一次資料に基づく。MCP仕様は版2025-11-25を対象とする。利用するSDKが対応する仕様の版は実装前に確かめる。
監査記録と再検証手段は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/mcp-server-design.md：典拠と既存サーバーへの適用：2026年10月2日」にある。

| 区分 | 資料 | 本書で使う節 |
| --- | --- | --- |
| 仕様 | [MCP Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) | Listing Tools、List Changed Notification、Tool、Tool Result、Output Schema、Error Handling、Security Considerations |
| 仕様 | [MCP Schema Reference](https://modelcontextprotocol.io/specification/2025-11-25/schema) | `InitializeResult.instructions`、`ToolAnnotations` |
| 仕様 | [MCP Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)、[MCP Prompts](https://modelcontextprotocol.io/specification/2025-11-25/server/prompts) | User Interaction Model |
| 仕様 | [MCP Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) | stdio、Streamable HTTPのSecurity Warning |
| 開発元の推奨 | [MCP Security Best Practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices) | Attacks and Mitigations |
| 開発元の推奨 | [Anthropic: Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | Running an evaluation、Choosing the right tools for agents、Namespacing your tools、Returning meaningful context from your tools、Optimizing tool responses for token efficiency、Prompt-engineering your tool descriptions |
| ホスト固有 | [Anthropic: Introducing advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) | Tool Search Tool、Programmatic Tool Calling、Tool Use Examples、Best practices（Claude Developer Platformのbeta機能） |
| ホスト固有 | [Claude Code: Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp) | MCP output limits and warnings、Scale with MCP tool search、For MCP server authors |

## 仕様が要求する契約

- `inputSchema`は有効なJSON Schemaのobjectとする（`null`は不可）。引数を持たないツールには`{"type": "object", "additionalProperties": false}`が推奨されている
- `outputSchema`を宣言したツールは、適合する構造化結果を`structuredContent`で返し、後方互換のため同じJSONを直列化したテキストも返すことが推奨されている
- 未知のツールと要求形式の誤りはJSON-RPCのプロトコルエラー、入力検証の失敗、外部APIの失敗、業務上の誤りは`isError: true`のツール実行エラーで返し、モデルが引数を直して再試行できる情報を含める
- `tools/list`はページングに対応する。`listChanged`を宣言したサーバーは一覧の変更時に`notifications/tools/list_changed`を送ることが推奨されている
- サーバーは全ての入力を検証し、アクセス制御、呼び出し頻度の制限、出力の無害化を実装する
- `ToolAnnotations`はヒントであり認可と入力検証の代わりにならないため、読取専用や非破壊の性質もサーバー側の処理で保証する
- stdioではstdoutへMCPメッセージ以外を書かず、ログはstderrへ書く。外部コマンドを起動するサーバーは子プロセスのstdoutも取り込むか切り離す
- Streamable HTTPでは全ての接続で`Origin`ヘッダーを検証する。ローカルではlocalhostだけで待ち受け、全ての接続を認証することが推奨されている。認可やトークンを扱う場合はSecurity Best Practicesの攻撃と対策を設計時に照らす
- ツールはモデルが選ぶ操作、resourcesはホストが文脈へ取り込む情報、promptsはユーザーが明示的に選ぶ定型の対話である。モデルに選ばせる必要の無い参照資料や定型文をツールにしない

## 設計の推奨

以下は努力目標とする。

### ツールの粒度

関連する操作と頻繁に連続する処理を、目的が明確な少数のツールへまとめる。目的はツール数の最少化ではなく、用途の区別と入力の明確さを保った統合であり、権限と副作用の境界もツールの境界と一致させる。
列挙値の引数（`action`、`mode`など）で複数の操作をまとめる場合は、値を列挙型でスキーマへ示し、操作ごとの必須・禁止の引数を説明と入力検証の双方へ置く。操作ごとに必須の引数や副作用が大きく異なるなら分ける。

### 発見と説明

ツール名、名前空間（複数サービスにまたがる同種の操作には共通の接頭辞）、引数名、説明は、別のツールと目的を区別できる語で書く。選択に必要な暗黙の知識（専門的な書式・用語・資源の関係）を説明し、誤用しやすい入れ子の構造や独自の書式には有効な呼び出し例を添える。
説明文は呼び出しの可否と方式を判定する基準として書く。ツールの組み合わせと実行順序は、そのツールを使う工程の手順文書へ置く。説明文は全ての呼び出しの前に読まれるためである。

### 単体での利用

基本的な呼び出しはツールの説明と引数スキーマだけで確定できる構成にする。instructionsは取り込むかをクライアントが選ぶ任意のヒントであり、実装言語のdocstringなどクライアントへ届かない説明はモデルへ届かない。

- ツールの説明には、呼ぶ目的と、似たツールとの選択条件を置き、重要な情報を先頭に置く（ホストが切り詰めるため）
- 引数の説明とスキーマには、意味、書式、省略時の動作、ほかの引数との組合せ条件を置く。列挙値は列挙型、必須の引数はスキーマで必須にする
- server instructionsには、サーバー全体の用途と、ツールを探して選ぶ判断を短く置く。プロジェクト固有の運用方針や詳しい手順は補助資料に置いてよい

### 応答

次の判断に必要な情報と、後続の呼び出しに必要な識別子を返す。取得範囲と粒度を選べるようにし、省略時の動作を明確にする。

- 応答を切り詰めた場合は、省略したことと続きを取得する方法を応答に含める。伝えずに切り詰めると、モデルは欠けた結果を全量として扱う
- ツール実行エラーの文面は`writing.md`「読み手別の追加注意点」のプログラムが出力するメッセージの項目に従い、直すべき入力を示す
- 固定のトークン上限は、利用場面の評価またはホストの制限を根拠とする場合に設ける

### 大規模なツール群

ツール定義の読込量や中間の応答量が問題になる規模では、ホストの動的なツール発見・遅延読込と、コードによる呼び出しと中間結果の集約を、減る呼出費用と追加する工程の費用を比べて検討する。これらはClaude Developer Platformの機能であり、サーバー側では発見に使われる名前、説明、instructionsを整えて対応する。

### 評価

粒度と説明は、実際の利用場面を模した複数の呼び出しを要する課題で、完遂率、誤った選択、呼び出し回数、応答量、所要時間を比べて調整する。記事の測定値は条件付きの事例として扱う。

## ホスト固有の条件

対象ホストにClaude Codeを含む場合は、次の制限と機能を設計の条件に加える。ほかのホストの制限は、そのホストの一次資料で確かめる。

- Claude Codeは各ツールの説明と各サーバーのinstructionsを、設定を変えない状態で2,048文字に切り詰める（環境変数`CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH`で変更可）。引数の説明が同じ上限を受けるかは同資料からは確定できない
- ツール検索が有効な場合、セッション開始時に読み込まれるのはツール名とserver instructionsだけである。instructionsには、扱う作業の種類、ツールを探すべき場面、主な機能を書く
- MCPツールの出力が1万トークンを超えると警告し、設定を変えない状態で2万5千トークンに制限する（環境変数`MAX_MCP_OUTPUT_TOKENS`で変更可）。本質的に大きな出力を返すツールは`tools/list`の`_meta["anthropic/maxResultSizeChars"]`で上限を上げられる（画像を返すツールには適用されない）

# MCPサーバーの設計指針

本書はMCPサーバーのツール、説明、応答を設計、実装または変更する主体と、その成果をレビューする主体へ判断の基準を示す。
モデルが読むツールの説明、引数のスキーマおよびツールの応答はコンテキストを消費する。目的に合う粒度と応答量を選ぶ設計は、完遂率と選択の精度を保ったままトークンと呼び出し回数を減らす。

各指針は典拠の区分を示す。
MCP仕様の要求は硬い制約として守る。開発元の推奨と実装例は、利用場面での評価により優劣が変わるため努力目標として扱う。
ホスト固有の機能と制限は、そのホストを対象に含む場合だけの条件として扱う。
説明文の書き方の一般則は`agent-documents-basics.md`「責務と構成」と`writing.md`に従う。

## 典拠

本書の記述は2026年10月2日に取得した次の一次資料に基づく。MCP仕様は版2025-11-25を対象とし、利用するSDKが対応する仕様の版を実装前に確かめる。
ホスト固有の値の観測記録と再検証手段は`docs/development/audit-records.md`にある。
節名は「agent-toolkit/skills/writing-standards/references/mcp-server-design.md：典拠と既存サーバーへの適用：2026年10月2日」である。

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

次の契約はMCP仕様の要求であり、クライアントとの相互運用と安全性の前提となる。

- `inputSchema`は有効なJSON Schemaのobjectとする（`null`は不可）。引数を持たないツールには`{"type": "object", "additionalProperties": false}`が推奨されている（Tools「Tool」）
- `outputSchema`を宣言したツールは、そのスキーマに適合する構造化結果を`structuredContent`で返す。後方互換のため、同じJSONを直列化したテキストも返すことが推奨されている（Tools「Structured Content」「Output Schema」）
- 未知のツールと要求形式の誤りはJSON-RPCのプロトコルエラーで返す。入力検証の失敗、外部APIの失敗、業務上の誤りは`isError: true`のツール実行エラーで返し、モデルが引数を直して再試行できる情報を含める（Tools「Error Handling」）
- `tools/list`はページングに対応する。`listChanged`を宣言したサーバーはツール一覧の変更時に`notifications/tools/list_changed`を送ることが推奨されている（Tools「Listing Tools」「List Changed Notification」）
- サーバーは全ての入力を検証し、アクセス制御、呼び出し頻度の制限、出力の無害化を実装する（Tools「Security Considerations」）
- `ToolAnnotations`の各値はヒントであり、クライアントは信頼できないサーバーの値を信頼しない（Schema「ToolAnnotations」）。annotationsは認可と入力検証の代わりにならないため、読取専用や非破壊の性質もサーバー側の処理で保証する
- stdioではstdoutへMCPメッセージ以外を書かず、ログはstderrへ書く（Transports「stdio」）。外部コマンドを起動するサーバーは子プロセスのstdoutも取り込むか切り離す
- Streamable HTTPでは全ての接続で`Origin`ヘッダーを検証する。ローカルで動かす場合はlocalhostだけで待ち受け、全ての接続を認証することが推奨されている（Transports「Security Warning」）。認可やトークンを扱う場合は、Security Best Practicesが挙げる攻撃と対策を設計時に照らす
- ツールはモデルが選ぶ操作、resourcesはホストが文脈へ取り込む情報、promptsはユーザーが明示的に選ぶ定型の対話として設計されている（Tools・Resources・Prompts「User Interaction Model」）。モデルに選ばせる必要の無い参照資料や定型文をツールにすると、モデルが選ぶ候補とツール定義の読込量が増える

## 設計の推奨

以下は開発元の推奨と、本書が利用場面に合わせて定める推奨である。利用場面ごとの評価で優劣が変わるため、各項目は努力目標とする。

### ツールの粒度

関連する操作と頻繁に連続する処理を、目的が明確な少数のツールへまとめる（努力目標。各エンドポイントをそのままツールにする構成より説明の重複、中間の応答、呼び出し回数が減る）。
典拠はWriting effective tools「Choosing the right tools for agents」である。

- 開発元の例は、利用者の一覧取得、予定の一覧取得、予定の作成を、予定を組む1つのツールへまとめる構成である
- 目的はツール数の最少化ではなく、用途の区別と入力の明確さを保った統合である。権限と副作用の境界もツールの境界と一致させる
- 列挙値の引数（`action`、`mode`など）で複数の操作を1つのツールへまとめる方法は選択肢の1つとする。操作ごとに必須の引数や副作用が大きく異なる場合は、まとめると引数の組合せ条件と注釈の値が操作によって変わり、誤った呼び出しを招く。まとめる場合は値を列挙型でスキーマへ示し、操作ごとの必須・禁止の引数を説明と入力検証の双方へ置く

### 発見と説明

ツール名、名前空間、引数名、説明は、別のツールと目的を区別できる語で書く（努力目標。モデルは名前と説明だけからツールを選ぶため、似た名前や曖昧な説明は誤った選択を招く）。
典拠はWriting effective tools「Namespacing your tools」「Prompt-engineering your tool descriptions」である。

- 複数のサービスにまたがる同種の操作には、サービス名などの共通の接頭辞で名前空間を付ける
- 専門的な書式、用語、資源どうしの関係など、暗黙の知識を説明へ書く。開発元は説明の小さな改善で評価結果が大きく改善した例を報告している
- 正しいJSONでも誤った使い方になり得る入れ子の構造や独自の書式には、有効な呼び出し例を説明へ添える（Advanced tool use「Tool Use Examples」）
- 説明の責務と、ツールの組み合わせ・実行順序を置く手順文書の責務の分担は`agent-documents-basics.md`「責務と構成」に従う

### 単体での利用

MCPサーバーは、接続したモデルがツールの説明と引数のスキーマだけで基本的な呼び出しを確定できる構成にし、別の規範文書への依存を極力減らす（努力目標。instructionsは任意のヒントでsystem promptへの追加もクライアントの選択である）。
典拠はSchema「InitializeResult.instructions」である。
開発元も説明の明確さを推奨している（Writing effective tools「Prompt-engineering your tool descriptions」）。「極力」の程度は、正しい呼び出しに必要な情報が各公開説明にそろうかで判断し、補助資料は併用してよい。

- ツールの説明には、そのツールを呼ぶ目的と、似たツールとの選択条件を置く
- 引数の説明とスキーマには、意味、書式、省略時の動作、ほかの引数との組合せ条件を置く。列挙値は列挙型で示し、必須の引数はスキーマで必須にする
- server instructionsには、サーバー全体の用途と、ツールを探して選ぶ判断を短く置く
- 引数を正しく組み立てるための必須の情報は、instructionsや外部文書への参照ではなく各公開説明へ置く。実装言語のdocstringなど、ツール定義としてクライアントへ届かない場所の説明はモデルへ届かない
- ホストが説明を切り詰める場合に備え、重要な情報を先頭に置く
- プロジェクト固有の運用方針や詳しい利用手順は補助資料に置いてよい。基本的な呼び出しの説明は前記の公開説明が持つ

### 応答

応答には次の判断に必要な情報と、後続の呼び出しに必要な識別子を返す（努力目標。不要な詳細はコンテキストを消費し、必要な情報の欠落は追加の呼び出しを生む）。
典拠はWriting effective tools「Returning meaningful context from your tools」「Optimizing tool responses for token efficiency」である。

- 検索、範囲指定、対象の限定、ページング、要約と詳細の選択を利用場面に合わせて設け、適切な省略時の値を持たせる。開発元の例は`concise`と`detailed`を選ぶ列挙型の引数である
- 応答を切り詰めた場合は、省略したことと、続きを取得する引数や呼び出しを応答に含める。省略を伝えずに切り詰めると、モデルは欠けた結果を全量として扱う
- エラーにはどの入力をどう直せば成功するかを示す。不透明なエラーコードやスタックトレースだけの応答は、再試行の方針を決める材料を欠く
- 固定のトークン上限は、利用場面の評価またはホストの制限を根拠とする場合に設ける

### 大規模なツール群

ツール定義の読込量や中間の応答量が問題になる規模では、ホストの動的なツール発見・遅延読込と、コードによる呼び出しと中間結果の集約を検討する（努力目標。小さな規模では追加の工程の負担が便益を上回る）。
典拠はAdvanced tool use「Tool Search Tool」「Programmatic Tool Calling」「Best practices」である。

- 開発元はツール検索の効果が大きい目安として、ツールが10件以上の場合と、定義が1万トークンを超える場合を挙げ、ツールが10件未満の小規模な構成では便益が小さいとしている
- コードによる呼び出しは、集計値だけを要する大量データの処理、依存する3回以上の呼び出し、多数の対象への並列処理で効果が大きく、単発の呼び出しでは負担が上回るとしている
- これらはClaude Developer Platformの機能であり、MCPサーバーの必須の実装とは別に扱う。サーバー側では、発見に使われる名前、説明、instructionsを整えることで対応する

### 評価

粒度と説明は、実際の利用場面を模した複数の呼び出しを要する課題で評価して調整する（努力目標。誤ったツールの選択や過剰な応答量は実際の呼び出しで初めて表れる。典拠はWriting effective tools「Running an evaluation」）。

- 完遂率、誤ったツールや引数の選択、呼び出し回数、応答量と総トークン、所要時間を比べる
- 特定の記事の測定値は、全ホストに共通の目標値ではなく条件付きの事例として扱う

## ホスト固有の条件

対象ホストにClaude Codeを含む場合は、次の制限と機能を設計の条件に加える（Claude Code「MCP output limits and warnings」「Scale with MCP tool search」「For MCP server authors」）。
ほかのホストの制限は、そのホストの一次資料で確かめる。

- Claude Codeは各ツールの説明と各サーバーのinstructionsを、設定を変えない状態で2,048文字に切り詰める。環境変数`CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH`で変更できる。引数の説明が同じ上限を受けるかは同資料からは確定できない
- ツール検索が有効な場合、セッション開始時に読み込まれるのはツール名とserver instructionsだけである。instructionsには、扱う作業の種類、ツールを探すべき場面、主な機能を書く
- MCPツールの出力が1万トークンを超えると警告を表示し、設定を変えない状態で2万5千トークンに制限する。上限は環境変数`MAX_MCP_OUTPUT_TOKENS`で変更できる。本質的に大きな出力を返すツールは`tools/list`の`_meta["anthropic/maxResultSizeChars"]`で上限を上げられる。この指定は画像を返すツールには適用されない

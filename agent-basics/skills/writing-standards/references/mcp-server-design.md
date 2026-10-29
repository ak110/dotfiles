# MCPサーバーの設計指針

本書は、MCP（Model Context Protocol）サーバーのツール定義、説明文、および応答メッセージを設計、実装、変更、またはレビューするエージェント向けに、判断の基準をまとめたものである。
Function hooksなどホスト環境側が登録するモデル向けツールに対しても、説明文、引数スキーマ、実行時の入力バリデーション、エラー応答に関する指針を同様に適用する（スキーマを宣言しただけで入力値が正しいと思い込まず、不正な入力に対してはモデルが修正して再試行できるエラー応答を返す）。なお、ホスト登録ツールの応答フォーマットは各ホストの契約に従うものとし、MCPのJSON-RPC仕様、トランスポート層の規定、およびサーバー通知の要件は適用しない。
ツールの説明文、引数スキーマ、および実行応答は、いずれもモデルのコンテキスト長を消費する。目的に適したツールの粒度と応答量を慎重に選定し、タスクの完遂率とツールの選択精度を高く維持したまま、消費トークン数とツールの呼び出し回数を削減できるようにする。

MCP仕様が求める要件は、遵守すべき硬い制約として扱う。MCP開発元によるベストプラクティス等の推奨事項は、利用シーンによって優劣が変わり得るため努力目標とし、特定のホスト固有の機能や制限事項は、そのホストをサポート対象に含める場合の条件として扱う。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `writing.md#「読み手別の追加注意点」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/writing.md#「読み手別の追加注意点」` |

## 典拠

本書は、2026年10月2日時点で取得した以下の公式一次資料に基づいている。MCP仕様としてはバージョン`2025-11-25`を対象としている。実際に利用するSDKが対応している仕様バージョンは、実装前に確認する。
監査記録と再検証手順は、`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/mcp-server-design.md：典拠：2026年10月2日」を参照する。
公式プラグイン`mcp-server-dev`の資料は2026年10月29日に取得した。取得した版と再検証手順は同書の「agent-basics/skills/writing-standards/references/mcp-server-design.md：典拠：2026年10月29日」を参照する。

| 区分 | 資料 | 本書で参照している主なセクション |
| --- | --- | --- |
| 仕様 | [MCP Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) | Listing Tools、List Changed Notification、Tool、Tool Result、Output Schema、Error Handling、Security Considerations |
| 仕様 | [MCP Schema Reference](https://modelcontextprotocol.io/specification/2025-11-25/schema) | `InitializeResult.instructions`、`ToolAnnotations` |
| 仕様 | [MCP Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)、[MCP Prompts](https://modelcontextprotocol.io/specification/2025-11-25/server/prompts) | User Interaction Model |
| 仕様 | [MCP Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) | stdio、Streamable HTTPのSecurity Warning |
| 開発元の推奨 | [MCP Security Best Practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices) | Attacks and Mitigations |
| 開発元の推奨 | [Anthropic: Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | Running an evaluation、Choosing the right tools for agents、Namespacing your tools、Returning meaningful context from your tools、Optimizing tool responses for token efficiency、Prompt-engineering your tool descriptions |
| 開発元の推奨 | `mcp-server-dev`の[build-mcp-server/references/tool-design.md](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/mcp-server-dev/skills/build-mcp-server/references/tool-design.md) | Descriptions、Tool annotations、Structured output |
| 開発元の推奨 | `mcp-server-dev`の[build-mcp-server/references/server-capabilities.md](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/mcp-server-dev/skills/build-mcp-server/references/server-capabilities.md) | 進捗通知、中断、elicitation、samplingなどを使う設計をする時に読む（本書には条文を置かない） |
| 開発元の推奨 | `mcp-server-dev`の[build-mcp-server/references/auth.md](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/mcp-server-dev/skills/build-mcp-server/references/auth.md)、[build-mcp-server/references/remote-http-scaffold.md](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/mcp-server-dev/skills/build-mcp-server/references/remote-http-scaffold.md) | リモートHTTPサーバーを作る時に読む |
| 開発元の推奨 | `mcp-server-dev`の`build-mcpb`、`build-mcp-app`の各スキルと、[build-mcp-server/references/deploy-cloudflare-workers.md](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/mcp-server-dev/skills/build-mcp-server/references/deploy-cloudflare-workers.md) | MCPB（実行環境を同梱した配布形式）、MCP Apps（チャット画面内に表示するUI）、Cloudflare Workersへの配備を採用するサーバーを作る時に読む（本書には条文を置かない） |
| ホスト固有 | [Anthropic: Introducing advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) | Tool Search Tool、Programmatic Tool Calling、Tool Use Examples、Best practices（Claude Developer PlatformのBeta機能） |
| ホスト固有 | [Claude Code: Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp) | MCP output limits and warnings、Scale with MCP tool search、For MCP server authors |

`mcp-server-dev`は公式マーケットプレイス（`anthropics/claude-plugins-official`）のプラグインである。プラグインを有効化しなくても、Claude Codeが複製したマーケットプレイスの`~/.claude/plugins/marketplaces/claude-plugins-official/plugins/mcp-server-dev/skills/`配下か、[GitHubの同じ配下](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/mcp-server-dev/skills)から読める。
前掲のツール設計資料（`build-mcp-server`）の冒頭にある「Anthropic Directory hard requirements」は、Anthropic Directoryへ提出するサーバーだけの審査条件である。読み書きのツール分割の必須化や説明文での行動指示の禁止などを含むが、本書の推奨には含めない。

## 仕様が要求する契約

- `inputSchema`には、有効なJSON Schemaの`object`型を定義する（`null`は指定できない）。引数を一切取らないツールの場合でも、`{"type": "object", "additionalProperties": false}`を明示することが推奨されている。
- `outputSchema`を宣言しているツールは、そのスキーマに適合する構造化データを`structuredContent`フィールドに入れて返す。
  後方互換性を維持するため、同じJSONを文字列化したテキスト表現も併せて返却することが推奨されている。
- 存在しない未知のツール呼び出しやリクエスト形式自体の不備はJSON-RPCレベルのプロトコルエラーとして返し、入力バリデーションの失敗、外部APIの呼び出し失敗、および業務ロジック上のエラーについては、`isError: true`を設定したツール実行エラーとして返す。その際、モデルが引数を正しく修正して再試行できるように具体的な情報を含める。
- `tools/list`はページネーション処理に対応させる。また、ツールの動的な一覧変更を通知する`listChanged`機能を宣言したサーバーは、一覧が更新された際に`notifications/tools/list_changed`通知を送信することが推奨されている。
- サーバー側ですべての入力値を厳格に検証し、アクセス制御、レートリミット（呼び出し頻度制限）、および出力値のサニタイズ（無害化）を適切に実装する。
- `ToolAnnotations`はクライアントへのヒント情報に過ぎず、サーバー側の認可チェックや入力バリデーションの代わりにはならない。読み取り専用や非破壊といった性質についても、サーバー側の処理で確実に保証する。
- stdioトランスポートを使用する場合、標準出力（stdout）にはMCPメッセージ以外のデータを一切出力してはならず、デバッグログ等はすべて標準エラー出力（stderr）に出力する。外部コマンドを実行するサーバーでは、子プロセスの標準出力をサーバー側で適切に取り込むか、切り離して管理する。
- Streamable HTTPトランスポートを使用する場合は、すべての接続に対して`Origin`ヘッダーを検証する。ローカル環境で起動するサーバーはlocalhostのみをリッスンし、すべての接続を認証することが推奨されている。認可情報や認証トークンを扱う設計をする際は、Security Best Practicesに記載された攻撃手法と対策を照らし合わせて確認する。
- MCPにおいて、Toolsは「モデル自身が呼び出す操作」、Resourcesは「ホスト環境がコンテキストへ取り込む参照情報」、Promptsは「ユーザーが明示的に選択して呼び出す定型プロンプト」という役割分担を持つ。モデルが能動的に選択する必要のない静的な参照資料や定型文を、安易にToolとして公開しないようにする。

## 設計の推奨

以下に挙げる項目は努力目標とする。

### ツールの粒度

関連する一連の操作や、連続して頻繁に呼び出される処理は、目的が明確な少数のツールへまとめる。その目的は、用途の区別や入力パラメーターの分かりやすさを維持しつつ適切に集約することであり、必要なアクセス権限や副作用の境界もツールの境界と一致させる。
列挙値の引数（`action`や`mode`など）を使って複数の操作を1つのツールにまとめる場合は、取り得る値をenumとしてスキーマに定義し、操作ごとに必須となる引数や禁止される引数の条件を、説明文と実行時の入力バリデーションの双方に明記する。操作ごとに必須引数や副作用の性質が大きく異なる場合は、無理にまとめずに別のツールとして分割する。

### ツール注釈

`ToolAnnotations`でツールの副作用をホストへ伝え、全ツールへ表示名`title`を付ける。ホストは注釈を自動承認、確認表示、再試行の判断に使う。

- 環境を変更しないツールに`readOnlyHint: true`を付ける。
- 読み取り専用でないツールのうち、追加だけを行うもの（新規作成、追送など）には`destructiveHint: false`を付け、削除・上書き・中断を伴うものには`destructiveHint: true`を付ける。MCP仕様では、読み取り専用でないツールで`destructiveHint`を省略すると`true`として扱う。
- 同じ引数で繰り返し呼んでも2回目以降が環境へ追加の効果を与えない、読み取り専用でないツールには`idempotentHint: true`を付ける。

### 発見と説明

ツール名、名前空間（複数の外部サービスにまたがる同種の操作には共通のプレフィックスを付ける）、引数名、およびツールの説明文は、他のツールと明確に役割を区別できる言葉遣いで記述する。ツールを選択するために前提となる暗黙の知識（特殊な書式、専門用語、管理対象リソース間の関係性など）を分かりやすく説明し、誤用しやすい入れ子構造のパラメーターや独自の記法については、有効な呼び出し例を添える。
ツールの説明文は、そのツールを今呼ぶべきかどうか、またどのようなパラメーターで呼ぶべきかをモデルが正しく判定するための基準として記述する。複数のツールをどのような手順や順序で組み合わせるべきかといったワークフローの詳細は、説明文ではなく、その作業手順書やスキルのドキュメントに記載する。ツールの説明文はあらゆる呼び出しの前にコンテキストとして読み込まれるためである。

### 単体での利用

基本的なツール呼び出しは、ツールの説明文と引数スキーマを見るだけでパラメーターを決定できる設計にする。server instructionsはクライアントが取り込むかどうかを任意に選択するヒントに過ぎず、またプログラミング言語のdocstringなどクライアントへ送信されないソースコード上の説明はモデルのコンテキストには届かないためである。

- ツールの説明文には、呼び出す目的と、類似のツールと使い分けるための条件を記述し、重要な情報を冒頭に配置する（ホスト環境によって文字数が切り詰められる場合があるため）。
- 引数の説明文とスキーマには、その引数の意味、期待される書式、省略した場合のデフォルト動作、および他の引数との依存関係を明記する。取り得る値が決まっているものはenum型で定義し、必須の引数はスキーマ上で明確に必須指定する。
- server instructionsには、そのサーバーが提供する機能の全体像と、ツールを探して選ぶ際の判断基準を簡潔に記述する。プロジェクト固有の運用ルールや詳細な手順は、必要に応じて外部の補助資料に記載する。

### 応答

応答には、モデルが次の行動を判断するために必要な情報と、後続のツール呼び出しに必要となる識別子（IDなど）を過不足なく含める。取得するデータの範囲や詳細度を引数で指定できるようにし、省略時のデフォルト動作を明確にしておく。

- 応答データを途中で切り詰めた場合は、一部を省略した旨と、続きのデータを取得するための具体的な方法を応答内に必ず含める。省略したことを伝えないまま切り詰めると、モデルは欠落した一部のデータを全体の完全な結果として誤認してしまう。
- ツール実行時のエラーメッセージは、`writing.md`の「読み手別の追加注意点」にあるプログラム出力メッセージの基準に従い、ユーザーやモデルがどの入力をどのように修正すべきかが具体的に分かるように記述する。
- 応答に固定のトークン上限を設ける場合は、実際の利用シナリオの検証結果や、ホスト環境側の制約を根拠として妥当な値を設定する。

### 大規模なツール群

ツールの定義数によるコンテキスト消費や、中間の実行結果のデータ量が問題になるような大規模環境では、ホスト側が提供する動的なツール検索・遅延読み込み機能や、コード実行によるプログラム的なツール呼び出しと中間結果の集約の導入を検討する。その際は、削減できる呼び出しコストと、追加される処理工程のオーバーヘッドを比較して採否を判断する。これらはClaude Developer Platformなどで提供されている機能であり、サーバー側ではツール検索で適切にヒットするようにツール名、説明文、instructionsを分かりやすく整備して対応する。

### 評価

ツールの粒度や説明文の品質は、実際の利用場面を想定した複数ステップの課題を用いて評価し、タスクの完遂率、ツールの誤選択の有無、呼び出し回数、応答データ量、所要時間などを比較しながら調整する。技術記事などに掲載されているベンチマーク測定値は、特定の条件下における一事例として参考にする。

## ホスト固有の条件

利用対象のホスト環境にClaude Codeが含まれる場合は、以下の機能仕様や制限事項を設計条件として考慮する。その他のホストの制限事項については、各ホストの公式一次資料で確認する。

- Claude Codeは、初期起動時に送信される各ツールの説明文および各サーバーのinstructionsを、デフォルト状態で4,096文字に切り詰める。ツール検索によって後からオンデマンドで読み込まれる説明文の上限は16,384文字である。初期読み込み時と検索後の読み込み時のどちらであっても説明が欠落しないよう、すべての説明文を初期送信の上限（4,096文字）以内に収めることが望ましい。2026年10月2日時点の公式資料では、この上限を環境変数`CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH`で変更可能とされているが、新バージョンにおける変数名は未確認である。なお、引数の説明文にも同様の文字数制限が適用されるかどうかは、参照したリリースノートに記載がなく未確定である。バージョンと典拠の記録は、`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/mcp-server-design.md：ホスト固有の条件：2026年10月13日」を参照する。
- ツール検索機能が有効になっている場合、セッション開始時に読み込まれる情報はツール名とserver instructionsのみとなる。そのためinstructionsには、扱う対象のタスク種別、どのような場面でツールを検索すべきか、および提供する主な機能を明記しておく。
- Claude Codeは、`readOnlyHint: true`のツールと`idempotentHint: true`のツールを、接続断などでツールが実行済みの可能性がある失敗の後にも再試行する。`readOnlyHint: true`のツールは並行して呼び出す場合もある。観測した版と再検証手順は、`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/mcp-server-design.md：ホスト固有の条件：2026年10月29日」を参照する。
- MCPツールの出力データが10,000トークンを超えると警告が表示され、デフォルト設定では最大25,000トークンに制限される（環境変数`MAX_MCP_OUTPUT_TOKENS`で変更可能）。仕様上どうしても大きなデータを返却する必要があるツールについては、`tools/list`の`_meta["anthropic/maxResultSizeChars"]`メタデータを設定することで上限を引き上げることができる（画像データを返すツールには適用されない）。

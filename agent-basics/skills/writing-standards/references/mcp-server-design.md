# MCPサーバーの設計指針

MCP（Model Context Protocol）サーバーのツール定義、説明文、応答を設計・実装・変更・レビューする時の基準を定める。
ホストが登録するモデル向けツール（Function hooksなど）にも説明文、引数スキーマ、実行時の入力検証、エラー応答の指針を適用し、応答の形式は各ホストの契約に従う（MCPのJSON-RPC・トランスポート・サーバー通知の規定は対象外）。
仕様の要求は硬い制約、開発元の推奨は努力目標、ホスト固有の機能と制限はそのホストを対象に含める時の条件とし、版で変わる事実は原則3（`agent-basics:writing-standards/references/quality-principles-facts.md`）に従い一次資料で確かめる。
説明文、引数スキーマ、応答はモデルの文脈を消費するため、粒度と応答量は完遂率とツールの選択精度への影響を確かめて選び、両者を保ったままトークンと呼び出し回数を減らす。

## 典拠

MCP仕様は版`2025-11-25`を対象とし、使うSDKが対応する仕様の版を実装の前に確かめる。

- 仕様: [Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)、[Schema Reference](https://modelcontextprotocol.io/specification/2025-11-25/schema)、[Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)、[Prompts](https://modelcontextprotocol.io/specification/2025-11-25/server/prompts)、[Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- 開発元の推奨: [MCP Security Best Practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)、[Anthropic: Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- 公式プラグイン`mcp-server-dev`の`build-mcp-server/references/`: [tool-design](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/mcp-server-dev/skills/build-mcp-server/references/tool-design.md)（説明・注釈・構造化出力）、[server-capabilities](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/mcp-server-dev/skills/build-mcp-server/references/server-capabilities.md)（進捗通知・中断・elicitation・sampling）
- 同じ配下のリモートHTTPサーバーと配備の資料: [auth](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/mcp-server-dev/skills/build-mcp-server/references/auth.md)、[remote-http-scaffold](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/mcp-server-dev/skills/build-mcp-server/references/remote-http-scaffold.md)、[deploy-cloudflare-workers](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/mcp-server-dev/skills/build-mcp-server/references/deploy-cloudflare-workers.md)
- 採用する時だけ読む`mcp-server-dev`のスキル: `build-mcpb`（実行環境を同梱した配布形式）、`build-mcp-app`（チャット画面内に表示するUI）
- ホスト固有: [Anthropic: Introducing advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)（Claude Developer PlatformのBeta機能）、[Claude Code: Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp)

`mcp-server-dev`は公式マーケットプレイス（`anthropics/claude-plugins-official`）のプラグインである。
有効化しなくても`~/.claude/plugins/marketplaces/claude-plugins-official/plugins/mcp-server-dev/skills/`か[GitHubの同じ配下](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/mcp-server-dev/skills)から読める。
`build-mcp-server`の冒頭の「Anthropic Directory hard requirements」はAnthropic Directoryへ提出するサーバーだけの審査条件であり、読み書きのツール分割の必須化や説明文での行動指示の禁止を含めて本書の推奨から除く。
取得した版と再検証の手順は、配布物の外の`docs/development/audit-records-mcp.md`の「agent-basics/skills/writing-standards/references/mcp-server-design.md：典拠：2026年10月2日」にある。
`mcp-server-dev`の資料は同書の「agent-basics/skills/writing-standards/references/mcp-server-design.md：典拠：2026年10月29日」にある。

## 仕様が要求する契約

- `inputSchema`は有効なJSON Schemaの`object`型とし（`null`は不可）、引数の無いツールも`{"type": "object", "additionalProperties": false}`を明示する（仕様の推奨）。
- `outputSchema`を宣言したツールはスキーマに合う構造化データを`structuredContent`に入れて返し、互換のため同じJSONを文字列化したテキストも返す（後者は仕様の推奨）。
- 未知のツールとリクエスト形式の不備はJSON-RPCのプロトコルエラー、入力検証の失敗・外部APIの失敗・業務ロジックのエラーは`isError: true`のツール実行エラーで返し、モデルが引数を直して再試行できる具体的な情報を含める。
- `tools/list`はページネーションに対応し、`listChanged`を宣言したサーバーは一覧の更新時に`notifications/tools/list_changed`を送る（後者は仕様の推奨）。
- サーバー側で全ての入力を厳格に検証し、アクセス制御、呼び出し頻度の制限、出力の無害化を実装する。
- `ToolAnnotations`はクライアントへのヒントで認可と入力検証の代わりにならないため、読み取り専用や非破壊の性質はサーバーの処理で保証する。
- stdioトランスポートでは標準出力をMCPメッセージだけに使い、ログは標準エラー出力へ出し、外部コマンドを実行するサーバーは子プロセスの標準出力を取り込むか切り離す。
- Streamable HTTPでは全ての接続で`Origin`ヘッダーを検証し、ローカルのサーバーはlocalhostだけで待ち受けて全ての接続を認証し（後者は仕様の推奨）、認可情報やトークンを扱う設計はSecurity Best Practicesの攻撃手法と対策で確かめる。
- Toolsはモデルが呼ぶ操作、Resourcesはホストが文脈へ取り込む参照情報、Promptsはユーザーが選んで呼ぶ定型のプロンプトであり、モデルが能動的に選ぶ必要の無い静的な資料や定型文はToolにせずにおく。

## 設計の推奨

本節の項目は努力目標とする。

### ツールの粒度

関連する一連の操作や続けて呼ばれる処理は目的が明確な少数のツールへまとめ、必要な権限と副作用の境界をツールの境界と一致させる。
タスクに要る能力を先に定め、それを満たすツールで始めて不足が分かった時に足す構成も比べ、資料の取得・検証・認可など完遂に要る能力を保つ（ツールの少なさは目的から外す）。
`action`や`mode`などの列挙値の引数で複数の操作をまとめる時は値をenumで定義し、操作ごとの必須・禁止の引数を説明文と実行時の入力検証の両方に書き、必須の引数や副作用が大きく違う操作は別のツールに分ける。

### ツール注釈

`ToolAnnotations`で副作用をホストへ伝え、全てのツールに表示名`title`を付ける（ホストは注釈を自動承認、確認の表示、再試行の判断に使う）。
環境を変えないツールに`readOnlyHint: true`を付ける。
読み取り専用でないツールのうち、追加だけを行うもの（新規作成、追送など）に`destructiveHint: false`、削除・上書き・中断を伴うものに`destructiveHint: true`を付ける（仕様では、読み取り専用でないツールで省略すると`true`として扱う）。
同じ引数で繰り返し呼んでも2回目以降が環境へ追加の効果を与えない、読み取り専用でないツールに`idempotentHint: true`を付ける。

### 発見と説明

ツール名、名前空間（複数の外部サービスの同種の操作に共通の接頭辞）、引数名、説明文は他のツールと役割を区別できる言葉で書き、選択の前提となる暗黙の知識を説明し、誤用しやすい入れ子の引数や独自の記法には呼び出し例を添える。
説明文は今呼ぶべきかとどの引数で呼ぶかの判定基準として書き、複数のツールを組み合わせる手順は、全ての呼び出しの前に読み込まれる説明文から外して作業手順書やスキルの文書に書く。

### 単体での利用

基本の呼び出しは説明文と引数スキーマだけで引数を決められる設計にする（server instructionsはクライアントが取り込むかを選ぶヒントであり、docstringなど送信されない説明はモデルの文脈の外にある）。
説明文には呼ぶ目的と類似のツールとの使い分けの条件を書き、ホストが切り詰める場合に備えて重要な情報を冒頭に置く。
引数の説明とスキーマには意味、書式、省略時の動作、他の引数との依存を書き、取り得る値が決まる引数はenum、必須の引数はスキーマで必須にする。
server instructionsにはサーバーの機能の全体像とツールを探して選ぶ基準を簡潔に書き、プロジェクト固有の運用や詳細な手順は補助の資料へ置く。

### 応答

応答には、モデルが次の行動を判断するのに要る情報と後続の呼び出しに要る識別子を過不足なく含め、取得の範囲と詳細度を引数で指定でき、省略時の動作を明確にする。
成功応答で次の行動を案内する文は後続の全ての場面で成り立つ1つの規則にし、場合分けを並べる時は列挙に無い場面の扱いも書き、同じ行動を定める規範と同じ形にそろえる。
応答を途中で切り詰めた時は、省略した旨と続きを取得する具体的な方法を応答に含める（伝えずに切り詰めると、モデルは一部を全体の結果と誤認する）。
実行時のエラーメッセージは`agent-basics:writing-standards/references/program-messages.md`「出力の基準」に従い、どの入力をどう直すかを具体的に示す。
応答に固定のトークン上限を設ける時は、実際の利用場面の検証結果かホストの制約を根拠に値を決める。

### 大規模なツール群

ツールの定義数や中間結果の量が文脈を圧迫する環境では、ホストの動的なツール検索・遅延読込と、コード実行によるツール呼び出しと中間結果の集約を、削減できる呼び出しの費用と増える工程の費用で比べて採否を決める。
これらはClaude Developer Platformなどの機能であり、サーバー側はツール検索で当たるようにツール名、説明文、instructionsを整える。

### 評価

ツールの粒度と説明文は、実際の利用場面を想定した複数ステップの課題で、完遂率、誤選択、呼び出し回数、応答量、所要時間を比べて調整し、技術記事のベンチマークの値は特定の条件の一事例として扱う。
能力を段階的に足す構成では、初期の能力で完遂できる課題と途中で不足が分かる課題を含め、必要なツールを足して同じ完了条件へ届くかを確かめる。

## ホスト固有の条件

Claude Codeを対象に含める時は次を設計条件にし、他のホストの制限は各ホストの一次資料で確かめる。

- Claude Codeは、初期に送る各ツールの説明文と各サーバーのinstructionsをデフォルトで4,096文字に切り詰め、ツール検索で後から読み込む説明文の上限は16,384文字であるため、全ての説明文を4,096文字以内に収める。
  2026年10月2日時点の公式資料は上限を環境変数`CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH`で変えられるとするが、新しい版での変数名と、引数の説明文への同じ制限の適用は未確認である。
- ツール検索が有効な時、セッション開始時に読み込まれるのはツール名とserver instructionsだけであるため、instructionsに扱うタスクの種別、ツールを検索すべき場面、主な機能を書く。
- `readOnlyHint: true`と`idempotentHint: true`のツールは、接続断などで実行済みの可能性がある失敗の後にも再試行され、`readOnlyHint: true`のツールは並行して呼ばれることもある。
- MCPツールの出力が10,000トークンを超えると警告が出て、デフォルトでは25,000トークンに制限される（環境変数`MAX_MCP_OUTPUT_TOKENS`で変更できる）。
  大きなデータを返す必要があるツールは、`tools/list`の`_meta["anthropic/maxResultSizeChars"]`で上限を引き上げられる（画像を返すツールは対象外）。

説明文の上限の典拠は、`docs/development/audit-records-mcp.md`の「agent-basics/skills/writing-standards/references/mcp-server-design.md：ホスト固有の条件：2026年10月13日」にある。
注釈による再試行の観測は、同書の「agent-basics/skills/writing-standards/references/mcp-server-design.md：ホスト固有の条件：2026年10月29日」にある。

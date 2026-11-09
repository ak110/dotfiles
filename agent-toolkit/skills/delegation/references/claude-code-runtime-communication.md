# Claude Codeでの委譲の実装：実行時能力と通信scope

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` |
| 委譲入力の言語指定を確定する前 | `base-contract-operations.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract-operations.md` |
| `Agent`ツールで起動した担当を継続する前 | `continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/continuation.md` |
| `Agent`ツールで起動した担当を継続する前 | `agent-tool-continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/agent-tool-continuation.md` |

## 実行時能力と通信scope

ツールの公開状況、呼び出し結果、完了通知の配送挙動は、プロバイダー、Claude Codeのバージョン、設定、通信スコープの組み合わせによって変化する。
そのため、実際に観測された組み合わせに対してのみ結果を適用する。

| provider・platform | Claude Code版・設定 | 通信scope | 観測結果 |
| --- | --- | --- | --- |
| Claude Platform on AWS | Sonnet 5、環境変数を`0`に設定 | 同一セッション内の親子・子孫 | `SendMessage`は成功したが、`ListAgents`ツールは公開されず、独立セッション間通信は成立しなかった |
| Claude Codeローカル | 2.1.241、`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`を設定しない | 同一セッション内の親子・子孫 | `SendMessage`と`ListAgents`の公開および呼び出し成功を確認した |

監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/claude-code-runtime.md：実行時能力と通信scope：2026年9月4日」に記載されている。

- `SendMessage`および`ListAgents`の利用可否は、現在のエージェントに実際にツールが公開されており、かつ呼び出しが成功するかどうかで判断する。
  環境変数、プロバイダー、agent定義での許可設定から推測せず、拒否されたツールは利用不能として扱う。
  agent定義の`tools`記述は利用可能な権限の上限を示すにすぎない。
- `ListAgents`が利用できない場合は、保持しているエージェントID、完了通知、成果物の状況、自身が起動してプロセスIDを保持しているプロセスの生存確認（停止を伴う場合は停止成否を含む）を組み合わせて判断する（以下「代替稼働観測」）。
  プロセス名の一致のみを使う推測は、生存や対象プロセスの判定から除く。
- 通常起動されるClaude委譲先に対しては、`agents_server`が`share/rules/`の`rules-subagent.md`と`rules-subagent.claude-code.md`をシステムプロンプトに連結する。
  `Agent`ツールのサブエージェントに対しては、`SubagentStart`フックが同一の2ファイルをコンテキストに追加する。
  通常起動と軽量起動の違いは`references/runtime-routing.md`「実行手段」で定める。
- `agents_server`の委譲先は独立したClaude Agent SDKセッションであり、ターンの終了時に`Stop`イベントが発生する。
  一方、`Agent`ツールのサブエージェントでは`SubagentStop`イベントが発生し、フックの入力に`agent_id`が渡される。
  規範上の役割とフックの種別は別個のものとして扱い、最上位セッション限定の工程では`起動経路`の記録に基づいて最上位かどうかを判定する。

| 通信scope | 宛先候補 | 成立条件 | 成立しない場合 |
| --- | --- | --- | --- |
| 同一セッション内の親子委譲 | `Agent`の起動結果が返すagent ID | 現在のエージェントに`SendMessage`が公開されており、対象IDが同一の委譲ツリー内で有効であること | 後述の「完了通知と中継の実行順」に従う |
| Agent Teams | team member名など機能が定める識別子 | 対象機能が有効であり、必要なツールの公開と呼び出しが成功すること | 本文書の規定に基づき設定を変更せず、同一セッション内の親子委譲へ読み替えることも行わない |
| 独立セッション間通信 | capability discoveryが返す機械可読識別子 | プロバイダーが機能を提供しており、`ListAgents`と`SendMessage`の実際の呼び出しが成功すること | 未対応のプロバイダーでは利用を見送り、代替機構の追加も行わない |

宛先には、起動結果として返されたagent ID、ホストが明示する機械可読な返信アドレス（reply address）、またはホストが値を提示し到達を確認できた`senderTaskId`のみを使用する（`from`、`origin.from`、`name`、`subagent_type`は宛先解決の対象外とする）。
稼働中の独立セッションへ即時の指示を送る場合は、利用可能な宛先一覧と通信手段から到達先を確定して送信し、到達できない場合は依頼元へ報告する。
`atk wi process-loop instruct`は次回起動時に読み込む指示を保存する仕組みであり、即時配送の結果とは区別して扱う。

## 委譲先での発火

- `Stop`は、対象セッション自身のターン終了で発火する。
  `agents_server`が起動した委譲先かどうかは環境変数`AGENT_TOOLKIT_DELEGATED_SESSION`で判定する。

- `SubagentStop`は、`Agent`ツールのサブエージェントのターン終了でのみ発火し、入力の`agent_id`で判別する。
  前述の環境変数は`agents_server`が起動したセッションにのみ付与される。
  `SubagentStop`に登録する処理は、サブエージェントが実行可能な処置に限定する。

区分の詳細は`claude-code-runtime-communication.md`「実行時能力と通信scope」に従う。

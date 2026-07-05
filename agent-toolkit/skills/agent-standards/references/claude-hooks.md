# Claude Code Hook実装ガイドライン

Hookは現状Claude Code固有の概念である。本ファイル全体がClaude Code固有事項を扱う。

## hookスクリプトの基本プロトコル

matcher・出力フィールド・メッセージ標識の記述指示が前提とする最低限の実装規約を示す。
スキーマ詳細は本スキル本体（SKILL.md）の公式ドキュメント節または`plugin-dev:hook-development`スキルを参照する。

- 入出力: stdinに呼び出しペイロードのJSONが渡され、stdoutに応答JSONを出力する
  - exit codeは0で正常完了とする
  - stderr経由の表示はexit 2との組合せで使う代替経路
- `${CLAUDE_PLUGIN_ROOT}`: Claude Codeランタイムが現プラグインのルートディレクトリに置換する組み込み変数
  `hooks.json`の`command`フィールドや、hookスクリプトから他リソースを参照するときに用いる
- 出力フィールドの併用: deny時の`permissionDecisionReason`と`hookSpecificOutput.additionalContext`は
  どちらもコーディングエージェントに届くため一方で十分。
  両方指定するとコーディングエージェント側で重複して表示される可能性があるため、片方に統一する

## matcher設定

`hooks.json`の`PreToolUse` / `PostToolUse`では`matcher`でhookを起動するツールを限定する。
`matcher`はツール名に対する正規表現として評価される。

- 特定ツールのみ: `"Write|Edit|MultiEdit"`のように`|`で列挙する
- 任意ツール: 空文字列`""`または`".*"`で全ツールを対象にする
  `Read`など個別に列挙しないツールも含めて捕捉したい場合に使う
- 個別の早期returnガード: `matcher`を広げた場合、hookスクリプト側で`tool_name`を
  確認し対象外を早期returnすることで処理コストと誤検出を抑える

## 出力フィールドの使い分け

hookやプラグインのJSON出力で利用できるフィールドは、表示先が異なる。
コーディングエージェントに行動を促す必要がある場合は`hookSpecificOutput.additionalContext`を主経路として使い、
`systemMessage`は使わない。
ただしStop/SubagentStopフックで当該ターンの継続を強制する用途
（振り返り誘導等、次のユーザー入力を待たず即時起動が必要な場面）では`decision: "block"`＋`reason`を採用する。
`additionalContext`は次のユーザー入力ターンまで実際の処理を起動しないため、ターン終了前の強制起動には適さない。
`reason`はStop/SubagentStop/PostToolUseで`decision: "block"`を併用する場合の理由欄として位置付ける。

### 共通フィールド（全hookイベント）

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `systemMessage` | ユーザーのみ | 情報通知。コーディングエージェントに届かない |
| `stopReason` | ユーザーのみ | `continue: false`時の終了メッセージ |

### Stop / PostToolUse

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `hookSpecificOutput.additionalContext` | コーディングエージェント | コーディングエージェントへフィードバックを渡す主経路とする（フックエラー扱いとならずターン継続を妨げない） |
| `reason` | コーディングエージェント（`decision: "block"`時のみ） | `decision: "block"`を併用する場合の理由欄として用いる（`block`の挙動はイベント別で、Stop/SubagentStopでは停止を防いでターン継続を強制し、PostToolUseではblock理由を直前のツール結果に添えて返す。挙動の強制が不要であれば`additionalContext`単独で出力する） |

### PreToolUse

| フィールド | allow/ask時 | deny時 |
| --- | --- | --- |
| `permissionDecisionReason` | ユーザーのみ | コーディングエージェント |
| `hookSpecificOutput.additionalContext` | コーディングエージェント | コーディングエージェント |

PreToolUseの`permissionDecision: "allow"`時にコーディングエージェントへ情報を渡すフィールドは
`hookSpecificOutput.additionalContext`に限られる。

組み込みのdeny / askルールはhookの戻り値に関わらず評価される。
`.claude/`配下への書き込み確認等の組み込みaskルールはPreToolUseの`allow`では上書きできない。
確認ダイアログを抑制したい場合はPermissionRequestイベントで`decision.behavior: "allow"`を返す。

### PermissionRequest

確認ダイアログ表示時に発火するイベント。ユーザーに代わって許可 / 拒否を決定するときに使う。
スキーマがPreToolUseと異なり、`hookSpecificOutput`直下に`decision`オブジェクトを置く。
`hookEventName`は`"PermissionRequest"`を指定する。

| フィールド | 用途 |
| --- | --- |
| `decision.behavior` | `"allow"`で許可、`"deny"`で拒否 |
| `decision.updatedInput` | `allow`時のみ。ツール入力を改変する |
| `decision.updatedPermissions` | `allow`時のみ。許可ルールの追加など |
| `decision.message` | `deny`時のみ。コーディングエージェントへ拒否理由を伝える |
| `decision.interrupt` | `deny`時のみ。`true`でClaudeを停止 |

組み込みdenyルールは`allow`でも上書きできないが、確認ダイアログ（ask相当）はスキップできる。
`matcher`はツール名で評価する（`Bash` / `Edit|Write`等）。
入力payloadは`tool_name` / `tool_input`に加え、`permission_suggestions`配列を受け取る。

## Stop/SubagentStopフックの再帰呼び出し対策

Stop/SubagentStopフックがターン終了をブロックすると、コーディングエージェントは新たな応答を生成し、
その応答に対して同じフックが再び発火する。
出力経路は用途別に使い分ける。
次のユーザー入力ターンまで待ってよい誘導は`hookSpecificOutput.additionalContext`を主に用いる。
当該ターン継続を強制する誘導（振り返りスキル起動等）は`decision: "block"`＋`reason`を主に用いる。
Stop/SubagentStopいずれのイベントも両経路の採用可能性がある。
判定条件が変化しない場合、この再帰はClaude Codeの既定上限（連続8回）まで繰り返される。
上限到達時は警告とともにフックの判定を上書きしてターンが終了する。
上限値は`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`環境変数で変更できる。

再帰の起点を断つため、Stop/SubagentStopフックは入力payloadの`stop_hook_active`が真の場合、
判定処理を行わず無条件で`decision: "approve"`を返す。
`stop_hook_active`は直前の同フック呼び出しが当該ターンの終了を一度ブロックしたことを示す。
本対策は出力経路によらず両イベントで必須とする。

ターン終了の言語的判定（完了文言・質問・待機表明の判別）をフック側のコードで
正規表現等により行うと誤検知が生じやすい。
コーディングエージェントへの誘導文の先頭に判定基準を事前チェックとして埋め込み、
基準を満たさない場合は誘導内容に従わずターンを終了する設計を推奨する。

## メッセージの記述言語

コーディングエージェントに直接渡る出力（`reason` / `additionalContext` / exit 2のstderr）は
英語で記述する。全文章を日本語で書く原則に対する意図的例外である。
ユーザーが目を通す必要のないシステム出力であることを、英語表記によって明示する。

ただし、hookメッセージ中で原本ファイル（`01-agent.md`・`CLAUDE.md`等）の章名・節名・キーワードを参照する場合は、
原本表記をそのまま引用する。
英訳した参照名（例:「言語表現」章を`language-style chapter`と訳すなど）は
原本の章名変更時に追従漏れの起点となるため使わない。
hookメッセージの目的はコーディングエージェントが参照先を特定できることであり、hookメッセージ全体の厳密な英語化ではない。

`systemMessage`はコーディングエージェントに届かずユーザー画面表示のみのため、本方針の対象外とする。
ユーザーが直接読む通知は対話型UI向け文体（敬体・日本語）で記述する。

## コーディングエージェント宛てメッセージの標識

コーディングエージェントに直接渡る出力（`reason` / `additionalContext` / exit 2のstderr）には、
自動生成であることを明示するプレフィックスとサフィックスを付ける。
hookの出力はユーザー発言と同じ形で会話コンテキストに注入されるため、指示として誤認されないよう二重の標識を設ける。

### プレフィックス

`[auto-generated]` または `[auto-generated: <plugin>/<hook>]` を行頭に置く。
プラグイン識別子やフック種別のみの内部名（例: `[agent-toolkit]`）はコーディングエージェントの観点では
「自動生成である」という意味論が伝わらないため使わない。

警告などの種別タグ（例: `[warn]`）は、警告であることを区別したい場合に並置する（任意）。

```text
[auto-generated: agent-toolkit/pretooluse] blocked: ...
[auto-generated: agent-toolkit/pretooluse][warn] detected ...
```

### サフィックス

メッセージ本文の末尾に以下の英文を一行追加する。

```text
(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)
```

コーディングエージェントに対して「妥当性を文脈と照らして判断してから行動する」ことを明示する。
`systemMessage` / `stopReason` などコーディングエージェントに届かないフィールドや、
`permissionDecision: "allow"`で追加メッセージを持たない経路には付けない。

### ヘルパー関数

hookスクリプトごとに次のようなヘルパーを持ち、発出箇所から呼び出す（重複実装は許容）。

```python
_MESSAGE_PREFIX = "[auto-generated: myplugin/myhook]"
_MESSAGE_SUFFIX = "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"

def _llm_notice(body: str) -> str:
    return f"{_MESSAGE_PREFIX} {body} {_MESSAGE_SUFFIX}"
```

## セッション状態ファイル

PreToolUseとPostToolUseの間で情報を共有する場合、セッション単位の状態ファイルを使う。
hookは1呼び出しごとに独立プロセスとして起動するため、メモリー上の変数では情報の引き継ぎができない。

- パス規則: `{tempdir}/{plugin名など}-{session_id}.json`
  `tempfile.gettempdir()`と`payload["session_id"]`から組み立てる
- 形式: 単一のJSONオブジェクト。フラグ名はsnake_caseで統一する
- 書き込み: PostToolUseで観測したイベント（テスト実行・スキル呼び出しなど）をフラグとして記録する
- 読み取り: PreToolUseで判定材料として参照する（例: テスト未実行警告・スキル先行呼び出し催促）
- 破損・不在時: 空辞書として扱い、安全側の判定にフォールバックする
- フラグの増加に伴い参照関係が把握しづらくなるため、プラグインごとに用途・書き込み元・
  読み取り元の対応表をドキュメント化することを推奨する
- `agent-toolkit`プラグイン自身が定義するフラグの一覧はSSOTを
  本スキル本体（SKILL.md）「セッション状態フラグ」節に置き、本ファイルへ再掲しない

### 並行書き込みの排他制御

Claude Codeは並列ツール呼び出しでhookを同時発火するため、複数プロセスから同一の状態ファイルへ書き込みが競合する。

- 状態ファイルへの書き込みは排他ロック付きの`update_state`ヘルパー経由のみで実施する
  `update_state`はロック取得・読み取り・変更・アトミック書き込みを単一トランザクションとして実行する
- 直接`write_state`するAPIは公開しない。「`read_state`→操作→直接書き込み」のパターンは禁止する
- ロックはPython標準ライブラリのみでPOSIX（`fcntl.flock`）とWindows（`msvcrt.locking`）の両環境を扱う
- 書き込みは同一ディレクトリの一時ファイルへ出力後`os.replace`でアトミックに反映する
  書き込み中断時は旧ファイル内容が保持される
- 並行書き込みの回帰テスト（`threading.Thread`による別キー同時書き込みで全キー保持を検証）を必ず備える

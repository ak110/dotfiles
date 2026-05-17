# Claude Code Hook実装ガイドライン

Hookは現状Claude Code固有の概念である。本ファイル全体がClaude Code固有事項を扱う。

## hookスクリプトの基本プロトコル

matcher・出力フィールド・メッセージ標識の記述指示が前提とする最低限の実装規約を示す。
スキーマ詳細は本スキル本体（SKILL.md）の公式ドキュメント節または`plugin-dev:hook-development`スキルを参照する。

- 入出力: stdinに呼び出しペイロードのJSONが渡され、stdoutに応答JSONを出力する
  - exit codeは0で正常完了とする
  - stderr経由の表示はexit 2との組合せで使う代替経路
- `${CLAUDE_PLUGIN_ROOT}`: Claude Codeランタイムが現プラグインのルートディレクトリに置換する組み込み変数。
  `hooks.json`の`command`フィールドや、hookスクリプトから他リソースを参照するときに用いる
- 出力フィールドの併用: deny時の`permissionDecisionReason`と`hookSpecificOutput.additionalContext`は
  どちらもコーディングエージェントに届くため一方で十分。
  両方指定するとコーディングエージェント側で重複して表示される可能性があるため、片方に統一する

## matcher設定

`hooks.json`の`PreToolUse` / `PostToolUse`では`matcher`でhookを起動するツールを限定する。
`matcher`はツール名に対する正規表現として評価される。

- 特定ツールのみ: `"Write|Edit|MultiEdit"`のように`|`で列挙する
- 任意ツール: 空文字列`""`または`".*"`で全ツールを対象にする。
  `Read`など個別に列挙しないツールも含めて捕捉したい場合に使う
- 個別の早期returnガード: `matcher`を広げた場合、hookスクリプト側で`tool_name`を
  確認し対象外を早期returnすることで処理コストと誤検出を抑える

## 出力フィールドの使い分け

hookやプラグインのJSON出力で利用できるフィールドは、表示先が異なる。
コーディングエージェントに行動を促す必要がある場合は`reason`または`additionalContext`を使い、`systemMessage`は使わない。

### 共通フィールド（全hookイベント）

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `systemMessage` | ユーザーのみ | 情報通知。コーディングエージェントに届かない |
| `stopReason` | ユーザーのみ | `continue: false`時の終了メッセージ |

### Stop / PostToolUse

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `reason` | コーディングエージェント（block時） | block理由をコーディングエージェントに提示する |
| `hookSpecificOutput.additionalContext` | コーディングエージェント | 補足情報をコーディングエージェントに提示する |

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

## メッセージの記述言語

コーディングエージェントに直接渡る出力（`reason` / `additionalContext` / exit 2のstderr）は
英語で記述する（プロジェクト方針が無い場合）。
ユーザーの作業コンテキスト（日本語の思考の流れ）にシステム出力が注入される際、英語であればシステムメッセージと
コーディングエージェントが一目で区別できるため。

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

- パス規則: `{tempdir}/{plugin名など}-{session_id}.json`。
  `tempfile.gettempdir()`と`payload["session_id"]`から組み立てる
- 形式: 単一のJSONオブジェクト。フラグ名はsnake_caseで統一する
- 書き込み: PostToolUseで観測したイベント（テスト実行・スキル呼び出しなど）をフラグとして記録する
- 読み取り: PreToolUseで判定材料として参照する（例: テスト未実行警告・スキル先行呼び出し催促）
- 破損・不在時: 空辞書として扱い、安全側の判定にフォールバックする
- フラグの増加に伴い参照関係が把握しづらくなるため、プラグインごとに用途・書き込み元・
  読み取り元の対応表をドキュメント化することを推奨する

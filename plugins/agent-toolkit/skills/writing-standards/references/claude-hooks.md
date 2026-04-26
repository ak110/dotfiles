# Claude Code Hook実装ガイドライン

## matcher設定

`hooks.json`の`PreToolUse` / `PostToolUse`では`matcher`でhookを起動するツールを絞り込む。
`matcher`はツール名に対する正規表現として評価される。

- 特定ツールのみ: `"Write|Edit|MultiEdit"`のように`|`で列挙する
- 任意ツール: 空文字列`""`または`".*"`で全ツールを対象にする
 （`Read`など個別に列挙しないツールも含めて捕捉したい場合に使う）
- 個別の早期returnガード: `matcher`を広げた場合、hookスクリプト側で`tool_name`を
  確認し対象外を早期returnすることで処理コストと誤検出を抑える

## 出力フィールドの使い分け

hookやプラグインのJSON出力で使えるフィールドは、表示先が異なる。
LLMに行動を促す必要がある場合は`reason`または`additionalContext`を使い、`systemMessage`は使わない。

**共通フィールド（全hookイベント）:**

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `systemMessage` | ユーザーのみ | 情報通知。LLMに届かない |
| `stopReason` | ユーザーのみ | `continue: false`時の終了メッセージ |

**Stop / PostToolUse:**

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `reason` | LLM（block時） | block理由をLLMに提示する |
| `hookSpecificOutput.additionalContext` | LLM | 補足情報をLLMに提示する |

**PreToolUse:**

| フィールド | allow/ask時 | deny時 |
| --- | --- | --- |
| `permissionDecisionReason` | ユーザーのみ | LLM |
| `hookSpecificOutput.additionalContext` | LLM | LLM |

PreToolUseの`permissionDecision: "allow"`でLLMに情報を渡せる唯一のフィールドは`hookSpecificOutput.additionalContext`。

## メッセージの記述言語

hookやプラグインが出力するメッセージは英語で記述する（プロジェクト方針が無い場合）。
ユーザーの作業コンテキスト（日本語の思考の流れ）にシステム出力が注入される際、英語であればシステムメッセージと
一目で区別できるため。

## LLM宛てメッセージの標識

LLMに直接渡る出力（`reason` / `additionalContext` / exit 2のstderr）には、自動生成であることを明示する
プレフィックスとサフィックスを付ける。
hookの出力はユーザー発言と同じ形で会話コンテキストに注入されるため、指示として誤認されないよう二重の標識を設ける。

### プレフィックス

`[auto-generated]` または `[auto-generated: <plugin>/<hook>]` を行頭に置く。
プラグイン識別子やフック種別のみの内部名（例: `[agent-toolkit]`）はLLMから見て「自動生成である」という
意味論が伝わらないため使わない。

警告などの種別タグ（例: `[warn]`）は並置する。

```text
[auto-generated: agent-toolkit/pretooluse] blocked: ...
[auto-generated: agent-toolkit/pretooluse][warn] detected ...
```

### サフィックス

メッセージ本文の末尾に以下の英文を一行追加する。

```text
(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)
```

LLMに対して「妥当性を文脈と照らして判断してから行動する」ことを明示する。
`systemMessage` / `stopReason` などLLMに届かないフィールドや、`permissionDecision: "allow"` で
追加メッセージを持たない経路には付けない。

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
hookは1呼び出しごとに独立プロセスとして起動するため、メモリー上の変数では情報を引き継げない。

- パス規則: `{tempdir}/{plugin名など}-{session_id}.json`
 （`tempfile.gettempdir()`と`payload["session_id"]`から組み立てる）
- 形式: 単一のJSONオブジェクト。フラグ名はsnake_caseで統一する
- 書き込み: PostToolUseで観測したイベント（テスト実行・スキル呼び出しなど）をフラグとして記録する
- 読み取り: PreToolUseで判定材料として参照する（例: テスト未実行警告・スキル先行呼び出し催促）
- 破損・不在時: 空辞書として扱い、安全側の判定にフォールバックする
- フラグの増加に伴い参照関係が把握しづらくなるため、プラグインごとに用途・書き込み元・
  読み取り元の対応表をドキュメント化することを推奨する

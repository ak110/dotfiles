# セッション記録の構造と集計：Claude Codeの記録

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## Claude Codeの記録

会話の要素は、レコードの`type`が`user`または`assistant`であるものだけで判定する。
Claude Code 2.1.301に存在する`api-request-blob`はAPIリクエストの控え（コピー）であり、同様の形式を持つ`message.content`内にツール入力・実行結果・発話が含まれるため、形式の一致だけでカウントすると二重計上になってしまう。
生のレコード本文や構造を調査する照会では、この控えも含めて表示される。
監査記録は`docs/development/audit-records.md`の「セッション記録のAPI要求の写し：2026年10月18日」に記載されている。

- ディレクトリ構造の深さは、メイン記録である深さ2（`<project>/<session-uuid>.jsonl`）と、サブエージェント記録である深さ4（`<session-uuid>/subagents/agent-<agentId>.jsonl`）のみである。
  孫エージェントの記録も含め、すべて祖先セッション直下の`subagents/`にフラットに配置される。
- `<project>`のディレクトリ名は、セッションを開始した作業ディレクトリの絶対パスに含まれる英数字以外の文字を`-`に置き換えたものである。
  そのため、worktreeなど別ディレクトリで開始したセッションは、元の複製元とは異なる`<project>`配下に置かれる。
  Claude Codeのメインエージェントが自身の記録を参照する場合は、SessionStart通知に示されたtranscriptの絶対パスをそのまま使う。
  更新時刻の順序や現在の作業ディレクトリ（cwd）から推測して特定しない（`rules-main.claude-code.md`「自身の会話の記録」を参照）。
  特定済みのセッションIDから別の記録を読み取る場合は、`atk run-script session-review-evidence -- --claude-session-id <ID>`へIDを渡す。
  ファイルパスそのものが必要な場合は、`find ~/.claude/projects -maxdepth 2 -name '<session-uuid>.jsonl'`で1件に確定させる。
  取得したパスは必ずクォートして渡す（クォートしないままグロブ展開されて複数語に分かれると、後続コマンドが標準入力を待ち続けてしまうため）。
- 親エージェントと子エージェントの記録の間で同一の事象が重複して現れるため、件数を確定する際は重複を除外する。
  ただし、集計対象を深さ2のメイン記録だけに限定すると、サブエージェント内部のイベント（ツール呼び出しなど）を取りこぼしてしまうため、対象を限定する場合はその適用条件を明記する。
- サブエージェントの起動回数を数える典拠としては、`subagents/*.meta.json`内の`agentType`、`description`、`toolUseId`、`spawnDepth`、`parentAgentId`を用いる。
  `parentAgentId`は`spawnDepth`が2以上の記録にしか現れないため、階層構造を復元する際は`spawnDepth`も併用する。
- 特定の性質を持つ行を抽出する処理では、その値がツール自身の出力なのか、ツールが読み取った外部ファイルの内容なのかを、レコードの構造から区別する（読み取ったファイルの本文も実行結果と同じ領域に格納されるため、単なる書式の一致だけで判定すると、ファイル内の例示記述まで実行時イベントとして数えてしまう）。
- 文面を含む`thinking`ブロックには、ツール呼び出しの合間に挟まれる地の文をサーバー側で要約したもの（ナレーション要約）が含まれており、エージェントが書いた原文でも拡張思考の全文でもない。
  そのため、その形状や長さ、使用単語などは、原文の性質を測定する集計対象から除外する。
  監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/review-session/references/session-records.md：Claude Codeの記録：2026年10月6日」に記載されている。

## スキル起動の判定

Claude Codeの記録では、`type`が`user`であるレコードの`message.content`に含まれる`tool_result`要素の`content`が、`Launching skill: <スキル名>`と完全一致しているかどうかで判定する。
呼び出し要求側は`type`が`assistant`の`tool_use`要素であり、`name`が`Skill`、`input.skill`にスキル名が設定される。
スキル名の部分一致で検索すると、利用可能なスキル一覧を保持する`attachment`型レコードに多数誤検出してしまう。

Codexの記録には、スキルの起動を一意に示す専用のレコードが存在しない（`SKILL.md`の読み込みとして実行されるため、スキル名は一覧表示や文脈の再掲、パスを含むコマンドの入出力など随所に現れる）。
そのためCodexでは、`type`が`response_item`、`payload.type`が`message`、`payload.role`が`user`であるレコードの`content`に含まれる`input_text`要素の`text`に、スキルの起動を依頼したプロンプト本文が含まれていることのみを判定条件とする。
なお、最初のuserレコードは実行環境が挿入する前置き文である。
このプロンプト本文を経由しない起動については、判定不能として扱う。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/review-session/references/session-records.md：スキル起動の判定：2026年9月10日」に記載されている。

## 暗号化された値

以下の値は提供元によって暗号化されて返されるデータであり、提供元の公式ドキュメントにおいて解釈をしないよう明記されている。
そのため、事実確認や判別に際しては値の有無と所在のみを確認対象とし、デコード処理や内部構造の解析しない。

- Claude Codeの記録に含まれる`thinking`ブロックの`signature`、および`redacted_thinking`ブロックの`data`
- Codexの記録において`payload.type`が`reasoning`であるレコードの`encrypted_content`

監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/review-session/references/session-records.md：暗号化された値：2026年10月7日」に記載されている。

## 進捗更新の判別

公式ドキュメント「Thinking」の「Progress updates between tool calls」節の基準に基づき、ツール呼び出しの間に行われる進捗更新（公式ドキュメント上の「progress update」、Claude Code内部での名称は`narration`）を判別する。
APIリクエストの`display`パラメーターが`"updates"`に設定された応答のうち、本文が空ではない`thinking`ブロックを持つものを進捗更新とみなす。
ただし、記録ファイル自体には`display`の値が直接記録されていないため、設定内容やホスト側の送信条件からその値を確認できた範囲のみを判別済みとし、残りは判別不能として件数を分けて計上する。
この節の観測結果についても、前節と同じ監査記録に記載されている。

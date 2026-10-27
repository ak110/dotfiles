# Claude Code・Codex Hook実装ガイドライン

hookの実装は各ホストの公式仕様に適合させ、Claude Code固有の上限値や出力契約にはホスト名を明記する。
遮断や警告の判定基準、出力フィールド、StopおよびSubagentStopフック、メッセージの言語と標識など、主題ごとの詳細な規定と参照タイミングは、`agent-basics:writing-standards`の`SKILL.md`にある読込表で確認する。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `host-official-references.md#「公式リファレンス（Claude Code）」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/host-official-references.md#「公式リファレンス（Claude Code）」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-hooks-block-warn.md#「遮断・警告フックの成立条件」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/claude-hooks-block-warn.md#「遮断・警告フックの成立条件」` |

## hookスクリプトの基本プロトコル

公式の一次資料は、Claude Code向けが<https://code.claude.com/docs/ja/hooks.md>（取得方法は`host-official-references.md`「公式リファレンス（Claude Code）」を参照）、Codex向けが<https://learn.chatgpt.com/docs/hooks>である。入力ペイロード（`transcript_path`、`last_assistant_message`、`agent_transcript_path`、`hookSpecificOutput`など）と出力形式はこれらに基づいて確定し、参照した節名を計画ファイルの実装者向け欄に引用する（努力目標）。

- 入出力: 標準入力（stdin）から呼び出しペイロードのJSONを受け取り、標準出力（stdout）へホストごとの仕様に沿った応答JSONを出力する。正常終了時は終了コード0を返す。
- 失敗時の扱い（Claude Code v2.1.295以降のcommand・HTTP hook）: hookが判定結果を返す前に失敗した場合に、対象の操作を続行させるか遮断するかを`onFailure`設定（指定なし時は`"continue"`、遮断時は`"block"`）で指定する。ここでの失敗とは、起動不能、タイムアウト、0や2以外の終了コード、JSON解析や検証の失敗、HTTP接続エラーや2xx以外のレスポンスなど、ホスト側が検知した障害を指す。これはhookが正常に判定し遮断や警告を返す動作とは区別して設定する。
  `"block"`は、hookが動作不良を起こしている際にも対象操作を確実に阻止すべきかどうかに基づいて選択する。hook停止中にすべての対象操作が中断するコストと、チェックなしで通過させるリスクの比較は`claude-hooks-block-warn.md`「遮断・警告フックの成立条件」に従う。
  なお、hookスクリプト自身が例外を捕捉して終了コード0で終了した場合はホストが失敗を検知できないため、`onFailure`による遮断は機能しない。また、`Stop`、`SubagentStop`、`TaskCompleted`、`TeammateIdle`、および`async`や`asyncRewake`を指定したバックグラウンドのcommand hookではこの設定は効果を持たない。詳細な仕様と制約は公式の[Block the action when a hook fails](https://code.claude.com/docs/en/hooks#block-the-action-when-a-hook-fails)を参照する。
- `${CLAUDE_PLUGIN_ROOT}`: Claude Codeでは現在のプラグインのルートパスへ自動置換される。Codexで置換されるのはプラグインhookの`command`内だけである。そのため、スキル本文から実行するコマンドでは、読み込んだSKILL.mdの絶対パスから特定したプラグインルートを使用する。
- Codexの信頼確認: プラグイン同梱のhookであっても、定義を変更した後は`/hooks`コマンドで内容を確認して信頼設定を行う。信頼設定が完了するまで、Codexはそのhookの実行をスキップする。
- 呼び出し主体の判別: サブエージェントとメイン会話セッションの区別には、共通入力の`agent_id`を使用する。`transcript_path`はサブエージェント内で発火した場合でもメインセッションの記録を指すため、サブエージェント自身の記録を参照するには`SubagentStop`の`agent_transcript_path`を用いる。監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月2日」にある。
- 同一ターンの本文の可視性: `PreToolUse`が発火した時点では、同じアシスタントターン内のテキストブロックはまだ`transcript_path`のJSONLファイルに書き込まれていない。同一ターンの本文を入力として判定する処理は、`PreToolUse`以外のイベントに配置する。また、思考ブロックやツール呼び出しのみで構成されるターンも記録されるため、直近の本文を調べる判定では、テキストブロックを含まないターンを除外して過去へ遡る。監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月6日」にある。
- 発火条件: hookの追加を計画に含める際は、対象イベントの発火条件を計画の実装者向け欄に明記する。`PostToolUse`はツール実行が成功したときにのみ発火する。`PostToolUseFailure`は実行が開始されたツールが失敗した際に発火して`additionalContext`を受け付けるが、入力検証による拒否や実行前の権限拒否では発火しない。`PermissionDenied`はauto modeによる拒否でのみ発火する（deny規則への一致、ユーザーによる手動拒否、`PreToolUse`による遮断では発火しない）。出力は`hookSpecificOutput.retry`のみを受け付けるため、回復手順の案内本文は別の手段で提供する。監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年9月8日」にある。`PostToolUseFailure`と`PermissionDenied`の発火条件の記録は、同じ文書の「agent-basics/skills/writing-standards/references/claude-hooks.md：hookスクリプトの基本プロトコル：2026年10月7日」にある。
- Codexの`PostToolUse`は、`tool_response`として任意のJSON値を渡す。シェル実行では終了コードが含まれず出力文字列のみが渡されるため、状態を記録する条件からコマンドの成否判定を外す。一方、`apply_patch`はパッチ適用が成功したときにのみ発火するため、ファイル編集が成功した後の状態記録に利用できる。
- Bashコマンドを対象とするhookの判定方式、および状態に応じて出力の有無を切り替えるhookの計画への記載方法は、`claude-hooks-block-warn.md`「遮断・警告フックの成立条件」で定める。
- 起動パスの互換性維持: hook定義の`command`が参照しているスクリプトを改名、移動、または削除する場合は、旧パスに新しいエントリーポイントを呼び出す互換スクリプトを残す。実行中のセッションは起動時に読み込んだ旧パスを保持し続けるため、実体が存在しないとツール呼び出しがhookの実行エラーによって拒否される。なお、共通エントリーポイントが未知のサブコマンドを終了コード0で通過させる設計であれば、サブコマンド自体の削除に互換スクリプトは不要である。互換スクリプトのdocstringには役割と撤去条件を明記し、旧定義を読み込んだセッションがすべて終了したことを確認できた場合にのみ撤去する（配布物の場合は、新エントリーポイントを含むバージョンのリリース後のバージョン更新以降を最低限の基準とする）。
- イベントごとのエントリーポイント集約: 同じイベントに対する複数の判定処理は、新しいhookを個別に登録せず既存のエントリーポイントに統合する（努力目標。個別に登録するとホストがその件数分プロセスを起動するため、実行時間や画面のログ行数が増加する）。エントリーポイント内では判定単位ごとに例外を隔離して1つの応答JSONに集約し、`matcher`による絞り込み処理は各判定ロジック内の早期リターンとして実装する。

## matcher設定

ツール名に基づいて`matcher`を評価するイベントは、`PreToolUse`、`PostToolUse`、`PostToolUseFailure`、`PermissionRequest`、`PermissionDenied`の5つである。
`"*"`、空文字列、およびキー自体の省略はすべてのツールに一致する。英数字、`_`、`-`、空白、`,`、`|`のみで構成される文字列は、`|`または`,`で区切られたツール名の完全一致として評価される。それ以外の文字を含む文字列は、先頭と末尾を固定しないJavaScriptの正規表現として評価される。
すべてのツールに一致させる新規登録では明示的に`"*"`を記述し（努力目標）、ツール名で評価しないイベントでは`matcher`キーを省略する。
一次資料は<https://code.claude.com/docs/en/hooks.md>の`Matcher patterns`節である。監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks.md：matcher設定：2026年9月4日」にある。

- `matcher`の対象を広く設定した場合は、スクリプト側で`tool_name`を確認して対象外のツールを早期リターンする。
- Codexに対しては、ツール名を明示して対象を絞り込んだmatcherを提供する。Claude Code向けの全ツール一致のmatcherをそのまま渡すと、入力仕様を確認していないツールでもハンドラーが意図せず起動してしまうためである。

## Codexの編集ツール入力

Codexの`apply_patch`はmatcher上で`Edit`や`Write`の別名として一致する。ただし、入力payloadの`tool_name`は`apply_patch`のままであり、変更内容は`tool_input.command`内に`*** Begin Patch`から`*** End Patch`までの構文で渡される。
1回の呼び出しの中に、`Add File`、`Update File`、`Delete File`、`Move to`、および`@@`区切りの差分（hunk）が複数ファイル分まとめて含まれる。

ホスト間の差異をチェック処理本体へ持ち込まないようにするため、編集入力は以下の2層に正規化して扱う。

- 操作記録: 入力データのみから操作種別、対象パス、順序付きの編集断片を抽出する。ファイルを読み取らないため、`PostToolUse`からも利用できる。
- 変更前後像: 対象ファイルの現在内容に対して操作記録を適用し、変更後の全体像を構築する。これは`PreToolUse`でのみ使用する。

相対パスはpayloadに含まれる`cwd`を起点として解決する。パッチ構文を解釈できない場合はhook側で遮断せず、妥当性の判定を`apply_patch`本体の処理に委ねる。

ホストの判定は、Codexがターン単位のhookに付与する空でない文字列の`turn_id`、および`UserPromptSubmit`でCodexのみが渡す`prompt`と`model`の組み合わせに基づいて行い、ツール名から推測することは避ける。

複数ファイルや複数チェックによる警告は、1つの`hookSpecificOutput.additionalContext`に統合して返す。標準出力全体が1つのJSONとして解析されるためである。

Codexのシェル実行および統合実行（`exec_command`）はmatcher上で`Bash`に一致し、`tool_input.command`に実行コマンド文字列が渡される。一次資料は<https://learn.chatgpt.com/docs/hooks>のTool coverageとし、matcherに指定する名前は`Bash`に限定する。

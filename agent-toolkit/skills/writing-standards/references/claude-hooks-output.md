# Hookの出力フィールド

本書はhookの出力フィールドの選択方針と、`PermissionRequest`・`UserPromptSubmit`の出力の扱いを定める。

## 出力フィールドの使い分け

本節は出力先の選択方針だけを定め、各フィールドのスキーマとイベント別の対応可否は`claude-hooks.md`「hookスクリプトの基本プロトコル」が挙げる公式ドキュメントに従う。

イベントごとの出力契約は`agent-toolkit/agent_toolkit/_hooks/output_contract.py`が定める。
同ファイルは公式のHooksリファレンスが定める契約をJSON Schemaで保持する。
`agent-toolkit/agent_toolkit/_hooks/output_contract_test.py`が登録済みの全hookの出力がその契約に合致するか確かめる。
フックを追加または変更する場合は、その契約とテストを同じ変更単位で更新する。

Claude Codeが表示する`Stop hook error: JSON validation failed`はプロンプト型hookの評価器が
モデルの応答をJSONとして解析できなかった場合に出る。この表示が出るのはプロンプト型hookの評価器に限る。
`/goal`はセッションの範囲で有効なプロンプト型Stop hookを登録する。
`/goal`を設定したセッションでは、その表示がコマンド型hookの出力形式とは無関係に現れる。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks-output.md：出力フィールドの使い分け：2026年9月4日」にある。

PreToolUse・PostToolUse・UserPromptSubmitでコーディングエージェントに行動を促す場合は`hookSpecificOutput.additionalContext`を第一の通知手段として使う。各フィールドがターン継続を強制するかは後掲の表に従う。
stderr出力は`exit 2`のblockと組み合わせる場合のみに限定する。
`systemMessage`はユーザーの判断・操作に影響する情報通知に限って使う。決定論的で失敗しない自動補正の発動など、反復発動してユーザーの対応を要しない事象は通知の対象に含めない。
Stop/SubagentStopでそのターン継続を強制する用途は、エラーとして遮断する場合（振り返り誘導等）に`decision: "block"`＋`reason`を、フックの想定内の助言に`hookSpecificOutput.additionalContext`を採用する。
永続ログはstderr出力ではなく`_hooks.stop_gate.append_stop_log`等の専用APIに集約する。

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `hookSpecificOutput.additionalContext` | コーディングエージェント | 行動を促す主要な通知手段。PreToolUse・PostToolUse・UserPromptSubmitでは継続を強制せず、Stop/SubagentStopでは継続を強制する |
| `hookSpecificOutput.updatedToolOutput` | コーディングエージェント | PostToolUseでモデルへ渡すツール結果の置き換え。同じイベントの複数のhookは元の出力に並行して動き、最後に返った置き換えが採られる |
| `reason` | コーディングエージェント（`decision: "block"`時のみ） | blockを併用する場合の理由欄 |
| `permissionDecisionReason` | deny時はコーディングエージェント、allow/ask時はユーザーのみ | PreToolUseの決定理由 |
| `systemMessage`・`stopReason` | ユーザーのみ | 情報通知と`continue: false`時の終了メッセージ |
| `decision.*` | PermissionRequest専用 | 許可・拒否の決定。`hookSpecificOutput`直下に置く |

deny時の`permissionDecisionReason`と`hookSpecificOutput.additionalContext`はどちらもコーディングエージェントに届くため、重複表示を避けて片方に統一する（努力目標。両方へ書くと同じ本文を2回読ませるため）。

`decision: "block"`の挙動はイベント別に異なる。
Stop/SubagentStopでは停止を防いでターン継続を強制し、PostToolUseではblock理由を直前のツール結果に添えて返す。
PreToolUse・PostToolUse・UserPromptSubmitで挙動の強制が不要であれば`additionalContext`単独で出力する。

- block通知は`_hooks.notice`のblock専用整形関数（`block_formatter`）で生成し、解消手段の`fix`を渡す。`warn`通知も同モジュールの整形関数へ解消手段を`fix`として渡す。いずれも`fix`を省くか空文字列または空白文字だけにすると`ValueError`となり、整形関数は本文の後へ`次の操作: <fix>`の行を置く
- block・warn本文の構成はこれらの整形関数に限る（独自の整形関数では解消手段の欠落を機械的に検出できなくなるため）。解消手段は本文へ混ぜず`fix`へ渡す。文面の基準は`writing.md`「読み手別の追加注意点」のプログラムが出力するメッセージの項目に従う
- `notice`区分は行動の指示そのものを本文とする定型の配送であり、`fix`を任意とする

警告専用のPreToolUse出力は`hookSpecificOutput.additionalContext`だけを返し、`permissionDecision`を省略する。
決定を省略すると通常の権限フローが適用され、警告表示とは独立に許可プロンプトが出る。

コーディングエージェントの出力を対象とするチェックは、適用境界を書き込み先ではなく読み手で定める。
ユーザーが直接読む本文を出力する操作は、ファイルへ書き込まない操作であっても、編集入力と同じ本文チェックへ通す。
Claude Codeでは`AskUserQuestion`の質問本文・見出し・選択肢の各欄と`ExitPlanMode`の計画本文が該当する。
本境界の対象は、ユーザーが直接読む本文に限る。

組み込みのdeny / askルールはhookの戻り値に関わらず評価される。
`.claude/`配下への書き込み確認等の組み込みaskルールはPreToolUseの`allow`では上書きできない。
確認ダイアログを抑制したい場合はPermissionRequestイベントで`decision.behavior: "allow"`を返す。

`updatedInput`による入力書き換えの効果は入力値の変更までとし、確認ダイアログの発生はそのまま残る。
ダイアログを伴う値を拒否する必要がある場合は書き換えでなくブロックで扱う。
`agents_server`では`engine`に応じたバックエンドをMCPサーバーが選択する。承認、ユーザー入力、認証更新および一覧操作は公開せず、実行中turnの明示的な中断だけをsession単位の`kill`として公開する。
PreToolUseは`send_message`・`kill`の保存済みsessionと、`<役割名>.subagent.md`の実行命令を持つ起動を確認する。
`start`の`delegate`・`explore`・`write`へ引用の外で`<役割名>.subagent.md`の手順を実行する命令を渡した場合は、タスク起動へ直すよう遮断する。
`Agent`・`Task`の実行命令では、1行目の正式な命令と宣言済み入力以外の行を遮断する。
文書の読解・引用・比較の対象への参照と、引用に載せた実行命令の例は通す。
参照だけから用途を確定できない場合も通し、会話の意味を推定する遮断・警告を加えない。
`start`の入力妥当性検証（`mode`ごとの欠落と混在を含む）は実行基盤へ委ね、入力の実行権限値はそのまま渡す。
`wait`は新しいturnを開始せず既存sessionの現在の状態を返すだけで、誤った作業ディレクトリでの実行を招かないため、PreToolUseのチェック対象へ含めず通過させる。
PostToolUseは成功した開始ツール`start`（全`mode`。統合前の旧名で記録された開始も含む）のcwdと、`wait`・`send_message`・`kill`のsession状態を記録する。

### PermissionRequest

確認ダイアログ表示時に発火するイベント。ユーザーに代わって許可 / 拒否を決定するときに使う。
スキーマがPreToolUseと異なり、`hookSpecificOutput`直下に`decision`オブジェクトを置く。
`hookEventName`は`"PermissionRequest"`を指定する。

組み込みdenyルールは`allow`でも上書きできないが、確認ダイアログ（ask相当）はスキップできる。

`Read(*.key)`のようにディレクトリを含まないdeny規則は、gitignore構文に従って任意の深さで一致するため、ディレクトリを走査する読み取りコマンドを一律に確認ダイアログの対象へ変える。
このダイアログは本イベントの`allow`でも抑止できないため、保護する対象はそのファイルを持つリポジトリの設定へ、走査対象を巻き込まない具体的なパスで書く。

`matcher`はツール名で評価する（`Bash` / `Edit|Write`等）。
入力payloadは`tool_name` / `tool_input`に加え、`permission_suggestions`配列を受け取る。

### UserPromptSubmit

`hookSpecificOutput.additionalContext`をそのターンの応答生成前にユーザー発話へ前置注入する誘導に使う。
本イベントが受理する出力は`hookSpecificOutput.additionalContext`までとする。
Claude Codeでは`decision`と独立に注入可能で、stdoutプレーン出力もコンテキストへ追加される。
Codexでは`hookSpecificOutput.hookEventName`を`UserPromptSubmit`とし、`additionalContext`を返す。

Claude CodeのUserPromptSubmit payloadから現在のセッション名を取得する入力値は得られない。
計画ファイルを扱うhookは、同一セッションでまだ出力していない場合だけ計画ファイル名のstemを
`sessionTitle`へ一度だけ出力する。
`sessionTitle`と`additionalContext`が同じ呼び出しで必要な場合は、`hookSpecificOutput`へ両方を含む1つのJSONを返す。
この契約はClaude Code専用とし、`sessionTitle`の出力先をClaude Code payloadに限る。

ユーザー発話への応答契約を注入するhookは、注記の本文の種別で注入の頻度を分ける。

- エージェント向け文書の読込だけを求める注記は、通常発話の受領ごとに返す。本文へ判定手順を書かず、その要件を定義する基準文書の所在だけを示す。
  ユーザーの介入のたびにその規範の所在を想起させるため、経過時間によらず毎回返す。
  本文を1文へ抑えることで、受領ごとに文脈へ載る量を一定に保つ
- 判定手順を本文へ持つ注記は、同一セッションの直前の通常発話からの経過時間を状態として保持し、閾値以上経過した通常発話にだけ返す。
  初回の通常発話はその注記の対象から除くが、経過時間の基準となる時刻を記録する

初回を含む通常発話ではその時刻を更新する。注記の記録と注入の対象は通常発話とし、ユーザーが入力したスラッシュコマンドで始まる発話を含める。ハーネスが挿入した通知と機械注入のターンは対象に含めない。
成立した注記が複数ある場合は、1つの`additionalContext`へ結合して返す。
この注入はホストを問わず有効であり、Codex payloadでも同じ`additionalContext`を返す。

ユーザーが書いた文は、Claude Codeの`AskUserQuestion`の回答（提示した`label`と一致しない`answers`の値、`response`、`annotations`の`notes`）としてPostToolUseにも届く。
ユーザー発話の内容を判定材料とする注記は両hookで同じ定義の本文を使い、`AskUserQuestion`の自由記述へは、1回の質問につき1回しか生じないため経過時間の閾値を適用せず毎回返す。

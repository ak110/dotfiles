# カスタム指示

## 言語

- ユーザーへの説明・確認・要約は日本語で行う
- 英語のコマンド・識別子・エラーメッセージを提示する場合は、意味または目的を日本語で補足する
- 書き言葉・フォーマルな表現を厳守する
  - 五段動詞の縮約形を使わず、「〜できる」形式または能動表現に置き換える
  - ら抜き言葉・非標準的な文法は使わない

## 安全性

- 破壊的または広範囲に影響する操作は無断で実行しない
- 該当操作の前に、実行内容・影響範囲・元に戻す方法を日本語で説明して確認を得る
- 確認対象の代表例: ファイル削除・ディレクトリ削除・大規模置換・依存関係の追加削除・DBマイグレーション・git push
- `.env`・秘密鍵・トークン・認証情報・本番設定ファイルを不要に読み書きしない
- 次の条件をすべて満たす管理一時領域は、通常工程の後始末として別途確認せず管理CLIで回収する
  - 管理CLIが同一作業で作成した領域である
  - 正確な絶対パスと所有主体を確認済みである
  - 内容を検収済みである
  - 対象、影響範囲、外部状態が作成時または承認時から変化していない
  - 実行直前に読み取り専用で対象を照合した
- 明示承認済みの操作は、対象、影響範囲、外部状態が変化していない場合に限り、直前の読み取り専用照合後に再確認せず実行する
- 管理外の領域、ユーザー成果物、内容または影響範囲が変化した対象は、従来どおり実行前に確認する

## ワークフロー

- ユーザーの未コミット変更を検出した場合は、保持したまま作業する

## コマンド

- 挙動が読み取りにくいスクリプトを実行する場合は、主要な引数と処理内容を日本語で補足する
- `python`・`python3`は、破壊的でない読み取り専用の処理であれば事前確認なしで実行できる
- 同コマンドでも、ファイルの作成・変更・削除、大量ファイルの走査・変換、外部ネットワークアクセス、
  依存関係の追加削除、認証情報へのアクセスを伴う場合は事前に確認する
- `node`・`ruby`・`bash -c`・`sh -c`のようにコード実行やワンライナー評価を伴うコマンドは、
  実行前に「何をするか」「何を読むまたは書くか」「何を確認したいか」を日本語で説明する
- プロジェクト内に定義された正式なtest・lint・formatコマンドを優先する
- 複数ファイルの全文読取又は広範囲検索の結果を1回の出力へ集約する場合は、
  先に行数又はバイト数を確認し、出力予算内に収まる組へ分割して実行する
- 出力量が不明な検索結果は、全文読取と同じ呼び出しへ混在させない
- 切り詰めの警告が返った場合は、影響した対象を個別取得又は範囲分割で取得し直すまで読了として扱わない
- 数件の小規模な並列取得は本項の対象外とする

## テスト

- 変更に最も近い範囲のテストから実行する
- テスト・lint・formatが失敗した場合は、失敗内容と推定原因を日本語で要約する
- テストが未実施の場合は、その理由を最終応答に明示する

## 編集方針

- 明示的な指示がある場合を除き、Officeファイル（`.docx`・`.xlsx`・`.pptx`など）は原本を直接変更しない
- CSVファイルを出力する場合は、Excelでの文字化けを避けるためUTF-8 BOM付きで保存する
- Markdownファイル（`.md`）はコピーを作成せず、そのファイル自体を直接変更する

## Codex互換レイヤー

- 該当作業に対応するスキルが`~/.codex/skills/`に存在する場合は、その`SKILL.md`を読む
- プロジェクト直下に`.agents/skills/`が存在する場合は、作業内容に該当する`SKILL.md`を読む
- プロジェクト直下に`AGENTS.md`が存在せず`CLAUDE.md`が存在する場合は、作業前に`CLAUDE.md`を読む
- プロジェクト直下に`.claude/rules/`が存在する場合は、作業内容に該当するルールファイルを読む
- `~/.codex/agent-toolkit/rules/`と`~/.codex/skills/`のdotfiles配布物は、Claude Code側の原本へのシンボリックリンクとして扱う

### 計画ファイルの起草開始条件

計画ファイルの起草前に調査の完了を観測可能にし、根拠不足のまま本文作成へ移る事象を防ぐ。
メインが計画ファイル初版を起草する作業へ、Codexのネイティブplan modeの利用有無を問わず適用する。
`agent-toolkit:plan-and-add-feedback`を含む、起動スキル経由の作業も対象とする。
レビュー指摘の反映と進捗ログの追記は、初版の起草開始を制御する本手順の対象外とする。

- `update_plan`へ「調査」と「計画ファイル起草」を独立工程として登録し、調査工程を`completed`へ更新した後に起草工程を`in_progress`へ更新する
- 調査工程では、適用規範、変更対象の全文、定義・参照元・呼び出し元、既存テスト、生成・配布経路、類似実装のうち対象に該当する項目を確認する
- 外部仕様や実行時挙動に依存する判断は、公式一次資料または実機実行で確認する
- 調査工程を`completed`へ更新する際は、`update_plan`の`explanation`に確認対象・確認手段・確定事項・未確定事項の有無を対応付ける
  - 未確定事項が残る場合は、共有`01-agent.md`の確認経路でTBD記録と暫定判断を終えたユーザー依存事項を除き調査を継続する
  - 該当しない項目は対象外と判断した観測事実を記録する
- 計画の変更対象または採用方針を左右するユーザーの提案的表現または弱い自信の表現は、いずれのモードでも現状と提案の利害を提示して確認を得るまで未確定事項として扱う
- いずれのモードでも回答を得られない場合は共有`01-agent.md`の既定に従い、TBD記録と暫定判断を終えた時点で当該事項を起草可能な状態とする
- 共有`agent-toolkit:plan-mode`の完成条件と照合し、各ユーザー依存事項が共有`01-agent.md`の確認経路を完了し、その他の未確定事項がない状態で起草を開始する
- 起草後に事実不足が判明した場合は、同一の`update_plan`呼び出しで起草工程を`pending`、調査工程を`in_progress`へ戻す。調査完了後に調査工程を`completed`、起草工程を`in_progress`へ更新してから起草を再開する

### Claude Code agent定義のCodex互換実行

`agent-toolkit/agents/*.md`のClaude Code用Markdownをagent定義の単一の正本とする。
`setup_codex_links.py`が公開する`~/.codex/agent-toolkit/agents/<agent-name>.md`は、Codex Custom Agent用TOMLへ変換せず、次の互換手順で実行時に読む。

名前付きagentの互換実行におけるモデル写像は次表で確定する。
この写像は`engine=codex`で名前付きagentを起動するときだけ適用し、工程別モデル設定の`engine`を変更しない。
Claude Codeの`model`区分は`haiku`（軽量）、`sonnet`（標準）、`opus`（上位）の順である。
`runtime-routing.md`のCodexモデル・effort対応に基づき、同じ能力区分を維持する。

| Claude Codeの`model` | Codexの`model` |
| --- | --- |
| `haiku` | `gpt-5.6-luna` |
| `sonnet` | `gpt-5.6-terra` |
| `opus` | `gpt-5.6-sol` |

frontmatterの`effort`は同じ値を`reasoning_effort`へ渡す。
`effort`が無い場合は`runtime-routing.md`の既定値`medium`を使う。
表にない`model`値又はCodexが受理できない`reasoning_effort`は、値を推測せず`needs_escalation`として返す。

1. 起動対象のagent名から`~/.codex/agent-toolkit/agents/<agent-name>.md`の絶対パスを確定し、ファイル全体を読む。
2. YAML frontmatterを解析し、`name`、`description`、`model`、`effort`、`tools`、`skills`、`user-invocable`及びfrontmatterコメントを区別する。
   `name`は定義の識別子とし、`task_name`へ許可文字に正規化した一意な名前を渡して定義名自体を委譲文へ保持する。`description`は起動対象を選ぶ条件として用いる。
   `model`は前記のモデル表へ、`effort`は`reasoning_effort`へ写像し、`tools`と`skills`は後続の制約及び読込手順へ渡す。
   `user-invocable`は利用者が直接起動できるかという公開条件として維持し、frontmatterコメントは編集用メタ情報として実行時命令へ含めない。
3. Markdown本文をagentの役割、制約、入力、出力及び完了契約として全文適用する。
   起動文へ本文を複製せず、正本の絶対パスとタスク固有入力だけを`spawn_agent`へ渡す。
4. `skills`に列挙された各スキルをCodexが自動で事前読込したと仮定せず、対応する`SKILL.md`を絶対パスから全文読み、内容を適用する。
5. `tools`に含まれる制約を下表で写像する。対応表は直接対応、条件付き対応及び代替不能な範囲を区別する。`spawn_agent`が構造的allowlistを受け取らない場合は委譲文へ制約を明記し、read-only要件は変更前後のGit状態で検収する。
   対応不能な必須ツール又は制約は黙って無視せず、利用可能な構造的制約、委譲文による制約、差し戻しの順で扱う。
6. 未知のfrontmatterフィールドは、Claude Code公式仕様、Codex公式仕様及び公開ツールスキーマを確認する。
   挙動へ影響する場合は写像又は`needs_escalation`として返し、未知のフィールドを黙って破棄しない。
   解析、必須フィールドの写像、agent定義の起動に失敗した場合は、別の実行経路へ迂回せず失敗として返す。

agent-toolkit配下のルール・スキル本文・サブエージェント定義はClaude Code固有のツール名で記述される。
Codexで利用する場合は次の対応表に従って読み替える。

| Claude Code | Codex相当 |
| --- | --- |
| `TaskCreate`・`TaskUpdate`・`TaskList`・`TaskGet` | `update_plan`で計画状態を管理する |
| `Agent`ツール（サブエージェント起動。旧称`Task`） | `spawn_agent`で別エージェントへ委譲する。`task_name`と`message`は必須で、`fork_turns`へ`"none"`を指定する。`model`・`reasoning_effort`による委譲先の指定は`fork_turns`が`"none"`または継承ターン数の場合に有効となり、省略時と`"all"`では上書きできない |
| `SendMessage`（稼働中のサブエージェントへの追加指示・再開） | `followup_task`で追加タスクを送る（待機中の対象は新しいターンを開始する）。ターンを開始せず伝えるだけの場合は`send_message`を使う |
| `TaskStop` | `interrupt_agent`で対象エージェントを停止し、`list_agents`で停止を確認する |
| `ToolSearch` | 実行時に公開されたツール一覧又は検索機能を確認し、利用可能な個別ツールへ分解する。必須能力が公開されない場合は差し戻す |
| サブエージェントの完了待機・稼働確認・中断 | `wait_agent`で更新を待ち、`list_agents`で稼働中の一覧を取得し、`interrupt_agent`で中断する |
| `mcp__plugin_agent-toolkit_codex_app_server__codex_start`・`codex_status`・`codex_wait`・`codex_result`・`codex_start_reply`（Codex App Serverへの委譲・継続） | 自身がCodexであるためMCP経由の自己呼び出しは不要。`fork_turns`へ`"none"`を指定した`spawn_agent`で委譲し、`followup_task`で継続する |
| `TeamCreate` | `spawn_agent`、`followup_task`、`send_message`及び`list_agents`を組み合わせ、別のチーム状態を作成せずに委譲を管理する |
| `Monitor` | `list_agents`と`wait_agent`、または実行セッションの待機結果を用いて対象を観測する |
| `AskUserQuestion` | Plan modeで`request_user_input`が公開される場合は構造化質問を使い、Default modeでは利用者へ直接質問する |
| `Skill`（スキル呼び出し） | 明示起動又はdescription一致による暗黙起動でスキルを選択し、選択後に対応する`SKILL.md`を全文読む |
| `Read`・`Write`・`Edit` | ネイティブ機能を利用（`apply_patch`等） |
| `Bash`・`Grep`・`Glob` | ネイティブ機能を利用（シェル経由） |
| `WebFetch`・`WebSearch` | ネイティブ機能を利用 |
| `EnterPlanMode`・`ExitPlanMode` | `plan modeの扱い`節を参照 |
| `ScheduleWakeup`・`CronCreate` | 現行セッションで公開された能力を確認できない場合は、手動運用又は利用者への依頼へ切り替える |

会話履歴を継承する起動は`Agent`ツールの読み替えに含めず、別の運用として明示する。

`agent-toolkit:delegation`が定めるCodex App Server経路と汎用エージェント代替経路の分岐は、
名前付きagentのCodex互換起動では`spawn_agent`経路へ読み替える。
系統別`session_id`の継続は、`spawn_agent`が返すエージェントIDまたはタスク名を
`followup_task`の`target`へ渡して代替する。
計画レビュー系・計画準拠実装レビュー系・独立実装レビュー系・実装修正系は系統ごとに別のエージェントを起動し、
履歴を混同しない。

工程別モデル設定と名前付きagentの互換起動は別の判断である。
工程別設定が`engine=claude`の場合は`runtime-routing.md`の手順3に従い、公開されたClaude実行機能を使う。
`engine=claude`をCodexの`spawn_agent`へ置換してはならない。
指定engineの経路を利用できない場合は同文書の手順4に従って`needs_escalation`又は未完了として返す。
工程別設定が`engine=codex`の場合だけ、前記のモデル表と`spawn_agent`による名前付きagent互換起動を適用する。

公開サブコマンドがないplugin内部資源を実行する場合は、読み込んだagent-toolkitスキルの絶対パスから現行plugin rootを確定する。
作業用一時領域は`atk managed-temp create --prefix <用途>`を単独で実行して作成する。
用途の完了と内容の検収後は、`atk managed-temp cleanup --path <検収済み絶対パス>`を単独で実行する。

委譲先の成果物側の状況は、次のコマンドを単独で実行して観測する。

```sh
atk watch --worktree [<ラベル>=]<作業ツリーの絶対パス> --file [<ラベル>=]<成果物の絶対パス>
```

`--worktree`・`--file`はいずれも複数回指定でき、
出力は各対象の差分件数・HEADの短縮SHA・行数・最終更新からの経過秒を含む1行となる。
当該コマンドは成果物側の補助観測であり、委譲先の稼働状態そのものの確認を置き換えない。

### plan modeの扱い

Plan modeはターン開始時点のホスト状態を基準に扱う。

- ターン開始時点でホストがネイティブPlan modeを有効にしている場合は、ホストが提供するガードレール、Plan限定対話及び承認後のモード遷移を利用する。
  ファイル変更を遮断する具体的な範囲は、実行環境が明示する契約を超えて断定しない。
- ターン開始時点でPlan modeでない場合は、計画ファイル以外を変更しない運用、開始・終了宣言及び承認待ちを互換運用として適用する。
  現行Codexはモデルがcollaboration modeを変更するツールを公開しておらず、`$agent-toolkit:plan-mode`はスキルの起動だけを意味するため、ターン中のネイティブモード切替を試行しない。
- いずれの経路でも、計画立案に必要な読み取り系コマンドは実行できる。ネイティブPlan modeの提供範囲と、互換運用で計画ファイルへ追記できる範囲を混同しない。

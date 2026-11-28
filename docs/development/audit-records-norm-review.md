# 監査記録: 規範の点検

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## 規範の点検記録

点検を実施するたびに、実施日の小見出しで記録を加える。

### 2026年11月11日

2026年11月11日、公式ガイド「Prompting Claude Fable 5.2」を基準に、`agent-toolkit:atk-mode/playbooks/refine-prompt.md`の「文書の点検」を規範全体へ1回実施した。
この記録は`.claude/skills/edit-dotfiles-norms/references/model-host-audit.md`が点検の要否の判定に使う。

- 基準にしたモデルID: `claude-fable-5-2`
- 基準にしたガイド: [Prompting Claude Fable 5.2](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-2)と、`agent-basics/skills/writing-standards/references/agent-documents-additions.md`「見直す契機と点検観点」
- ホストごとの確認済みの版: Claude Code 2.1.322、codex-cli 0.167.1
- 対象の版: 2026年11月10日のdevelopからmasterへのマージcommit（`6ec5050fa`）の本文
- 対象: 572ファイル
  - `agent-toolkit/share/rules/`の常時規範8
  - `agent-toolkit/skills/`と`agent-basics/skills/`のスキル本体21、references 481、生成物19
  - エージェントへ返す通知文を定義するhookのソース41
  - `agents_server`の利用説明2
- 1段階目（観点ごとの検索と一致箇所の読み取り）の判定: 提案あり40、該当なし513、生成物19、本文を読まないと判定できないファイル0
- 2段階目（通読）: 常時規範8ファイルに限る
- 提案: 63件（1段階目41件、2段階目22件）で、採否は未決定である
- 点検対象の規範への編集: なし
- 提案一覧: 全63件の対象、引用、根拠をそろえた原稿は再取得できておらず、本記録の集計だけでは個々の提案の採否を再判定できない（2026年11月14日の再確認では、保存先の一時領域が存在しなかった）
  - 保存された実行・レビュー記録には集計と原稿の一部があるが、本文の永続化時の省略がある
  - 元の一時領域がなくなった操作と理由は未確認である

評価を担当したのは`gpt-6.1-sol`の担当であり、Claude Fable 5.2自身による評価と、追加指示の無いモデルとの比較は実施していない。
提案が挙げる行動への影響は、この点検では検証していない。

### 2026年11月14日

GPT-6.1 Solを対象として、常時規範、両プラグインの資料、hook通知の生成元を点検した。
対象版は2.258.6の公開後の本文（`b6af4c7d9`）に固定した。

- 基準モデル: `gpt-6.1-sol`
- 一次資料: [GPT-6.1 Solモデル情報](https://developers.openai.com/api/docs/models/gpt-6.1-sol)と[Using GPT-6](https://developers.openai.com/api/docs/guides/latest-model#gpt-61-sol)
- 確認したホスト版: Claude Code 2.1.323、codex-cli 0.167.2
- 対象: 633ファイル
  - 常時・役割規範8、agent-basics資料225、agent-toolkit資料305
  - Python hook候補ソース86、Function hooks候補ソース9
- 一次走査: 提案あり91、該当なし482、独立通読で判断する対象60
  - 各対象の検索結果と該当文脈を記録した
- 独立通読: 常時規範8と疑義のある対象を合わせた123ファイル
  - 一次走査と別の担当が20・68・35ファイルを読んだ
- 実行モデル: 一次走査はSolのlow・mediumでの未完遂を回復し、highで全対象の結果を取得した
  - 独立通読の待機結果はSolで、物理記録のidentityは未確認である
- 提案: 一次走査では91ファイルに候補があり、独立通読では16件を挙げた
  - 両段階の候補は重なるため加算しない
  - 採否は未決定である
- 対象規範への編集: 公式資料への参照追加と本記録だけ
  - 監査提案の適用は含めない
- 提案一覧と対象別判定の保存先: [Sol監査の判定と提案](audit-sol-2026-11-14.json)
  - 対象633件の判定、一次提案91ファイル、独立通読の提案16件と根拠を保存した
  - 作業用記録の保持期限後も、この記録と同じ版から取得できる

Using GPT-6の挙動説明はAstraの観測であり、Solでの実証と区別した。
Solによる変更前・変更後・追加指示なしの比較は実施していない。
提案が挙げる費用や挙動への効果は未試行の仮説である。
hook候補の件数とソース容量は、実際に配送される通知本文の量を表さない。

再点検では同じ対象版から両プラグインのスキル・references、常時規範、hook生成元を列挙する。
観点別検索で該当文脈を確認し、常時規範と判定を保留した対象を別担当が通読する。
対象別の判定集合と列挙集合を照合し、提案の採否と実モデルでの効果検証は別工程へ渡す。

### 2026年11月15日

Claude Opus 5.5の公式ガイドと、Claude Code 2.1.322から2.1.323、codex-cli 0.167.1から0.167.2への変更を基準に点検した。
対象版は`147fbe3fdbcb5eafcbf6f6311f9419228eb5b3dd`に固定した。

- 基準モデル: `claude-opus-5-5`
- 一次資料: [Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)、[Claude Code変更履歴](https://raw.githubusercontent.com/anthropics/claude-code/refs/heads/main/CHANGELOG.md)、[Codex v0.167.2](https://github.com/openai/codex/releases/tag/rust-v0.167.2)
- 実行ホストで観測した版: Claude Code 2.1.325、codex-cli 0.169.0
  - 点検する変更は上記の旧版間に限定した。
- 一次走査: 規範文書524と通知生成側97の計621ファイル
  - 固定文字列14語による候補はR 270、P 10、一致なしN 341だった。
  - 独立担当が候補280ファイルの一致文脈を確認し、一般的な権限・所有権・復元や部分一致を区別した。
- 独立通読: 常時規範8を含む38ファイル
  - 最初の18と追加20を同じ担当が通読した。
  - 一次Nの全文通読と全621本文の通読は実施していない。
- 評価モデル: `gpt-6.1-sol`、最終返却のeffortはmedium
  - Opus 5.5自身の評価と、実ホスト機能の試行は実施していない。
- 提案3件: effort入力の説明の不整合、モデル間のeffort校正、Codex常時配送の条件付き操作例の分離
  - 提案の採否と規範への適用は行っていない。
  - 変更前・変更後・文書なしの比較、費用と品質の改善は未試行である。
- 提案一覧と全対象の判定: [Opus 5.5・ホスト更新の点検結果](audit-opus55-hosts-2026-11-15.json)。

Code Modeはopt-inであり、通常運用へ一律適用する提案はしていない。
hookのheartbeatは処理の終了を保証しない。
Codexの対象変更はVertex AIへのツール更新の送信方式であり、全ホストのツール入力契約の変更とは扱わなかった。
検索語の一致なしは概念の不在を保証せず、通知生成側の容量は実際の配送量と区別する。

### 2026年11月22日: Codex 0.168.0〜0.169.0

[0.168.0の公式変更履歴](https://github.com/openai/codex/releases/tag/rust-v0.168.0)と[0.169.0の公式変更履歴](https://github.com/openai/codex/releases/tag/rust-v0.169.0)を使い、対象版 `e61ed45d422a346cfc4baff78061d0e19a10bc29` の規範と通知の生成元を点検した。
`host-codex.md`「資料の読取」「Codexホスト契約の適用」、委譲の復旧、作業契約と圧縮復元の説明を、変更点と比べた。

| 対象 | 判定 |
| --- | --- |
| 後続・途中入力 | 起動と送信、ホストによる配送、終了を別々に確認する既存手順を維持する |
| 出力の保持とCode Modeの既定切り詰め | 保存の有無と本文の受信範囲は別に確認する。部分取得と分割再取得の手順を維持する |
| 親専用の能力、準備済みMCP | 子のツールと権限は子側の実行環境で確認する。準備済みの呼び出しも同じ扱いとする |
| `features.multi_agent` | 検索した配布設定・規範に旧キーの指定は見つからなかった。既存設定を案内する時の無効化キーは `agents.enabled` |
| 未信頼の作業ディレクトリ | 提案: `codex exec` ではsandbox設定とプロジェクトの信頼を区別する。未信頼時はプロジェクト設定とMCPが無効で、`--trust` は信頼を保存する操作 |
| 承認済み計画の引き渡し、中断と取消 | ホストが失敗時に元のセッションを保つ修正と、作業契約・引き継ぎ記録の検収は別。中断結果は操作した識別子で確認する |

提案の採否と規範の変更は行っていない。
評価は指定された変更と関連文書・生成元の固定文字列検索に限る。
実バイナリの起動、hookの配送、app-serverクライアント別の操作、全規範の通読は未点検とした。

### 2026年11月22日: Claude Code 2.1.323から2.1.326

[Claude Code公式変更履歴](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)の2.1.325と2.1.326を、`e253bcfcdb45310e229553e59f5650876768b62f` の関連規範と照合した。
2.1.324には独立した節がなく、版固有の変更は未確認とした。

| 観点 | 照合した本文と判定 |
| --- | --- |
| 権限とsandbox | 読み取り専用Git操作やsandbox内Bashの既定が変わっても、`concepts-auto-mode.md` が記録する許可・拒否の境界は変わらない。許可済みとみなす操作の追加は不要。 |
| 複数のPreToolUse hook | `claude-hooks.md` は入力変更と拒否を分けている。提案: 2.1.326以降だけを対象に、あるhookが更新した入力を次のhookで検証する時の実機確認を追加する。出力を並行に置き換えるPostToolUseの規約とは混ぜない。 |
| Hook失敗時の診断 | 2.1.326の表示改善はhookが完了した証拠ではない。`claude-hooks.md`「出力フィールドの使い分け」の拒否・警告の区分を変える必要はない。 |
| スキルとプラグインの読み込み | 未導入・未公開と、Code Modeのインラインコマンドが許可待ちになる場合を区別する。`host-claude-code.md`「資料の取得」の実在するrootからの解決は保つ。起動済み委譲先での新規スキルの有効化は未試行。 |
| Code Mode・MCPの入出力 | 2.1.326で正確なツール名の探索や並行結果の対応に修正が入った。`host-claude-code.md`「Code Modeでのツールの探索と実行結果の回収」が求める公開スキーマ、元の呼び出しと結果の照合は引き続き必要。 |
| 委譲先の出力と再開 | 2.1.325の出力量・上限到達後の応答の修正を、`host-claude-code.md`「結果の受け取り方」と比較した。通知だけでは作業完了を示さない。圧縮後の復元は `work-contract.md` の既存手順を保つ。 |
| WorktreeCreate | `process-wi-lane-resources.md` はhookが返した作業ツリーのパスを所有登録へ保存する。2.1.325での起動契機・実パスの修正はこの契約を変えない。 |
| HTTP hook | `claude-hooks.md`「hookスクリプトの基本プロトコル」は通信・実行失敗の `onFailure` と判定結果の拒否を区別する。履歴の設定修正から組織設定の実効値までは判断しない。 |
| Stop hookと目標 | 2.1.326でStop hook実行中の目標変更が失われる不具合が修正された。提案: 目標を変更した時は、ターンの終了通知でなく、再開後に保持された目標を確認する。 |
| 管理設定のPreToolUse拒否 | 拒否をテレメトリーへ記録する修正は、`claude-hooks.md`「出力フィールドの使い分け」の拒否を送る条件を変えない。観測時は管理設定側の拒否も区別する。 |
| 2.1.325の相対パス・複合コマンドの許可 | `host-claude-code.md`「Code Modeでのツールの探索と実行結果の回収」は入力が拒否された後に迂回しない。複合コマンドの判定修正後も、構成要素を許可済みと扱う案は採らない。相対パス・MCPとの組み合わせの実機結果は未確認。 |
| 2.1.326のLinuxでの終了処理 | sandbox内のコマンドを止めた後の空のlockファイルの修正は、`subagent.md`「作業ツリーと認証情報の扱い」の終了確認を不要にしない。呼び出し元で所有するプロセスと結果を確認する。 |

点検範囲は変更履歴と上記の規範に限る。
無人・予約実行のスキル起動、管理設定によるauto mode、WorktreeCreateの実パス、並行MCP結果の配送、委譲先の出力サイズと上限後の再回答は、実環境では未確認。
提案の採否と規範本文の改訂は行っていない。

### 2026年11月28日: Claude Code 2.1.327〜2.1.332とCodex 0.170.0〜0.172.0

点検済みのバージョンはClaude Code 2.1.332、codex-cli 0.172.0。
Linuxの実行環境では `claude --version` と `codex --version` でバージョン番号だけを観測した。
以下25行は[Claude Code公式変更履歴](https://raw.githubusercontent.com/anthropics/claude-code/refs/heads/main/CHANGELOG.md)とCodexの公式リリース ([0.170.0](https://github.com/openai/codex/releases/tag/rust-v0.170.0)、[0.171.0](https://github.com/openai/codex/releases/tag/rust-v0.171.0)、[0.172.0](https://github.com/openai/codex/releases/tag/rust-v0.172.0)) を `faf0adcbed2d117fcd241a6c36e5b5e1fa901560` の関連規範・実装と照合した結果であり、各機能を実行した記録ではない。

`claude-hooks.md`に、MCP hookの失敗時の遮断、Code ModeのPreToolUseの処理失敗、フックの遮断・失敗に伴う長いログと通常出力の別、Desktopでの応答記録待ち、`PermissionDenied`の再試行の条件を明記した。
`host-claude-code.md`は子孫の不在確認に要る一覧の全ページ取得を、`host-codex.md`は任意のstdio MCPの一覧キャッシュと初回呼び出しまでの未起動の区別を補った。
ホストの実機入力、Desktop・Cloud・Windows等の個別環境、認可の動作は未観測。
この点検では、既存のhook実装や権限の処理は変更していない。

ホスト別・委譲・認可の手順は `agent-toolkit/skills/atk-mode/playbooks/`、その内部仕様は同 `references/` を起点とする。
`claude-hooks.md`、`agent-skills.md`、`investigation.md` は `agent-basics/skills/writing-standards/references/` のファイルを指す。
Pythonは `_agents_server/` と明記したファイルを除き `agent-toolkit/agent_toolkit/_hooks/` を指す。
行番号は照合元の位置である。
改訂した規範の位置は節名で示す。

| バージョン | 対象と照合した箇所 | 結果と未観測の範囲 |
| --- | --- | --- |
| Claude Code 2.1.327 | `ExitWorktree`・作業ディレクトリ境界・auto・承認前Bash初期化 → `confirmation.md`「認可を要する操作」「承認源と適用範囲」、`permission-denial.md`「共通判断」、`subagent.md` 151–161 | 維持。worktree・未保存作業の保護、作用させる操作そのものの承認、拒否の非迂回はホストが何を質問するかと独立。公式の `ExitWorktree` の確認にはallow／auto／bypass等の例外があり、承認が必ず出るとは扱わない。実機のモード別ダイアログ・初期化順は未観測。 |
| Claude Code 2.1.327 | 再読込後の `--agents`・子モデル/権限 → `host-claude-code.md` 22–26, 92–99, 116–126 と `model-settings.md`「工程別モデル設定」；孫・失敗追送・遅延配送 → `host-claude-code.md` 103–106, 131–157, `delegation-waiting.md`「待機区間の構成」、`background_tasks_entries.py` 64–93 | 維持。再読込結果と実効モデル・公開ツールを観測する契約、受理と受信を分ける条件、未達通知の再送制限は残る。子・孫は親子IDで対応し、通知の到達先を普遍化しない。プラグイン再読込後の個別agentと3階層以上の配送は未観測。 |
| Claude Code 2.1.327 | Stopの失敗と拒否表示／SubagentStop失敗 → `claude-hooks.md` 25–30, 136–166；managed PostToolUseの重複 → `operations.md` 48–69 と `posttooluse.py` `_observe_tool`；圧縮直前結果 → `work-contract.md` 163–189 | 維持。hookの障害、正常に返した判定、結果の配送を区別しており、表示の修正は判定に使わない。圧縮で直近結果が保たれても、作業契約・原本・実行証拠の復元を省かない。managed hook＋modの二重配送と圧縮直後の原文は未観測。 |
| Claude Code 2.1.328 | `mcp_tool`＋`onFailure` → `claude-hooks.md`「hookスクリプトの基本プロトコル」に反映。Code Mode出力とUserPromptSubmit → `claude-hooks.md` 69–82, 112–116、`user_prompt_submit.py` 173–184, 227–247；実行前拒否とmanaged PostToolUse → 同38–40、`posttooluse.py` 214–248、`stop.py` `_completion_verdict` | 維持（既存の処理）。UserPromptSubmitは入力に文脈を挿す用途、結果を書き換えるのはPostToolUseの `updatedToolOutput`。実行前拒否をPostToolUseで回収する前提はない。公式のPostToolBatchには一部が届くが拒否の印は付かず、失敗判定に流用しない。managed/Code Mode別の実機イベントは未観測。 |
| Claude Code 2.1.328 | Code Mode再開後のスキル・hook文脈 → `work-contract.md` 168–188；切り詰め・中断通知、auto既定・sandbox起動不能 → `host-claude-code.md` 76–90、`operations.md` 48–83、`permission-denial.md` 7–35；子孫待ち → `delegation-waiting.md`「待機区間の構成」、`background_tasks_entries.py` 74–103 | 維持。再開後の要点・未受領本文を現物から戻し、REPL中断と正常終了を分ける。既定モードや起動不能の警告・拒否を権限付与と解釈しない（公式の拒否と `sandbox.failIfUnavailable` の分岐は混ぜない）。子孫の待機は識別子と結果で回収。再開時の復元、sandbox起動不能、子孫待ちは未観測。 |
| Claude Code 2.1.329 | Code Mode PreToolUse失敗 → `claude-hooks.md`「hookスクリプトの基本プロトコル」に反映。Monitorの `type` → `claude-hooks.md` 153–161 と `background_tasks.py` 147–162、`background_tasks_launches.py` 170–218；長いMCP → `background_tasks_entries.py` 71–85, 101–106、`background_tasks_notifications.py` `_resolve_task_notification_ids`；`--add-dir` → `host-claude-code.md` 70–74 | 維持。Monitorはすでに `monitor` と記載し、入力は非teammateを集計（`shell` 固定でない）、履歴側はMonitor呼び出しIDを照合。MCPの背景移行と完了通知は別集合。任意の追加ディレクトリを自動探索の前提とせず実在・公開に従う。実機のMonitor/MCP移行・外部ディレクトリ探索は未観測。 |
| Claude Code 2.1.330 | SessionStart envの `claude -p --continue` → `host-claude-code.md` 57–62、`rules_context.py` 1–11, 44–52；skillの `model` → `model-settings.md`「工程別モデル設定」「modelとreasoning effort」；MCPからのターン終了 → `delegation-waiting.md`「完了通知を待ってターンを終える場合」；`ExitWorktree`／`rm` → `subagent.md` 151–161 と `confirmation.md`「認可を要する操作」 | 維持。新しい通知でセッション識別を更新し、envの保持を読了・配送の根拠にしない。起動した担当の実効モデルとスキル内の指定は別。MCPの終了要求は作業完了の証拠ではない。ホストが削除を許しても共有と担当外の保護は続く。env継承、skillのモデルの切替、MCP終了、削除承認は未観測。 |
| Claude Code 2.1.331 | DesktopのStop／SubagentStopと最後の返信 → `claude-hooks.md` 155–165；`PermissionDenied.retry` → 同38–40、`posttooluse.py` 229–240（拒否を記録し再試行を返さない）、`permission-denial.md`「共通判断」；追送許可・失敗 → `host-claude-code.md` 146–153；出力保存先 → `operations.md` 65–69、`persisted_output.py` 27–61 | 規範に適用範囲を明記: Desktopの記録待ちは公式履歴の限定にとどめ、全ホストで本文が読めるとはしない。2.1.331以降の `retry: true` が促すのはユーザーが求めた同じ呼び出しの再試行で、拒否の撤回・自動実行・認可ではない。現在のプラグインの拒否記録処理、追送の到達確認、Bash/PowerShellの出力置換と一般の保存先回収は維持。Desktop・Agent追送・Code Mode出力退避は未観測。 |
| Claude Code 2.1.332 | MCP由来スキルと `PreSkillShellCommand` → `agent-skills.md` 62以降、`host-claude-code.md` 70–74、`claude-hooks.md` 5–7；mid-turnと `isSynthetic` → `_agents_server/claude_turns.py` 48–63, 185–212、`user_prompt_submit.py` 89–117、`background_tasks_entries.py` 74–85；worktree内の規範重複とdiscard確認 → `full_reads.py` 34–49、`rules_context.py` 1–11、`subagent.md` 151–161、`confirmation.md`「認可を要する操作」 | 一般対応を現行構成へ追加しない。既存の配布スキル・実ルートはMCPカタログとは別。新hookの登録は不要で以前のバージョンでは起動ダイアログを招く。SDKの `steer` は現に文字列queryを送り、ツールキットの出所判定は `source`・包み等で行うため、`isSynthetic` を機械転送に付けるべき経路は静的点検だけで否定できない。公式条件はフラグを取得しない環境のSDK／`claude -p`・Opus 4.8以降。実際のSDK入力形式とhookに届く出所／実際の依頼の経路を未観測として分け、ユーザー入力まで一律に機械扱いする変更はしない。起動は案内だけを出し、実体本文照合と破棄の認可は維持。 |
| Codex 0.170.0 | 環境が異なる同名MCP → `host-codex.md` 70–76；権限更新・子sandbox → 同109–125 と `permission-denial.md` 29–35 | 維持。同じ名前のサーバーを環境越しに集約する実装や規範ではなく、現在の `ALL_TOOLS` をその場で確認。子に実効権限が保存されても権限拡張を仮定せずホストの診断に従う。実験的な複数環境の同名登録と子の制約は未観測。 |
| Codex 0.171.0 | prepared child base/developer instructions・圧縮の追加文脈 → `host-codex.md` 109–125、`work-contract.md` 163–189、`precompact.py` `main`、`rules_context_codex.py` 8–10 | 維持。ホストの命令階層と仕事の成果・認可を保存する責務は別。SessionStart案内は原本の代替とせず、配送済みと読了を区別。ホストが作る子と圧縮前後の文脈配送は未観測。 |
| Codex 0.171.0 | V2空成功／idleへの案内／スレッド単位の保留 → `host-codex.md` 123–125, 167以降、`delegation-waiting.md`「待機区間の構成」、`delegation-recovery.md` 87–104 | 維持。既存は `send_message` のJSON本文に依存せず、稼働中に限って追加、終了/待機中は `followup_task`、開始または結果の観測で成立とする。公式 #61894 はidle宛の起こし方、#62093は空成功であり、「空なら未送達」とはしない。V2個別のidle/queued/emptyは未観測。 |
| Codex 0.171.0 | `codex exec --remote`／cwd変更時の権限プロファイル／旧ターンの承認取消防止 → `host-codex.md` 109–140、`confirmation.md`「承認源と適用範囲」「回答の受領」 | 維持。Remote app-serverに切り替えても対象・権限は現在のホストから確認し、元の質問と実回答が一致して初めて承認とする。remote接続・cwd移動・遅着イベントは未観測。 |
| Codex 0.171.0（条件付） | Cloudのplugin/skillカタログ・同梱hook信頼/配送・モデル要求hook → `agent-documents.md` 30–56、`claude-hooks.md` 31–35、`rules_context_codex.py` 8–10 | 維持。配送表の未確認欄をCloudカタログ変更だけで埋めない。同梱hookも信頼確認、現在の登録はモデル要求hookへ依存しない。Cloudカタログ・信頼ダイアログ・モデルhook発火は未観測。 |
| Codex 0.172.0 | 任意stdio初回起動 → `host-codex.md`「MCPツールの呼び出し」に反映。gRPC統一・ネストしたApps結果・JS preload・異OSルート → `host-codex.md` 68–107, 109–125、`operations.md` 31–38, 48–69 | 維持。手順は公開ツール名・スキーマと、診断＋構造化結果に従いgRPC設定やpreloadへ依存しない。Apps固有部分の消去や異OSのローカルパス変換を仮定しない。通信・Appsと異OS接続は未観測。 |
| Codex 0.172.0 | `codex exec resume` の権限・cwd・root、prepared forkで親の承認証拠を渡さない、Apps別承認 → `host-codex.md` 109–140、`confirmation.md` 153–169、`subagent.md` 22–34、`investigation.md`「実行結果の検証」 | 維持。保存状態の復元は現行の認可ではなく、forkでも依頼と許可された対象・外部作用を判断する。承認レビュー担当の設定もユーザー承認の代わりではない。resume・prepared fork・個別Apps承認は未観測。 |
| Codex 0.172.0 | 親中断とメールボックス → `delegation-recovery.md` 87–104、`work-contract.md` 163–189、`rules_context_codex.py` 8–10 | 維持。公式 #62348 は中断された親が子から受け取る待ちメッセージを親の次turnに残す修正（親から送った追送が次の子turnに届く保証という意味ではない）。#62292のホスト側メール配送も認可と受領の証明にしない。中断後の親の次turn、メールボックス注入と初期文脈の多重化は未観測。 |
| Claude Code 2.1.327付記 | PowerShell承認／相対 `--add-dir`／RC停止時のgitignored保護 → `permission-denial.md` 7–35、`confirmation.md`「認可を要する操作」、`host-claude-code.md` 70–74、`subagent.md` 151–161；plugin agent再開と起動待ち、ListAgentsページ、子出力回復 → `host-claude-code.md` 22–26, 131–172、`operations.md` 65–69 | **ListAgentsだけ変更**。現行156–157, 165, 171–172は子孫全体の停止・不在を要し、ページ取得を明示しない。長い結果がページ化される公式変更を踏まえ「続きがあれば公開手段で全ページを回収。全量が確認できない時は未知の子孫の不在を確定しない」と補った。固定の未確認引数を定めない。他は承認・Git管理外も保護・再読込／出力退避の確認を維持。全機能未観測。 |
| Claude Code 2.1.328付記 | PermissionRequest書換え後sandbox → `claude-hooks.md` 101–110 と `permission-denial.md` 7–35；headless未実行／`/goal` とStop → `claude-hooks.md` 136–149；MCP更新・質問/承認待ち → `host-claude-code.md` 22–43, 76–90；auto-compact、保留・破棄とremote再開 → `work-contract.md` 163–189、`host-claude-code.md` 101–106, 146–153 | 維持。書換えはsandboxを免除しない。延期したツールの再実行・Stopと作業完了・ダイアログの時間切れとユーザー回答・queuedと受信を別扱いしている。auto-compactのプロンプト再構築で既読や承認を仮定しない。これらの組合せは未観測。 |
| Claude Code 2.1.329付記 | managed hookの読込失敗・出所 → `claude-hooks.md` 9–13, 118–134；引数なし・探索上限・MCP終了 → `host-claude-code.md` 76–89、`operations.md` 48–69；plugin削除後headless／再接続とRC圧縮 → `host-claude-code.md` 22–26, 57–62、`work-contract.md` 163–189 | 維持。管理環境で定義や出所を確認し、ツールの公開スキーマ、診断、一部／全量、終了結果の根拠を個別に確かめる。再接続時は診断と版、圧縮後は再通知と記録を根拠とする。表示・接続・RCでの復元は未観測。 |
| Claude Code 2.1.330付記 | 中断/圧縮の読取状態・CLAUDE.mdリンク → `full_reads.py` 34–49、`work-contract.md` 168–174、`host-claude-code.md` 70–74；同名agent／prompt-hookモデルalias → 同116–126、`model-settings.md`「modelとreasoning effort」；背景停止／完了競合、承認済み計画の起床、headless出力障害 → `background_tasks_entries.py` 95–103、`_agents_server/claude_turns.py` 81–86, 112–125, 245以降、`confirmation.md`「承認源と適用範囲」、`operations.md` 55–68 | 維持。読了は現在の本文、選択は具体名と実効モデル、完了は停止通知やプロセスに依存する独立の証拠で決める。計画承認を実行の権限拡張としない。headless出力エラーは終了値・診断で区別。再開、リンク、alias、競合、障害時終了値は未観測。 |
| Claude Code 2.1.331付記 | Desktopの最終応答記録待ち → `claude-hooks.md`「Stop/SubagentStopフック」の「入力」に反映。Skill permissionの保存 → `claude-hooks.md` 101–110 と `confirmation.md`「承認源と適用範囲」；resume時Code Mode上限 → `host-claude-code.md` 42–43, 76–90；plugin部分更新・XMLを含む圧縮要約 → 同22–26、`work-contract.md` 163–189 | 維持。保存値を認可原本やワイルドカード一般の許可とせず、制限/打ち切りの診断・再接続・取得版を確認。XMLを含む要約は要求・認可の原文に代えない。正式な保存先・個別再開制限・部分更新・XML要約は未観測。 |
| Claude Code 2.1.332付記 | 長大なhook遮断・障害ログ → `claude-hooks.md`「出力上限」に反映。保留メッセージと前景fork終了 → `host-claude-code.md` 131–157、`background_tasks_entries.py` 60–67；Code Modeの背景/期限/長いスクリプト/MCP配列/変数復元 → 同76–90、`operations.md` 48–83、`background_tasks.py` 91–119；Artifact公開／外部資料とDesktop/VS Code plan拒否・確認 → `confirmation.md`「認可を要する操作」「事前承認と事後承認」、`host-claude-code.md` 31–55 | 維持。通知は実行完了と検収に分ける。前景forkは背景タスクとみなさず公開結果を待つ。Code Modeは現在のスキーマと回収を使い、再開時の変数保全を仮定しない。Artifact原文読み取りと公開承認、ダイアログの有無と承認の成立を別に保つ。held/foreground、Code Mode各条件、Artifact、Desktop/VS Codeの各承認は未観測。 |
| Codex 0.170.0付記 | Windows sandbox構成の再実行でACL保持 #60943 → `host-codex.md` 109–115 と `permission-denial.md` 29–35 | 維持。既存のACLの保持は拒否を設定変更で迂回する根拠にはならない。Windows実機の構成再実行は未観測。 |
| Codex 0.171.0付記 | 子のinterruptで圧縮要約を保持 #61916、Code Modeで子ツールを公開する条件 #61854 → `host-codex.md` 109–125、`delegation-recovery.md` 95–104、`work-contract.md` 163–189 | 維持。中断可能条件は変わらず、要約の保持は証拠回復を不要にしない。公開はモデル/環境ごとに確認しツール名だけを前提にしない。remote子の中断・圧縮・モデル別カタログは未観測。 |

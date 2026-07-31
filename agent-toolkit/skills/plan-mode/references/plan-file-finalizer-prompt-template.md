# plan-file-finalizerの起動契約

本ファイルを呼び出し元側の起動契約の正本とする。
入力の搬送形式に加え、正しい`subagent_type`、起動形式、起動前準備、
完了報告の検収、後続処理を定める。
受信側の解釈は`agent-toolkit/agents/plan-file-finalizer.md`の`## 入力`を正本とし、
改訂時はペアで更新する。呼び出し元の読込対象は本referenceに限定する。

呼び出し元は起草済み計画ファイルの実在を確認し、
Agentツールで`subagent_type: agent-toolkit:plan-file-finalizer`を指定する。
`model`、`name`、`run_in_background`を省略し、実際の受領経路を起動結果から判定する。
完了報告は次の全欄を検収する。

- `summary`、`plan_file_path`、`continuation_state`、`status`、`review_completed`
- `implementation_route`、`review_route`、`implementation_thread_id`、`review_thread_id`
- `review_rounds`、`implementation_history`、`review_history`
- `check_results`、`post_application_check_diff`、`post_application_check_results`
- `worktree_check_results`
- `scope_baseline`、`scope_changes`、`out_of_scope_findings`
- `review_summary`、`escalation_points`

`status: completed`は、`review_completed: true`と全機械チェック成功を必須とする。
致命的・重大指摘が解消し、計画ファイル実体を確認でき、未承認の`scope_changes`が無いことも確認する。
採用した初版内補正と承認済みのスコープ拡大が反映済みである場合だけ受理する。
呼び出し元は`review_summary`の各見解を実測確認し、不採用と確定した指摘には
`02-claude-code.md`「サブエージェント運用」節が定める不対応注記を付してから実装工程へ進む。
`status: needs_escalation`では`escalation_points`を必須とし、
呼び出し元が採否または不足事項を確定してから、同じ起動契約で新規起動する。
新規起動では返却された`continuation_state`を引き継ぎ、初回総合レビューが完了する前の返却は
`continuation_state: 初回レビュー前`として再開する。
初回総合レビュー完了後に採否が未確定なら、
`continuation_state: 初回レビュー後・採否確定前`として採否を確定する。

`scope_changes`に未承認のスコープ拡大が1件以上ある`status: needs_escalation`は、
finalizerから呼び出し元への
内部の判断移管であり、ユーザー確認を直接意味しない。
呼び出し元は初版との差分と根拠を実測し、`01-agent.md`「協調と自律」に従って
セッション状態フラグ`process_feedbacks_skill_invoked`からモードを判定する。
元の成果を技術的に成立させる内部追随で成立案が一意の場合は、
`### エージェント判断`へ根拠を記録して承認できる。
その他も方針から判断できる事項は確認せず自律処理する。
協調モードでは同節「協調モードでの確認」の条件に該当する場合だけ
`AskUserQuestion`で確認する。
自律モードでは同節の例外を除き、`atk mq add --type=tbd`で記録して暫定判断で続行する。
`out_of_scope_findings`は現在の計画を停止させず、`01-agent.md`「完遂と先送り」に従って
同一作業内の対応またはキュー登録を確定する。
`scope_changes`に未承認のスコープ拡大が0件の、
その他の`status: needs_escalation`は返却論点を解決する。
確定後は返却された`continuation_state`に対応する縮減プロンプトでfinalizerを新規起動する。
初回総合レビューと採否確定が完了した場合だけ、
`continuation_state: 採否確定後・反映前`、採用指摘、確定済みの採否を指定する。
この状態では、まだ存在しない反映結果と反映差分を入力に要求しない。
反映後にfinalizerを再起動する場合は、
`continuation_state: 反映後・再レビュー前`、反映結果、反映差分、
前回の反映後機械修正前後差分、反映後最終検査結果も全文転記する。
再起動後の計画ファイルから`scope_baseline`を再計算させない。
すべての継続・再起動で、初回の`scope_baseline`、全ラウンドの累積`scope_changes`、
両系統の経路、`threadId`、全応答履歴、累積`review_rounds`を全文転記する。
未開始の系統は返却値の`not_started`、`threadId: なし`、履歴`なし`、回数`0`を転記する。
途中で利用不能になった系統は`unavailable`と、利用不能になる直前の`threadId`、
全応答履歴、累積`review_rounds`を転記する。

`implementation_route: unavailable`または`review_route: unavailable`に起因する
`status: needs_escalation`では、呼び出し元が不能な系統ごとに
Agentツールで`subagent_type: claude`を新規起動する。
機械チェック・修正系は`model: sonnet`、総合レビュー系および設計判断を含む修正系は
`model: opus`を指定する。`name`と`run_in_background`は省略し、実際の受領経路を起動結果から判定する。

起動プロンプトには次の絶対パスと実行時入力だけを含め、各資料を着手時に全文Readさせる。

- `${CLAUDE_PLUGIN_ROOT}/skills/codex-exec/references/plan-codex-review.md`
- 機械チェック・修正系では
  `${CLAUDE_PLUGIN_ROOT}/skills/codex-exec/references/plan-codex-review-fix-task.md`、
  総合レビュー系では
  `${CLAUDE_PLUGIN_ROOT}/skills/codex-exec/references/plan-codex-review-task.md`
- 計画ファイル
- 適用する品質規範、対象固有スキル、プロジェクト規範
- 作業ディレクトリ、対象、完了条件

Claude代替の起動文には、完了報告をツール戻り値で1回だけ返し、
`SendMessage`で能動送付せず、待機対象の結果を含める指示を明記する。
呼び出し元がtask referenceの必須欄と成果物実体を検収する。
検収済みの応答全文と対象系統（`implementation`または`review`）を
固有の見出しでfinalizerへ再入力し、同エージェントに後続検収を継続させる。

`plan_file_path`がplan mode用サンドボックスパスを付記する場合は、
呼び出し元がfinalizer反映後の全文をReadで検収し、
`~/.claude/plans/`配下の正規パスへWriteで反映する。サンドボックスパスは保持する。

## 必須見出し

起動プロンプト本文へ次の3つのH2見出しを、この表記のまま含める。各見出し直下に非空の本文を置く。

| 見出し | 記載内容 |
| --- | --- |
| `## 計画ファイルパス` | 呼び出し元が初版を書き込み済みの絶対パス（`plan-mode/SKILL.md`「計画ファイル（本ファイル）のパス」節と同一の命名規約。plan mode下でサンドボックスパスへ書き込んだ場合は当該サンドボックスパスを記す） |
| `## permission_mode` | `plan`または非`plan` |
| `## 作業ディレクトリ` | 対象リポジトリの絶対パス（`agent-toolkit:codex-exec`によるcodex初回起動時の`cwd`として転記する） |

`## 作業ディレクトリ`を対象worktreeの唯一の入力として使う。

見出しの実在と非空に加え、`## 計画ファイルパス`が指すパスを`expanduser().resolve(strict=False)`で
正規化したうえで実在することも機械検査される。パスを一意に抽出できない場合は当該検査をブロックしない
（安全側の設計。曖昧な記述は必須見出しの非空検査で担保される範囲に留める）。パスを一意に抽出できた
場合は、正規化に失敗したケースを含め非実在として扱い、厳格にブロックする。内容の妥当性
（合意済み事項が実際にユーザー確認を経ているか等）は呼び出し元自身の調査完遂責務
（`01-agent.md`「調査と検証」節）に委ねる。

## 追加情報

本referenceが受理する継続情報と実施済み結果は、固有の見出しで追記してよい。

- 作業ディレクトリが作業用複製の場合: `source_repository_path`として複製元リポジトリの絶対パス
- 作業ディレクトリが作業用複製でない場合: `source_repository_path: 対象外`

- 継続または再起動の場合: 実装・修正系とレビュー系の経路、`threadId`、
  全応答履歴、累積`review_rounds`
- 再開または再レビューの場合: 実施済みレビュー結果と、呼び出し元が確定した採否
- 呼び出し元がClaude代替した場合: 検収済みの応答全文と、
  `implementation`または`review`の対象系統
- 初回レビュー開始後の継続または再起動の場合: 初回の`scope_baseline`と
  承認状態・反映状態・反映結果を含む全ラウンドの累積`scope_changes`の全文
- `初回レビュー前`の場合: `continuation_state`
- `初回レビュー後・採否確定前`の場合: `continuation_state`、実施済みレビュー結果
- `採否確定後・反映前`の場合: `continuation_state`、前回の採用指摘、確定済みの採否
- `反映後・再レビュー前`の場合: `continuation_state`、前回の採用指摘、確定済みの採否、
  反映結果、反映差分、前回の反映後機械修正前後差分、反映後最終検査結果

継続情報が無い系統は初回起動として新しく開始する。
実施済みレビュー結果と確定済み採否が無い場合は、未実施かつ未確定として扱う。
計画ファイル本文、agent定義の委譲手順、定義frontmatterで読み込む規範は起動文へ転記しない。
呼び出し元は対象worktreeと条件付き複製元を委譲前後に実測し、
`worktree_check_results`と一致した場合だけ完了報告を受理する。

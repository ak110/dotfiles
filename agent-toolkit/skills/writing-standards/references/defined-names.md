# 定義済みの名前の一覧

本書はagent-toolkitの規範が定義し、複数の文書で使う名前を、指示対象と定義元とともに1行ずつ並べる。
名前を書く全ての主体（規範、設計文書、WI、計画、コミットメッセージおよびコードの書き手）が、既存の対象を名前で指す時と新しい名前を付ける時に本書を全文読む。
定義そのものは定義元だけに置き、本書は名前から定義元へ至る索引とする（`agent-documents-basics.md`「主要用語の初出定義先」）。

## 命名の方針

ユーザーは2026年10月2日に、工程・レビュー・検証・判定・担当などの名前をその場で作成せず、定義して統一すると確定した。
適用範囲はagent-toolkitの規範に従って書く全ての成果物とし、名前は次の方針で選ぶ。

1. 定義を読まなくても指す対象が分かる自然な語を選ぶ
2. 一般的な語（独立、root、固定、管理など）に独自の意味を持たせず、別々の概念に似た名前を付けない
3. 識別子（スキル名、コマンド名、ファイル名、定数名など）がある対象は、日本語の説明的な名前を作成せず識別子で書く
4. ユーザーが名付けた名前と、`atk serve`の画面などエンドユーザーが目にする場所で使う名前は残す
5. 境界があいまいな名前は、書き手が決めずにユーザー確認する
6. 返却形式の欄名も日本語名にする
7. 用例の多さや既存の定義の有無を、名前を残す根拠にしない

対象を指す時は、識別子があれば識別子を、無ければ本書の名前を使う。
識別子も本書の名前も無い対象へ繰り返し参照する名前が要る場合は、自然な語を選び、定義元へ1文の定義を置いて本書へ行を加えてから使う。
名前の候補は次の手順で作成し、候補の順位は方針1と方針7に従う。

1. 候補は本書の一覧だけでなく、同じ指示対象を既存成果物がすでに呼んでいる語（本書の一覧に無い語を含む）からも作成する
2. 候補語ごとに、対象リポジトリの全追跡ファイルを固定文字列で検索する。範囲には変更予定のディレクトリの外にある設計文書、経緯記録、エンドユーザー向け文書と隠しディレクトリを含める。既存の用例があれば、その意味と新しい概念との異同を確かめる
3. 別の意味の用例がある語は対象を一意に指さないため、候補から外す。ユーザーが指定した語も同じく推奨する候補から外し、その語を変える案は方針5に従ってユーザー確認する。既存の用例を書き換えて衝突を解消する案でも語は両方の意味に読めるまま残るため、その語は候補から外したままとする

規範、設計、計画、WI、確認の本文に新しく導入する概念名へこの手順を適用し、1つの文書の中だけで定義する語と、質問の説明のために作成した語も含める。既存の語を候補に入れた後の順位は、他の候補と同じ方針1と方針7で決める。候補を一覧だけから選ぶと、一覧に無い既存の呼び方が候補に挙がらず、別の意味で使われている語を新しい概念へ割り当てることになる。
`agent-documents-basics.md`「主要用語の初出定義先」の「定義を読まなくても指示対象を推測できる一般的な語を優先」は方針1と同じ向きであり、方針2と合わせて読む。
`agent-toolkit/share/rules-main.md`と`writing.md`の「実在する名前が無い対象は説明で書く」規定は名前が要らない対象に適用し、繰り返し参照する工程などの名前では本節の方針を優先する。

## 一覧

識別子で書く対象（スキル名、コマンド名、ファイル名など）は本書へ載せない。
定義元の列は、先頭のリポジトリ相対パスと節名で示す。`defined_names_invariant_test.py`が各行の名前が定義元に現れることを確かめる。

| 名前 | 指示対象 | 定義元 |
| --- | --- | --- |
| ユーザー | コーディングエージェントへ要求・指示・判断を与える人。エンドユーザーに含まれる | `agent-toolkit/rules/01-agent.md`「役割分担」 |
| エンドユーザー | ソフトウェア・設定・文書などの成果物を使う人の総称。ユーザーを含む | `agent-toolkit/rules/01-agent.md`「役割分担」 |
| 選定工程 | `agent-toolkit:process-wi`の3つの主要工程のうち、処理対象のAWIを固定する工程 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| レーン工程 | `agent-toolkit:process-wi`の3つの主要工程のうち、レーンごとに計画、実装、レビューおよび統合を行う工程 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| 公開工程 | `agent-toolkit:process-wi`の3つの主要工程のうち、版数更新、push、CI確認と公開後の操作を行う工程 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| picker | 選定工程で処理対象のAWIを固定する担当 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| 処理対象WI | pickerが選定時に固定した、`agent-toolkit:process-wi`の1回の実行で処理するAWI | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| レーン担当 | 各レーンの計画、実装、レビュー修正、履歴統合および主作業ツリーへの統合を同じthreadで担う担当 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| レビュー修正担当 | 実行レビューの指摘だけを修正する、レーン担当の担当種別 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| CI修正担当 | CI記録が認可する失敗だけを修正する、レーン担当の担当種別 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| 終端担当 | 公開工程のpush、CI、検証失敗時の修正、プロジェクト固有の公開後の操作および延期adoptを担う委譲先 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| 監査担当 | 自動コードレビュー監査で未処置の対象を判定する委譲先 | `agent-toolkit/skills/process-wi/SKILL.md`「自動コードレビュー監査」 |
| 自動コードレビュー | GitHub Copilotのレビューなど、外部サービスが自動で付けるレビュー | `agent-toolkit/skills/process-wi/SKILL.md`「自動コードレビュー監査」 |
| WI作成 | 目的、消費主体、完成条件と実現方式を定めたWIを作成する工程 | `agent-toolkit/share/workflow-phases.md`の工程表 |
| 計画 | WIから外部仕様、変更対象、受入シナリオ、テストと検証コマンドを定める工程 | `agent-toolkit/share/workflow-phases.md`の工程表 |
| 実行 | 計画どおり実装し、受入シナリオを検証する工程 | `agent-toolkit/share/workflow-phases.md`の工程表 |
| 実行レビュー | 実装後に要件・外部仕様の水準で実装とテストを確認する工程 | `agent-toolkit/share/workflow-phases.md`の工程表 |
| 計画担当 | 計画工程を担う主体（`agent-toolkit:process-wi`ではレーン担当、対話型ではメイン） | `agent-toolkit/share/workflow-phases.md`の工程表の直後 |
| 実装担当 | 実行工程を担う主体（`agent-toolkit:process-wi`ではレーン担当、レビュー修正担当、CI修正担当、対話型ではメイン） | `agent-toolkit/share/workflow-phases.md`の工程表の直後 |
| 全体検証 | リポジトリ全体を対象にした自動チェックとテストの実行 | `agent-toolkit/share/workflow-phases.md`の工程表の直後 |
| 受入シナリオ検証 | 計画の受入シナリオを公開された呼び出し手段から検証する結合・E2Eテストを、変更範囲の検証で実行すること | `agent-toolkit/share/workflow-phases.md` |
| 公開工程判定 | リポジトリ全体の自動チェックとCIの成功を述べる完成条件を、公開工程で判定すること | `agent-toolkit/share/workflow-phases.md` |
| 直接影響範囲 | 変更した定義、文言、パスを参照、呼び出し、生成または期待値として持ち、合わせて直す必要がある箇所の全体 | `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`「要件・外部仕様」 |
| 変更範囲の検証 | 変更するファイルとその直接影響範囲に限った検証 | `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`「検証と終端工程」 |
| 統合時の完成条件判定 | 統合指示の前に、完成条件証拠の各行がWIの完成条件と原文要求に過不足なく対応するかを確かめる確認 | `agent-toolkit/share/exec.parent.md`「統合の指示と受領」 |
| 初回レビュー | 実行レビュー担当が最初に行う実行レビュー | `agent-toolkit/share/exec-review.subagent.md` |
| 再レビュー | 指摘の修正後に同じ実行レビュー担当が行う実行レビュー | `agent-toolkit/share/exec-review.subagent.md` |
| 引き継ぎ再レビュー | 継続できなくなった実行レビュー担当に代わり、新しい担当が引き継いで行う再レビュー | `agent-toolkit/share/exec-review.subagent.md` |
| 実行レビュー担当 | 実行レビューを行う委譲先 | `agent-toolkit/share/exec-review.subagent.md` |
| ユーザビリティレビュー | 画面差分を実際の操作で確かめるレビュー | `agent-toolkit/share/usability-review.parent.md` |
| 並列画面レビュー | 画面差分のあるレーンで、ユーザビリティレビューと実行レビューを並列に行う運用 | `agent-toolkit/share/usability-review.parent.md` |
| WI投入担当 | WIの本文を起草して投入する委譲先 | `agent-toolkit/share/add-wi.subagent.md` |
| 一括置換後レビュー担当 | 一括置換の文字単位差分を全件読み、意味や対応関係が変わった箇所を返す委譲先 | `agent-toolkit/share/bulk-replace-review.subagent.md` |
| 投稿前レビュー担当 | 外部サービスへ送る文面を送信前にレビューする委譲先 | `agent-toolkit/share/external-write-review.subagent.md` |
| 調査担当 | 不具合の原因を調査して原因分析を返す委譲先 | `agent-toolkit/share/defect-investigation.subagent.md` |
| 読者別探索担当 | 読者ごとに成果物を探索し、読者適合を判定する委譲先 | `agent-toolkit/share/reader-fit-review.subagent.md` |
| 説明担当 | 選定結果の包含、除外または構成の理由を説明する読み取り専用の委譲先 | `agent-toolkit/share/pick-wi-explain.parent.md` |
| ユーザー確認 | エージェントがユーザーへ判断を求める行為 | `agent-toolkit/rules/01-agent.md`「役割分担」 |
| ユーザーへの報告 | ユーザーの判断を求めずに成果、事実、未達などを届ける行為 | `agent-toolkit/rules/01-agent.md`「役割分担」 |
| 要件・意図の曖昧さ | 目的、対象範囲、期待結果、制約、外部から観測できる挙動など、ユーザーしか答えを持たない事項の解釈が、原文、先行発話、現物の調査を経ても1つに定まらない状態 | `agent-toolkit/skills/user-confirmation-and-report/SKILL.md`「確認要否の判定」 |
| 報告本文の判定 | Stop hookが作業完了報告・振り返り結果報告・AWI投入結果報告の本文へ適用する、対策の対応・見送りの根拠・未確定の観測・最終報告の確定の判定 | `agent-toolkit/skills/completion-report/SKILL.md`「工程」 |
| 3段判定 | 新しい対象、操作、設計を許容性、必要性、実装品質の順に判定すること | `agent-toolkit/rules/01-agent.md`「QCDと3段判定」 |
| 委譲の要否判定 | 委譲するかと委譲の単位を決める判定 | `agent-toolkit/skills/delegation/references/routing.md` |
| 元担当 | 同じ作業のために既に起動した委譲先 | `agent-toolkit/skills/delegation/references/runtime-routing.md`「Codex後続操作の共通先行条件」 |
| 独立文脈レビュー | 成果物の作成者と文脈を共有しない委譲先が先入観なく行うレビュー（実行レビューなど） | `agent-toolkit/skills/delegation/references/routing.md`「会話を引き継ぐ委譲」 |
| 委譲元 | 委譲先を起動した主体 | `agent-toolkit/skills/delegation/SKILL.md` |
| 委譲先 | `Agent`ツールで起動する子（サブエージェント）と`agents_server`で起動する子sessionの総称 | `agent-toolkit/skills/delegation/SKILL.md` |
| 委譲プロンプト | 委譲先を起動するときに渡す指示の本文 | `agent-toolkit/skills/delegation/references/base-contract.md` |
| 返却値 | 委譲先が`<役割名>.subagent.md`の`## 出力`に従って返す値 | `agent-toolkit/skills/delegation/references/base-contract.md` |
| エージェント向け文書 | コーディングエージェントが直接読み込む文書 | `agent-toolkit/skills/writing-standards/references/agent-documents-basics.md`冒頭 |
| 読込表 | スキル本体と`<役割名>.parent.md`・`<役割名>.subagent.md`の冒頭に置き、時点または条件と、その時点で全文読む参照資料を対応付ける表 | `agent-toolkit/skills/writing-standards/references/agent-documents-basics.md`「責務と構成」 |
| 配送範囲表 | 常時規範と配送文がどの主体へ届くかを示す表 | `agent-toolkit/skills/writing-standards/references/agent-documents-basics.md`「責務と構成」 |
| 常時規範 | agent-toolkitが読み手へ常に配送する規範（`rules/`配下と`share/rules-main*.md`・`rules-subagent*.md`） | `agent-toolkit/skills/writing-standards/references/agent-documents-basics.md`「責務と構成」 |
| プロジェクト規範 | 対象リポジトリが規範として定める指示 | `agent-toolkit/skills/writing-standards/references/agent-documents-basics.md`「責務と構成」 |
| プロジェクト方針 | プロジェクト規範に加え、規範化されていない記述に書かれた方針 | `agent-toolkit/skills/writing-standards/references/agent-documents-basics.md`「責務と構成」 |
| 作成規範 | 対象成果物の種別に適用する作成側の品質基準 | `agent-toolkit/skills/review-standards/SKILL.md`「対象成果物の作成規範」 |
| 完成条件 | WIの`## 完成条件`が定める、完了を判定する条件 | `agent-toolkit/skills/wi-standards/references/awi-body.md`「通常AWIの本文」 |
| 完了条件 | 計画の`## 要件・外部仕様`が持つ合否の条件。WIの同種の項目は完成条件と呼ぶ | `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`「要件・外部仕様」 |
| レビュー指摘管理表 | `atk review-table`が操作する8列のTSVで、計画ファイルと同じstemの`.exec-review.tsv` | `agent-toolkit/skills/review-standards/SKILL.md`「レビュー指摘管理表の共通操作」 |
| CI対応レビュー指摘管理表 | 対応する計画が無いCI失敗について、CIのエラーへの対応内容をレビューしたときの指摘の管理表（`ci-<OID>.exec-review.tsv`） | `agent-toolkit/skills/review-standards/SKILL.md`「レビュー指摘管理表の共通操作」 |
| セッション記録 | Claude CodeとCodexが保存する会話の記録（transcript） | `agent-toolkit/skills/writing-standards/references/session-records.md`冒頭 |
| メイン記録 | Claude Codeのセッション本体の記録（深さ2） | `agent-toolkit/skills/writing-standards/references/session-records.md`「Claude Codeの記録」 |
| サブエージェント記録 | Claude Codeのサブエージェントの記録（`subagents/agent-<agentId>.jsonl`、深さ4） | `agent-toolkit/skills/writing-standards/references/session-records.md`「Claude Codeの記録」 |
| 計画バンドル | 計画ファイルと、同じstemの付属ファイル（計画ファイル（バグ）、レビュー指摘管理表など）の組 | `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`「計画ファイルの保存と参照」 |
| 計画の所有記録 | 計画バンドルを所有するセッションを示す`~/.claude/plans`の局所状態 | `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`「計画ファイルの保存と参照」 |
| バックグラウンドタスク | BashやAgentを背景で動かした非同期の処理 | `agent-toolkit/skills/writing-standards/references/session-state-and-flags.md` |
| バックグラウンドタスクの所有記録 | 自セッションが起動したバックグラウンドタスクとAgent・Taskの識別子を、PostToolUseが保存した記録 | `agent-toolkit/skills/writing-standards/references/session-state-and-flags.md` |

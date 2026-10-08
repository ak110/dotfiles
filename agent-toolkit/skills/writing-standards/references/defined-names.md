# 名前の方針と定義済みの名前の一覧

本書は名前を選ぶ方針と、agent-toolkitの規範が定義し複数の文書で使う名前の一覧を定める。一覧は名前ごとに、名前を見分ける短い見出し語と定義元を1行ずつ並べる。
本書の読み手は名前を書く全ての主体（規範、設計文書、WI、計画、コミットメッセージおよびコードの書き手）であり、本書を読む時点（既存の対象を名前で指す時と新しい名前を付ける時）は`SKILL.md`の読込表が定める。
定義そのものは定義元だけに置き、本書は名前から定義元へ至る索引とする（`agent-documents-basics.md`「主要用語の初出定義先」）。

## 命名の方針

ユーザーは2026年10月2日に、工程・レビュー・検証・判定・担当などの名前をその場で作成せず、定義して統一すると確定した。
適用範囲はagent-toolkitの規範に従って書く全ての成果物とし、名前は次の方針で選ぶ。

1. 定義を読まなくても指す対象が分かる自然な語を選ぶ
2. 一般的な語（独立、root、固定、管理など）に独自の意味を持たせず、別々の概念に似た名前を付けない
3. 識別子（スキル名、コマンド名、ファイル名、定数名など）がある対象は、日本語の説明的な名前を作成せず識別子で書く
4. ユーザーが名付けた名前と、`atk serve`の画面などエンドユーザーが目にする場所で使う名前は残す
5. 境界があいまいな名前は原要求と既存の定義を調べ、`agent-toolkit:user-confirmation-and-report`「確認要否の判定」を適用する。技術的に確定する境界は自ら決め、ユーザーだけの選好や適否が残る場合だけ確認する
6. 返却形式の欄名も日本語名にする
7. 用例の多さや既存の定義の有無を、名前を残す根拠にしない

対象を指す時は、識別子があれば識別子を、無ければ本書の名前を使う。
識別子も本書の名前も無い対象へ繰り返し参照する名前が要る場合は、自然な語を選び、定義元へ1文の定義を置いて本書へ行を加えてから使う。
名前の候補は次の手順で作成し、候補の順位は方針1と方針7に従う。

1. 候補は本書の一覧だけでなく、同じ指示対象を既存成果物がすでに呼んでいる語（本書の一覧に無い語を含む）からも作成する
2. 候補語ごとに、対象リポジトリの全追跡ファイルを固定文字列で検索する。範囲には変更予定のディレクトリの外にある設計文書、経緯記録、エンドユーザー向け文書と隠しディレクトリを含める。既存の用例があれば、その意味と新しい概念との異同を確かめる
3. 別の意味の用例がある語は対象を一意に指さないため、候補から外す。ユーザーが指定した語も同じく推奨する候補から外し、その語を変える案は方針5に従ってユーザー確認する。既存の用例を書き換えて衝突を解消する案でも語は両方の意味に読めるまま残るため、その語は候補から外したままとする
4. 画面などエンドユーザーが目にする場所に表示する名前の候補は、同じ表示領域（同じ欄や同じ一覧の行、同じダイアログなど）にある既存ラベルの記法を調べて、その記法で作成する。記法には言語、大文字と小文字、語の区切りを含む。既存の慣例を先に適用する根拠は`ui-ux.md`「判断の順序」にある。記法の異なる既存ラベルは設計記録で目的を確かめ、目的の記録があれば揃える対象から外す。記録が無ければ方針4に従って残す案を推奨し、揃える案は目的が記録に無いことを添えて選択肢に載せる

規範、設計、計画、WI、確認の本文に新しく導入する概念名と、画面などエンドユーザーが目にする場所に新しく表示する名前へこの手順を適用し、1つの文書の中だけで定義する語と、質問の説明のために作成した語も含める。既存の語を候補に入れた後の順位は、他の候補と同じ方針1と方針7で決める。候補を一覧だけから選ぶと、一覧に無い既存の呼び方が候補に挙がらず、別の意味で使われている語を新しい概念へ割り当てることになる。
本節の手順で定義元と本書へ行を加えた名前は、`agent-toolkit/share/rules-main.md`「ユーザー向け発話ルール」と`writing.md`がいう実在する名前に当たる。定義を置く前の対象は、両規定に従い説明で書く。

## 一覧

識別子で書く対象（スキル名、コマンド名、ファイル名など）は本書へ載せない。
「指示対象」列は名前を見分ける短い見出し語であり、定義は定義元の列が示す所在だけが持つ。定義元の列は、先頭のリポジトリ相対パスと節名で示す。`defined_names_invariant_test.py`が各行の名前が定義元に現れることを確かめる。

| 名前 | 指示対象 | 定義元 |
| --- | --- | --- |
| ユーザー | エージェントへ要求・指示・判断を与える人 | `agent-toolkit/rules/01-agent.md`「役割分担」 |
| エージェント | ユーザーの要求に応じて調査・判断・作業をするコーディングエージェント（メインエージェント・サブエージェント・委譲先） | `agent-toolkit/rules/01-agent.md`「役割分担」 |
| エンドユーザー | 成果物を使う人 | `agent-toolkit/rules/01-agent.md`「役割分担」 |
| 選定工程 | process-wiの工程 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| レーン工程 | process-wiの工程 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| 公開工程 | process-wiの工程 | `agent-toolkit/skills/commit/references/publish.md`冒頭 |
| picker | 選定工程の担当 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| 処理対象WI | 1回の実行で処理するAWI | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| レーン担当 | レーン工程の担当 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| レビュー修正担当 | レーン担当の担当種別 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| CI修正担当 | レーン担当の担当種別 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| 終端担当 | 公開工程の担当 | `agent-toolkit/skills/process-wi/SKILL.md`「用語」 |
| 監査担当 | 自動コードレビュー監査の委譲先 | `agent-toolkit/skills/process-wi/SKILL.md`「自動コードレビュー監査」 |
| 自動コードレビュー | 外部サービスのレビュー | `agent-toolkit/skills/process-wi/SKILL.md`「自動コードレビュー監査」 |
| WI作成 | 作業の工程 | `agent-toolkit/share/workflow-phases.md`「工程表」 |
| 計画 | 作業の工程 | `agent-toolkit/share/workflow-phases.md`「工程表」 |
| 実行 | 作業の工程 | `agent-toolkit/share/workflow-phases.md`「工程表」 |
| 実行レビュー | 作業の工程 | `agent-toolkit/share/workflow-phases.md`「工程表」 |
| 計画担当 | 計画工程の担当 | `agent-toolkit/share/workflow-phases.md`「用語」 |
| 実装担当 | 実行工程の担当 | `agent-toolkit/share/workflow-phases.md`「用語」 |
| 全体検証 | 検証の範囲 | `agent-toolkit/share/workflow-phases.md`「用語」 |
| 受入シナリオ検証 | 検証の種類 | `agent-toolkit/share/workflow-phases.md`「用語」 |
| 公開工程判定 | 完成条件の判定 | `agent-toolkit/share/workflow-phases.md`「用語」 |
| 直接影響範囲 | 変更の影響範囲 | `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`「要件・外部仕様」 |
| 変更範囲の検証 | 検証の範囲 | `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`「検証と終端工程」 |
| 統合時の完成条件判定 | 統合前の判定 | `agent-toolkit/skills/review-standards/references/exec-review-recording.md`「統合時の完成条件判定」 |
| 初回レビュー | 実行レビューの回 | `agent-toolkit/share/exec-review.subagent.md` |
| 再レビュー | 実行レビューの回 | `agent-toolkit/share/exec-review.subagent.md` |
| 引き継ぎ再レビュー | 実行レビューの回 | `agent-toolkit/share/exec-review.subagent.md` |
| 実行レビュー担当 | 実行レビューの委譲先 | `agent-toolkit/share/exec-review.subagent.md` |
| ユーザビリティレビュー | 画面のレビュー | `agent-toolkit/share/usability-review.parent.md` |
| 画面差分 | 画面の変更 | `agent-toolkit/share/workflow-phases.md`「用語」 |
| 並列画面レビュー | 画面差分のある運用 | `agent-toolkit/share/workflow-phases.md`「用語」 |
| WI投入担当 | WI作成の委譲先 | `agent-toolkit/share/add-wi.subagent.md` |
| 一括置換後レビュー担当 | 一括置換の委譲先 | `agent-toolkit/share/bulk-replace-review.subagent.md` |
| 投稿前レビュー担当 | 外部投稿の委譲先 | `agent-toolkit/share/external-write-review.subagent.md` |
| 調査担当 | 原因調査の委譲先 | `agent-toolkit/share/defect-investigation.subagent.md` |
| 読者別探索担当 | 読者適合の委譲先 | `agent-toolkit/share/reader-fit-review.subagent.md` |
| プロンプト評価担当 | refine-promptの委譲先 | `agent-toolkit/share/refine-prompt.subagent.md`冒頭 |
| 説明担当 | 選定結果の説明の委譲先 | `agent-toolkit/share/pick-wi-explain.parent.md` |
| ユーザー確認 | ユーザーへの行為 | `agent-toolkit/rules/01-agent.md`「役割分担」 |
| ユーザーへの報告 | ユーザーへの行為 | `agent-toolkit/rules/01-agent.md`「役割分担」 |
| 要件・意図の曖昧さ | 確認を要する事項 | `agent-toolkit/skills/user-confirmation-and-report/SKILL.md`「確認要否の判定」 |
| 報告本文の判定 | Stop hookの判定 | `agent-toolkit/skills/completion-report/SKILL.md`「工程」 |
| 3段判定 | 判断の順序 | `agent-toolkit/rules/01-agent.md`「QCDと3段判定」 |
| 委譲の要否判定 | 委譲の判定 | `agent-toolkit/skills/delegation/references/routing.md` |
| 元担当 | 既存の委譲先 | `agent-toolkit/skills/delegation/references/codex-runtime.md`「後続操作の共通先行条件」 |
| 独立文脈レビュー | レビューの形態 | `agent-toolkit/skills/delegation/references/routing.md`「会話を引き継ぐ委譲」 |
| 委譲元 | 委譲先を起動したエージェント | `agent-toolkit/skills/delegation/SKILL.md` |
| 委譲先 | 委譲元が起動したエージェント | `agent-toolkit/skills/delegation/SKILL.md` |
| 委譲プロンプト | 委譲の指示 | `agent-toolkit/skills/delegation/references/base-contract.md` |
| 返却値 | 委譲先の出力 | `agent-toolkit/skills/delegation/references/base-contract.md` |
| エージェント向け文書 | エージェントが直接読み込む文書の種別 | `agent-toolkit/skills/writing-standards/references/agent-documents-basics.md`冒頭 |
| 読込表 | 文書内の表 | `agent-toolkit/skills/writing-standards/references/agent-documents-basics.md`「読込表」 |
| 配送範囲表 | 文書内の表 | `agent-toolkit/skills/writing-standards/references/delivery-scope.md`冒頭 |
| 常時規範 | 規範の種別 | `agent-toolkit/skills/writing-standards/references/agent-documents-basics.md`「置き場所と配送」 |
| プロジェクト規範 | 規範の種別 | `agent-toolkit/skills/writing-standards/references/agent-documents-basics.md`「置き場所と配送」 |
| プロジェクト方針 | 方針の範囲 | `agent-toolkit/skills/writing-standards/references/agent-documents-basics.md`「置き場所と配送」 |
| 作成規範 | 品質基準 | `agent-toolkit/skills/review-standards/SKILL.md`「対象成果物の作成規範」 |
| 完成条件 | WIの条件 | `agent-toolkit/skills/wi-standards/references/awi-body.md`「通常AWIの本文」 |
| 完了条件 | 計画の条件 | `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`「要件・外部仕様」 |
| レビュー指摘管理表 | 実行レビューの指摘と応答を記録する表 | `agent-toolkit/skills/review-standards/SKILL.md`「レビュー指摘管理表の共通操作」 |
| CI対応レビュー指摘管理表 | 計画の無いCI失敗のレビュー指摘管理表 | `agent-toolkit/skills/review-standards/SKILL.md`「レビュー指摘管理表の共通操作」 |
| セッション記録 | 会話の記録 | `agent-toolkit/skills/writing-standards/references/session-records.md`冒頭 |
| メイン記録 | 会話の記録 | `agent-toolkit/skills/writing-standards/references/session-records.md`「Claude Codeの記録」 |
| サブエージェント記録 | 会話の記録 | `agent-toolkit/skills/writing-standards/references/session-records.md`「Claude Codeの記録」 |
| 計画バンドル | 計画のファイル群 | `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`冒頭の呼称の表 |
| 計画の所有記録 | 計画の局所状態 | `agent-toolkit/skills/plan-mode/references/plan-file-storage.md`「計画ファイルの保存と参照」 |
| バックグラウンドタスク | 非同期の処理 | `agent-toolkit/skills/writing-standards/references/session-state-and-flags.md` |
| バックグラウンドタスクの所有記録 | セッション状態の記録 | `agent-toolkit/skills/writing-standards/references/session-state-and-flags.md` |

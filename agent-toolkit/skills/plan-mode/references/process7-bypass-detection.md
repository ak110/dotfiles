---
# 同期注記: 「メイン側実施済み観点の内訳」欄の趣旨はplan-reviewer.md「入力」節・
# launch-prompts-integrity.mdのplan-reviewer雛形と意図的に重複する（構成は各ファイルの用途に合わせる）。
# 改訂時は3ファイルを同時更新する。
---
# 整合性チェック・codexレビューのバイパス機械検出の詳細規定

本ファイルは`integrity-checks.md`から分離した詳細規定である。
対象節は「変更履歴と変更内容の対応照合」「整合性チェック・codexレビューの実施手順」
「整合性チェック・codexレビューのバイパス機械検出」「サブエージェント連携の設計整合性」とする。

## 変更履歴と変更内容の対応照合

`## 変更履歴`の各項目要約に含まれるバッククォートトークン（ファイルパス・節名アンカー）が
`## 変更内容`側H3見出し・対象ファイル一覧に対応するかを`pretooluse.py`が機械検査する。
既存の意味の同期確認（`integrity-checks.md`「計画文内・他ファイルとの整合」節）と両者を並行実施する。

## 整合性チェック・codexレビューの実施手順

1. 計画ファイル初版Write完了後、同一メッセージ内で次を並列起動する
   - codexレビュー: `plan-codex-delegate`を観点分担で並列起動する。
     並列度・観点分担の詳細は`codex-review.md`「plan-file-creatorからの起動」節に従う
     （`mcp__codex__codex`直接経路でも事前のReadを必須とする。別ターンでの先行実行は不採用とする）
   - サブエージェント`plan-reviewer`: `codex-review.md`「codex利用可否の3段階判定」節の
     段階3が成立した場合のみ起動するフォールバックとする。
     起動する場合の観点・雛形・埋め込み欄は次のとおり（既定経路では本項は適用しない）。
     観点は「計画文内・他ファイルとの整合」「変更履歴と変更内容の対応照合」「機械チェック適合性」
     「編集対象スキル固有規定の事前適用」「サブエージェント連携の設計整合性」とする。
     これに「対象ファイル現状との突合による単体品質・日本語表現の重大不備」を新たに追加する
     - 起動プロンプトは`launch-prompts-integrity.md`「plan-reviewer雛形」節を機械転記して構築する。
       埋め込み欄は計画ファイルパス・対象ファイル現状の絶対パスリスト・関連参照ファイル一覧・
       メイン側担当節の実施有無・メイン側実施済み観点の内訳・前回指摘一覧・担当観点・参照すべき規範スキル・完了報告書式
     - plan-reviewer雛形の「メイン側実施済み観点の内訳」欄へ、
       機械チェック実施結果・遡及スキャン結果・横断grep確認結果を機械的に列挙して転記する
     - 転記された実施済み観点はplan-reviewer側で再点検を省略し未カバー観点へ集中する。
       転記されない観点は既存規定に従い継続点検する（本欄の文言重複は本ファイル冒頭の同期注記を参照）
     - 対象ファイル現状との突合観点は、計画が成立しない致命的な単体品質・日本語表現の不備検出に限定する。
       体裁・表記の指摘は軽微指摘として扱い、反映を必須としない
   - 次のサブエージェント群（`codexレビュー`・`plan-reviewer`と、条件を満たすときの`agent-doc-validator`）は
     省略・軽量化の対象外とする。「縮退表明は発行しない」は`agent-toolkit/rules/01-agent.md`「完遂原則」項に従う
   - サブエージェント`agent-doc-validator`（条件付き起動）:
     `## 変更内容`「対象ファイル一覧」にコーディングエージェント向け文書対象ファイル（対象範囲は
     `process7-bypass-detection.md`の`agent_doc_validator_invoked`項に集約）が含まれる場合のみ、
     計画ファイル本文と対象ファイル現状を突合し`01-agent.md`方針および`agent-standards`スキル方針への適合性をレビューする
     - 起動プロンプトは`launch-prompts-integrity.md`「agent-doc-validator雛形」節を機械転記して構築する。
       埋め込み欄は計画ファイルパス・対象ファイル一覧・前回指摘一覧・担当観点・参照すべき規範スキル・完了報告書式
     - 非起動判定時は`## 変更内容`「対象ファイル一覧」の機械照合結果を判定根拠として
       完了報告の`review_summary`欄`agent-doc-validator`行へ記載する
2. 「ユーザー発話・提示素材との照合」と「計画文内・他ファイルとの整合」の
   ユーザー発話照合・横断grep確認部分は、呼び出し元が`plan-file-creator`起動前に実施済みの
   結果を入力として受け取る
3. 全指摘が出揃った時点で重大度に基づき対応要否を判断する。
   軽微指摘はplan-file-creatorの判断で取捨し、対応する指摘を計画ファイルの該当セクションへ反映する
   - 判断前に`plan-reviewer`・`agent-doc-validator`の各完了報告の冒頭にある
     `## 観点網羅`欄を検査する。未点検（`[ ]`）観点が残る場合は当該観点のみを対象に限定して
     再起動する（全観点の再レビューはしない）
   - 指摘の件数・重大度・反映に要する変更規模（設計変更・共有モジュール新設等を含む）によらず、
     同一セッション内で計画ファイルへ反映して後続工程（`ExitPlanMode`・`plan-impl-executor`起動）へ続行する。
     縮退禁止は`agent-toolkit/rules/01-agent.md`「完遂原則」項に従う
   - 設計判断を要する指摘（構成要素の配置先・責務帰属・データの格納先・方式選択等）を
     サブエージェントへ委譲する場合、委譲元が対応方針を確定し、
     指摘ごとの確定済み方針を委譲プロンプトへ含める。
     設計判断分の委譲先への委ねを避けて計画内の記述間矛盾の再発を防ぐ
4. 反映後にplan-file-creatorが機械チェックを実行する
   - コマンド: `uvx pyfltr run-for-agent --no-fix --work-dir=. <計画ファイルパス>`（cwdをプロジェクトルートに設定）
   - `--no-fix`必須（`## 背景`原文転記領域の無断整形を避けるため）。
     `pre-commit`はリポジトリ外パスでエラーになるが他コマンド結果を主軸とする
   - 判定対象は計画ファイル本文全域だが`## 背景`配下の原文転記領域は違反許容
     （`integrity-checks.md`「ユーザー発話・提示素材との照合」節既定）
   - 並列チェッカーが構造違反を検出していた場合は、本ステップで指摘を反映し機械チェック違反を同時解消する
   - 検出違反は計画ファイル本文へ反映する。
     `plan-file-creator`は完了報告のみを返し、`ExitPlanMode`の呼び出し可否判定は呼び出し元（メイン）が
     `permission_mode`に応じて工程7への遷移時に行う
5. 指摘反映後・スコープ追加後の再レビュー起動は、反映済み指摘・確定済み仕様を審査対象外と明示し、
   審査対象を差分・追加スコープへ限定する（担当観点維持。前回指摘は雛形の前回指摘一覧の欄へ転記）。
   反映確認は機械チェックの再実行と、反映箇所のplan-file-creatorによる自己照合で完結してよい。
   codexレビューは`threadId`（MCP版）または`SESSION_ID`（CLI版）を継続させる
   （全面改訂時の破棄規定は`codex-review.md`既定に従う）
6. 完了判定は`agent-toolkit/agents/plan-file-creator.md`「進め方」節の完了条件（レビュー・機械チェック1周実施、重大指摘の全消化、exit 0通過）に従う
7. 人的レビューで再発検出された事象は、同種の網羅化を機械チェック側へ前段化する
   （規則化可能なパターンは統合ランナーへ検査関数を追加する）

## 整合性チェック・codexレビューのバイパス機械検出

`plan-file-creator`が内部で起動する各サブエージェント（`codexレビュー`・`plan-reviewer`）の起動は
次のセッション状態フラグへ記録される（`agent-doc-validator`は条件付きフラグとして扱う）。

- `plan_reviewer_invoked`（記録は継続するが、下記のPreToolUseゲートは本フラグを必須としない。
  `codex-review.md`「codex利用可否の3段階判定」節の段階3が成立した場合のみ
  `plan-reviewer`を起動しフラグを立てる自己統治規定とする）
- `codex_review_invoked`（PreToolUseゲートの必須フラグ）
- `agent_doc_validator_invoked`は条件付きで扱い、対象は`## 変更内容`「対象ファイル一覧」に
  コーディングエージェント向け文書対象ファイルが含まれる計画とする。
  該当ファイル群: `agent-toolkit/rules/`・`.claude/rules/`・`.claude/skills/`・`agent-toolkit/agents/`配下、
  `agent-toolkit/skills/`配下、`.chezmoi-source/dot_claude/rules/`・`.chezmoi-source/dot_claude/skills/`配下、
  `AGENTS.md`、`CLAUDE.md`

記録は`agent-toolkit/scripts/posttooluse.py`が担う。
`agent-toolkit:plan-file-creator`配下から起動された場合の記録先・伝播経路は、`session-state-flags.md`のplan-file-creator関連フラグ項を参照する。
`agent-toolkit/scripts/pretooluse.py`は`ExitPlanMode`と`plan-impl-executor`起動
（Agentツール`subagent_type`判定）の両ハンドラを持つ。
`codex_review_invoked`（条件成立時は`agent_doc_validator_invoked`を含む）の未起動時にブロックする。
`plan_reviewer_invoked`は本ゲートの判定対象外とする。
フラグは新計画着手時（`agent-toolkit:plan-mode`スキル起動時）にリセットする。

## サブエージェント連携の設計整合性

スキル・サブエージェント定義・計画ファイル間の連携設計を点検する。

- 起動プロンプトへ埋め込む情報の必要十分性: 独立コンテキストで起動するサブエージェントが
  判断・実装に必要とする情報（対象パス・規範参照・成功条件等）が起動プロンプトへ漏れなく列挙されているか照合する
- 必要な規範スキルの呼び出し明記: サブエージェントが呼び出すべきスキル群
  （`agent-standards`・`writing-standards`・`review-standards`等）が計画本文で明示されているか照合する

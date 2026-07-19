---
# 同期注記: 「共通遵守事項」節の「サブエージェント検知コードのSSOT参照確認」バレットは
# `agent-toolkit/skills/agent-standards/SKILL.md`「セッション状態フラグ」節と意図的に重複する。
# 改訂時は両ファイルを同時更新する。
# 同期注記: 「共通遵守事項」節の「起草前textlint-violations.md読み込み」バレットは`plan-file-guidelines.md`「計画ファイル全体の遵守事項」節をSSOTとする。
# 本ファイルと`agent-toolkit/rules/03-claude-code.md`起草関連バレットは当該SSOTを参照する形で同期する（改訂時は3ファイルを同時更新する）。
# named subagent能動送付規定は `agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節・
# `agent-toolkit/skills/plan-mode/references/launch-prompts-integrity.md`・
# `agent-toolkit/skills/process-feedbacks/references/explore-template.md`「Explore委譲雛形」節配下「制約」ブロック
# と意図的に重複する
# 改訂時は4ファイルを同時更新する
# 加えて `agent-toolkit/agents/plan-implementer.md`「出力」節との同期は本ファイル
# 「## 運用ガイダンス」の既存規定に従う。
# 非同期処理継続義務のバレット（「共通遵守事項」節およびコードブロック内`## 完遂条件`欄）は
# `agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節と
# `agent-toolkit/agents/plan-impl-executor.md`「停止禁止」節の同旨規定と意図的に重複する。
# 改訂時は3ファイルを同時更新する。
# 「共通遵守事項」節・コードブロック内`## 完遂条件`欄の`git diff --stat`実体照合バレットは
# `agent-toolkit/agents/plan-impl-executor.md`「出力」節・`agent-toolkit/agents/plan-implementer.md`「出力」節・
# `agent-toolkit/agents/spec-driven-implementer.md`「出力」節の`verification`欄記述と意図的に重複する。
# 背景再委譲禁止バレット（fb08反映）は`agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節の
# 非同期処理の完遂義務（background再委譲時の待機義務）とは独立の新設規定であり、当該節をSSOTとしない。
# 本ファイル内の2箇所（「共通遵守事項」節・「起草・改訂委譲雛形」節コードブロック内`## 完遂条件`欄）を同時更新する。
---

# 計画ファイル起草・改訂委譲プロンプト雛形

`plan-impl-executor`の`plan-implementer`起動プロンプトを、
コピー可能な完全コードブロックで集約する。
`agent-toolkit/references/plan-impl/execution-process.md`「サブエージェント起動プロンプト」節から機械転記して使用する。

## 共通遵守事項

本節を正とし、`## 起草・改訂委譲雛形`節のコードブロック内`## 完遂条件`欄は本節から機械転記した重複である。
機械転記時に起動プロンプト本文へ含めるため意図的に重複させている。改訂時は本節を先に更新し、コードブロック内欄へ反映する。

雛形適用時は次の完遂条件を必ず含める。
`plan-implementer`が計画本文の起草・改訂タスクを担当する場合、完遂条件は当該タスクの成果物へ適用する。
実装のみを担当するタスクでは、完遂条件のうち「計画本文への記載」項目は呼び出し元（`plan-impl-executor`）の責務として残る。
`plan-implementer`は`wc -l`実測結果・`grep`一致確認結果・実在確認結果・`pyfltr`通過結果を完了報告で共有する形で完遂する。

- 計画本文に記載する対象ファイル一覧の行数は現行`wc -l`実測値のみとし、見込み行数の厳密な事前算出（scratchpad複製・仮適用実測を含む）は行わない
- 計画本文に書く関数名・シグネチャは節間`grep`で一致確認する
- 計画に記載するファイルパス・規範名は`test -e`または`Read`で実在確認する
- 変更対象の関数・定数・フィクスチャについて定義元ファイルを`grep`で特定し、
  シグネチャ・形式の変更を伴う場合は定義元とその利用側・対応テスト・共有フィクスチャを
  対象ファイル一覧へ含める
- 完了報告前に`uvx pyfltr run-for-agent --no-fix <対象>`を通過させる
  （`--no-fix`はfixステージを止めるため、事前に別途fixステージを実行済みとする）
- 計画ファイル本文の起草・改訂タスクでは`<対象>`に計画ファイル自体も含める
- `check_plan_file.py`はtextlintのsentence-length等の全文検査を内包しないため、当該検査を委譲時点で通過させる
- サブエージェントのgit操作は既定で禁止する。単独起動で`git push`等をサブエージェントに担わせる必要がある場合は、
  メイン側の起動プロンプトへ許可する操作を明示的に記述する
- サブエージェントによる規範文書・スキル定義・サブエージェント定義の編集は
  `agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節の例外規定に従い、
  メイン側の起動プロンプトで改訂許可を明示された範囲でのみ行う
- コーディングエージェント向け文書の縮減・統合・縮小を含むタスクを委譲する場合、
  起動プロンプトへ`agent-toolkit:trim-agent-docs`スキルの起動指示を明示する
  （縮減観点の適用と再発予防のため）
- サブエージェント起動・完了・名前解決に関わるコード（`Agent`・`Task`のtool_use判定等）を
  新規追加・改訂する場合、`agent-toolkit:agent-standards`「セッション状態フラグ」節の
  `tool_name in ("Agent", "Task")`SSOT表記を`grep`で確認して同一集合を使う
- 計画専用機械チェックとして統合ランナー
  `agent-toolkit/skills/plan-mode/scripts/check_plan_file.py`を起草完了前に実行し違反・警告を解消する
- 起草前に`agent-toolkit/skills/writing-standards/references/textlint-violations.md`「頻出違反パターン予防策」節を`Read`で読み込む
  - SSOTは`plan-file-guidelines.md`「計画ファイル全体の遵守事項」節
  - 差分ラベルを含む計画では`plan-file-diff-labels.md`「フェンス配置」節と`plan-file-diff-labels.md`「差分ラベル6種」節も`Read`で読み込む
- named subagentとして`run_in_background=true`起動する場合、
  完了時に完了報告本文をSendMessage(to: 'main')で能動送付する義務を必須ゲートとする。
  `idle_notification(available)`のみでメイン要求を待つ挙動は未完遂扱いとし、SubagentStopフックがブロックする
- foreground起動・background起動の別を問わず、Agent/Task/fork系Skillの完了報告本文の
  async-wait表明はPostToolUse側の検出対象に含まれる。
  async-wait表明は待機表明のまま完了扱いにする挙動を指す。
  検出時は`decision: block`で再委譲または継続駆動を促す
- 起草・改訂委譲の担当範囲は起草作業と`check_plan_file.py`による機械チェック通過までとする。
  `plan-implementer`は計画本文の起草・改訂に専任し、整合性チェック・codexレビュー相当の
  サブエージェント起動は行わない。当該工程は`agent-toolkit:plan-file-creator`が
  自身の担当範囲として実施する（`plan-implementer`とは役割が異なる別エージェントであり、
  本雛形の起草・改訂委譲先ではない）
- `plan-implementer`が非同期処理（`run_in_background=true`のBashジョブ・別コマンドの完了待ち等）を伴う場合は
  当該待機の完了まで動作を継続する。完了報告本文に待機表明を含めない
  （詳細規定は`agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節の非同期処理に係る完遂義務に従う）
- 委譲されたタスクは自身で同期実行し、配下エージェントへのbackground再委譲をしない。
  `agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節の非同期処理の完遂義務とは独立の新設規定とする
- 完了報告前に`git diff --stat`を実行し、対象ファイル一覧の各ファイルが期待する差分を含んでいるか実体照合する。
  照合結果を完了報告の`verification`欄に含める。
  版更新（`agent_toolkit_bump.py`）・自動生成物は`pyfltr`のpass結果と差分実体が乖離しやすいため実体差分確認を義務とする
- 新設ファイル・新設節の変更方針は執筆指示文（「〜を移設する」「〜を記載する」等）ではなく、
  適用後にそのままファイル本文となる完成形の文面で`text`コードブロック内に記載する
- 起草・改訂対象領域の同期先・SSOT一覧と、計画が新設・拡張する機構の領域固有規約リファレンスの
  読込指示を含める。対象ファイル一覧の網羅確認と領域固有規約の適合確認に用いる。
  対象が`agent-toolkit/`配下の場合は、編集用スキル（`agent-toolkit-edit`等）の
  同期先ドキュメント節・セッション状態フラグ節・関連規範referencesを対象とする

## 起草・改訂委譲雛形

`plan-implementer`サブエージェントの起動プロンプトは次のとおり。
雛形コードブロック内の埋め込み欄の説明（「同欄は〜使用される」）は
サブエージェントが条件分岐に用いる情報のためコードブロック内に残す。

    以下のタスクを{実装 | 修正再実装}してください。

    - 計画ファイル: `{呼び出し元から渡された計画ファイルパス}`
    - 編集対象の文書種別: {コーディングエージェント向け文書 | 一般ドキュメント | コード・テストコード}
      同欄は`plan-implementer`側の規範スキル読込条件分岐に使用される
    - 担当範囲区分: {全項目担当 | 部分担当（範囲: <明示>）}
      同欄は`plan-implementer`側の`completed`返却条件判定に使用される

    ## タスク

    {タスクの具体的な記述}

    計画ファイル本文の`## 変更内容`該当節を1文以上引用したうえで実装する。
    複数の共通境界を候補列挙している計画では、いずれか1つに限定せず実装対象として明記された全候補を実装し、
    テスト影響がある場合はメインへエスカレーションする。
    本タスク範囲は段階化・将来拡張等のラベルを付けた先送りをせず全件完遂すること。
    一部のみ実装して`completed`を返さないこと。

    ## 完遂条件

    次を必ず適用してから`completed`を返す。

    - 計画本文に記載する対象ファイル一覧の行数は現行`wc -l`実測値のみとし、見込み行数の厳密な事前算出（scratchpad複製・仮適用実測を含む）は行わない
    - 計画本文に書く関数名・シグネチャは節間`grep`で一致確認する
    - 計画に記載するファイルパス・規範名は`test -e`または`Read`で実在確認する
    - 完了報告前に`uvx pyfltr run-for-agent --no-fix <対象>`を通過させる
      （`--no-fix`はfixステージを止めるため、事前に別途fixステージを実行済みとする）
    - 計画ファイル本文の起草・改訂タスクでは`<対象>`に計画ファイル自体も含める
    - `check_plan_file.py`はtextlintのsentence-length等の全文検査を内包しないため、当該検査を委譲時点で通過させる
    - サブエージェントのgit操作は既定で禁止する。単独起動で`git push`等をサブエージェントに担わせる必要がある場合は、
      メイン側の起動プロンプトへ許可する操作を明示的に記述する
    - サブエージェントによる規範文書・スキル定義・サブエージェント定義の編集は
      `agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節の例外規定に従い、
      メイン側の起動プロンプトで改訂許可を明示された範囲でのみ行う
    - コーディングエージェント向け文書の縮減・統合・縮小を含むタスクを委譲する場合、
      起動プロンプトへ`agent-toolkit:trim-agent-docs`スキルの起動指示を明示する（縮減観点の適用と再発予防のため）
    - サブエージェント起動・完了・名前解決に関わるコード（`Agent`・`Task`のtool_use判定等）を
      新規追加・改訂する場合、`agent-toolkit:agent-standards`「セッション状態フラグ」節の
      `tool_name in ("Agent", "Task")`SSOT表記を`grep`で確認して同一集合を使う
    - 計画専用機械チェックとして統合ランナー
      `agent-toolkit/skills/plan-mode/scripts/check_plan_file.py`を起草完了前に実行し違反・警告を解消する
    - 起草前に`agent-toolkit/skills/writing-standards/references/textlint-violations.md`を`Read`で読み込み頻出違反パターン予防策の節に従う（SSOT: `plan-file-guidelines.md`「計画ファイル全体の遵守事項」節）
      - 差分ラベルを含む計画では`plan-file-diff-labels.md`「フェンス配置」節と`plan-file-diff-labels.md`「差分ラベル6種」節も`Read`で読み込む
    - named subagentとして`run_in_background=true`起動する場合、
      完了時に完了報告本文をSendMessage(to: 'main')で能動送付する義務を必須ゲートとする。
      `idle_notification(available)`のみでメイン要求を待つ挙動は未完遂扱いとし、SubagentStopフックがブロックする
    - foreground起動・background起動の別を問わず、Agent/Task/fork系Skillの完了報告本文の
      async-wait表明はPostToolUse側の検出対象に含まれる。
      async-wait表明は待機表明のまま完了扱いにする挙動を指す。
      検出時は`decision: block`で再委譲または継続駆動を促す。
    - 起草・改訂委譲の担当範囲は起草作業と`check_plan_file.py`による機械チェック通過までとする。
      `plan-implementer`は計画本文の起草・改訂に専任し、整合性チェック・codexレビュー相当の
      サブエージェント起動は行わない。当該工程は`agent-toolkit:plan-file-creator`が
      自身の担当範囲として実施する（`plan-implementer`とは役割が異なる別エージェントであり、
      本雛形の起草・改訂委譲先ではない）
    - `plan-implementer`が非同期処理（`run_in_background=true`のBashジョブ・別コマンドの完了待ち等）を伴う場合は
      当該待機の完了まで動作を継続する。完了報告本文に待機表明を含めない
      （詳細規定は`agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節の非同期処理に係る完遂義務に従う）
    - 委譲されたタスクは自身で同期実行し、配下エージェントへのbackground再委譲をしない。
      `agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節の非同期処理の完遂義務とは独立の新設規定とする
    - 完了報告前に`git diff --stat`を実行し、対象ファイル一覧の各ファイルが期待する差分を含んでいるか実体照合する。
      照合結果を完了報告の`verification`欄に含める。
      版更新（`agent_toolkit_bump.py`）・自動生成物は`pyfltr`のpass結果と差分実体が乖離しやすいため実体差分確認を義務とする
    - 新設ファイル・新設節の変更方針は執筆指示文（「〜を移設する」「〜を記載する」等）ではなく、
      適用後にそのままファイル本文となる完成形の文面で`text`コードブロック内に記載する
    - 起草・改訂対象領域の同期先・SSOT一覧と、計画が新設・拡張する機構の領域固有規約リファレンスの
      読込指示を含める。対象ファイル一覧の網羅確認と領域固有規約の適合確認に用いる。
      対象が`agent-toolkit/`配下の場合は、編集用スキル（`agent-toolkit-edit`等）の
      同期先ドキュメント節・セッション状態フラグ節・関連規範referencesを対象とする

    ## 修正指摘（修正再実装時のみ）

    {レビュアー指摘テキストとメインが具体化した修正方針（対象ファイルパス・該当位置・修正方針）を
     まとめて記述する。具体化方針は欄内に統合し欄外に分散させない}

    ## 完了報告要件

    `changed`欄では、計画ファイル`## 変更内容`の各タスク項目（親バレット）に対し、
    当該項目に含まれる個別指示と対応箇所を1件につき1サブバレットの形で展開し、必ず1件以上列挙する。
    個別指示はフィードバック番号・修正指摘番号・独立した変更要求等を指す。
    対応箇所は親バレットの`path/to/file`が変更ファイルパスを担い、サブバレットが該当節名・行範囲を担う。
    `path/to/file`はプロジェクトルート起点の絶対パスで記載する
    （呼び出し元が完了報告本文のみで対象ファイルを特定できるようにするため）。
    未着手項目では親バレットが`未着手（needs_escalation時のみ）`を保持する。
    サブバレットは未着手時点で予定する対応箇所を記述する。
    レビュー指摘対応等で実装差分が計画ファイル本文diff案・`### エージェント判断`の記述と乖離した場合、
    `changed`欄の別サブバレット（`[乖離]`ラベル付き）で乖離箇所と実装最終形を明示する。
    書式SSOTは`agent-toolkit/skills/plan-mode/references/plan-file-diff-labels.md`「進捗ログの乖離注記」節に集約する。
    `verification`欄には検証コマンドのpass/failに加えて`git diff --stat`実行結果または差分不一致時の是正内容を含める（対象ファイル一覧の実体照合結果）。

## 運用ガイダンス

本雛形の`## 完了報告要件`を正とし、`agent-toolkit/agents/plan-implementer.md`の`## 出力`節は本雛形の重複である。
機械転記時に起動プロンプト本文へ含めるため意図的に重複させている。改訂時は本雛形を先に更新し、`plan-implementer.md`へ反映する。

起動元は上記の`[乖離]`サブバレット報告を確認したのち、計画本文の同期更新を実施する。

サブエージェント起動プロンプトでは、計画ファイル本文の`## 変更内容`と矛盾する追加指示を書かない。
実装意図の追加補足が必要な場合は、委譲前に計画ファイル本文へ反映してから委譲する。
既存記述の維持範囲・追記位置の詳細指定など、委譲プロンプトが計画ファイル本文を上書き解釈する形の指示は、
plan-implementerが計画ファイル本文と委譲プロンプトの齟齬で判断分岐する構造となり計画ファイル自立性を損なう。
委譲プロンプトで計画ファイル本文の`## 変更内容`節に無い詳細差分を新たに書き起こす運用も、
いかなる理由（例: コンテキスト節約・220行超過ファイル制約回避・多層ネスト起動下の効率化）があっても採用しない。
詳細差分は必ず計画ファイル本文へ事前に埋め込み、委譲プロンプトは計画ファイル本文を引用する形に限定する。

- 起動プロンプト作成前に、プロンプト内で参照する計画ファイルの節名・見出し・記述内容を、
  当該計画ファイル本文の`Read`または`grep`で実在確認する
- 複数回改訂を経た計画ではplan-impl-executorの内部記憶と計画ファイル本文が乖離する可能性があるため、
  委譲直前に本文側を優先確認する

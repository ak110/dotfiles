# 実装レビューの起動詳細

`agent-toolkit/skills/careful-review/SKILL.md`「codex実装レビューの起動」節から分離した詳細を収録する。
収録内容は`plan-codex-delegate`（`用途: 実装差分レビュー`）の分担起動閾値・カテゴリ定義・継続呼び出し方針、
および`plan-impl-reviewer`（フォールバック時）の分割起動閾値とする。

## codexの分担起動閾値

- 既定: 単一`plan-codex-delegate`インスタンスへ対象範囲全体を渡す
- 対象合計ファイル数が10以上または対象合計行数が概ね1000行以上の場合、
  カテゴリ別に2並列起動する（コード・テストコード／一般ドキュメント・コーディングエージェント向け文書）
- 対象合計ファイル数が30以上または差分2000行以上の場合は3並列に分割する
  （コード・テストコード／一般ドキュメント／コーディングエージェント向け文書）

既存`plan-impl-reviewer`の1000行/ファイル・5ファイル/300行の分割閾値は踏襲しない。
`plan-codex-delegate`はcodex側の大容量コンテキスト処理を前提とし、
対象ファイル1件あたりの行数上限を設けない。

## 渡す情報

各`plan-codex-delegate`（`用途: 実装差分レビュー`）インスタンスへ次を渡す。

- プロジェクトルート: `careful-review`実行時のプロジェクトルート絶対パス（`{project_directory}`）
- 対象範囲: ファイルパス列挙またはgit範囲（担当カテゴリの対象ファイルのみに限定する）
- 対象外ファイル: 一時ファイル一覧
- 計画ファイルパス: `careful-review`が受け取った計画ファイルパス（無しの場合は省略）
- 差分取得コマンド: `git diff {基準点}..HEAD`と未追跡ファイルを含む作業ツリー参照
- 担当カテゴリ: コード・テストコード／一般ドキュメント／コーディングエージェント向け文書のいずれか

## 継続呼び出し

`用途: 実装差分レビュー`の再レビューは`threadId`（MCP）・`SESSION_ID`（CLI）いずれの継続方式も使わない。
`plan-spec-reviewer`・フォールバック時の`plan-impl-reviewer`と同様の扱いとする。
`agent-toolkit/skills/careful-review/SKILL.md`「再レビュー」節の新規`Agent`起動方式に従い、毎回独立に評価する。
理由は`agent-toolkit/skills/review-standards/SKILL.md`「レビューの基本姿勢」節の独立評価原則を適用するためである。
`agent-toolkit/skills/careful-review/SKILL.md`の上限なしサイクルモデルは
`codex-review.md`の反映確認レビュー上限（初回+1回）と両立しない。
計画ファイル種別の`threadId`継続方式とは非対称とする。
CLIフォールバック時の出力先は`$(mktemp --suffix=.review.md)`とする。
`plan_full_path`（計画ファイル種別専用の変数）には依存しない
（`careful-review`単独実行時は計画ファイルが存在しないため）。

## plan-impl-reviewerの分割起動（フォールバック時）

`codex-review.md`「codex利用可否の3段階判定」節の段階3が成立し`plan-impl-reviewer`を起動する場合は、
対象ファイル件数と種別に応じて分割起動を検討する。
種別ごとに呼び出すスキルが異なるため、件数が多いほど分割効果が大きい。

- 対象が多い場合は次カテゴリに分けて並列起動する
  - コード・テストコード（`agent-toolkit:coding-standards`＋`agent-toolkit:writing-standards`）
  - 一般ドキュメント（`agent-toolkit:writing-standards`）
  - コーディングエージェント向け文書（`agent-toolkit:writing-standards`＋`agent-toolkit:agent-standards`）
- 対象が合計5ファイル以上または合計差分が概ね300行以上の場合はカテゴリ分割を優先する
  （自動bumpで連動更新される`plugin.json`・`marketplace.json`等は閾値カウント対象外）
- 単一カテゴリ内の対象ファイル数が10以上または合計行数が概ね2000行以上の場合は当該カテゴリ内で更に分割起動する。
  APIエラー等の異常終了時の再委譲手順は`agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節に従う
- 対象合計ファイル数が30以上または合計差分が2000行以上の場合は`plan-impl-reviewer`の初回並列度を3以下に抑える。
  一般ドキュメント・コーディングエージェント向け文書を統合し2グループへ集約する（併読は`agent-toolkit:writing-standards`・
  `agent-toolkit:agent-standards`）。本条件は単一カテゴリ内分割ルールより優先し、`plan-spec-reviewer`・
  `agent-doc-validator`の独立起動は維持する。重大以上の指摘が集中する領域のみ2サイクル目で追加分割する

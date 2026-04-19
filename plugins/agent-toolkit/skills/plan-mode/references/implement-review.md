# 実装・レビューフェーズの詳細手順

`plan-mode`スキルで`ExitPlanMode`後に進める実装フェーズとレビューフェーズの詳細手順、
サブエージェント呼び出しテンプレート、レビュアー出力ファイルパス規約をまとめる。
SKILL.md本体から分離したのは、記述量が多く本体の肥大化を避けるため。

## 全体の流れ

1. `ExitPlanMode`直後にメインが実装着手直前のHEADを`BASE_SHA`として記録する
2. 計画ファイルの「変更内容」節を起点にタスク分解し、`TaskCreate`へ登録する
3. 各タスクを`plan-implementer`へ順次委譲する
4. format/lint/test合格後、必要なら`spec-reviewer`→`code-quality-reviewer`の順でレビュアーを直列起動する
5. 指摘があれば該当タスクを`plan-implementer`へ差し戻し、合格まで繰り返す
6. レビュー合格後に成果物を変更した場合はformat/lint/testとレビューを再実行する

軽微な実装（例: 数行の修正・既存パターンを踏襲した追加・ドキュメントのみの修正など）は、
メイン判断でレビュー省略してよい。判断基準は本ファイル末尾の「レビュー省略の判断基準」節を参照する。

`spec-driven`文脈から呼ばれた場合も同じ流れで進める（呼び元による分岐は設けない）。
`spec-driven`文脈では`ExitPlanMode`直後にメイン側で`spec-writer`を並行起動するが、
`plan-mode`はそれを意識せず実装フェーズを進めてよい（両者とも計画ファイルをインプットとして独立に動く）。

## 実装フェーズ

### BASE_SHAの記録

`ExitPlanMode`直後、実装着手前のHEADを`BASE_SHA`として記録する。

```bash
git rev-parse HEAD
```

レビュー時の差分起点として`BASE_SHA`を参照する。
`plan-implementer`はタスク単位でコミットしない設計のため、レビュー時点の差分は未コミットの作業ツリー差分として扱う。

### タスク分解とTaskCreate登録

計画ファイルの「変更内容」節を起点にタスクを確定し、`TaskCreate`へ登録する（検証・コミットタスクも含める）。

タスク粒度の目安:

- 1タスクあたり1〜3ファイル程度の変更
- 1タスク内で検証が完結するよう、ファイル横断の依存を避ける
- テストコードの追加は対応する実装タスクと同一タスクにまとめる

### plan-implementer委譲

`TaskCreate`の未完了タスクを先頭から1つずつ取り出し、`plan-implementer`エージェントへ委譲する。
実装結果を確認し、`TaskUpdate`で完了にする。

plan-implementer呼び出しテンプレート:

```text
以下のタスクを実装してください。

タスク: {タスクの具体的な記述}
計画ファイル: `~/.claude/plans/{自動生成ファイル名}.md`
{spec-driven文脈のみ} 作業版ドキュメント: `docs/v{next}/{作業テーマ名}.md`
差し戻し指摘（該当時のみ）: {レビュアー出力ファイルパス}
制約: coding-standardsスキルを事前に呼び出し、品質基準に従うこと。gitコミット・pushは行わないこと
完了条件: 変更後にプロジェクトのformat/lint/testが全て通ること
```

## レビューフェーズ

### 前提

レビューフェーズに入る前に以下を満たしていることを確認する。

- 全タスクの実装が完了している
- `plan-implementer`以外の最終調整（`spec-driven`文脈では`spec-writer`による作業版ドキュメントの立ち上げ含む）が完了している
- プロジェクトのformat/lint/testが合格している

### spec-reviewer起動

仕様適合性・ドキュメント整合性レビューを`spec-reviewer`に委譲する。

spec-reviewer呼び出しテンプレート:

```text
以下の実装について仕様適合性レビューを実施してください。

計画ファイル: `~/.claude/plans/{自動生成ファイル名}.md`
BASE_SHA: {ExitPlanMode直後に記録したHEAD}
対象外ファイル: {一時ファイル一覧}
出力ファイル: {出力ファイルパス}（差し戻しループでは上書き）

{spec-driven文脈のみ}
作業テーマ名: {作業テーマ名}
作業版ドキュメント: `docs/v{next}/{作業テーマ名}.md`
差分内の他の作業版ドキュメント・`README.md`・横断ドキュメント: {同一開発中バージョンディレクトリ配下の他作業テーマ・横断・README.mdのうち差分に含まれるもの}
恒常配置側の該当ドキュメント: `docs/features/{機能名}.md` または `docs/topics/{トピック名}.md`（既存改修時のみ）
改修前の該当節スナップショット: `docs/v{next}/{作業テーマ名}.md`内の「改修前スナップショット」節（既存改修時のみ）

差分取得: `git diff {BASE_SHA}`と、未追跡ファイルを含む作業ツリー参照で行うこと
制約: 書き込みは出力ファイル1つのみ。実装者レポートは鵜呑みにせず、コードと差分で独立検証すること。gitコミット・pushは行わないこと
戻り値: 判定（✅/❌）と指摘件数
```

指摘があれば該当タスクを`plan-implementer`へ差し戻す（レビュアー出力ファイルのパスを差し戻し指摘として渡す）。
再実装後はformat/lint/testを再実行し、同じレビュアー出力ファイルを上書きしながら合格まで繰り返す。
`✅ 仕様適合`が返るまで次ステップへ進まない。

### code-quality-reviewer起動

`spec-reviewer`合格後、コード品質レビューを`code-quality-reviewer`に委譲する。

code-quality-reviewer呼び出しテンプレート:

```text
以下の実装についてコード品質レビューを実施してください。

計画ファイル: `~/.claude/plans/{自動生成ファイル名}.md`
BASE_SHA: {ExitPlanMode直後に記録したHEAD}
対象外ファイル: {一時ファイル一覧}
出力ファイル: {出力ファイルパス}（差し戻しループでは上書き）

{spec-driven文脈のみ}
作業テーマ名: {作業テーマ名}
作業版ドキュメント: `docs/v{next}/{作業テーマ名}.md`

差分取得: `git diff {BASE_SHA}`と、未追跡ファイルを含む作業ツリー参照で行うこと
制約: 書き込みは出力ファイル1つのみ。coding-standardsスキルを事前に呼び出し、品質基準に従うこと。gitコミット・pushは行わないこと
戻り値: Assessment（approve/reject）と指摘件数（Critical/Important/Minor別）
```

指摘があれば`plan-implementer`へ差し戻して再実装する。
再実装後はformat/lint/testと`spec-reviewer`の再実行もループに含める。
`Assessment: approve`が返るまで次ステップへ進まない。
指摘が繰り返される場合（同種の指摘が2回以上続くなど）はループを中断してユーザーへエスカレーションする。

### レビュー合格後の変更時の再実行規則

レビュー合格後に成果物（コード・ドキュメント・テストなど）を変更した場合は、
変更量に応じてformat/lint/testと各レビュアーを再実行する。

- コード変更あり: format/lint/test →`spec-reviewer`→`code-quality-reviewer`を再実行
- ドキュメントのみの変更（`spec-driven`文脈）: format/lint/test→`spec-reviewer`を再実行。
  コード挙動が変わらないため`code-quality-reviewer`は省略してよい
- 軽微な誤字修正・コメント調整など: メイン判断で再実行省略可

## レビュー省略の判断基準

以下の条件を総合的に勘案し、レビュー省略が妥当かメインが判断する。

- 変更規模が小さい（数行〜数十行程度、1〜2ファイル）
- 影響範囲が限定的（公開インターフェースを変更しない、他モジュールへ波及しない）
- 既存パターンを踏襲している（類似実装が既に存在する、設計判断を伴わない）
- 判断の余地が少ない（単純な誤字修正・既知バグの明確な修正など）
- テスト済み・検証済み（自動テストで挙動が担保されている）

レビュー省略時は計画ファイルの「変更履歴」節にその判断理由を記録する。
判断に迷ったら省略せず実行する方針とする。

## レビュアー出力ファイルパス規約

計画ファイル名（`~/.claude/plans/{自動生成ファイル名}.md`）から派生する出力ファイル名は、
拡張子直前に`.review-spec`・`.review-quality`を挿入する規則で生成する。

- `spec-driven`文脈: `docs/v{next}/.cache/{作業テーマ名}.review-{kind}.md`
- 単独`plan-mode`文脈: `~/.claude/plans/{plan名}.review-{kind}.md`
  - 例: `~/.claude/plans/foo-bar-plan-m-xxx.md` →
    `~/.claude/plans/foo-bar-plan-m-xxx.review-spec.md`・`~/.claude/plans/foo-bar-plan-m-xxx.review-quality.md`

差し戻しループでは同一ファイルを上書きする（前回指摘と混在させない）。
レビュー完了後、`spec-driven`文脈のファイルはCleanupで削除する。
単独`plan-mode`文脈のファイルは計画ファイル本体と同じ`~/.claude/plans/`配下に残るが、
作業完了後の扱いはユーザー判断に委ねる（通常は削除してよい）。

## サブエージェントへのコミット禁止指示

上記の各呼び出しテンプレートには「gitコミット・pushは行わないこと」を制約として必ず含める。
コミット事故防止のため、コミット・pushはメインの責務として一元管理する。
詳細は配布ルール`~/.claude/rules/agent-toolkit/agent.md`の「検証とコミット」節を参照。

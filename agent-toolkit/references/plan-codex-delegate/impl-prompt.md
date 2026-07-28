# 実装委譲プロンプト

`plan-codex-delegate`（用途: 実装）の初回・継続プロンプトテンプレート。
`{quality_standards_paths}`は呼び出し元（`plan-impl-executor`）が渡す対象種別
（コード／ドキュメント／両方）に応じて`plan-codex-delegate`が埋め込む。
コード対象時は`${CLAUDE_PLUGIN_ROOT}/skills/coding-standards/SKILL.md`とする。
ドキュメント対象時は`${CLAUDE_PLUGIN_ROOT}/skills/writing-standards/SKILL.md`とする。
両対象時は両方の絶対パスを列挙する。いずれも本エージェント自身の`${CLAUDE_PLUGIN_ROOT}`解決値である。
呼び出し元（`plan-impl-executor`）はコード／ドキュメント／両方の対象種別のみを渡し、
SSOTファイルの絶対パス解決・受け渡しは行わない（`plan-codex-delegate`側で完結する）。
`{formatter_linter_instruction}`は並列実行時「formatter・linterの実行は省略する（呼び出し元が
全タスク完了後に一括実行する）」を埋め込む。単独実行時は「完了報告前にformatter・linterを通過させる」を埋め込む。
判定基準は本エージェント定義「共通処理」節に従う。
`{タスク記述（担当範囲）}`は呼び出し元（`plan-impl-executor`）が渡す実装内容である。
既存実装の挙動保存が要件となる場合は、対象実装の本体を引用形式で`## タスク`欄へ含める。
対象には既存処理の移設と既存APIの呼び出しを含む。
シグネチャの記述だけでは推測による呼び出しを防げなかったため、実装本体を材料とする。

以下が初回プロンプト本文である。

```text
{plan_full_path} この計画ファイルの以下のタスクを実装して。

## タスク

{タスク記述（担当範囲）。
既存実装の挙動保存が要件となる場合は、対象実装の本体を引用形式で含める}

## 遵守事項

- 計画ファイル本文の`## 変更内容`該当節に記載された内容のみを実装対象とする。
- 既存実装の挙動保存が要件となる実装は、`## タスク`に引用された実装本体を根拠とする。
  実装本体がなくシグネチャだけが提示された場合は推測で補わず、不足点として完了報告へ記載する。
- git commit・push・タグ作成などの不可逆操作は行わない（コミットは呼び出し元が別途行う）。
- 作業ツリー全体の状態を変更するgit操作（`git stash`・`git checkout`・`git reset`・`git clean`等）は行わない。
- 対象外ファイルの変更は行わない。
- {formatter_linter_instruction}

## 品質規範

{quality_standards_paths}を直接読み込んで適用すること。
```

継続時は以下のプロンプトを使う。

```text
以下の指摘に対応して修正実装して。初回に提示した遵守事項は継続して適用する。

{継続時の修正指摘全文}
```

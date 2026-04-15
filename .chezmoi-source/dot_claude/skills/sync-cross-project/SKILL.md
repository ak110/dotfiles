---
name: sync-cross-project
description: 作者個人の姉妹プロジェクト群の間でツールチェイン（Makefile、mise、pre-commit、GitHub Actionsなど）やドキュメント構成を揃える際に必ず使う。`/sync-cross-project`、「他プロジェクトへの反映」「プロジェクト間の同期」などのキーワードで自動トリガーしてよい。プロジェクト固有のアプリケーションロジック変更は対象外
user-invocable: true
---

# 姉妹プロジェクト間のツールチェイン・ドキュメント構成同期

## 目的と前提

作者個人の複数の姉妹プロジェクトはツールチェインやドキュメント構成を極力揃える方針であり、コメントの表記揺れや軽微な並び順も含めて一字一句差分が減る方向で整備している。
1プロジェクトで変更した場合、後述のマトリクスに基づいて他プロジェクトへの波及要否を確認し、必要ならユーザーに提案する。

対象プロジェクトの一覧と絶対パスはセッション開始時にコンテキストへロードされるローカル指示から得る。
本スキルでは対象を「対象プロジェクト群」と抽象的に扱い、個別プロジェクトのパスは前述のコンテキスト経由で参照する。

明らかにプロジェクト固有の変更や、「ツールチェインやドキュメント構成など」以外の変更であれば確認不要。

## 判定手順

変更内容を受け取ったら以下を実施する。

- 変更内容の分類を特定する（例: pre-commit設定、mise設定、CI workflow、README構成など）
- 「変更時の同期対象マトリクス」で波及プロジェクトを決める
- 「意図的に維持している差異」に該当しないか確認する
- 該当プロジェクトに対して、必要ならサブエージェント（`general-purpose`）で並列調査して差分を把握する
- 同期が必要なプロジェクトと推奨アクションをユーザーに報告する

スコープに含むのは「ツールチェインやドキュメント構成」の範囲。
典型パスは以下。

- ビルド/タスク: `Makefile` / `mise.toml`
- Python設定: `pyproject.toml`
- Node.js設定: `package.json`
- lint/format: `.pre-commit-config.yaml`
- CI: `.github/workflows/**`
- ドキュメント: `README.md` / `CLAUDE.md` / `docs/**/development.md` / `docs/**/security.md`

## 変更時の同期対象マトリクス

変更内容に応じて確認すべきプロジェクトを示す。
プロジェクト名とローカルパスの対応はコンテキスト上のローカル指示から取得する。

| 変更内容 | dotfiles | pyfltr | pytilpack | smpr | glatasks | gv | lc |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GitHub Actions全般 | o | o | o | o | o | o | o |
| リリースワークフロー | - | o | o | - | o | o | o |
| git-cliff設定 | - | o | o | - | o | o | o |
| Makefile構成 | o | o | o | o | o | - | - |
| mise設定 | o | o | o | o | o | o | o |
| pre-commit設定 | o | o | o | o | o | o | o |
| textlintルール | o | o | o | o | o | o | o |
| pyfltr設定・更新 | o | - | o | o | o | o | o |
| pinact/アクション更新 | o | o | o | o | o | o | o |
| UV_FROZEN運用 | o | o | o | o | o | - | - |
| ドキュメント構成 | o | o | o | △ | o | o | o |
| Python CI構成 | o | o | o | o | - | - | - |

o=対象、-=対象外、△=緩め

## 意図的に維持している差異

以下の差異はプロジェクト間で意図的に異なる設定としている。
統一対象外として扱う。

| 差異 | 対象 | 理由 |
| --- | --- | --- |
| `textlint-rule-prh` | dotfilesのみ | Claude Codeのコンテキスト汚染を防ぐためtextlint系を特に厳しくする方針 |
| `--dist=loadfile`（pytest） | dotfilesのみ | テストのファイル単位のセットアップ/ティアダウンに依存するため |
| pytilpackの`docs.yaml`に`paths:`なし | pytilpackのみ | mkdocstringsがPythonソースからドキュメントを生成するため、ソース変更でもdocs workflowが起動する必要がある |

## pnpmに関する既知の注意点

- `pnpm/action-setup` v6は`packageManager`フィールドにSHAハッシュがないとlockfile解析エラーになる場合がある。`corepack use pnpm@<version>`でSHAハッシュ付きに更新すること
- pnpmの最新版では`NPM_CONFIG_*`環境変数の読み取りが不安定（`pnpm config get`がenv varを無視するケースがある）。env var経由の設定反映テストには`npm config get`を使う
- `pnpm-workspace.yaml`の設定は`NPM_CONFIG_*`環境変数より優先される

## 補足事項

注意点・運用ノウハウ・統一方針を以下にまとめる。

### ドキュメント・運用方針

- ツールチェイン周りの修正の場合は以下のメンテナンスも検討する（忘れがちなので注意）
  - `pyfltr/docs/guide/recommended.md`
  - `pyfltr/docs/guide/recommended-nonpython.md`
- 他プロジェクト作業中に `~/.claude/rules/agent-basics/*` や `/agent-toolkit:tidy-unpushed-commits` の問題を発見したらdotfiles側を修正する（マスター）
- 各プロジェクトの `docs/development/development.md` の以下3セクションは共通文面で統一済み。変更時は他プロジェクトへの波及を確認する
  - 「役割分担（末尾2段落）」
  - 「UV_FROZEN（Python系）」
  - 「コミットメッセージ（Conventional Commits）」
- README.mdのセクション構成や記載内容の粒度を変更する場合は全プロジェクトで揃える。共通構成は「概要・特徴・前提条件・インストール・ドキュメントリンク」

### gv / lc（Windows用プロジェクト）の特殊事情

- Linuxでの検証はlint系（prettier / markdownlint / textlint）のみ確認可能。cargo-clippy / cargo-test / cargo-denyはWindowsターゲットのためLinuxでは失敗する
- Makefileではなく `mise.toml` のタスクを使用する。pre-commitフレームワークは `uvx pre-commit` で呼び出す
- `package.json` の `lint` / `lint:fix` スクリプトは `CLAUDE.md` もtextlint / markdownlint-cli2対象に含めている。新規Node系プロジェクトでも同様に設定する
- `taiki-e/install-action@cargo-deny` はツール名タグ形式のためpinactでハッシュピン不可（gvの `.pinact.yaml` で除外済み）

### pre-commit / pyfltr / ビルド関連

- 全プロジェクトでpre-commitフレームワークにより `pyfltr fast` が走る
  - `markdownlint-fast` / `textlint-fast` によりmd変更時のlintが軽量に実行される
  - Python系は `uv run --frozen pyfltr fast`、gv / lc / glatasksは `uvx pyfltr fast` で呼び出す

### CI / リリース関連

- CI workflow（Python系）のステップ順はpyfltrを基準に統一済み
  - Install uv → Setup Node.js → Setup pnpm → Configure pnpm security → Setup mise
  - Install dependencies → Test with pyfltr → Prune uv cache for CI
- `release.yaml` の `GH_TOKEN` は `${{ github.token }}` に統一済み（推奨構文）
- `release.yaml` のCI待機ロジックはbash系（pyfltr / pytilpack / glatasks）が `gh api` + `jq` 方式、PowerShell系（gv / lc）が `check-suites` API方式
- リリース時のバージョニング基準は以下のとおり（セマンティックバージョニングとは異なる）
  - バグ修正・軽微な機能追加: パッチ（CI定義上は「バグフィックス」）
  - 大きめの機能追加・軽微な破壊的変更: マイナー
  - 大規模な機能追加などのみ: メジャー

# 同期対象と意図的な差異

## 変更時の同期対象マトリクス

変更内容に応じて確認すべきプロジェクトを示す。

| 変更内容 | dotfiles | pyfltr | pytilpack | smpr | glatasks | gv | lc |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GitHub Actions全般 | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| リリースワークフロー | N/A | ★ | ★ | N/A | ★ | ★ | ★ |
| git-cliff設定 | N/A | ★ | ★ | N/A | ★ | ★ | ★ |
| Makefile構成 | ★ | ★ | ★ | ★ | ★ | N/A | N/A |
| commit.template設定 | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| mise設定 | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| pre-commit設定 | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| textlintルール | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| pyfltr設定・更新 | ★ | N/A | ★ | ★ | ★ | ★ | ★ |
| pinact/アクション更新 | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| UV_FROZEN運用 | ★ | ★ | ★ | ★ | ★ | N/A | N/A |
| ドキュメント構成 | ★ | ★ | ★ | △ | ★ | ★ | ★ |
| Python CI構成 | ★ | ★ | ★ | ★ | N/A | N/A | N/A |

★=必須同期、△=確認推奨（smprは厳密一致不要）、N/A=スキップ

パッケージ管理系に固有の規範を横展開する場合、対象リポジトリの判定はコンテキスト上のローカル指示の
プロジェクト一覧の記載だけを根拠とせず、対象ファイルまたはロックファイルの実在を確認して確定する。

本スキルの対象範囲（ツールチェイン・ドキュメント構成）に該当すると判定済みで、マトリクスに該当行が無い変更内容は、
「[前提](../SKILL.md#前提)」節の対象プロジェクト一覧を候補集合とする。
判定手順で列挙した適用前提を、各候補の現物を観測して確かめるか、明文化された方針と比べる。
適用前提が成立したプロジェクトだけを★相当の必須確認へ進める。

`commit.template設定`はsetupタスク（`make setup`または`mise run setup`）から
`git config --local commit.template .gitmessage`を呼ぶ実装を指す。
`.gitmessage`本文の追加変更も含め、setup実装と本体ファイルを揃えて変更する。

## 意図的に維持している差異

以下の差異はプロジェクト間で意図的に異なる設定としている。
統一対象外として扱う。

- 非同期テストのloopとfixture寿命: dotfilesと同梱agent-toolkitはfunction単位で隔離し、テスト間の未完了taskや状態の混入を防ぐ。
  姉妹プロジェクトのsession単位の設定は各プロジェクトの要件として維持する。
  ブラウザーなどsession寿命を明示したfixtureは、その宣言した寿命を使う

- `textlint-rule-prh`（dotfilesのみ）: Claude Codeのコンテキスト汚染を防ぐためtextlint系を特に厳しくする方針
- Python対応・CI版集合（dotfilesと同梱agent-toolkit）: ユーザーが過去のPython版をサポートしないと決めたため、対応する版を1つ（現在は3.15）に限り、CIも同じ版で実行する。
  他プロジェクトのPython版matrixへそろえず、版宣言は将来版を許容する下限で表す
- pytilpackの`docs.yaml`に`paths:`なし（pytilpackのみ）: mkdocstringsがPythonソースから
  ドキュメントを生成するため、ソース変更でもdocs workflowが起動する必要がある
- Dependabot alertsの有効・無効（dotfiles・GLATasksは有効、pytilpackは無効）:
  pytilpackはライブラリであり、ロックファイルが開発専用のためエンドユーザーの実行環境への脆弱性の影響が限定的である
- dotfilesの`AGENTS.md`に「開発手順」「注意点」章なし（dotfilesのみ）: dotfilesの`AGENTS.md`は概要と参照先の案内だけへ縮約し、
  開発手順と注意点を`.claude/skills/`配下のスキルへ置くため、
  [references/doc-structure.md](doc-structure.md)の`AGENTS.md`必須章のうちこの2章を適用対象から外す

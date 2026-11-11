# バージョン更新の詳細手順

本書は配布プラグインのバージョン更新と生成物の同期手順をまとめた資料である。
対象は`.claude-plugin/marketplace.json`の`plugins[]`に載る各プラグイン（`agent-toolkit/`と`agent-basics/`）で、判定と更新はプラグインごとに行う。
本書の「定義元の2ファイル」は、対象プラグインの`<プラグインのディレクトリ>/.claude-plugin/plugin.json`と、`.claude-plugin/marketplace.json`の同名エントリを指す。
`.chezmoi-source/`配下のchezmoi配布物、`bin/`配下のCLIラッパー、`scripts/`配下のヘルパースクリプトのみを変更する場合は対象外とし、バージョン番号を据え置く。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 動作検証を実行する前 | `../../develop-dotfiles/references/verification-values-execution.md` |
| 全レーンの完了後にバージョンを更新する時点、および本書の更新手順へ入る前 | `agent-toolkit:process-wi/references/finish-session.md` |
| Codex導入版変更後にrootを再解決する前 | `agent-toolkit:/share/rules/rules-common.codex.md` |
| 公開前の統合差分に`rust/claude-statusline/`の変更がある時、statusline採番規定を確認する前 | `../../release-dotfiles/SKILL.md` |
| 公開前の統合差分に`rust/claude-statusline/`の変更があり、最終検証を選ぶ時 | `./../../develop-dotfiles/references/publishing.md` |

## 判定基準

プラグインのディレクトリ配下に差分がある変更では、`*_test.py`だけの差分を除き、必ずバージョン番号を更新（バンプ）する。
コメント、docstring、文書、誤字、入出力の挙動を変えない内部整理の差分も更新の対象とする。
Codexの導入処理は版数の不一致だけで再導入するため、版数を据え置いた差分は稼働環境へ届かず、導入済みの配布物が原本と食い違う。
この条件は`scripts/check_plugin_version.py`がCIの`plugin-version` jobとpyfltrの`plugin-version`で検査し、据え置いた差分を失敗にする。

- PATCH（`+0.0.1`）: 軽微な修正（不具合修正、フック処理の改善、検出処理の調整、文言の修正、既存見出し内における規範の補強など）
- MINOR（`+0.1.0`）: 機能の追加、検出範囲の大幅な拡大、descriptionの変更、節の新設など、規模の大きい変更
- MAJOR（`+1.0.0`）: ユーザーからの明示的な指示がある場合に限定して適用する（詳細な規定は`agent-toolkit:commit`の`references/publish.md`「版数と生成物」を参照する）
- 現行のバージョンが`major.minor.patch`という3つの数値要素で表せない場合は、プロジェクト固有の対応表またはユーザーからの明示的な指定がある場合にのみ区分を適用する。
  指定がない場合はバージョン更新せず、判定不能として報告する

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| plan modeでの取り扱いに関する判断・作業の前 | `version-bump-planning.md` |
| 競合解決と統合後の確認・未プッシュ範囲での統合・バージョン更新に関する判断・作業の前 | `version-bump-execution.md` |
| Codex導入後のrootの再確認に関する判断・作業の前 | `version-bump-codex-root.md` |
| 新規CLI公開時の疎通確認に関する判断・作業の前 | `version-bump-new-cli.md` |

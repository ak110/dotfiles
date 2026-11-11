# バージョン更新の詳細手順：plan modeでの取り扱い

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 動作検証を実行する前 | `../../develop-dotfiles/references/verification-values-execution.md` |
| 全レーンの完了後にバージョンを更新する時点、および本書の更新手順へ入る前 | `agent-toolkit:process-wi/references/finish-session.md` |
| Codex導入版変更後にrootを再解決する前 | `agent-toolkit:/share/rules/rules-common.codex.md` |
| 公開前の統合差分に`rust/claude-statusline/`の変更がある時、statusline採番規定を確認する前 | `../../release-dotfiles/SKILL.md` |
| 公開前の統合差分に`rust/claude-statusline/`の変更があり、最終検証を選ぶ時 | `./../../develop-dotfiles/references/publishing.md` |

## plan modeでの取り扱い

- 計画ファイルの`## 要件・外部仕様`に、変更のあるプラグインごとの更新種別（PATCH／MINOR／MAJORまたは`bump不要`）と、`version-bump.md`「判定基準」に基づく判断根拠を記述する。
  具体的なバージョン番号の決定や既存の更新コミットとの統合処理は、`scripts/agent_toolkit_bump.py`に委ねる。
  バージョン更新する実装では、検証を実行する前に`scripts/agent_toolkit_bump.py --plugin <プラグイン名> {種別}`をプラグインごとに実行する。
- バージョン更新を伴う計画では、差分の有無にかかわらず定義元の2ファイルを変更対象の説明に含める。
  ジェネレーターが整合性を保証する派生ファイル（派生マニフェストや、`agent-toolkit/share/rules/common/`から生成される`.chezmoi-source/dot_codex/AGENTS.md`など）は、変更対象の説明に列挙しない。
  これらは、正式な生成コマンドの実行と生成結果の一致確認手順を`## 検証`に記述する。
  派生マニフェストは、定義元の更新後に`scripts/sync_codex_plugin_manifests.py`を実行して反映し、`--check`オプションで整合性を確認する。
- バージョン更新の完了条件は、対象プラグインごとに、公開済みの基準（`git push`済みの最新のplugin manifest）に対して要求された種別以上の更新が含まれており、定義元の2ファイルと派生マニフェストの`version`が一致していることとする。
  既存の未プッシュの更新内容で条件を満たしており、`scripts/agent_toolkit_bump.py`がファイル変更なし・終了コード0で終了した場合も完了とみなす。

`agent-toolkit:process-wi`における特則:

- 各レーンは、プラグインごとの更新種別（`bump不要`を含む）とその根拠、ならびにMAJORの場合は認可の根拠を計画ファイルに記録し、レーン内ではバージョン更新コマンドを実行しない。
- AWI本文に記載されたバージョン区分は、起草時点でその要求単体を評価した判定である。
  メインエージェントは、同一の実行サイクルで統合された変更全体に対してプラグインごとに`version-bump.md`「判定基準」を適用し、より上位の区分が存在する場合はそれを採用する。
  公開担当へ渡す`bump種別`はプラグインごとの区分とする（例: agent-toolkitはminor、agent-basicsはpatch）。
  公開担当は、すべてのレーンのマージが完了した後に、変更のあるプラグインごとに`scripts/agent_toolkit_bump.py --plugin <プラグイン名> <種別>`を1回ずつ実行する。
- バージョン更新を完了条件に含むAWIは、`agent-toolkit/share/lane-integration.subagent.md`の延期`adopt`の手順に従い、公開担当がバージョン番号、派生マニフェスト、および公開結果を検収した後に`adopt`する。
- 統合ブランチをpushする前に上流ブランチの進捗を取り込むためrebaseを行った場合は、`version-bump-execution.md`「競合解決と統合後の確認」の基準に基づいて再度比較し、必要に応じて公開担当が再度のバージョン更新とマニフェスト同期を実施する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| バージョン更新の詳細手順：競合解決と統合後の確認の基準を適用する前 | `version-bump-execution.md` |
| バージョン更新の詳細手順の基準を適用する前 | `version-bump.md` |

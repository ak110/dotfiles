# バージョン更新の詳細手順：競合解決と統合後の確認

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 動作検証を実行する前 | `../../develop-dotfiles/references/verification-values-execution.md` | 判断 | `read:*/.claude/skills/develop-dotfiles/references/verification-values-execution.md` |
| 全レーンの完了後にバージョンを更新する時点、および本書の更新手順へ入る前 | `agent-toolkit:process-wi/references/finish-session.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session.md` |
| Codex導入版変更後にrootを再解決する前 | `agent-toolkit:/share/rules/rules-common.codex.md` | 判断 | `read:agent-toolkit:*/share/rules/rules-common.codex.md` |
| 公開前の統合差分に`rust/claude-statusline/`の変更がある時、statusline採番規定を確認する前 | `../../release-dotfiles/SKILL.md` | 判断 | `read:*/.claude/skills/release-dotfiles/SKILL.md` |
| 公開前の統合差分に`rust/claude-statusline/`の変更があり、最終検証を選ぶ時 | `./../../develop-dotfiles/references/publishing.md` | 判断 | `read:*/skills/develop-dotfiles/references/publishing.md` |

## 競合解決と統合後の確認

「バージョン更新」手順2の統合時は、次の順に確認する。

1. 配布対象の採番漏れを防ぐため、公開担当は統合差分ごとの採番要否を各定義元で確定し、レーンの引渡しと比較する。
   `rust/claude-statusline/`には`release-dotfiles`の採番規定を適用し、`develop-dotfiles`の`references/publishing.md`「公開前の検証」の`--enable=statusline-version`による最終検証へ接続する。
2. 競合後の整合性を保つため、rebaseやmergeで`version`が競合したら`(major, minor, patch)`の数値タプルで大きい方を定義元の2ファイルへ反映する。
   `scripts/sync_codex_plugin_manifests.py`で派生マニフェストを同期し、`--check`の判定を採用する（最新0、不整合1、引数誤用2）。
3. 更新が配信されることを保証するため、rebaseやmerge後は競合の有無によらず公開済み統合先と定義元の`version`を比較する。
   未公開コミットが`version-bump.md`「判定基準」の更新対象を含み、版数が同じなら統合後の版数を基準に再度更新する。
   別作業が先に同じ版数へ更新し、競合せず統合された場合も対象となる。

## 未プッシュ範囲での統合

`scripts/agent_toolkit_bump.py`は、未プッシュのコミット範囲にすでに存在する更新以下の種別が指定された場合、処理を行わずスキップ（no-op）する。
そのため、後続の編集ごとに毎回追加で更新を実行する必要はなく、更新種別の引き上げ（格上げ）が必要な場合にのみ上位の種別を指定して実行する。
なお、`git push`を実行した後の追加コミットは、新たな未プッシュ範囲として扱う。

## バージョン更新

変更のあるプラグインごとに、プッシュ前に次の順で進める。
版数を据え置くと、`claude plugin update`は更新を配信せず「最新です」と応答する。

1. 採番の主体・種別を揃えるため、`version-bump.md`「判定基準」と`version-bump-planning.md`「plan modeでの取り扱い」で対象ごとの種別と認可、実行主体・時機を確定する。
   各レーンは計画への記録にとどめ、公開担当が全レーンのマージ後に実行する。
2. 更新を配信するため、`scripts/agent_toolkit_bump.py --plugin <プラグイン名> {patch|minor|major}`を実行する。
   `--plugin`省略時はagent-toolkitを対象とし、既存更新との統合は「未プッシュ範囲での統合」、rebaseやmergeは「競合解決と統合後の確認」に従う。
3. 定義元の一致を保つため、2ファイルの`version`と`description`を常に揃える（`description`は手動同期）。
   agent-toolkitなら`agent-toolkit/.claude-plugin/plugin.json`とmarketplaceの`name == "agent-toolkit"`が対応し、`manifest_ssot_invariant_test.py`が検証する。
4. 配布形式を揃えるため、生成元を編集して再生成する。
   生成ファイルは手動編集しない。
   Agent Pluginsの`plugin.json`・`mcp.json`とCodexマニフェストは、定義元の2ファイルと`agent-toolkit/.mcp.json`から`scripts/sync_codex_plugin_manifests.py`が生成する。
   Antigravityの`agent-toolkit-agy/`・`agent-basics-agy/`は`scripts/sync_antigravity_plugin.py`が生成し、`post_apply`が`~/.gemini/config/plugins.json`へ登録する。
   `--plugin agent-basics`なら品質プラグインのみを生成できる。
   元の名前空間と品質本文を保ち、両配布物の長文スキルを同じ短いSKILL.md・全文資料生成処理へ渡す。
5. 公開前の整合性を保証するため、`version-bump-planning.md`「plan modeでの取り扱い」の生成結果一致・完了条件を確認する。
   続けて`develop-dotfiles`の`../../develop-dotfiles/references/verification-values-execution.md`「検証コマンドの実行」の特定ファイル限定形式でSSOTテストを含む動作検証を行う。
   Codex導入版を変更した場合は、次の処理で使う資源を`version-bump-codex-root.md`「Codex導入後のrootの再確認」の順で確定する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| dotfilesの開発手順の基準を適用する前 | `../../develop-dotfiles/SKILL.md` | 判断 | `read:*/.claude/skills/develop-dotfiles/SKILL.md` |
| バージョン更新の詳細手順：Codex導入後のrootの再確認の基準を適用する前 | `version-bump-codex-root.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/version-bump-codex-root.md` |
| バージョン更新の詳細手順：plan modeでの取り扱いの基準を適用する前 | `version-bump-planning.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/version-bump-planning.md` |
| バージョン更新の詳細手順の基準を適用する前 | `version-bump.md` | 判断 | `read:*/.claude/skills/agent-toolkit-edit/references/version-bump.md` |

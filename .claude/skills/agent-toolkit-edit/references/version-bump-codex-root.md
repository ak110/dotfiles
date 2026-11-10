# バージョン更新の詳細手順：Codex導入後のrootの再確認

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 動作検証を実行する前 | `../../develop-dotfiles/references/verification-values-execution.md` | 判断 | `read:*/.claude/skills/develop-dotfiles/references/verification-values-execution.md` |
| 全レーンの完了後にバージョンを更新する時点、および本書の更新手順へ入る前 | `agent-toolkit:process-wi/references/finish-session.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session.md` |
| Codex導入版変更後にrootを再解決する前 | `agent-toolkit:/share/rules/rules-common.codex.md` | 判断 | `read:agent-toolkit:*/share/rules/rules-common.codex.md` |
| 公開前の統合差分に`rust/claude-statusline/`の変更がある時、statusline採番規定を確認する前 | `../../release-dotfiles/SKILL.md` | 判断 | `read:*/.claude/skills/release-dotfiles/SKILL.md` |
| 公開前の統合差分に`rust/claude-statusline/`の変更があり、最終検証を選ぶ時 | `./../../develop-dotfiles/references/publishing.md` | 判断 | `read:*/skills/develop-dotfiles/references/publishing.md` |

## Codex導入後のrootの再確認

1. 導入版の混在を防ぐため、Codexプラグインの導入版変更後は保持したroot情報を破棄し、`agent-toolkit/share/rules/rules-common.codex.md`「Codexのplugin root解決」のmarketplace限定照会と抽出で再解決する。
   marketplaceは`ak110-dotfiles`とし、`installed`から`pluginId == "agent-toolkit@ak110-dotfiles"`を一意に特定する。
   起動通知の変更前rootは導入版変更前の処理に用いる。
2. 実行資源の一致を保証するため、新版のplugin cache directory内のroot、`skills/`、実行対象スクリプトの実在と、manifestの`version`と導入版の一致を確認して使用する。
   確認できなければ未完了とし、古いrootへ戻さない。
   `source.path`は配布元を示し、cache rootには新版のrootを用いる。
3. 解決結果を判断できるよう、識別子、marketplace、version、有効状態、解決結果と診断のみをモデルへ返す。
   取得失敗、エントリ不在、一意に定まらない状態、無効化、実体パス欠落を区別し、全一覧は返さない。

# バージョン更新の詳細手順：新規CLI公開時の疎通確認

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 動作検証を実行する前 | `../../develop-dotfiles/references/verification-values-execution.md` | 判断 | `read:*/.claude/skills/develop-dotfiles/references/verification-values-execution.md` |
| 全レーンの完了後にバージョンを更新する時点、および本書の更新手順へ入る前 | `agent-toolkit:process-wi/references/finish-session.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session.md` |
| Codex導入版変更後にrootを再解決する前 | `agent-toolkit:/share/rules/rules-common.codex.md` | 判断 | `read:agent-toolkit:*/share/rules/rules-common.codex.md` |
| 公開前の統合差分に`rust/claude-statusline/`の変更がある時、statusline採番規定を確認する前 | `../../release-dotfiles/SKILL.md` | 判断 | `read:*/.claude/skills/release-dotfiles/SKILL.md` |
| 公開前の統合差分に`rust/claude-statusline/`の変更があり、最終検証を選ぶ時 | `./../../develop-dotfiles/references/publishing.md` | 判断 | `read:*/skills/develop-dotfiles/references/publishing.md` |

## 新規CLI公開時の疎通確認

配布用プラグインにおいて新しいCLI、コマンド、またはラッパースクリプトを公開する計画を立てる際は、エンドユーザー環境で正常に起動できるようになるまでの導入手順を`## 要件・外部仕様`に明記する。
対象はインストールスクリプト、post-apply処理、PATHの通し方、bash補完の登録、Windows用ペアファイルの作成である。
プラグイン単体を利用するエンドユーザーが、PATHの追加や環境変数の設定以外の特別な設定なしにコマンドを実行できる状態を目標基準とする。
プラグインの`bin/`ディレクトリの実配置先は、バージョン番号付きのキャッシュディレクトリとなる。
dotfilesを利用するエンドユーザーは`.bashrc`でのPATH追加によって、またプラグイン単体を利用するエンドユーザーは`install-claude.sh`や`install-claude.ps1`が配置する動的解決ラッパーによって、このパス構造を意識せずにコマンドを実行できるようにする。

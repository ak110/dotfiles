# dotfiles環境の振り返り観点

`agent-toolkit:session-review`が独立advisorへ渡す追加観点である。

## pyfltr

セッションでpyfltrを使用した場合は、対象コマンドの成功・失敗だけでなく、検索・置換・検査の結果表示、
警告、エラー、復旧手順が判断と修正を支援したかを評価する。個別の操作ミスを一般化せず、
CLI自体の改善で同じ失敗を防げる観測事象がある場合だけ候補化する。

## dotfiles配布物

現在の対象がdotfilesリポジトリまたはその配布物である場合は、次を追加で点検する。

- chezmoi原本と配布先、Claude CodeとCodex、POSIXとWindowsの同期境界
- agent-toolkitのplugin、rules、skills、agents、hooks、settings、生成物の責務境界
- 常時ロード文書から条件付きreferenceへ移せる手順や、不要になった状態・フック・リンク
- 配布、更新、旧資産のcleanup、バージョン更新、生成物同期に必要な追随
- project固有規範と全利用者向け配布規範の反映先の妥当性

対象が別リポジトリの場合は、観測事象がdotfiles配布物に起因すると裏付けられた候補だけをdotfiles向け提案とし、
対象プロジェクトの改善提案と混同しない。

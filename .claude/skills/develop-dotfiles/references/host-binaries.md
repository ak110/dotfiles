# dotfilesのホスト本体のバイナリの検索

## ホスト本体のバイナリの検索

ホスト本体のバイナリの検索は、ホスト機能の公式ドキュメントと提供元のIssueトラッカーの既知の報告の確認を補う手段である。
調査の結論と回避策の採否は、ドキュメントとIssueの内容と合わせて`agent-basics:bugfix/references/causes.md`「場面ごとの確かめ方」に沿って判断する。

- バイナリの実体のパスは`B=$(readlink -f "$(command -v claude)")`や`C=$(readlink -f "$(command -v codex)")`で解決する。
  特定のClaude Codeの版を対象にする場合は`~/.local/share/claude/versions/<バージョン>`を指定する。
- 件数は`timeout 60 rg -a -c -F -- '<検索文字列>' "$B"`、前後の文脈は`timeout 60 rg -a -o -- '.{0,200}<検索文字列>.{0,200}' "$B"`で取得する。
  文脈検索の検索文字列は正規表現としてエスケープし、出力はmanaged-tempへ保存してから読む。
  タイムアウトなどの上限到達による中断と、該当なしの結果を分けて判断する。
- 一致はファイル先頭付近の文字列テーブルの断片と後方のJavaScriptソースの両方に現れうるため、件数が少なければ`-m`で制限せずに全件を保存する。
  正規表現の`.`は改行や不正なUTF-8シーケンスで一致が止まるため、取得した前後の文脈は指定した文字数より短くなることがある。
- 検索には`rg`を使う。
  Claude CodeのBashツール内の`grep`は組み込みのugrepに置き換わっており、ugrepとGNU grepのどちらでも`.{0,N}`による文脈取得が30秒以内に完了しなかったためである。
  この観測結果と再検証の手順は`docs/development/audit-records-project-environment.md`「develop-dotfiles：ホスト本体のバイナリの検索：2026年10月4日」に記録されている。

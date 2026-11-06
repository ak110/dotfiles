# dotfilesのホスト本体のバイナリの検索

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/investigation.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「直接的原因と事象時点の確定」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「直接的原因と事象時点の確定」` |

## ホスト本体のバイナリの検索

ホスト本体バイナリの検索は、ホスト機能に関する公式ドキュメントや提供元のIssueトラッカーにおける既知の報告の確認を補足するための手段である。調査の結論や回避策を採用するかどうかは、ドキュメントやIssueの内容と合わせて総合的に判断する。判断基準は`agent-basics:writing-standards`の`references/investigation.md`および`agent-basics:bugfix/references/root-cause-analysis.md`「直接的原因と事象時点の確定」に準拠する。

- バイナリの実体パスは、`B=$(readlink -f "$(command -v claude)")`や`C=$(readlink -f "$(command -v codex)")`を実行して解決する。特定のClaude Codeバージョンを対象にする場合は`~/.local/share/claude/versions/<バージョン>`を指定する。
- ヒット件数の確認には`timeout 60 rg -a -c -F -- '<検索文字列>' "$B"`を用い、前後の文脈確認には`timeout 60 rg -a -o -- '.{0,200}<検索文字列>.{0,200}' "$B"`を実行する。文脈検索の検索文字列は正規表現として適切にエスケープする。コマンドの出力結果はいったんmanaged-tempに保存してから参照する。タイムアウトなどの上限到達による中断と、該当なしの結果を明確に区別して判断する。
- 検索文字列の一致箇所は、ファイル先頭付近の文字列テーブルの断片と後方のJavaScriptソースコードの両方に現れる可能性がある。そのため、件数が少ない場合は`-m`で制限せずに全件を保存する。なお、正規表現の`.`は改行や不正なUTF-8シーケンスで一致が止まるため、取得された前後の文脈が指定した文字数より短くなることがある。
- 検索には`rg`を使用する。Claude CodeのBashツール内の`grep`は組み込みのugrepに置き換わっており、ugrepおよびGNU grepのいずれを用いても`.{0,N}`による文脈取得が30秒以内に完了しなかったためである。この観測結果と再検証の手順は`docs/development/audit-records.md`「develop-dotfiles：ホスト本体のバイナリの検索：2026年10月4日」に記録されている。

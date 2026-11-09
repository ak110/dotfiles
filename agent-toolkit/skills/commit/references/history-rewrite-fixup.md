# 履歴書換え：fixupの実行上の制約

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## fixupの実行上の制約

1. fixupの作成前に、最古のfixup対象コミットと元のHEADを短縮OIDで記録する。
   さらに`git for-each-ref --format='%(refname) %(objectname)' refs/heads`の出力結果も保持しておく（`history-rewrite-recovery.md`「失敗時の扱い」でローカルブランチ参照の変動比較に用いるため）。
   続いて、対象範囲`<最古fixup対象>^..<元HEAD>`について次の3点を確認する。
     まず、`git rev-list --first-parent --merges`を実行してマージコミットが含まれていないこと、および`git rev-list --first-parent --reverse`で取得したすべてのOIDがプッシュ未了であることを確認する。
     次に、`git log --first-parent --format='%H%x00%s'`を実行し、各fixup対象コミットの件名が範囲内で一意であることを確認する。
     もし件名が一意でないコミットに対して通常のfixupを作成する場合は、`git commit -m 'fixup! <対象の完全OID>'`とする（autosquashは`fixup!`の後に続く文字列をコミットハッシュとしても解決できる）。
     一方、`amend:`や`reword:`の対象件名が一意でない場合や、範囲内に制御語（`fixup!`・`squash!`・`amend!`と半角スペース1文字）で始まるコミットやマージコミットがある場合は、fixupを作成せずにautosquashを中止する。
   これらの判定処理自体が失敗したときも中止し、`history-rewrite-recovery.md`「失敗時の扱い」に従う。
     TOCTOU（確認時と実行時の状態不一致）を防ぐため、autosquashの実行直前にもこれらの条件を再判定する

2. 通常の`--fixup=<sha>`は`fixup! <統合先の件名>`、`amend:`および`reword:`はどちらも`amend! <統合先の件名>`という件名のコミットを作成する（`reword!`という接頭辞は未定義である）。
   編集バッファの1行目にある接頭辞と件名を書き換えてしまうとautosquashの対象から外れるため、内容を差し替える場合は必ず3行目以降を編集する
   `--fixup`オプションは`-m`や`-F`と併用できずエラーになる。
   そのため、非対話環境ではエディター指定の環境変数`GIT_EDITOR`に1行目を保持したまま2行目以降を差し替えるスクリプトを指定し、`amend:`は`git commit --no-verbose --fixup=amend:<sha>`として実行する。
   これにより`commit.verbose=true`が設定されている環境でも、コミット差分がコミット本文に混入するのを防げる。
   この挙動の対照検証記録は、`docs/development/audit-records.md`の「agent-toolkit/skills/commit/references/history-rewrite.md：fixupの実行上の制約：2026年10月8日」にある
   `amend:`や`reword:`を用いる際は、統合先コミットにすでに付与されている異なるトレーラー（trailer）を保持し、統合後のコミットメッセージに`Co-Authored-By:`が1回だけ記載されるように整える

3. fixupを作成した直後は、`git log -1 --format=%s`を実行して期待通りの件名（`fixup! <統合先の件名>`、`fixup! <対象の完全OID>`、または`amend! <統合先の件名>`）と完全に一致していることを確認し、一致した場合にのみautosquashを実行する

4. 参照とサブモジュールを保持するため、履歴の統合は、`GIT_SEQUENCE_EDITOR=: git -c submodule.recurse=false -c rebase.updateRefs=false rebase -i --autosquash <base>`を用いて実行する。
   `<base>`には対象コミットの親コミット、またはそれより前のコミットを指定する。
   実行直前には、`history-rewrite-verification.md`「履歴確認の起動形」に示す`git log`を単独で再度実行する。
   このコマンド単位の設定により、作業中以外のローカルブランチ参照の更新と、サブモジュールの作業状態の変化を防止する。
   なお、`--no-update-refs`オプションはGit 2.38で追加された引数であり、agent-toolkitが対応環境とするGit 2.34.1では認識されない（Git 2.38未満の環境にはブランチ参照を自動更新する機能自体がない）

対応するGit環境での履歴統合と参照保持の再検証記録は、`docs/development/audit-records.md`「agent-toolkit/skills/commit/references/history-rewrite.md：fixupの実行上の制約：2026年10月18日」にある。
また、サブモジュールの状態保持に関する再検証記録は、同書の「agent-toolkit/skills/commit/references/history-rewrite.md：親だけのGit操作とサブモジュール：2026年10月18日」にある。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 履歴書換え：失敗時の扱いの基準を適用する前 | `history-rewrite-recovery.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/history-rewrite-recovery.md` |
| 履歴書換え：操作前後の確認の基準を適用する前 | `history-rewrite-verification.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/history-rewrite-verification.md` |

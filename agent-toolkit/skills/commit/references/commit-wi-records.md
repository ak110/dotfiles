# コミット：WI実装commitの対応

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 保存済みの検証結果を再利用する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` | | | |
| 未コミット検証の実行前とcommit後の適用確認前 | `../../../share/verification-evidence-observation.md` | | | |
| 取得版の異なる結果をレビューへ渡す前 | `../../../share/exec-review-recording-grounds.md` | | | |
| Git識別子・worktree・履歴・公開済みの状態を判定または操作する前 | `git-identifier.md` | | | |
| 公開工程またはリリース操作に着手する前 | `publish.md` | | | |
| amend、fixup、autosquash、rebaseまたはpush済み判定の前 | `history-rewrite.md` | | | |
| 実際にpushする前 | `push-and-ci.md` | 操作 | `bash:git push` | |
| コミットメッセージを書く前（計画ファイルやPR説明へコミットメッセージ案を書く前を含む） | `message.md` | | | |
| 既存未コミット差分を不採用として破棄する前 | `stash-cleanup.md` | | | |
| 一時ブランチ、`git stash`の退避、hookの退避ファイルまたは別パスへの複製を削除する前と、委譲先の完了報告が退避識別子か複製パスを開示したとき | `stash-cleanup.md` | | | |
| CI失敗を扱う前 | `agent-toolkit:commit/references/ci-failure-handling.md` | | | |
| 計画ファイルを作成する時と、公開インターフェース、エンドユーザー向けのメッセージ、永続データの形式を新設・変更する時 | `agent-toolkit:commit/references/design-time.md` | | | |
| テストコードを書く時とレビューする時、条件分岐と判定条件を新設・変更する時 | `agent-toolkit:commit/references/testing.md` | | | |

## WI実装commitの対応

WI実装commitとAWIの対応は、計画（計画なしなら引き継ぎ記録）と同じディレクトリ・ファイル幹（stem）の`<stem>.wi-commits.jsonl`にのみ構造化して記録する。
書込には`atk run-script plan-progress`と`atk run-script plan-rewrite`、読取には`atk run-script plan-commits`を使う。
commitは一意な短縮OIDで扱い、入力には完全OIDも使える。
各工程の文書には操作時機、記録・worktree、固有入力だけを書き、詳細は本節を参照する。
オプションは`--help`で確認し、計画・引き継ぎ記録・worktreeは絶対パス、AWIはファイル名で渡す。

**1.** AWIとの対応を保持するため、実装コミットを作成する前に、対応するAWIの集合と現在のHEADを取得する。
`git commit`を単独で実行して正常に完了した場合に限り、作成前のHEAD、新しいHEAD、worktree、および対応するすべてのAWIを`atk run-script plan-progress`へ渡して記録する。
引き継ぎ記録は`--handoff`と対象AWI全件の`--allowed-awi`を指定する。
レビュー修正やCI修正も同様に全件記録する。
cherry-pickやfast-forwardで複数commitを取り込んだ場合は操作前後のHEADで一括記録できる。
AWI集合が異なれば集合ごとの区間直前を作成前HEAD、区間末尾を新HEADとして記録する。
区間末尾は現在HEADのfirst-parent履歴にあればよい。
マージコミットや記録済みのコミットが含まれる範囲は一括記録できないため、マージは使わずにコミットを取り込み、未記録の区間のみを渡す。

**2.** WI終了時の記録を正しくするため、AWIを完了状態にするエージェントは、以下のコマンドを実行し、出力されるJSON Linesの`awi`および`commits`から、最新のコミット一覧を短縮OIDで取得する。

```sh
atk run-script plan-commits -- <計画Markdownの絶対パス> --worktree <対象worktreeの絶対パス>
atk run-script plan-commits -- <引き継ぎ記録Markdownの絶対パス> --worktree <対象worktreeの絶対パス> --handoff --allowed-awi <記録の対象AWI全件を反復指定>
```

対象を絞る場合は`--awi <AWIファイル名>`を反復指定する。
省略時は計画の関連WI全件（引き継ぎ記録は`--allowed-awi`の全件）を取得する。
同stemのJSONLは保存先であり位置引数に渡さない。
保存で計画のパスが移動しても、同名の保存済み計画が一意ならそのまま指定できる。
同名が複数あれば保存先の実在パスを指定する。
取得した短縮OIDは`atk wi adopt --commit`に渡し、複数のコミットがある場合は`--commit <OID>`を繰り返し指定する。
コミットの説明などのメモは`--note-file`に記録する。
実装差分を伴わない項目に対応記録がない場合は、その項目を除外した対象を`--awi`で指定する。
実装コミットを持つはずの項目で記録が欠落している場合、対象外のAWIが含まれる場合、あるいはOIDを解決できない場合は、記録の生成側で内容を補正してから再取得する。
`atk lane integrate`へ渡すadoptの`--commit`も現在の取得結果から作り直し、manifestのWI行の`commit_record`へ元の計画または引き継ぎ記録Markdownの絶対パスを指定する。
CLIは統合直前に祖先関係と対象AWIのcommit集合の一致を確認する。
記録済みのコミットへAWIを追加するには、旧OIDを同じOIDへ写す対応表`{"<旧OID>": [{"commit": "<同じOID>", "awi": [<記録後の全AWI>]}]}`を`atk run-script plan-progress`の`--rewrite-map`へ渡す。
`awi`には記録後に対応付けるAWIを全件書き、計画の関連WI（`--handoff`では`--allowed-awi`）に無いAWIは記録を変えずに拒否される。
メッセージの説明文や件名から対応を推測しない。

**3.** 履歴変更後の対応を継承するため、履歴の書き換えやコミットの分割によってWI実装コミットのOIDが変化した場合は、`history-rewrite-wi-records.md`「WI実装commitの対応の継承」に従う。

検収済みの対応表と書き換え前のHEADを、変更範囲に含まれるWI実装コミットを持つすべての計画および引き継ぎ記録とともに、1回の`atk run-script plan-rewrite`呼び出しで渡す。
必要な情報に不足がある場合、同コマンドはいずれの記録も変更せずに失敗する。
同コマンドは、対応記録に含まれる完了済みAWIの対応コミットや件名も、新しいコミットに合わせて更新する。
private-notesの保存に失敗した場合は、原因となるエラーを解消したうえで同じ引数で再実行する。
指定が漏れている記録がある場合、コマンドは書き込み前に該当する記録を一覧で提示するため、それらを引数に追加して再実行する。
複数の対応表を扱う場合は、書き換えの順序に従って`--rewrite-map`を繰り返し指定し、合成後のOIDが現在のHEADに含まれていることを確認する。

**4.** 公開情報と内部記録を分けて保存する。
実装差分なしで充足した項目、WIと無関係なcommit、回答のみのUWIは対応記録の対象外とし、進捗ログや引き継ぎ記録を根拠にcommit指定を省く。
公開するメッセージにはWI識別子や内部の認可情報を記載せず、対応記録へ残す。
計画と対応記録を`atk plans commit`で保存する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 履歴書換え：WI実装commitの対応の継承の基準を適用する前 | `history-rewrite-wi-records.md` |

# 履歴の変更と復旧

push前のcommitへ修正を統合する（amend、fixup、autosquash、rebase）手順書であり、失敗した時の復旧も扱う。
方針: 公開済みの履歴を書き換えると、他の作業ツリーやCIが取得した参照を解決できなくなる。
このため最初にpush済みかを判定し、書き換えの前後を記録と比較で確かめる（原則4、`agent-basics:writing-standards/references/quality-principles-constraints.md`）。
上から実行する。
通常のcommitの検証、ステージング、メッセージは`agent-toolkit:atk-mode/playbooks/commit.md`に従う。
OIDが変わった後のWIとの対応は本書「WI実装commitの対応の継承」で引き継ぐ。
ホストの通常動作（amendより新しいcommitを足す、など）と衝突する場合も、本書の統合の方針を適用する。

## プッシュ済み判定

1. `git fetch --all --prune`の後に`git for-each-ref --contains=<対象sha> refs/remotes/`を実行する。
   1行以上出ればいずれかのリモート追跡ブランチから到達できるためpush済み、空ならpush前と判定する。
   `git log --decorate`は参照の先端にしか印を付けず祖先を検出できないため、判定に使わない。
2. amendとfixupの対象はpush前のcommitに限る。
   例外は、`agent-toolkit:atk-mode/references/ci.md`「原因commitへ取り込む区分」の3条件を操作の直前に全て確かめた場合だけである。

## 修正方法の選択

1. 統合先を決める。
   同じ実装の単位と目的の変更は、工程によらず既存のcommitへ統合する。
   レビュー修正を除き、独立した新しい目的を持つ修正と、統合先に合うpush前のcommitが無い修正は、新しいcommitにする。
   付帯作業は関連する開発のcommitへ含め、独立した目的として扱わない。
   通常の実装でレビューの指摘を直す場合は、中間commitの公開契約を保つため、レビュー修正だけのcommitを作らずに既存の実装commitへ統合し、次で統合先を決める。
   - 指摘管理表と現在の履歴を比べ、採用した指摘のIDと実装単位のcommitの短縮OIDを対応付ける。
   - 過去のcommitへのfixupは、修正が統合先の時点で単独で成立し、変更範囲の検証を単独で再実行できる場合に限る。
     対象の直後から今のHEADまで各commitを`git diff-tree`や差分で調べ、変更箇所と順序から中間commitの公開契約を判定する。
     同じファイルを変えていることだけで競合と判断しない。
   - 対応を特定できない修正、複数の単位にまたがって分けられない修正、過去の単位へ統合すると中間commitの公開契約を保てないか単独で検証できない修正は、そのレビューが対象とする計画の実装commitのうち最も新しいものへ統合する。
   - 統合先がpush済みか、OIDが一致しない場合は、履歴と作業ツリーを変えずに中断し、続行できない理由を報告する。
2. 対象の位置に合う操作を選ぶ。
   どの操作でも、先に本書「操作前後の確認」の手順1〜3を行う。
   - 直前のcommitだけが対象: 修正、検証、ステージングの後に本書「プッシュ済み判定」をやり直し、push前なら`git commit --amend --no-edit`で統合する。
   - 過去のcommitを含む: 最終commitを含む全ての統合先へ、履歴の順に1単位ずつ修正、検証、ステージングしてfixupを作る。
     メッセージを変えないなら`git commit --fixup=<sha>`、`Co-Authored-By:`などの追加と更新は`--fixup=amend:<sha>`、メッセージだけの変更は`--fixup=reword:<sha>`を使い、本書「fixupの実行上の制約」に従う。
     fixupを1つ作るごとに、対象のOID、件名、作業ツリーがcleanであることを確かめてから次の単位へ進む。
     全てのfixupを作った後に、autosquashを1回だけ実行する。
     autosquashの単位は、レビューの識別子（指摘管理表の絶対パスとround値）と最古のfixup対象の短縮OIDの組であり、受け渡しや再開があっても1回にする。
     再開した時は本書「履歴確認の起動形」の`git log`を実行し、件名が`fixup!`、`squash!`、`amend!`で始まるcommitがある時だけautosquashを実行する。
   - 作成済みのfixupを通常のcommitへ組み直す: 元のfixupごとの差分、件名、本文を個別に取得し、新しいcommitと1対1で比べる。
     最終のツリーが一致しても、メッセージの取り違えは検出できない。
3. 統合の後は本書「操作前後の確認」の手順4〜6へ進む。

push前で1人の担当が専有する作業ツリーでは、本書「プッシュ済み判定」で安全を十分に確保できる。
リモートの広告参照との比較、replace参照、graft、浅いcloneへの防御は、実際の事象を観測して記録した時に足す。

## 操作前後の確認

1. 親リポジトリだけを対象とするrebaseの開始・継続・中止と、checkout・switch・restoreには、環境の再帰設定によらずサブモジュールの作業状態を保つため、コマンドごとに`git -c submodule.recurse=false`を付ける。
   初期化済みのサブモジュールごとに、操作の前後でHEAD、ブランチ、作業内容が一致することを確かめる。
   サブモジュールの参照そのものを更新する操作は別に扱う。
2. fixupとamendの前に、変更したファイルへ正式なformatterを実行してその差分を検収し、対象のcommitに属する差分だけをステージングする。
   `git status --short`で未ステージの差分が残っていないことを確かめてからcommitする。
   pre-commitのhookが新しい差分を作ると、ステージ済みと未ステージの差分が同じファイルに並存してcommitが成立しない（本書「失敗時の扱い」）。
3. 操作の直前に本書「履歴確認の起動形」の`git log`を単独で実行して対象を特定し直し、`git blame`、`git log -p -n 20 -- <ファイル>`、`git show --stat <sha>`で統合先を確定する。
4. autosquashの後は、全てのfixupを作った直後のHEADと`<最古fixup対象>^`で次を実行する。

   ```text
   atk run-script history-compare -- --operation autosquash --work-dir <worktreeの絶対パス> --old-base <保持した親OID> --old-head <autosquash直前HEAD> --new-base <保持した親OID> --new-head <書換え後HEAD> --output <managed-temp内の対応表JSONの絶対パス>
   ```

   このコマンドは、最終のツリー、件数、制御用の件名（`fixup!`など）が残っていないこと、fixup対象以外の変更内容が保たれていることを検収し、新旧の対応表を作る。
   `reword:`の対象もfixup対象に含む。

5. amendの後は、amendの直前に保持したHEADとその親OIDで次を実行し、新旧が同じ親を持つ異なる1件ずつであることを検収して、1対1の対応表を作る。
   amendは内容を変える操作であるため、ツリーとパッチの一致は検収しない。

   ```text
   atk run-script history-compare -- --operation amend --work-dir <worktreeの絶対パス> --old-base <保持した親OID> --old-head <amend直前HEAD> --new-base <保持した親OID> --new-head <amend後HEAD> --output <managed-temp内の対応表JSONの絶対パス>
   ```

   手順4か5が0以外で終わったら、本書「失敗時の扱い」へ進む。
6. 書き換えた後は、各中間状態のHEADへ`git -c submodule.recurse=false checkout <参照>`で切り替えて変更範囲の検証をやり直し、同じ形で元へ戻る。
   `git log -1 --format=%B <統合後sha>`で最終のメッセージと`Co-Authored-By:`を確かめ、ステージングの状態と`git show HEAD:<path>`で反映の漏れが無いことを確かめる。
   続けて本書「WI実装commitの対応の継承」へ進む。

## fixupの実行上の制約

1. fixupを作る前に、最古のfixup対象と元のHEADを短縮OIDで控え、`git for-each-ref --format='%(refname) %(objectname)' refs/heads`の出力を保存する（本書「失敗時の扱い」でローカルブランチの参照の移動を比べるため）。
   範囲`<最古fixup対象>^..<元HEAD>`について次を確かめる。
   - `git rev-list --first-parent --merges`が空であり、`git rev-list --first-parent --reverse`の全OIDがpush前である。
   - `git log --first-parent --format='%H%x00%s'`で、各fixup対象の件名が範囲の中で一意である。
     件名が一意でない対象への通常のfixupは`git commit -m 'fixup! <対象の完全OID>'`で作る（autosquashは`fixup!`の後の文字列をcommitハッシュとしても解決する）。
   - `amend:`か`reword:`の対象の件名が一意でない場合と、範囲内に制御語（`fixup!`・`squash!`・`amend!`と半角スペース1文字）で始まるcommitかmerge commitがある場合は、fixupを作らずにautosquashを中止する。

   判定そのものが失敗した時も中止し、本書「失敗時の扱い」へ進む。
   確認と実行の間に状態が変わることを防ぐため、autosquashの直前にも同じ判定をやり直す。
2. 通常の`--fixup=<sha>`は件名`fixup! <統合先の件名>`のcommitを、`amend:`と`reword:`はどちらも件名`amend! <統合先の件名>`のcommitを作る（`reword!`という接頭辞は無い）。
   編集バッファの1行目の接頭辞と件名を書き換えるとautosquashの対象から外れるため、内容は3行目以降で差し替える。
   `--fixup`は`-m`・`-F`と併用できないため、非対話の環境では`GIT_EDITOR`に、1行目を保って2行目以降を差し替えるスクリプトを指定する。
   `amend:`は`git commit --no-verbose --fixup=amend:<sha>`で実行し、`commit.verbose=true`の環境でも差分がメッセージの本文へ入らないようにする。
   `amend:`と`reword:`では、統合先にある異なるtrailerを保ち、統合後のメッセージに`Co-Authored-By:`が1回だけ入るようにする。
3. fixupを作った直後に`git log -1 --format=%s`が期待する件名（`fixup! <統合先の件名>`、`fixup! <対象の完全OID>`、`amend! <統合先の件名>`）と完全に一致することを確かめ、一致した時だけautosquashへ進む。
4. autosquashは`GIT_SEQUENCE_EDITOR=: git -c submodule.recurse=false -c rebase.updateRefs=false rebase -i --autosquash <base>`で実行する。
   このコマンド単位の設定で、作業中以外のローカルブランチの参照とサブモジュールの作業状態を保つ。
   `<base>`は対象の親か、それより前のcommitにする。
   実行の直前に、本書「履歴確認の起動形」の`git log`を単独で実行し直す。
   `--no-update-refs`はGit 2.38で加わった引数であり、対応環境のGit 2.34.1は認識しないため使わない（2.38より前のGitには参照を自動で更新する機能が無い）。

挙動の確認記録は`docs/development/audit-records-commit-history.md`の次の節にある。

- 「agent-toolkit/skills/atk-mode/playbooks/history-rewrite.md：fixupの実行上の制約：2026年10月8日」
- 「agent-toolkit/skills/atk-mode/playbooks/history-rewrite.md：fixupの実行上の制約：2026年10月18日」
- 「agent-toolkit/skills/atk-mode/playbooks/history-rewrite.md：親だけのGit操作とサブモジュール：2026年10月18日」

## 失敗時の扱い

`pre_fixup`、`fixup`、`autosquash`、`amend`の各phase名と、続行できない理由を返す形式は、`agent-toolkit:atk-mode/playbooks/review-fix.md`「レビュー修正の履歴統合」の実装担当の契約に従う。
この契約を受け取っていない担当は、観測した事実をそのまま委譲元へ報告する。

1. 操作前後の履歴、index、作業ツリーを比べ、変更済みの範囲、差分がどの成果に属するか、公開済みかを確かめる。
   認可された担当の範囲で原因を解消でき、成果と公開済みの履歴を保てる場合は、同じ担当が復旧と再試行を続ける。
2. 履歴を変えていないamendとfixupの失敗では、formatterなどが足した差分を検収し、そのcommitに属する差分だけをステージングして再試行する。
   履歴の変更の途中で失敗した場合は、変更済みの範囲を確定し、保存した操作前の記録との差を確かめて、成果を保ったまま続けるか戻す。
   帰属の分からない差分の破棄、公開済みの履歴の書き換え、認可の範囲の拡大、ユーザー固有の値が要る場合は、その不足だけを委譲元へ返す。
3. `rebase.updateRefs=false`を付けずにrebaseしたと分かった場合は、保存したローカルブランチの参照のOID一覧と今を比べる。
   作業ブランチ以外の参照が動いていたら、参照名、変更前後のOID、復旧に要る認可の申請を委譲元へ返し、復旧の方針を委ねる。
4. autosquashが競合で止まった場合は、競合が「採用済みの指摘への修正」と「統合先commitの変更」だけでできており、解消後も中間commitの公開契約を保てる時に限り、同じ担当が解消してよい。
   解消したパスだけをステージングし、本書「履歴確認の起動形」の`git log`を単独で実行してから`git -c submodule.recurse=false rebase --continue`で続ける。
   再び止まったら、その都度同じ条件を判定する。
   担当外の変更を含む競合、帰属を特定できない修正、中間commitの公開契約を保てない場合は、競合の状態のまま中断して委譲元へ報告する。
5. 委譲元へ返す時は、失敗した操作の名前、終了コード、標準エラーに、失敗した時点の`git status --short`と`git log --oneline -5`の出力を添える。
6. 成功したら、本書「操作前後の確認」と本書「WI実装commitの対応の継承」を終え、復旧した事実と根拠を既存の記録へ残す。

## 一部の変更パスを外した対応の確認

commitの件数を保ったまま旧commitの変更パスの一部を意図して外した書き換えでは、外したパスごとに`--exclude-path <旧OID> <リポジトリ相対パス>`を反復指定して対応表を作る。
commit全体を外した書き換えと、1件を複数へ分けた書き換えには使わない。

```text
atk run-script history-compare -- --operation rebase --work-dir <worktreeの絶対パス> --old-base <旧base> --old-head <書換え直前HEAD> --new-base <新base> --new-head <書換え後HEAD> --exclude-path <旧OID> <リポジトリ相対パス> --output <managed-temp内の対応表JSONの絶対パス>
```

パスはパターンとして展開されず、文字列が一致する変更パスだけが除かれる。
残る内容も競合の解消で変わった場合は、同じ旧OIDを`--resolved-conflict`にも指定する。
受け付けない指定と検収の内容はコマンドの`--help`と診断が示す。
成功出力が示す除外した旧OID、対応先、パスを検収の入力にし、対応表を本書「WI実装commitの対応の継承」の記録の更新へ渡す。

## 履歴確認の起動形

本書が求める`git log`は、範囲か件数を指定して出力を限る。
基準のOIDを保持している工程では`git log --oneline --decorate <保持している基準OID>^..HEAD`を実行し、autosquashの直前と、競合を解消してrebaseを再開する直前は、最古のfixup対象を基準のOIDにする。
基準のOIDを持たない工程では`git log --oneline --decorate -n 20`を実行する。
3,000件を超えるcommitを持つリポジトリで件数を限らないと、実行環境のバッファの上限に達して履歴を確かめられないためである。

## WI実装commitの対応の継承

amend、fixup、rebase、commitの分割などでWI実装commitのOIDが変わった場合に行う。
記録の保存先と読み書きのコマンドは`agent-toolkit:atk-mode/references/commit-wi-records.md`に従う。
通常のレーンの統合はmergeで行って既存のOIDとWIの対応を保つため、本節を呼び出さない。

1. 対応表を作る。
   `atk run-script history-compare`の検収が成功した時に保存した、完全OIDの新旧の対応表を使う。
   操作に合わせて`--operation rebase`、`--operation autosquash`、`--operation amend`を使い、それぞれbaseを除くheadまでの範囲を指定する。
   autosquashとamendの起動形は本書「操作前後の確認」にある。
   - 形式は、1対1か多対1の対応を表す`{"<旧OID>":"<新OID>"}`である。
     commitを分けた場合は`{"<旧OID>":[{"commit":"<新OID-A>","awi":["<WI-A>.md"]},{"commit":"<新OID-B>","awi":["<WI-B>.md"]}]}`の形でAWIの割り当てを明示する。
     元の各commitのAWIは、新しいcommitのどれかへ全件を割り当てる（同じAWIを複数の新しいcommitへ割り当ててよい）。
   - WIの対応を持たないcommitでも、fixupとその統合元が同じ新しいcommitへ統合される場合は、両方の旧OIDを含める。
   - rebaseの後は、競合を解消して`rebase --continue`した場合も、まず指定なしで比較する。
     `history-compare`が「変更内容が対応しない旧commit」として示したOIDだけを、`--resolved-conflict <旧OID>`で反復指定する。
     統合先の後続のcommitが同じ箇所を書き換えたために指定を求められた場合は、統合先の書き換えの内容を読み、書き換え後の統合先でレーンの変更が成立することを確かめた時だけ指定する。
     統合先に同一の変更が既にあって旧commitが全て省かれた場合は、`--new-base`と`--new-head`に同じOIDを指定する。
   - 件名、順序、必要なパスの欠落や対応の曖昧さは、診断に従って端点、指定、履歴を直す。
     内容を保った履歴を組めなければ、保存済みの成果を保って委譲元へ返す。
   - 変更パスの一部を意図して外した書き換えは、本書「一部の変更パスを外した対応の確認」で対応表を作る。
   - 成功出力が示す増えたパス、外れた旧パス、既存のcommitへ対応付けた旧commitを、検収の入力にする。
2. 記録を更新する。
   書き換えの直前のHEADと対応表を、書き換えた範囲のcommitを参照する全ての計画（作業中と保存済み）と引き継ぎ記録とともに、1回の呼び出しで渡す。
   書き換えを繰り返す間は、毎回同じ記録の集合を渡す。

   ```text
   atk run-script plan-rewrite -- --worktree <worktree> --previous-head <書換え前のHEAD> --rewrite-map <対応表のJSONファイルの絶対パス> --completed-step <工程> --result <結果> --plan <計画>... --handoff <引き継ぎ記録>...
   ```

   - 指定の漏れた記録、記録済みのOIDの欠落、対応表の誤りがあれば、同コマンドはどの記録も変えずに失敗して不足を示すため、示された記録を加えるか生成側で補って再実行する。
   - 複数の対応表は書き換えた順に`--rewrite-map`を反復指定し、合成後のOIDが今のHEADの履歴に含まれることを確かめる。
   - 同コマンドは、渡した記録に対応する`adopted`のAWIの`## 処理結果`にあるcommitのOIDと件名も更新し、private-notesへ保存する。
     保存に失敗すると非0で終わるため、同期と保存のエラーを除いてから同じ引数で再実行する。
   - 1つの記録だけを更新する場合は、`atk run-script plan-progress`の`--rewrite-map`を使う。
3. 検証の結果を引き継ぐ。
   再レビューを頼む時は、対応表を`rewrite_map`として実行レビューの担当へ渡し、新しいHEADで完了の条件を判定し直させる。
   OIDの対応だけで、前の検証の結果を新しい版へ流用しない。
   変更の内容や条件が結果に影響する検証だけをやり直し、影響しない項目には取得時のHEADと省いた理由を書く。
4. `atk plans checkout`で取得した保存済みの計画を履歴に追随させた担当は、工程の最後に、更新した全ての計画バンドル（未完了のWIや延期したadoptを含む計画も含む）を`atk plans commit`で保存し、終了コードと警告で正常な終了を確かめる。
   保存の時機は`agent-toolkit:atk-mode/references/plan-storage.md`「計画ファイルの保存と参照」に従う。

## 返す報告に含める項目

統合した操作（amend、fixup、autosquash、rebase）と新旧のHEAD、`history-compare`の終了コードと対応表の所在、更新したWIとの対応の記録、失敗した場合の操作の名前と終了コードと状態の出力を返す。

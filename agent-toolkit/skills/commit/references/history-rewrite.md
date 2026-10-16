# 履歴書換え

通常commitの検証、stage、messageは親スキルに従う。
本ファイルはClaude Codeのシステムプロンプト「常に新規コミットを作成する」指示を上書きする。

## プッシュ済み判定

`git fetch --all --prune`後の`git for-each-ref --contains=<対象sha> refs/remotes/`が1件以上を出力すれば、対象はいずれかのremote-tracking refから到達可能でありプッシュ済み、空ならプッシュ未了とする。`git log --decorate`はref先端にだけ装飾を付けて祖先を検出できないため、判定には`git for-each-ref`を使う。
amendとfixupの対象はプッシュ未了のコミットに限る。公開済みの履歴を書き換えると、取得済みの他の作業ツリーとCIの参照が解決できなくなる。例外は`references/push-and-ci.md`「pushと監視」の「原因commitへ取り込む区分」で、同区分の3条件を操作の直前に確かめた場合だけ適用する。

## 修正方法の選択

通常の実装モードのレビュー修正は実装commitへ統合し、レビュー修正専用のcommitは作成しないこととする。
レビュー修正担当はレビュー指摘管理表と現行履歴から、採用指摘IDと実装単位commitの短縮OIDの対応を確定してから統合する。統合先は採用指摘に対応する実装単位commitとし、最終単位（HEAD）はamend、過去単位はfixupとautosquashで統合する。
過去単位へのfixupは、修正が統合先の時点で独立して成立し、変更範囲の検証を再実行できる場合に限る。対象commitの直後から元HEADまでのcommitが同じ箇所を変えていないかを`git diff-tree`と差分で確かめ、中間commitの公開契約を保てるかを変更順と箇所から判定する（同じファイルの変更というだけでは競合と判定しないこと）。
対応付けできない修正、複数単位へ不可分にまたがる修正、過去単位へ統合すると中間commitの公開契約を保てないか独立して検証できない修正は、そのレビューが対象とする計画の実装commitのうち最も新しいものへ統合する（HEADならamend、そうでなければfixupとautosquash）。統合先がpush済みかOIDが一致しない場合は、履歴と作業ツリーを変えずに続行できない理由を返す。

- 最終単位だけが対象: 修正・検証・stage後に`amend` phaseでプッシュ済み判定を再実行し、成功した場合だけamendする
- 過去単位だけが対象: fixupとautosquashだけを行う
- 両方が対象: 過去単位を先に実装してautosquashし、成功後に同じ実装担当が書換え後HEADへ最終単位の修正だけを実装・検証・stageし、2回目のプッシュ済み判定（対象は書換え後HEAD）の成功後にamendする

過去単位が複数ある場合は、履歴順に1単位ずつその単位の修正だけをstageしてfixupを作成し、作成ごとに対象OIDと件名、作業ツリーのcleanを確かめてから次へ進み、全fixupの作成後に1回だけautosquashする。
autosquashの単位は、レビュー結果の識別子（レビュー指摘管理表の絶対パスとround値の組）と最古fixup対象の短縮OIDの組とし、同じ組への実行は、受け渡し・照会・工程の再開をまたいでも全fixupの作成後の1回に保つ。再開時は`## 履歴確認の起動形`の`git log`に件名が`fixup!`・`squash!`・`amend!`で始まる行がある場合だけ実行する。
作成済みfixupを通常commitとして再構成する場合は、元fixupごとに差分・件名・本文を採取して新commitと一対一で比べる（メッセージの取り違えは最終tree一致だけでは検出できないため）。
autosquash成功後は`git rev-parse --short=7 HEAD`で書換え後HEADを取得し、書換え前後の実装単位を対応付ける。厳密な比較では各短縮OIDを直前に解決する。

`rewrite_guard`の受渡しは`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`「レビュー修正の履歴統合」だけに置く。本節の前段は通常実装のレビュー修正に限って適用する。
未pushかつ単一の実装担当が所有する作業ツリーの保護は本書のプッシュ済み判定で足り、remote広告refとの比較、replace ref、graft、浅い複製への防御は観測事象を記録してから追加する。

工程によらず、同じ実装単位で変更目的が同じ修正は同じ件名のcommitを増やさずに統合する。

- 直前のコミットを完成させる修正は`git commit --amend --no-edit`
- それより前の未プッシュコミットを完成させる修正は、メッセージを変えなければ`git commit --fixup=<sha>`、`Co-Authored-By:`などを追加・更新するなら`--fixup=amend:<sha>`、メッセージだけを変えるなら`--fixup=reword:<sha>`。統合先の件名が範囲内で重複する場合は`## fixupの実行上の制約`の完全OIDを件名にしたfixupを使う
- 独立した変更目的を持つ修正と、統合先に適する未プッシュコミットが無い修正は新規コミットとする（通常の実装モードのレビュー修正には適用せず、前段の統合先へ統合する）。`agent-toolkit/rules/01-agent.md`の付帯作業は関連する開発のcommitへ含めても独立した変更目的として数えない

## 操作前後の確認

- fixupとamendは、変更ファイルへの正式formatterの実行、formatterの差分の検収、そのcommitへ帰属する差分だけのstage、`git status --short`で未stageの差分が無いことの確認を終えてからcommitする。pre-commitがcommit時に初めて差分を変えると、stage済みと未stageの差分が併存してcommitが成立しない（その場合は`## 失敗時の扱い`）
- 操作直前に`## 履歴確認の起動形`の`git log`を単独で実行して対象commitを再特定し、`git blame`か`git log -p -n 20 -- <ファイル>`と`git show --stat <sha>`で統合先を確定する
- autosquash後は、全fixup作成直後のHEADと`<最古fixup対象>^`を使って次を実行する。同操作が最終tree一致、件数、制御件名の残存0、fixup対象以外のpatch-id不変を検収し、対応表を生成する（`reword:`の対象もfixup対象として扱う）。非0なら`## 失敗時の扱い`に従う

  ```text
  atk run-script history-compare -- --operation autosquash --work-dir <worktreeの絶対パス> --old-base <保持した親OID> --old-head <autosquash直前HEAD> --new-base <保持した親OID> --new-head <書換え後HEAD> --output <managed-temp内の対応表JSONの絶対パス>
  ```

- 書き換え後は各中間`HEAD`で変更範囲の検証を再実行し、`git log -1 --format=%B <統合後sha>`で最終メッセージと`Co-Authored-By:`を、stage状態と`git show HEAD:<path>`で未反映差分が無いことを確かめる

## fixupの実行上の制約

- 通常の`--fixup=<sha>`は件名`fixup! <統合先の件名>`、`amend:`と`reword:`はどちらも件名`amend! <統合先の件名>`のコミットを作成する（`reword!`という目印は無い）。編集バッファの1行目の目印と件名を書き換えるとautosquashの対象から外れるため、差し替えるのは3行目以降に限る
- `--fixup`と`-m`・`-F`の併用はエラーになる。非対話環境では`GIT_EDITOR`へ1行目を保持して以降を差し替える処理を指定し、`amend:`は`git commit --no-verbose --fixup=amend:<sha>`で実行する（`commit.verbose=true`の環境でも差分が本文へ混入しない形である）。対照観測は`docs/development/audit-records.md`の「agent-toolkit/skills/commit/references/history-rewrite.md：fixupの実行上の制約：2026年10月8日」にある
- fixup作成前に、最古fixup対象と元HEADを短縮OIDで保持し、`git for-each-ref --format='%(refname) %(objectname)' refs/heads`の出力も保持する（`## 失敗時の扱い`のlocal branch refsの比較元）。範囲`<最古fixup対象>^..<元HEAD>`について3点を確かめる。
  `git rev-list --first-parent --merges`でmerge commitが無いこと、`git rev-list --first-parent --reverse`で得た全OIDがプッシュ未了であることを確かめる。
  `git log --first-parent --format='%H%x00%s'`で各fixup対象の件名が範囲内で一意かを判定する。
  件名が一意でない対象への通常のfixupは`git commit -m 'fixup! <対象の完全OID>'`で作成する（autosquashは`fixup!`の後の語をハッシュとしても解決する）。
  次のいずれかに当たる場合はfixupを作成せずにautosquashを中止し、`## 失敗時の扱い`に従う。`amend:`・`reword:`の対象件名が一意でない場合、範囲内に件名先頭が制御語（`fixup!`・`squash!`・`amend!`）と半角空白1文字に完全一致する既存commitがある場合、範囲にmergeがある場合、これらの判定が失敗した場合である。
  autosquash直前にもTOCTOU対策として再判定する
- fixup作成直後に`git log -1 --format=%s`で期待件名（`fixup! <統合先の件名>`、`fixup! <対象の完全OID>`または`amend! <統合先の件名>`）との完全一致を確かめ、一致した場合だけautosquashする
- 統合は`GIT_SEQUENCE_EDITOR=: git -c rebase.updateRefs=false rebase -i --autosquash <base>`（`<base>`は対象コミットの親以前）で行い、直前に`## 履歴確認の起動形`の`git log`を単独で再実行する。作業branch以外のlocal branch refの更新は設定で抑止する。`--no-update-refs`はGit 2.38で追加された引数で、agent-toolkitが対応するGit 2.34.1は受理しない（Git 2.38未満はref更新の機能自体を持たない）
- `amend:`・`reword:`では統合先の既存の異なるtrailerを保持し、`Co-Authored-By:`を統合後に1回だけ残す

## 失敗時の扱い

本節の`pre_fixup`・`fixup`・`autosquash`・`amend`の各phase名と、続行できない理由を返す返却は`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`が定める実装担当の契約の値とする。この契約を受け取っていないエージェントは、同じ観測結果を委譲元へ報告する。

これらのphaseのいずれかが失敗した場合は、後述の内容競合を除き、失敗の事実と観測結果を委譲元へ返して同じ指摘の履歴統合を終える。復旧操作と再試行は委譲元の判断を得てから行う。失敗時点の履歴とindexの状態は種別ごとに異なり、状態を確定しない復旧や再試行はcommitの消失を招く。
`rebase.updateRefs=false`を指定せずにrebaseしたことを観測した場合は、local branch refsを事前に保持したOIDと比べる。作業branch以外のrefが動いていたら、そのrefと変更前後のOIDと復旧に必要な許可を委譲元へ返し、復旧は委譲元の判断に委ねる。
autosquashが内容競合で停止した場合は、競合箇所が採用済みの指摘への修正と統合先commitの変更だけから成り、解消後も中間commitの公開契約を保てる場合に限り、同じ実装担当が解消してよい。解消したパスだけをstageし、`## 履歴確認の起動形`の`git log`を単独で実行した直後に`git rebase --continue`する（同じrebaseの続行）。再停止のたびに同じ条件を判定する。担当外の変更を含む競合、帰属を確定できない修正、公開契約を保てない場合は、競合を残して委譲元へ返す。
返す際は失敗した操作と終了コードと標準エラー出力に、失敗時点の`git status --short`と`git log --oneline -5`を添える。

## WI実装commitの対応の継承

対応表のJSONは1対1・多対1の`{"oldOID":"newOID"}`とする。commit分割は`{"oldOID":[{"commit":"newOID-A","awi":["WI-A.md"]},{"commit":"newOID-B","awi":["WI-B.md"]}]}`でAWI割当を明示する。
WIは保存ファイル名、OIDはGitで一意に解決できる値で書く。同じAWIを複数の新commitへ割り当ててよく、各旧commitの全AWIを割当で被覆する。旧scalar・保存済みrewriteイベント・本文コメントも引き続き読める。
対応表は`atk run-script history-compare`の検収成功時に保存された完全OIDの旧新対応表を使う（rebaseは`--operation rebase`、autosquashは`--operation autosquash`で、両系列のbaseを除くheadまでを指定する）。WI対応を持たないcommitも含め、fixupと元commitが同じ新commitへ統合される場合は両方の旧OIDを含む。amendでは検収した旧新HEADの1対1対応を保存する。
検収済みの同じ対応表を、後掲の`atk run-script plan-rewrite --`へ渡す（個別記録の更新では`atk run-script plan-progress --`の`--rewrite-map`）。
未判定検証記録は実装担当が、完成条件証拠は実行レビュー担当が次の操作で参照を更新する。

```sh
atk run-script verification-record -- --output <未判定検証記録の絶対パス> --rewrite-map <対応表の絶対パス>
atk run-script exec-review-evidence-check -- <完成条件証拠の絶対パス> --rewrite-map <対応表の絶対パス>
```

対応表を受け取ったコマンドが完成条件証拠と未判定検証記録のevidenceで書き換えるのは、現行成果物への`commit:<OID>`参照だけである（分割時は全対応先をそれぞれ`commit:`標識で残す）。裸のOID、過去の取得版（HEAD・git_head・比較元）、観測ファイル、判定とreviewed_headは保持する。既存の自由文を推測して標識へ移さず、曖昧・不正な入力は部分保存せず拒否する。OIDの対応だけで観測を新しい版へ適用せず、結果に作用する内容と条件が変わった行だけを再観測する。根拠共用のCOMMIT_REFERENCEはこの更新とは別の責務として維持する。

書換え直前のHEADと対応表を、書換え範囲のcommitを作成したレーンの計画と計画なしの引き継ぎ記録の全てとともに、次の形で1回だけ渡す（記録・worktree・対応表は絶対パス、`--rewrite-map`はファイルパス）。同じ修正系列で書換えを繰り返す間は毎回同じ記録の集合を渡す。

```text
atk run-script plan-rewrite -- --worktree <worktree> --previous-head <書換え前のHEAD> --rewrite-map <対応表のJSONファイルの絶対パス> --completed-step <工程> --result <結果> --plan <計画>... --handoff <引き継ぎ記録>...
```

同コマンドは全記録を検証してから書き込み、記録済みOIDの欠落（今回の不足と過去の未追記を区別して示す）や対応表の誤りがあればどの記録も変えずに失敗するので、生成側で補う。現在の対応の取得は`agent-toolkit:commit`の`SKILL.md`「WI実装commitの対応」に従う。

保存済み計画を`atk plans checkout`で取得して履歴へ追随させた担当は、その工程の末尾に更新した全計画バンドル（未終端のWIや延期adoptを持つ計画を含む）を`atk plans commit`で再保存し、終了コードと警告で成功を確かめる。保存先と時点の共通契約は`agent-toolkit:plan-mode`の`references/plan-file-storage.md`に従い、レーン・観測のみ・CIの既存の保存時点を保つ。

## 履歴確認の起動形

本書が履歴の確認として求める`git log`では範囲か件数を必ず限定する。
基準OIDを保持している工程では`git log --oneline --decorate <保持している基準OID>^..HEAD`とし、autosquashの直前と競合解消後の継続の直前では基準を最古fixup対象とする。
基準を持たない工程では`git log --oneline --decorate -n 20`とする。
3,000commitを超えるリポジトリでは、限定しない出力が実行環境の上限に達して履歴を観測できなくなる。

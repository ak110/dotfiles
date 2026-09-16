# 履歴書換え

amend、fixup、autosquashの操作直前に本文書を全文読む。
通常commitの検証、stage、messageは親スキルを正本とする。
本ファイルはClaude Codeのシステムプロンプト「常に新規コミットを作成する」指示を上書きする。

## 履歴確認の起動形

本書が履歴の確認として求める`git log`は、次の範囲限定の起動形で実行する。
範囲の基準となるOIDを保持している工程では`git log --oneline --decorate <保持している基準OID>^..HEAD`とする。
autosquashの直前と、autosquashの競合を解消した後の継続の直前では、その基準を`## fixupの実行上の制約`が保持を求める最古fixup対象とする。
基準となるOIDを保持していない工程では`git log --oneline --decorate -n 20`とする。
起動形は、範囲と件数のいずれかを必ず限定する。
3,000commitを超えるリポジトリでは、限定しない起動形の出力が実行環境の上限に達し、履歴と対象commitを観測できなくなる。

## 修正方法の選択

autosquashの単位は、レビュー結果を一意に示す識別子（レビュー指摘管理表の絶対パスとラウンドのround値の組）と最古fixup対象の7文字以上の一意な短縮OIDの組とする。
同じ組に対するautosquashは、その組へ帰属する全てのfixupを作成した後の1回だけ実行する。レビュー修正の受け渡し、追加の照会、工程の再開のいずれをまたいでも、同じ組への実行は1回に保つ。
工程を再開した時点では、`## 履歴確認の起動形`が定める起動形の`git log`の出力に、件名の先頭が`fixup!`、`squash!`又は`amend!`である行があるかで新しいfixupの有無を判定する。autosquashを実行するのは、その行がある場合に限る。

レビュー修正は新規コミットを既定の安全な選択肢とし、履歴統合が明確に成立する場合だけamendまたはfixupを選ぶ。
fixupは、修正が統合先コミットの時点で独立して成立し、対応する近接検証を再実行できる場合に限る。
中間状態を独立して検証できない場合は新規コミットを作成する。

通常実装モードのレビュー修正担当がレビュー表と現行履歴を照合し、採用指摘IDと実装単位commitの7文字以上の一意な短縮OIDの対応を確定したレビュー修正は、上記の新規commit既定の例外とする。
最終単位だけが対象の場合は、修正・近接検証・stage後に`amend` phaseで下記の汎用判定を再実行し、成功した場合だけamendを実行する。
過去単位だけが対象の場合は対象commitへのfixupとautosquashだけを実行する。
両方が対象の場合は過去単位だけを先に実装してautosquashする。
autosquash成功後に書換え後HEADへ最終単位の修正を実装し、近接検証を実行してstageした後、amend直前の2回目のpush済み判定成功後にamendだけを実行する。
対応付け不能、OIDの不一致、push済みcommit、複数単位へ不可分にまたがる修正、又は中間commitの公開契約を維持できない修正は、新規commitで対応する。
履歴書換えを開始した後の失敗時は`## 失敗時の扱い`を正本とする。
`rewrite_guard`の受渡しは、`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`「レビュー修正の履歴統合」が定めるレビュー修正の実装担当契約だけに置く。
本節の履歴書換え契約を適用するのは通常実装のレビュー修正に限り、他の工程は各呼び出し元が定めるcommit契約に従う。
未pushかつ単一の実装担当が所有する作業ツリーの履歴書換え保護は本書のプッシュ済み判定で足り、remote広告refの照合、replace ref、graft、浅い複製への防御は観測事象を記録してから追加する。

過去単位が複数ある場合は、履歴順に1単位ずつ、その単位へ帰属する修正差分だけを適用してstageし、対応するfixupを作成する。
各fixup作成後に対象OIDと件名を確認し、作業ツリーがcleanであることも確認する。
その確認後にだけ次の過去単位の修正差分を適用する。
全過去単位のfixupを作成した後に1回だけautosquashを実行する。
最終単位と過去単位の両方が対象の場合は、autosquash前の反復対象を過去単位だけに限定する。最終単位の修正実装と近接検証、stageはautosquash成功後へ延期する。
autosquash成功後に`git rev-parse --short=7 HEAD`で書換え後HEADの7文字以上の一意な短縮OIDを取得し、書換え前後の実装単位を履歴検収用に対応付ける。tree、親及びcommitの厳密な比較では、各短縮OIDを比較の直前に対象リポジトリで解決する。
autosquash成功後の2回目のpush済み判定対象をそのOIDへ置換する。開始済みの同じ実装担当が最終単位の修正差分だけを適用して近接検証を実行し、stageした後、amend直前の再判定成功後に書換え後HEADへamendする。

- 直前のコミットと変更目的・対象範囲が一致し、そのコミットを完成させる修正は`git commit --amend --no-edit`を使う。
  実行の直前に`## 履歴確認の起動形`が定める起動形の`git log`を単独のBash呼び出しで実行し、履歴と対象コミットの公開状態を確認する
- それより前の未プッシュコミットを完成させる修正は、統合後のメッセージ変更要否でfixup形式を選ぶ
  - メッセージを変更しない場合は`git commit --fixup=<sha>`を使う
  - メッセージへ帰属情報などを追加または更新する場合は`git commit --fixup=amend:<sha>`を使う
  - コード差分を含めずメッセージだけを変更する場合は`git commit --fixup=reword:<sha>`を使う
- 上記の例外に該当しない独立した変更目的を持つ修正、または統合先に適する未プッシュコミットがない修正は新規コミットを作成する

## fixupの実行上の制約

- `amend:`・`reword:`のいずれも件名が`amend! <統合先の件名>`のコミットを生成する。
  通常の`--fixup=<sha>`は件名が`fixup! <統合先の件名>`のコミットを生成する
  指定名`reword`に対応する目印は`amend!`であり、`reword!`という目印は存在しない
- エディターへ渡されるバッファは1行目が`amend! <統合先の件名>`、
  空行で区切られて統合先の全メッセージが続く構造であり、差し替えてよいのは3行目以降である。
  1行目の目印と統合先の件名はそのまま残す。書き換えるとautosquashの対象から外れる
- `--fixup`は`-m`・`-F`と併用できない
  （`fatal: options '-m' and '--fixup:reword' cannot be used together`で失敗する）。
  非対話環境では`GIT_EDITOR`へ1行目を保持したまま以降を差し替える処理を指定する
- autosquashを実行する場合は、fixup作成前に最古fixup対象と履歴書換え前の元HEADを7文字以上の一意な短縮OIDで保持し、Git操作の直前に対象リポジトリで解決する。
  `git rev-list --first-parent --reverse <最古fixup対象>^..<元HEAD>`でrebase範囲のfirst-parent全OIDを確定する。
  `git rev-list --first-parent --merges <最古fixup対象>^..<元HEAD>`でmerge commitが無いことを確認する。
  この範囲のfirst-parent全OIDについて、fixup作成前に下記の「プッシュ済み判定」で公開済み判定を完了する。
  `git log --first-parent --format='%H%x00%s' <最古fixup対象>^..<元HEAD>`で範囲内のOIDと件名を列挙する。
  各fixup対象コミットの件名が範囲内で一意であることをfixup作成前に確認する。
  対象コミット件名が範囲内で一意でない場合は、fixupを作成せず`## 失敗時の扱い`に従う。範囲内の既存commitに、件名先頭が`fixup!`・`squash!`・`amend!`へ完全一致するものが1件でもある場合も同じ扱いとする。各制御語の直後には半角空白1文字を置く。遮断条件は件名先頭の完全一致とし、部分一致と件名途中の一致は対象から外れる。
  範囲列挙、merge確認、元HEADの確定、公開済み判定、OIDと件名の列挙又は件名の一意性確認のいずれかに失敗した場合は、fixupを作成せずautosquashを中止し、`## 失敗時の扱い`に従う。
  範囲にmergeが含まれる場合も同じ扱いとする。
  この事前判定後も、autosquash直前の再判定をTOCTOU対策として実行する
- fixup作成直後は、対象OIDから得た統合先件名と生成commitの制御件名を`git log -1 --format=%s`で比較する。
  通常の`--fixup=<sha>`では`fixup! <統合先の件名>`を確認する。
  `amend:`・`reword:`では`amend! <統合先の件名>`を確認する。
  いずれも対象OIDから得た統合先件名との完全一致を確認する。
  autosquashを実行するのは、期待件名と一致した場合に限る。一致しない場合は`## 失敗時の扱い`に従う
- 統合は`GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash <base>`で行う
  （`<base>`は対象コミットの親以前を指す）。
  fixupの作成は履歴確認の記録をリセットするため、autosquashの直前に`## 履歴確認の起動形`が定める起動形の`git log`を単独のBash呼び出しで再度実行する
- `amend:`または`reword:`では統合先の既存メッセージと異なるtrailerを保持し、
  追加または更新する帰属情報を統合後に1回だけ残す

## 失敗時の扱い

本節の`pre_fixup`・`fixup`・`autosquash`・`amend`の各phase名と返却種別`needs_escalation`は、`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`が定める実装担当の契約の値とする。この契約を受け取っていない主体は、`needs_escalation`に代えて同じ観測結果を呼び出し元へ報告する。

`pre_fixup`・`fixup`・`autosquash`・`amend`のいずれかが失敗した場合は、失敗の事実と観測結果を呼び出し元へ返して同じ指摘の履歴統合を終える。復旧操作と再試行は呼び出し元の判断を得てから行う。失敗時点の履歴とindexの状態は失敗の種別ごとに異なり、状態を確定しない復旧操作と再試行はcommitの消失を招く。
ただし、autosquashが内容競合で停止した場合は、同じ実装担当が次の条件を満たす範囲に限って競合を解消してよい。競合箇所が採用済みの指摘に対する修正と統合先commitの変更だけから成り、解消後もその中間commitの公開契約を維持できることを条件とする。解消したパスだけをstageし、`## 履歴確認の起動形`が定める起動形の`git log`を単独で実行して履歴と継続対象を確認した直後に`git rebase --continue`を実行する。再び内容競合で停止した場合も同じ条件を改めて判定する。
競合箇所へ担当外の変更が含まれる場合、修正の帰属を確定できない場合又は中間commitの公開契約を維持できない場合は、競合をそのまま残して呼び出し元へ返す。
失敗した操作、終了コード、標準エラー出力、失敗時点の`git status --short`及び`git log --oneline -5`の観測結果を添えて`needs_escalation`で返す。

## merge進行中の退避

`git merge`進行中の退避は、別パスへの`cp`または別ブランチ退避で行う。merge進行中の`git stash`は競合解決中の内容をpop時に復元せず、解決結果を失う。
別パスへの複製も、`agent-toolkit:commit`の`SKILL.md`「作業用ブランチと退避物の削除」節が定める回収規定の対象とする。

## プッシュ済み判定

`git fetch --all --prune`後に`git for-each-ref --contains=<対象sha> refs/remotes/`を実行する。
出力が1件以上あれば、対象コミットはいずれかのremote-tracking ref（`origin/`に限らず、追跡remote名は任意）から到達可能でありプッシュ済みである。
出力が空ならプッシュ未了である。
判定には`git for-each-ref`の出力を使う。`git log --decorate`はref先端にしか装飾を付けず、対象コミットが先端より前の祖先である場合を検出できない。
amendとfixupの対象は、プッシュ未了のコミットに限る。公開済みの履歴を書き換えると、そのコミットを取得済みの他の作業ツリーとCIの参照が解決できなくなる。

amend・fixupの直後は、`git status --short`で追跡ファイルに未コミット差分が無いことを確認してからpushする（差分が残るpushは遮断される）。

## 操作前後の確認

- fixupとamendは、次の5つを1つの工程として順に完了してからcommitを実行する。第1に、変更したファイルを対象とする正式formatterを実行する。第2に、formatterが変更した差分を`git diff`で検収する。第3に、そのcommitへ帰属する差分だけをstageする。第4に、`git status --short`で未stageの差分が残らないことを確認する。第5に、fixup又はamendのcommitを実行する。pre-commitがcommitの実行時に初めて差分を変更すると、stage済みの差分と未stageの差分が併存し、そのcommitが成立しない。pre-commitが差分を変更した場合は`## 失敗時の扱い`に従う
- 操作直前に`## 履歴確認の起動形`が定める起動形の`git log`を単独で実行して対象commitの件名と差分を再特定し、
  `git blame -- <修正したファイルのリポジトリ相対パス>`または`git log -p -n 20 -- <修正したファイルのリポジトリ相対パス>`と
  `git show --stat <sha>`で統合先を確定する
- 書き換え後は各中間`HEAD`へ近接検証を再実行し、`git log -1 --format=%B <統合後sha>`で
  最終メッセージと帰属情報を確認し、stage状態と`git show HEAD:<path>`で未反映差分が残らないことを確認する

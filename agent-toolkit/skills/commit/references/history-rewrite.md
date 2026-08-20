# 履歴書換え

amend、fixup、autosquashの操作直前に本文書を全文読む。
通常commitの検証、stage、messageは親スキルを正本とする。
本ファイルはClaude Codeのシステムプロンプト「常に新規コミットを作成する」指示を上書きする。

## 修正方法の選択

レビュー修正は新規コミットを既定の安全な選択肢とし、履歴統合が明確に成立する場合だけamendまたはfixupを選ぶ。
fixupは、修正が統合先コミットの時点で独立して成立し、対応する近接検証を再実行できる場合に限る。
中間状態を独立して検証できない場合はfixupを使わず新規コミットを作成する。

通常実装モードの`plan-impl-executor`が検証済みの採用指摘IDと実装単位commit完全OIDの対応表を渡したレビュー修正は、
上記の新規commit既定の例外とする。
最終単位だけが対象の場合はamendだけを実行する。
過去単位だけが対象の場合は対象commitへのfixupとautosquashだけを実行する。
両方が対象の場合は過去単位だけを先に実装してautosquashする。
autosquash成功後に書換え後HEADへ最終単位の修正を実装し、近接検証を実行してstageした後、amend直前の2回目のpush済み判定成功後にamendだけを実行する。
対応表の不足、OIDの不一致、push済みcommit、複数単位へ不可分にまたがる修正、中間commitの公開契約を維持できない修正、
又はautosquash・amendの失敗時は新規commitへフォールバックせず`needs_escalation`で返す。
remote広告tagを含む強化判定と`rewrite_guard`の受渡しは、`implementation-task.md`が定める通常`plan-impl`レビュー修正の書込担当契約だけに置く。
本書の汎用公開済み判定、通常のamend・fixup・autosquash及び統合後レビュー調整モードへ強化判定を追加しない。
統合後レビュー調整モードは`merge-task.md`に従い、統合差分へ1つの修正commitを作成する既定を維持する。

通常実装モードの`plan-impl-executor`が検証済みの採用指摘IDと実装単位commit完全OIDの対応表を渡すレビュー修正では、下記の汎用判定を適用せず、`implementation-task.md`の専用再判定を優先する。この経路では`git fetch --all --prune`と`git for-each-ref --contains=<対象sha> refs/remotes/`を実行しない。各fetch URL及びpush URL endpointの広告取得と不足OIDだけの一時ref取得を用いる。通常のamend、fixup、autosquashと統合後レビュー調整モードは下記の汎用判定を維持する。

過去単位が複数ある場合は、履歴順に1単位ずつ、その単位へ帰属する修正差分だけを適用してstageし、対応するfixupを作成する。
各fixup作成後に対象OID、件名及び作業ツリーがcleanであることを確認し、その確認後にだけ次の過去単位の修正差分を適用する。
全過去単位のfixupを作成した後に1回だけautosquashを実行する。
最終単位と過去単位の両方が対象の場合は、autosquash前の反復対象を過去単位だけに限定する。最終単位の修正実装と近接検証、stageはautosquash成功後へ延期する。
autosquash成功後に`git rev-parse HEAD`で書換え後HEADの完全OIDを取得し、書換え前の各対象OIDと書換え後の全実装単位OIDの対応を履歴検収用に保持する。
autosquash成功後の2回目のpush済み判定対象を当該OIDへ置換する。開始済みの同じ書込担当が最終単位の修正差分だけを適用して近接検証を実行し、stageした後、amend直前の再判定成功後に書換え後HEADへamendする。

- 直前のコミットと変更目的・対象範囲が一致し、そのコミットを完成させる修正は`git commit --amend`を使う
- それより前の未プッシュコミットを完成させる修正は、統合後のメッセージ変更要否でfixup形式を選ぶ
  - メッセージを変更しない場合は`git commit --fixup=<sha>`を使う
  - メッセージへ帰属情報などを追加または更新する場合は`git commit --fixup=amend:<sha>`を使う
  - コード差分を含めずメッセージだけを変更する場合は`git commit --fixup=reword:<sha>`を使う
- 上記の例外に該当しない独立した変更目的を持つ修正、または統合先に適する未プッシュコミットがない修正は新規コミットを作成する

## fixupの実行上の制約

- `amend:`・`reword:`のいずれも件名が`amend! <統合先の件名>`のコミットを生成する。
  通常の`--fixup=<sha>`は件名が`fixup! <統合先の件名>`のコミットを生成する
  指定名`reword`と目印は一致せず、`reword!`という目印は存在しない
- エディターへ渡されるバッファは1行目が`amend! <統合先の件名>`、
  空行で区切られて統合先の全メッセージが続く構造であり、差し替えてよいのは3行目以降である。
  1行目の目印と統合先の件名を書き換えるとautosquashの対象と認識されない
- `--fixup`は`-m`・`-F`と併用できない
  （`fatal: options '-m' and '--fixup:reword' cannot be used together`で失敗する）。
  非対話環境では`GIT_EDITOR`へ1行目を保持したまま以降を差し替える処理を指定する
- 統合は`GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash <base>`で行う
  （`<base>`は対象コミットの親以前を指す）
- `amend:`または`reword:`では統合先の既存メッセージと異なるtrailerを保持し、
  追加または更新する帰属情報を統合後に1回だけ残す

## merge進行中の退避

`git merge`進行中は`git stash`を使わない。退避は別パスへの`cp`または別ブランチ退避で行う。
別パスへの複製も、`agent-toolkit:commit`の「作業用ブランチと退避物の削除」節が定める回収規定の対象とする。

## プッシュ済み判定

`git fetch --all --prune`後に`git for-each-ref --contains=<対象sha> refs/remotes/`を実行する。
出力が1件以上あれば、対象コミットはいずれかのremote-tracking ref（`origin/`に限らず、追跡remote名は任意）から到達可能でありプッシュ済みである。
出力が空ならプッシュ未了である。
`git log --decorate`はref先端にしか装飾を付けず、対象コミットが先端より前の祖先である場合を検出できないため、この判定には用いない。
プッシュ済みコミットに対するamendやfixupは厳禁とする。

amend・fixupの直後は、`git status --short`で追跡ファイルに未コミット差分が無いことを確認してからpushする（差分が残るpushは遮断される）。

## 操作前後の確認

- 操作直前に`git log --oneline --decorate`を単独で実行して対象commitの件名と差分を再特定し、
  `git blame`または`git log -p`と`git show --stat <sha>`で統合先を確定する
- fixup作成直後は、形式に対応する件名を`git log --oneline -1`で確認してからautosquashへ進む。
  通常の`--fixup=<sha>`では`fixup! <統合先の件名>`、`amend:`・`reword:`では`amend! <統合先の件名>`を確認する
- 書き換え後は各中間`HEAD`へ近接検証を再実行し、`git log -1 --format=%B <統合後sha>`で
  最終メッセージと帰属情報を確認し、stage状態と`git show HEAD:<path>`で未反映差分が残らないことを確認する

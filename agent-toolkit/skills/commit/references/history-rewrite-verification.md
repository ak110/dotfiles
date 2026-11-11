# 履歴書換え：操作前後の確認

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 操作前後の確認

**1.** 親リポジトリのみを対象とする履歴統合や検証のためのブランチ切り替えでは、初期化済みのサブモジュールごとに操作前後のHEAD、ブランチ、および作業内容が一致していることを確認する。
サブモジュールの参照自体を更新する操作は別途扱う。
親リポジトリのみを対象としたrebaseの開始・継続・中止、ならびにcheckout・switch・restoreを実行する際は、環境側の再帰設定によらずサブモジュールの作業状態を保持するため、コマンドごとに`git -c submodule.recurse=false`を明示的に指定する。

**2.** fixupやamendを実行する際は、変更ファイルに対して正式なformatterを実行し、その差分を検収したうえで、fixupやamendの対象コミットに帰属する差分のみをステージングする。
そのあと`git status --short`を実行し、未ステージングの差分が残っていないことを確認してからコミットする。
もしコミット時にpre-commitフックによって新たなファイル差分が発生すると、ステージング済みと未ステージングの差分が混在してコミットが成立しなくなる（この場合は`history-rewrite-recovery.md`「失敗時の扱い」に従う）。

**3.** 操作する直前に、後述の「履歴確認の起動形」に示す`git log`を単独で実行して対象コミットを再度特定する。
さらに`git blame`や`git log -p -n 20 -- <ファイル>`、および`git show --stat <sha>`を用いて、統合先のコミットを確実に確定する。

**4.** autosquashを実行したあとは、すべてのfixupを作成した直後のHEADと`<最古fixup対象>^`を用いて以下のコマンドを実行する。
この処理により、最終的なツリーの一致、コミット件数、制御用件名（`fixup!`等）の残存がないこと、およびfixup対象以外の変更内容が保持されていることを検証し、新旧コミットの対応表を生成する（`reword:`の対象もfixup対象として扱う）。
終了コードが0以外となった場合は、`history-rewrite-recovery.md`「失敗時の扱い」に従う。

  ```text
  atk run-script history-compare -- --operation autosquash --work-dir <worktreeの絶対パス> --old-base <保持した親OID> --old-head <autosquash直前HEAD> --new-base <保持した親OID> --new-head <書換え後HEAD> --output <managed-temp内の対応表JSONの絶対パス>
  ```

  変更内容の同一性は、前後の文脈を含まない（文脈0行の）差分からハンクの位置情報とblobの識別子を除去した値が一致するかどうかで判定する。
  この値が異なる場合でも、新しいコミットの親ツリーを一時的なインデックスに読み込み、古いコミットのパッチを`git apply --cached --3way`で適用して得られたツリーが新しいコミットのツリーと一致すれば、同一の内容とみなす。
  いずれの方法でも一致しない場合は、変更内容が意図せず変化したものとして検収を失敗とする。
  なお、差分における空白文字の差異も変更内容の一部として扱う

**5.** amendを実行したあとは、amendの直前に保持していたHEADとその親OIDを用いて以下のコマンドを実行する。
この処理により、新旧のコミットが同一の親コミットを持つ単一の異なるコミットであることを検証し、新旧HEADの1対1の対応表を生成する（amendは内容を変更する操作であるため、ツリーやパッチの一致検証は行わない）。
終了コードが0以外となった場合は、`history-rewrite-recovery.md`「失敗時の扱い」に従う。

  ```text
  atk run-script history-compare -- --operation amend --work-dir <worktreeの絶対パス> --old-base <保持した親OID> --old-head <amend直前HEAD> --new-base <保持した親OID> --new-head <amend後HEAD> --output <managed-temp内の対応表JSONの絶対パス>
  ```

**6.** 履歴の書き換え後は、各中間状態の`HEAD`に切り替えて変更範囲の検証を再度実行する。
続いて、`git log -1 --format=%B <統合後sha>`によって最終的なコミットメッセージと`Co-Authored-By:`の内容を確認し、ステージング状態および`git show HEAD:<path>`によって反映漏れの差分がないことを確認する。
中間コミットへの切り替えや検証後の復帰には、それぞれ`git -c submodule.recurse=false checkout <参照>`を使用する。
`history-rewrite-wi-records.md`「WI実装commitの対応の継承」へ進む。

## 一部の変更パスを外した対応の確認

commitの件数を保ったまま、旧commitの変更パスの一部を意図して外した書換えは、外したパスを`--exclude-path <旧OID> <リポジトリ相対パス>`で明示して対応表を生成する。
外したパスごとに反復指定する。
commit全体を外した書換えと、1件を複数へ分割した書換えには使わない。

  ```text
  atk run-script history-compare -- --operation rebase --work-dir <worktreeの絶対パス> --old-base <旧base> --old-head <書換え直前HEAD> --new-base <新base> --new-head <書換え後HEAD> --exclude-path <旧OID> <リポジトリ相対パス> --output <managed-temp内の対応表JSONの絶対パス>
  ```

パスはGitのパターンとして展開されず、文字列が一致する変更パスだけが除外される。
同コマンドは、旧commitの変更パスから指定分を除いた集合と新commitの変更パス集合の一致、残る変更内容の同一性、件名、相対順を検収する。
次の指定は対応表を保存せずに拒否される。

- 旧系列にないOID
- 旧commitが変更していないパス
- 新commitでも変更されているパス
- 旧commitの変更パスを全て除く指定

指定のない変更パスの減少は、意図しない欠落と区別できないため拒否される。
除外を指定した旧commitは、統合先の既存変更への自動的な対応付けの対象から外れ、分離後のcommitとの対応を検収される。
残る内容も競合解消で変わった場合は、同じ旧OIDを`--resolved-conflict`へ併せて指定する。
除外指定だけでは残る内容の変化は受理されない。
併せて指定した場合も、除外後に残る旧commitの変更パスと新commitの変更パスは完全に一致させる。
統合先への追随による変更パスの増減が受理されるのは、除外指定のない競合解消に限られる。
成功出力には除外した旧OID、対応先、パスが示されるため、検収の入力にする。
対応表は他の操作と同じ形式で保存されるため、`history-rewrite-wi-records.md`「WI実装commitの対応の継承」の記録更新へそのまま渡す。

## 履歴確認の起動形

本書が履歴確認のために求める`git log`では、表示する範囲または件数を必ず指定して出力を限定する。
基準となるOIDを保持している工程では、`git log --oneline --decorate <保持している基準OID>^..HEAD`を実行する。
autosquashの直前、および競合を解消してrebaseを再開する直前には、最古のfixup対象コミットを基準OIDとする。
基準となるOIDを持たない工程では、`git log --oneline --decorate -n 20`を実行する。
コミット数が3,000件を超えるような大規模リポジトリでは、件数を限定せずに出力すると実行環境のバッファ上限に達し、履歴を正常に確認できなくなるためである。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 履歴書換え：失敗時の扱いの基準を適用する前 | `history-rewrite-recovery.md` |
| 履歴書換え：WI実装commitの対応の継承の基準を適用する前 | `history-rewrite-wi-records.md` |

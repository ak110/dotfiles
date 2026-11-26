# WIとcommitの対応の記録

AWIを完了させるエージェントが、どのcommitがどのWIを実装したかを推測でなく記録から示し、ユーザーと後続の担当が変更の由来を追えるようにする。

WIを実装したcommitとAWIの対応を記録し、取得する定義である。
履歴の書き換えでOIDが変わった後の引き継ぎは`agent-toolkit:atk-mode/playbooks/history-rewrite.md`「WI実装commitの対応の継承」にある。
対応は、計画（計画が無ければ引き継ぎ記録）と同じディレクトリ、同じファイル幹（stem）の`<stem>.wi-commits.jsonl`だけに記録する。
書き込みは`atk run-script plan-progress`と`atk run-script plan-rewrite`、読み取りは`atk run-script plan-commits`で行う。
引数は各コマンドの`--help`で確かめ、計画、引き継ぎ記録、worktree、対応表は絶対パスで、AWIはファイル名で渡す。
同じstemのJSONLは保存先であり、位置引数に渡さない。
commitは一意な短縮OIDで扱い、入力には完全なOIDも使える。
WIとの対応は記録から取得し、メッセージの件名や説明文から推測しない（原則3、`agent-basics:writing-standards/references/quality-principles-facts.md`）。

## WI実装commitの対応

1. 記録する。
   実装commitを作る前に、対応するAWIの集合と今のHEADを取得する。
   `git commit`を単独で実行して正常に終わった時だけ、作成前のHEAD、新しいHEAD、worktree、対応する全てのAWIを`atk run-script plan-progress`へ渡す。
   - 旧書式の計画と引き継ぎ記録では、`--completed-step`と`--result`も渡し、本文へ進捗の行を追記する。
     新書式の計画は進捗ログを持たないため、両者を省いてよく、本文を変えずに対応だけを記録する（`--rewrite-map`も同じ）。
     `atk run-script plan-rewrite`も、新書式の計画は本文を変えずに対応だけを更新する（同コマンドの`--completed-step`と`--result`は必須のまま）。
   - 1つのcommitが複数のAWIの変更を含む場合は、全件を同じ記録へ渡す。
   - 引き継ぎ記録へは、`--handoff`と対象AWI全件の`--allowed-awi`を指定する。
   - 公開の時点で計画なしで進行中のAWIと計画のAWIの変更を1つのcommitが含む場合は、同じ作成前HEADと新しいHEADで、計画の`related_wis`にあるAWIを計画へ、残りを引き継ぎ記録へ記録する。
     手順2の取得と`commit_record`も、AWIごとに記録した側を使う。
   - レビュー修正とCI修正のcommitも全件記録する。
   - cherry-pickやfast-forwardで複数のcommitを取り込んだ場合は、操作前後のHEADで一括して記録する。
     AWIの集合が異なる場合は、集合ごとに区間の直前を作成前HEAD、区間の末尾を新しいHEADとして記録し、区間の末尾は今のHEADのfirst-parentの履歴にあればよい。
   - 記録済みのcommitを含む区間は渡さず、AWIの集合ごとの未記録の区間だけを指定する。
     first-parentの区間の通常のcommitと、競合解消の差分を持つmerge commitを記録し、取り込んだ他レーンのcommitと変更の無い接続のmergeは含めない。
2. 取得する。
   AWIを完了状態にするエージェントは、次で最新のcommitの一覧を短縮OIDで取得する（出力はJSON Linesの`awi`と`commits`）。

   ```sh
   atk run-script plan-commits -- <計画Markdownの絶対パス> --worktree <対象worktreeの絶対パス>
   atk run-script plan-commits -- <引き継ぎ記録Markdownの絶対パス> --worktree <対象worktreeの絶対パス> --handoff --allowed-awi <記録の対象AWI全件を反復指定>
   ```

   - 対象を絞る場合は`--awi <AWIファイル名>`を反復指定し、実装差分を伴わない項目はそれを除いた対象を指定する。
   - 保存で計画のパスが移っても、同名の保存済み計画が一意ならそのまま指定できる。
     同名が複数あれば、保存先の実在するパスを指定する。
   - 取得した短縮OIDを`atk wi adopt --commit`へ渡し、複数のcommitは`--commit <OID>`を反復する。
     commitの説明などのメモは`--note-file`に書く。
   - 実装commitを持つはずの項目の記録が欠けている場合、対象外のAWIが含まれる場合、OIDを解決できない場合は、記録の生成側を直してから取得し直す。
   - `atk lane integrate`へ渡すadoptの`--commit`も今の取得結果から作り直し、manifestのWI行の`commit_record`へ元の計画か引き継ぎ記録のMarkdownの絶対パスを指定する。
   - 記録済みのcommitへAWIを足すには、旧OIDを同じOIDへ写す対応表`{"<旧OID>": [{"commit": "<同じOID>", "awi": [<記録後の全AWI>]}]}`を`atk run-script plan-progress`の`--rewrite-map`へ渡す。
3. 記録の対象を分ける。
   実装差分なしで満たした項目、WIと関係の無いcommit、回答だけのUWIは対応の記録の対象外とし、引き継ぎ記録を根拠にcommitの指定を省く。
   計画と対応の記録は`atk plans commit`で保存する。

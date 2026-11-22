# WIの保存と取得

起草を終えたWIの原稿を検証し、キューへ保存し、保存結果を検収する手順書である。
保存済みのWIと回答済みUWIの取得も扱う。
起草は`agent-toolkit:atk-mode/playbooks/wi-drafting.md`で終えてから本書へ進み、上から実行する。
報告用UWIは`agent-toolkit:atk-mode/references/report-formats.md`「報告用UWIの確認と保存」に従う。

保存前に確かめるのは、意味、必要事項、成立性、後続が引き継げることに限り、意味を変えない表現だけの推敲と、そのための再検収は省く。
根拠は原稿、既存の作業記録、CLIの保存出力を直接参照し、揃っていればその所在だけを渡す。
不足する情報だけを既存の記録へ補い、原稿の要約や調査の経過を別の証拠ファイルへ書き直す作業は省く。
`atk`のコマンド、引数、順序は公開契約であり、本書のとおりに使う。

## 保存前の確認

1. 原稿と根拠資料を`agent-toolkit:atk-mode/playbooks/managed-temp.md`の一時領域へ保存する。
   既存項目の更新では、`atk wi show <ファイル名>`で現行本文と保留の状態を確かめる。
2. 回答済みUWIとの整合を確かめる。
   対象リポジトリの回答済みUWIを`atk wi list --target-repo=<対象リポジトリの絶対パス> --type=uwi --answered=yes --skip-pull --summary-only`で取得する。
   同じ実装領域に関わる項目は`atk wi show`で回答を読み、新しい要求と両立するかを確かめる。
   両立しない部分があれば保存を控え、元の要求と回答を優先して本文を直す。
   ユーザーだけが決められる値が残る場合に限り、`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」に従って確認する。
3. 保存せずに検証する。
   - 新規は`atk wi add --dry-run --body-file <原稿の絶対パス> --source <source>`を使う。
   - 更新は`atk wi edit <ファイル名> --dry-run --body-file <原稿の絶対パス>`を使う。
   - `source`は`agent-toolkit:atk-mode/references/origin-and-approval.md`「由来と`source`」で決め、原稿のfrontmatterに指定済みなら`--source`を省く。
   - UWIの新規では`--type=uwi`と、その問いに要る回答形式の指定を加える。
   - 作業用の抽出先を固定する場合は`--opening-output <抽出先の絶対パス>`を付け、複数原稿では`--body-file`と同じ順に反復する。
     比較入力との区別は`agent-toolkit:atk-mode/references/draft-semantic-review.md`に従う。
   - 受領済みの冒頭の抽出がある時は`--compare-opening-file <抽出の絶対パス>`を付け、複数の原稿では`--body-file`と同じ順に反復する。

   並行して準備した原稿も1件ずつ検証する。
   コマンド、終了コード、出力を証拠に残す。
   dry-runが原稿ごとに保存するユーザー向けの部分（冒頭の抽出）の保存先を、入力原稿と要求の識別子に対応付ける。
   原稿を変えたらdry-runをやり直し、抽出を更新する。
   複雑な引用の取り込みには`atk run-script wi-quote-check`を使ってよい。
4. 本書「投入前の読み直し」で各原稿を読み直し、不備を直して手順3からやり直す。
5. 調べ残しを片付ける。
   広がりの探索を限定した場合は、`agent-basics:bugfix/references/spread.md`に従い、探した範囲と探していない範囲を原稿と証拠に書く。
   是正の範囲は`agent-basics:bugfix/references/prevention.md`「害と費用の比べ方」で決める。
   未確認の範囲は`agent-toolkit:atk-mode/playbooks/wi-drafting.md`「未確定事項と確認」に従う。
   未確認の範囲には、投入前に確定すべき事項、範囲語や開放列挙のうち未判定の対象、検索に掛からず読んでいない対象、権限と到達可能性の未確認を含む。
   確かめる手段が無い範囲は、`agent-toolkit:atk-mode/references/awi-body.md`「調査結果と判断根拠」の確認対象外の条件か、`agent-toolkit:atk-mode/references/origin-and-approval.md`「範囲語と程度」で扱う。
   どちらでも扱えない場合だけ「続行できない理由」として報告する。
   後続へ委ねる具体化は、`agent-toolkit:atk-mode/playbooks/wi-drafting.md`「確定の判定」に従う。
6. 意味の検収へ渡す。
   原稿、抽出の保存先、要求、必要な根拠を投入前レビューへ渡し、冒頭だけの先行判定に使わせる。
   付随型の投入担当は、原稿と証拠を委譲元へ返して意味の検収を待つ。
   専念型と直接起草するメインは、`agent-toolkit:atk-mode/playbooks/wi-drafting.md`「直接起草と独立した検収の選択」で決めた検収へ進む。
   検収で確かめる観点は`agent-toolkit:atk-mode/references/draft-semantic-review.md`にある。
7. 指摘を受けたら、単語だけの指示を除き、指摘された不一致を同じ原稿へ反映して再提出する。
   ユーザーが読む部分の語句への指示では、直した箇所の全体に`agent-toolkit:atk-mode/playbooks/wi-drafting.md`「ユーザーが読む部分の合格基準」を適用し直す。
   各指示を、投入前に解消すべき成立性の不足、計画工程へ委ねる具体化、説明から補われた要件のどれかに分ける（`agent-toolkit:atk-mode/references/draft-semantic-review.md`「初回と再判定・差戻し」）。
   後の2つは投入の条件へ足さず、判定の根拠を成立性の証拠に残して報告する。
   根拠には、要件とみなされた値の出所（逐語の要求、実際の回答、エージェントが作った質問文や選択肢の説明）を確かめた結果も含める。
   計画担当の判断に要る具体化は、計画段階で決める事項として本文に書く。
   実現方式を変える修正は、開始から終了まで、途中で失敗した時の再開まで処理がつながることを確かめてから再提出する。

### 投入前の読み直し

各原稿を他の原稿から切り離し、本文だけを読んで次を確かめ、全てを満たす原稿だけを保存する。
報告用を除くUWIには、`agent-toolkit:atk-mode/references/awi-body.md`の各節と原因分析の項目を除いて当てる。

- 必要性: 完成条件は、起草者が足した条件も含め、元の目的に対する必要性と由来を`agent-toolkit:atk-mode/references/awi-body-completion.md`「完成条件」で判定する。
  ユーザーの明示要求や必要な品質を変える場合は、`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」へ渡す。
- 原文との対応: 逐語引用と反映内容を原文と読み比べ、要求の脱落・追加・意味の変化と、ユーザーの回答とエージェントの説明の混同が無い。
  回答済みの注記は、送達した質問だけに付ける。
  分割した場合は、要求と割当先が対応する。
  要件と技術判断の分類が`agent-toolkit:atk-mode/playbooks/wi-drafting.md`「確定した要件と委譲元の判断」に従い、原文と実際の回答の出所で保存の直前まで裏付けられる。
  説明の追記は`agent-toolkit:atk-mode/references/drafting-decisions.md`「採否と実現方式の確定」の必要性の判定を経ており、質問への回答を無関係な変更要求へ変えていない。
- 判断の展開: ユーザーが確定した判断を複数の通常AWIへ展開する場合は、判断ごとに各本文の反映箇所か非該当の理由を対応付ける。
  保存するのは、全ての判断を反映した集合に限る。
- 対象集合: 対象と除外が、原文、`## 反映内容と反映先`、原因分析と整合し、原因分析を含む場合は原因の条件から導いた母集団も満たす。
  仕分け・要約・統合をした本文は、`agent-toolkit:atk-mode/references/drafting-decisions.md`「対象集合の仕分け」の元の調査結果との対応を保ち、その対応を証拠に残す。
- 本文の自己完結: 指示語の参照先は同じ本文に置き、本文だけで着手できるかを`agent-toolkit:atk-mode/references/awi-body.md`「通常AWIの本文」で確かめる。
- 名詞句と名称: タイトルと本文で既存の要素（スキル、工程、担当、hookの通知など）を指す名詞句は、定義元が実在し、本文から定義へ到達できる。
  会話やcommitメッセージで使った呼称も確かめる。
  別の文書の節名・用語・略号の表記は`agent-basics:writing-standards/references/writing.md`「文章の共通規定」に従う。
  新しい名称は`agent-basics:writing-standards/references/defined-names.md`「命名の方針」に従い、長い正式名称でもタイトルで不当に省かない。
  ユーザーが読む部分は、`agent-toolkit:atk-mode/playbooks/wi-drafting.md`「ユーザーが読む部分の合格基準」を満たす。
- 節の内容: `agent-toolkit:atk-mode/references/awi-body.md`「要求固有性と記載量」と「節の構成と分担」に従う。
  既存の値を変える対象ごとに、`agent-toolkit:atk-mode/references/drafting-decisions.md`「方針と判断の記録との比較」の結果を書いている。
- 採用案: `agent-toolkit:atk-mode/references/drafting-decisions.md`「実現方式の成立」の各項目を満たし、確かめた結論と再取得先を本文と成立性の証拠に書いている。
  観測した事象を契機とする要求では、対策が直接の原因と主要な害に確実に作用する。
  採否と適用範囲を左右する技術的な主張は、`agent-basics:writing-standards/references/investigation.md`「根拠と主張の一致」に従い、入力の生成・変換の手順と観測から保証できる範囲を突き合わせる。
- 原因分析: `agent-basics:bugfix`を起動して起草し原因を確定した通常AWIでは、`## 原因分析`のバグ単位ごとに`agent-basics:review-standards`「文章成果物の実害」の不具合対応の条件を満たす。
  推測だけの原因は確定とせず、調査を続けるか「続行できない理由」として報告する。
  `広がり:`は、ユーザーが指定した対象集合を覆う。
  再発防止を全て見送る記録は、原因となった入力や条文を除く・改める案を比べている（`agent-basics:bugfix/references/prevention.md`「再発防止を採用しない場合」）。
  重い案だけを挙げて見送った記録は、この条件を満たさない。
  記録の形と記録先は`agent-toolkit:atk-mode/references/analysis-records.md`に従う。
- 完成条件: `agent-toolkit:atk-mode/references/awi-body-completion.md`「完成条件」に従い、権限・起動条件・待機の手順を調べて同じセッションで確実に観測できる方法を選ぶ。
  人手・後日・配布後の確認を置く場合は、同節の例外に当たる根拠が本文にあるかを確かめる。
- 具体化の記録: 対象、適用範囲、外部から観測できる結果、除外を新たに具体化した本文は、判定軸と結果を記録の形式で書いている。
  判定軸は`agent-toolkit:atk-mode/playbooks/confirmation.md`「原文からの具体化を要する軸」、記録の形式は`agent-toolkit:atk-mode/playbooks/user-utterance.md`「原文の保持と取り込み」に従う。
- 複数リポジトリ: 代表AWIを参照する原稿は、`agent-toolkit:atk-mode/references/cross-repository-submission.md`「独立したAWIへの分割と依存」で参照先を取得し、共通本文と固有本文を合わせて読む。
  冒頭が単独で読め、自分のリポジトリだけで完成を判定できる。
  依存待ちは必要なものに限り、他のリポジトリに固有の要求はそのリポジトリのAWIへ置く。
- UWIの情報: 各情報が`agent-toolkit:atk-mode/references/uwi-format.md`「載せる情報」の基準で必要十分であり、重複しない。

## 投入と取得

作成主体と検収の構成は`agent-toolkit:atk-mode/playbooks/wi-drafting.md`「直接起草と独立した検収の選択」で決める。
別の分析や実装の途中で通常AWIを投入・更新する主体は、`agent-toolkit:atk-mode/roles/add-wi.parent.md`の付随型で独立した担当へ調査と起草を委譲し、親が意味を検収した後に同じ担当が保存する。
ユーザー固有の値は、ユーザーへ確認できるメインへ戻す。

### 投入

1. 保存してよい状態かを確かめる。
   付随型の投入担当は、同じスレッドで`保存開始`と検収済みの要求の識別子を受け取った対象だけを保存する。
   専念型と直接起草するメインは、決めた検収を終えた対象を保存する。
   保存の直前に原稿の現行本文を確かめ、`agent-toolkit:atk-mode/playbooks/wi-drafting.md`「重複判定」が再照会を求める場合はその結果も確かめる。
   検収の後に原稿が変わっていたら、保存せずに修正と検収へ戻る。
   例外は、検収済みの参照関係に沿って、先行項目の仮の名前を正式なファイル名へ置き換えただけの差分である。
   置換後の本文と依存の指定が先行項目の保存結果と完全に一致すれば、同じ検収結果として保存へ進む。
   参照先、依存の必要性、要求の内容のどれかに変更が及ぶ差分は、修正と検収へ戻す。
   ユーザー固有の判断の回答を待っている要求は、保存を指示されても回答まで保存を保留する。
2. 保存する。
   - 新規は`atk wi add --body-file <原稿の絶対パス>`で保存する。
   - `ファイル名`を受け取った要求は新規に保存せず、`atk wi show`で取得した現行本文を改訂して`atk wi edit <ファイル名> --body-file <原稿の絶対パス>`で保存する。
   - 投入は要求ごとに1回だけ行う。
   - 同じリポジトリで同時に確定した複数のAWIは、`--body-file`を反復して1回の`atk wi add`にまとめる（努力目標。commitとpushを1回にするため）。
   - 依存があれば先行項目から保存し、確定した正式なファイル名を後続の`depends_on`と本文へ書く。
     書く依存は、`agent-toolkit:atk-mode/references/states-and-dependencies.md`「依存と冷却時間」の先行成果の必要性の基準で確定したものに限る。
   - 検収済みの原稿との一致を保存の時にも確かめる場合は、dry-runと同じ`--compare-opening-file`を本保存へ付ける（不一致なら保存は止まる）。
   - 系統（継続して作業が入るoriginのブランチ）を登録したリポジトリ宛てでは、要求の内容から宛先の系統を決めて`--target-branch <系統>`で指定する。
     系統を登録したリポジトリは、`atk config repos list`で`branches`を持つ登録である。
     処理中の投入では、処理中の系統を内容から決まる系統とする。
     両方の系統に必要な修正は、取り込み元の系統（`merge_from`の値の側。保守ブランチなど）を宛先にした1件のAWIとし、取り込む側の系統へはリリース前のマージで反映する。
     内容から系統が決まらない場合、手動の投入（`/atk-mode add-awi-by-user`）ではユーザーへ確認し、振り返りなどの自動の投入では`default_branch`の系統とする。
   - WIの追跡先と本文形式を同じ工程で指定しているリポジトリでは、その規範が定める形式と時機で書き、登録の後に完了を確かめる。
   - 変更対象が現在の作業と重なる場合は、別のworktreeで並行して着手できる利点と着手の遅れを比べて投入の時機を選ぶ（努力目標）。
3. 警告かエラーが出たら、同じ手順で原因を除いて再投入する。
   自力で直せない要求は「続行できない理由」を報告し、他の要求の投入を続ける。

### 保存結果の検収

1. `atk wi add`・`atk wi edit`による本文一致の確認、private-notesのcommitとpush、終了コードを確かめる。
   終了コード0なら、本文の再取得は省く。
   意味と構造、保存操作の警告とエラーを確かめ、表現だけの推敲と表記の診断は対象外とする。
   未プッシュの通知がある場合だけ`atk wi commit`で反映する。
2. 既存項目の更新で保留（`hold`）にした項目を解除（`unhold`）する。
   専念型では作成主体が保存本文の一致を確かめた後に、付随型では委譲元が保存結果を検収した後に解除する。
   ユーザーの回答待ちと、起草か保存の失敗が続く間は保留を保つ。
   既存項目を変えずに再利用する場合と取り下げる場合も、必要な更新と採否を確定し、未解決の懸念が残る間は保留を保つ。
   誤って投入した項目の削除と保留からの終了は、`agent-toolkit:atk-mode/references/states-and-dependencies.md`「保留と本文修正」「終了」に従う。

### 取得

1. ユーザーからキュー項目の外部更新（UWIへの回答、AWIの投入、ユーザーコメント、本文の編集、状態遷移など）を知らされたら、`atk wi show <ファイル名> --pull`で本文を強制同期して取得する。
   項目を特定するための一覧も`atk wi list --pull`で取得する。
   同期しても項目が無いか更新前のままなら、送信側の保存が終わっていない可能性がある。
   次の確認時点を`agent-toolkit:atk-mode/playbooks/delegation-waiting.md`「外部サービスの状態変化を待つ場合」で決める。
   その時点で、特定済みなら`atk wi show <ファイル名> --pull`を、未特定なら同じ抽出条件の`atk wi list --pull`をやり直す。
   同期の成功と更新内容の到達は分けて扱う。
   通常の軽い参照、投入前の回答済みUWIとの整合確認、同期済みの候補の定期的な参照は、強制同期の対象から外す。
   private-notesへの直接のGit操作は`agent-toolkit:atk-mode/references/states-and-dependencies.md`「対象リポジトリとprivate-notesの区別」に従う。
2. 投入済みのWIへ着手する前にも、本書「保存前の確認」の手順2で回答済みUWIとの整合を確かめる。
3. 投入済みの項目は、目的、変更内容、原因分析、固有の補足条件から完成状態を導き、旧形式の節も読んで対象を確定する。
   任意の節や引用位置の記録を省いた本文も、書かれた内容から着手できる。
4. キューの原文の変化への追加の対策（原文のスナップショット、ハッシュや差分の比較、変化時の処理の遮断やUWI化）は設けず、既存の取得機能を使う。
   検収のための一時的な原本の保存は、この対策と別に扱い、使ってよい。

## 返す報告に含める項目

要求ごとの結果（保存した正式なファイル名、再利用、続行できない理由）、保存の終了コードと警告、保留の解除、未確認の範囲とその扱いを返す。

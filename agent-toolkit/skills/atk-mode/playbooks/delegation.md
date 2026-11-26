# 委譲の起動

委譲の操作の最初の手順書であり、委譲するかの判断から`agents_server`の`start`による起動までを扱う。
方針: 委譲は起動、依頼文、読み込み、検収、統合の費用を伴うため、独立した評価、並行、モデルの使い分けの利点がそれを上回る時に使う（原則2、`agent-basics:writing-standards/references/quality-principles-qcd.md`）。
認可、全体の調整、検収、成果の所有、終了の観測は委譲元が担う（原則7、`agent-basics:writing-standards/references/quality-principles-ownership.md`）。

`agents_server`とホスト組み込みのどちらでも、起動する側を委譲元、起動される側を委譲先とする。
起動と終了の観測の手段が無い環境では、分担案と依頼文の下書きを返し、計画上の分担と実際に起動した担当を区別して書く。

## 条件と行動

表で条件に当たる資料を全文読み、該当する手順を適用する。
初回起動は「要否判定」から「送信」までを順に行う。
起動済みの担当の待機・受領・継続だけを行う時は、表が示す手順へ直接進む。

| 条件 | 行動 |
| --- | --- |
| 委譲するか、分担と単位、並行の形を決める（計画の起草前、独立した複数の単位か長い工程の着手前、確定した構成の変更前） | 本書「要否判定」から |
| `start`（`task`・`delegate`・`write`）、`Agent`ツール、Codexの組み込み委譲で起動する | 本書「送信」 |
| 稼働中の委譲先へ追送する、依頼文か引き継ぎ記録先の行を書く | `agent-toolkit:atk-mode/references/delegation-prompt.md` |
| `explore`・`shell`を起動する | 本書「軽量の起動」 |
| 役割文書を持つ委譲を起動する | 呼び出し元の起動契約（`agent-toolkit:atk-mode/roles/<役割名>.parent.md`か呼び出し元の手順書の節）と、本書「`<役割名>.parent.md`を持つ委譲の起動」 |
| 後続・並列の書き込み担当のworktreeと書き込み所有権を割り当てる、直接連絡できない別セッションと共有資源の作業を申し送る | 委譲内の書き込みは本書「作業ツリーの保護」、別セッションへの申し送りは `agent-toolkit:atk-mode/references/shared-resource-handoff.md` |
| 実機の確認、対照実験、識別子の観測だけのために起動する | 本書「実機の観測だけを目的とする起動」 |
| 委譲先のモデルとeffortを選ぶ、成果が受入れ基準に届かずモデルかeffortを変える | `agent-toolkit:atk-mode/references/model-settings.md` |
| 完了通知を処理する、待機区間を組む、待機と停滞を判定する、待機を開始できないか`atk agents wait`が異常の終了コードで終わった | `agent-toolkit:atk-mode/playbooks/delegation-waiting.md` |
| 委譲先の完了報告を受領して検収する、`気付いた改善点:`を中継する | `agent-toolkit:atk-mode/playbooks/delegation-receiving.md` |
| 既存の担当を継続・交代・再起動する、可用性の障害で代替起動する、`agents_server`のMCPサーバーが再起動した、委譲済みの要求について自分で本文を読む・調べる・回答する | `agent-toolkit:atk-mode/playbooks/delegation-recovery.md` |
| Claude Codeで`Agent`ツールかバックグラウンドタスクを起動する、定期の再確認を設定する、Code Modeでツールを探す | `agent-toolkit:atk-mode/playbooks/host-claude-code.md` |
| Codexで委譲、待機、状態確認、中断に着手する | `agent-toolkit:atk-mode/playbooks/host-codex.md` |
| ホストごとの違い（Claude Code、Codex、Antigravity、OpenCode）を比べる | `agent-toolkit:atk-mode/references/hosts.md` |
| 委譲先から`助言依頼:`で始まる通知を受けた、委譲先として助言を求める | `agent-toolkit:atk-mode/playbooks/advice-request.md` |
| 事象単位の調査を委譲する、コンテキスト消費が大きい調査を切り出す | `agent-toolkit:atk-mode/playbooks/investigation-delegation.md` |
| エージェント向け文書の置き場所を選ぶ、サブエージェント定義か委譲が関わる手順を書く | `agent-toolkit:atk-mode/references/agent-documents.md` |
| 引き継ぎ記録を書く・読む、会話の圧縮の後に委譲の状態を復元する | `agent-toolkit:atk-mode/references/work-contract.md` |
| 実装の工程を委譲する時の受領の決まりを確定する | 作業`process-wi`では`agent-toolkit:atk-mode/playbooks/lane-receipt.md`、それ以外は`agent-toolkit:atk-mode/playbooks/write-plan.md` |
| レビュー指摘への応答と修正を委譲する、採否を確定する | `agent-toolkit:atk-mode/playbooks/review-fix.md` |
| 結果本文の保存期間と回収を決める | `agent-toolkit:atk-mode/playbooks/managed-temp.md` |
| 自分が委譲先として起動された | `agent-toolkit:atk-mode/playbooks/subagent.md` |

## 要否判定

1. 計画、設計、原因分析、採否、結論の統合は、全体の文脈を持つ担当が所有し、委譲するのはそこから切り出せる作業に限る。
2. 費用を数える。
   冒頭の費用と利点に、再試行の負担を含めて比べる。
   委譲元の文脈の温存は、会話の圧縮と記録からの復元で代わるため、委譲の理由に数えない。
   役割文書か手順が委譲を指定している場合も、その目的が今回の入力で成り立つかを、直接の処理や既存の成果の利用と比べる。
   独立した文脈のレビュー、役割の契約、書き込み所有権は、費用によらず保つ。
3. 定型の処理は、モデルを起動する前に、既存のCLI、API、標準コマンドで実行する案と比べる（`agent-basics:writing-standards/references/design-heuristics.md`「案の採否と実施順の判定」）。
4. 判断を持つ仕事と、内容と完了の判定が確定した作業（読み取り、走査、編集、コマンド実行）を分ける。
   確定した作業は`chores`・`chores_fast`の工程へ渡す候補にする（工程は`agent-toolkit:atk-mode/references/model-settings.md`「工程別モデル設定」）。
   書き込み先を分けただけでは、変更の内容と完了の判定は確定しない。
5. 既存の担当が詳しい文脈を持つ調査と判断は、新しく本文を読む前にその担当へ戻す（`agent-toolkit:atk-mode/playbooks/delegation-recovery.md`「継続と新規起動」）。
   調査で対象の文脈を持った担当は、書き込み権限があれば修正まで自分で行ってよい。

## 委譲単位の確定

似た対象は同じ担当が知見を再利用し、広い所在探索は`explore`へ分けられる。
単独で順に行う案と独立した群で分担する案を総費用で比べる。
文書群の読み取りでは、1回の応答上限と全体量を別に見る。

1. 起動の前に、委譲する変更、完了を確かめる成果物と成功の証拠、委譲元に残す判断と検証を対応付ける。
   対応付けは既存の依頼、計画、検証記録を使い、新しい表は作らない。
2. 要件の具体化、構造の選定、原因分析、検証範囲の判断が残る書き込みの単位は、判断を所有する担当が処理する。
   役割文書を渡す起動でも、切り出すのは確定した作業だけにし、元の担当が役割全体の責務を持ち続ける。
3. 実行時機・作業ディレクトリ・権限・原本・完了判定が共通で、書き込み所有権が競合しない確定作業は、1回の起動にまとめる候補とする。
4. 先行の出力を後続の入力に使う一連の処理は、1件の委譲にまとめて順を示す。
   判断の工程と、その結果を入力にする確定作業は、この理由でまとめない。
5. 同じ母集団を複数の担当へ分ける時は、全要素と担当の対応を作り、未割当と重複が無いことを集合の演算で確かめる。
   同じ対象を独立した観点で意図して重ねる場合は除く。
6. 実行の順を決める時は、先行の出力を使うデータの依存と、共有資源・同じファイル・認可による順序の制約を分ける。
   依存も競合も無い読み取りと作業は並行できる。
7. 確定した構成（担当の数、範囲、起動の順）は、依存か書き込み所有権が変わって成り立たなくなった時だけ変える（努力目標。途中の停止と再起動で進行中の成果が失われるため）。
   変える時は、稼働中の担当を止める前に同じ基準で構成を計算し直し、残す成果を確定してから移る。

### 並行と比較の進め方

次の進め方は、条件に当たる時だけ使う。

- 同じ課題を複数の候補に解かせる時は、候補には課題だけを渡し、採点の基準は判定する側だけが持つ。
- 並行の委譲を起動する前に、分担して全てを集めるのか、競わせるのかを決める。
  競わせる時は終了の条件（最初に合格した1件、全件の順位付け、複数回の最良）も起動の前に決める。
- 構造を変える危険の大きい設計では、利用側の書き方を先に書き、構造の違う設計を2つ以上作って比べる。
- 同種の委譲を多数起動する前に、最初の1単位を起動から検収まで通してから広げる。
  失敗例: 同じ形の依頼を10件同時に起動し、8件が同じ返し方の誤りを起こし、依頼文の1か所の欠陥が8件の手戻りになった。

### 作業ツリーの保護

書き込みの担当と主体の交代を、差分の消失と競合から守る。

- 同じworktreeで複数の書き込み担当を並列に動かす時は、編集の対象と、各操作の出力先（生成物、lockfile、スナップショット）が重ならない集合へ分けて割り当てる。
- 生成物とその原本、宣言とlockfileのように、稼働中のCLI、hook、MCP、テストが同時に読み、実行時に整合を保つ一体の組は、組ごと同じ書き込み担当へ割り当てる。
- worktree全体か共有状態に作用する操作は、そこを使う全ての担当の終了を確かめてから、呼び出し元が直列に行う。
  該当するのは、commit、Git hook、indexか履歴を変えるGit操作、ファイルを書き換える整形とlint（`pyfltr run`など）、生成物の同期、依存の更新、環境の構築である。
  依存の宣言、lockfileの更新、環境の準備は一連の工程として、並行の担当の起動前か全員の終了後に行い、並行の担当は途中で必要な依存の変更を委譲元へ返す。
- 担当の稼働中に呼び出し元が同じworktreeを編集してよいのは、担当がファイルの集合だけを持ちcommit・整形・同期をしない分担で、割り当てた集合と一体の組に重ならないファイルに限る。
- 同じworktreeの書き込みの主体を替える時は、後続の担当の起動前にも委譲元が自分で書く前にも、稼働中の担当の終了を確かめる。
  稼働中なら作業をその担当へ戻す。
  別のworktreeの主体どうしは、対象のファイルが重なっても並行してよく、競合は統合の時に解く。
- 終了を確かめて書き込み所有権を引き取った者は、引き取った事実と行った操作を記録し、そのworktreeで次に起動する担当へ伝える。
  伝えないと、次の担当は自分が出していない履歴の変化を異常として止まる。
- 委譲先の作業ディレクトリと上位のディレクトリ（worktree、個別に作った一時領域）の削除は、委譲先の終了を確かめてから委譲元が行う。
  セッション全体のmanaged-tempは自動の回収に任せる。
- レーンの計画と要求は同じ専用worktreeで扱い、異なるレーンは別のworktreeへ割り当てる。
  レーン内の分担と内部の群（別branch・別worktree）は`agent-toolkit:atk-mode/playbooks/implementation.md`「レーン内の分担」に従う。
- レーン担当の起動前に、上流への追随を終え、staged、unstaged、追跡外で無視されない変更が空であることを確かめる。
  レビュー修正担当は、レーン担当の終了を確かめてから、引き継ぎ記録の修正内容と未commitの差分が一致することを確かめて渡す。
  実行レビュー担当は、実装担当の終了後に、同じ最終HEADへ1ラウンドに1回だけ割り当てる。
- 複製の作業ツリーで起動する委譲先には、着手前に`git fetch`で分岐元との差分を両方向に確かめ、分岐元が進んでいれば先に追随させる（努力目標）。
- worktreeの外にある認証情報のファイルは、元の場所に置いたまま扱う（`agent-toolkit:atk-mode/playbooks/subagent.md`「作業ツリーと認証情報の扱い」）。

## 実行手段

`start`のうち、`mode`が`delegate`の起動と、`mode`を宣言しないか`delegate`を宣言した役割文書の`task`を通常起動と呼ぶ。
`mode`が`explore`・`shell`・`write`の起動と、それらを宣言した役割文書の`task`を軽量起動と呼ぶ（`agent_toolkit/_agents_server/launch_prompts.py`の`LIGHTWEIGHT_LAUNCH_KINDS`と一致させる）。
軽量起動にはプロジェクトの規範が自動で届かないため、作業に必要な指示は依頼文で渡す。
`handoff_from`で前任から後任を起こす引き継ぎ起動は、前任の起動の区分を継ぐ（`agent-toolkit:atk-mode/playbooks/delegation-recovery.md`）。

1. 委譲は`agents_server`を標準とし、`atk agents wait`で終了を観測する。
2. `mode`は作業の形で選ぶ。
   - 役割文書がある作業は`task`で渡し、手順、権限、検証、返却の形式は役割文書に任せる。
   - 対話・要求の組み直し、複数対象の網羅や長い記録の分類、成果ファイルの作成や全公開操作と呼び出し元の対応表の検収を伴う調査は`delegate`にする。
     自由本文と全体作業契約を渡せば`mode`は省ける。
   - 割り当て済みの範囲の実装・修正は `start(mode=delegate, model_type=execute)` で渡せる。
     委譲元が作業範囲、書き込み所有権、完成判定を定め、通常の作業契約を使って検収する。
     レーンと役割の契約が必要な場合は所定の `exec` を使う。
   - 単一対象の結論と所在を返す調査は`explore`、終了状態と要約で成り立つコマンド実行は`shell`にする。
   - 画像・画面の品質レビューにも担当に応じて `review` または `review_fast` を使い、候補列を独自に組まない。
3. 次の場合に限り、Claude Codeの`Agent`ツールとCodexの`spawn_agent`を使う。
   - `agents_server`のMCPツールを呼べない場合。
     切り替えた工程、本来の設定値（engine、モデル、effort）、理由を報告に書く。
   - `agents_server`の各modeに同等の手段が無い専用のagent定義（プロジェクト固有の定義など）を使う場合。
     同等かは各modeが公開する機能で、工程の候補となる実行エンジンごとに判定し、対応する`mode`で起動して同等の結果を得られなかった場合も含める。
     組み込みの汎用定義の起動はPreToolUseのhookが遮断するので、通知に従う。
     Claude Codeの組み込みの`web-fetch`はhookが遮断しない。
     直接の`WebFetch`を持たない会話でWebページを読む標準の手段のため、取得にはそのまま使う。
   - ユーザーが明示した場合。
   - 本書「会話を引き継ぐ委譲」でforkを選ぶ場合。
   組み込みの委譲の依頼文には、`/atk-mode subagent <役割名>`の起動か、`agent-toolkit:atk-mode/playbooks/subagent.md`と役割文書の絶対パスを書く。
4. 役割文書を指定する委譲は、委譲元の会話を引き継がない形で起動する。
   Codexで`agents_server`を使えず組み込みの委譲で代える時は、`spawn_agent`に`fork_turns: "none"`を明示する。
5. 起動時の可用性の障害による候補の切り替えはサーバーに任せる。
   実行中の担当は、経過時間や無応答の長さだけを理由に置き換えず、終了まで観測を続ける。
6. 通常の委譲が使えず認可の範囲でCodex CLIを直接使う場合は、[非対話の直接起動](delegation-start-recovery.md#codex-cliの非対話の直接起動)に従う。

### 会話を引き継ぐ委譲

[会話を引き継ぐ起動](delegation-start-recovery.md#会話を引き継ぐ起動)の適用条件を確かめる。

## 送信

起動の準備から`start`の発行までを上から実行する。

1. 対象、読み取り・書き込みの権限、完了の条件、返却で受け取る観測値を確定する。
   委譲する方針に未回答の確認か未反映の指摘が残っていれば、先に解く。
2. 自由本文の`delegate`を起動する前に、セッションのmanaged-temp直下に現行の`work-contract.md`が無ければ、`agent-toolkit:atk-mode/references/work-contract.md`「作成と所有」に従い、目的・完遂事項・守る制約を先に記録する。
   委譲先も作業が長引いた時や圧縮の後に目的を見失わないためである。
   その絶対パスを`start`の`whole_contract_path`へ渡す。
   役割の`task`では、起動契約の入力の区間（`<!-- input-contract:... -->`）が案内する準備に従う。
   軽量起動にはこの準備は不要である。
3. 依頼文が指す入力を確かめる。
   - パスは全て絶対パスで書き、既存の入力ファイルと、委譲先が自分で解決するplugin配下の資源が実在することを確かめる。
   - 読ませるファイルには行数か取得範囲を添える（努力目標。初回の読み込みが出力の上限で途中までになると取り直しの往復が起きるため）。
   - コマンドの出力を読ませる時は、全量をmanaged-tempへ保存して絶対パスで渡す。
   - 比較の基準、PATH上のコマンド、plugin配下の資源の版が、委譲元の前提と一致することを確かめる。
   - 起動から使用までに別の主体が変えうる値（統合先の先端OID、rebaseの起点など）は、取得の手段と時点を添え、使う直前の取り直しを指示する。
   - フォアグラウンドで孫の終了を見る委譲先には、孫の起動手段と、その終了を見る公開の待機機能（`atk agents wait`、Codexの`wait_agent`）を組で渡す。
   役割の起動では、必須入力と資源の実在と版をサーバーが確かめるので、この確認と行数の添付を省いてよい。
   不足か不一致を解けない時は起動を見送り、不足の項目、無かったパス、不一致の対象と観測値を報告する。
4. 本書「実行手段」で手段と`mode`を、[工程別モデル設定](../references/model-settings.md#工程別モデル設定)で工程を決め、役割起動では`model_type`を省く。
   ユーザーが上書きや`write_model`を指定した場合だけ、[本人指定の手順](../references/model-settings.md#本人指定による上書き)で原文・回答を確かめる。
5. 書き込みを伴う時は、本書「作業ツリーの保護」で書き込み先と主体を確定する。
6. 依頼文を`agent-toolkit:atk-mode/references/delegation-prompt.md`「委譲プロンプトの構成」で組み、入れる要素を同書「入力の適格性と追送」で選ぶ。
   役割文書を持つ委譲は本書「`<役割名>.parent.md`を持つ委譲の起動」に従う。
7. 引数、省略時の値、応答の項目を、起動の直前にMCPツールのスキーマで確かめ、`start`を1回発行する。
   Codexで新規起動する場合は、ここで決めた引数を`agent-toolkit:atk-mode/playbooks/host-codex.md`「agents_serverの起動」の非同期受付へ渡す。
   返った`session_id`と`handoff_record_path`を保持し、待機と回収は`agent-toolkit:atk-mode/playbooks/delegation-waiting.md`へ進む。

起動手段にかかわらず、適用する暫定規範を`agent-toolkit:atk-mode/references/delegation-prompt.md`「入力の適格性と追送」に従って届ける。
通常起動は既存の全体作業契約と更新時の再読通知を使う。
軽量起動（同じmodeの役割を含む）やホスト組み込みなど、全体作業契約が自動で届かない委譲は、該当する項目だけを起動依頼に入れる。
役割の起動では既存の依頼用入力に添え、未宣言の入力名を追加しない。
稼働中の担当に関係する追加・更新・解除は、既存の再読通知か、原本と変わった項目が分かる追送で届ける。

### `<役割名>.parent.md`を持つ委譲の起動

呼び出し元の起動契約（共有の`<役割名>.parent.md`か、呼び出し元の手順書へ統合された契約）で手順が決まった委譲は、次の手順で起動する。

1. 起動契約を読み、前提の条件、入力値の決め方、受領と検収の要件に従う。
   入力と担当の手順書の扱いは、起動契約の入力一覧にある注記に従う。
2. `start`へ、起動契約が定める絶対パスを`cwd`、役割名（例: `add-wi`）を`subagent_md_path`、名前付きの入力を`extra_params`として渡す。
   `mode`は指定せず、`model_type`は起動契約に値の決め方がある時だけ指定する。
   全体作業契約は、起動契約の入力の区間が案内する時だけ`whole_contract_path`へ渡す。
3. `extra_params`の値へ、`agent-toolkit:atk-mode/references/delegation-prompt.md`「委譲プロンプトの構成」の値の制限、`未検証`の明記、`forwarded-user-input`の標識、絶対パスを適用する。
   入力名は起動契約の名前のまま使い、宣言の無い入力名は加えない。
4. 確定した引数で`start`を1回呼ぶ。
   Codexなら本書「送信」と同じく非同期受付へ渡す。
   拒否された時は、診断が示す項目と指示に従って再発行する。
5. 返った`session_id`を`atk agents wait`で観測する。
   Claude Codeでは、初回の`start`の処理の中でagent-toolkitのmodが定期の再確認を設定し、その結果が`start`の返却の後に通知される。

### 軽量の起動

自己完結した1回の入力と返却で終わる作業に`explore`・`shell`・`write`を使う。

1. 全体の点検の分担は`agent-toolkit:atk-mode/playbooks/investigation-delegation.md`「コンテキスト消費が大きい調査の切り出し」で決める。
2. 入力は命令1文と必要な値（元の要求、対象、完了の条件、返却の形式、基準の記録）に限り、軽量起動に届かないプロジェクト固有の指示と出力の保持は依頼文で渡す。
   依頼文は`agent-toolkit:atk-mode/references/delegation-prompt.md`「委譲プロンプトの構成」で組む。
3. 各modeの入力は、公開の`start`の説明とスキーマで確かめ、`cwd`へ作業ディレクトリの絶対パスを渡す。
   `explore`と`write`は`prompt`を、`shell`は`command`と`summary_policy`を使い、スキルの手順を要する作業は`task`か`delegate`へ回す。
   shellの要約に関わる暫定規範は`summary_policy`に入れ、実行に関わる条件は適合するコマンドを渡した上で同じ欄に明示する。
   工程が `write` になる起動は、[本人指定による上書き](../references/model-settings.md#本人指定による上書き)に従い、原文・回答の所在を渡す。
4. `explore`は読み取り専用とし、後続の入力には`atk agents wait`が保存した`agent_message_path`を直接指定する。
5. `shell`と`write`の書き込み先を、生成物も含めて他の担当と重ならない範囲に限り、同じ担当の交代は先行の終了後に行う。
6. `session_id`を保持し、`agent-toolkit:atk-mode/playbooks/delegation-waiting.md`で終了状態と結果本文を回収する。
7. 終了後に差分、実行結果、診断を確かめ、返却の形式と、対象・権限・完了の条件との整合を検収する（`agent-toolkit:atk-mode/playbooks/delegation-receiving.md`）。
   不足は同じ担当へ差し戻し、結果本文は再保存せず、後続が参照する`agent_message_path`は参照を終えるまで残す。

## 実機の観測だけを目的とする起動

応答の内容を使わず、実機の確認、対照実験、識別子の観測だけのためにエージェントCLIか`agents_server`の委譲を起動する時の指定である。
観測にも作業に合う工程設定を使い、観測のために候補やeffortを変更しない。
共有状態の隔離は`agent-basics:writing-standards/references/investigation.md`「エージェントCLIを検証で起動する時の隔離」に従う。

- `agents_server`: `task`は役割の工程を使い、それ以外では該当する工程名を渡す。
  検証の対象が`delegate`か`task`の起動そのものでない限り、`mode`は`explore`か`shell`にする。
- Claude CodeのCLI: 工程設定のClaude候補に合わせて`--model`と`--effort`を指定し、`--no-session-persistence --setting-sources ""`を付ける。
  加えて`--tools "" --strict-mcp-config --disable-slash-commands --system-prompt <文>`を指定する。
  プロンプトは`--`の後ろに置く（`--tools ""`が後続の位置引数を値として取るため）。
  `--bare`はOAuthの認証を読まないので使わない。
- CodexのCLI: 工程設定のCodex候補に合わせてモデルとeffortを指定し、`--ignore-user-config --ignore-rules`を付ける。
  記録が不要なら`--ephemeral`も加える。
  `codex exec`はChatGPTの認証で系列名`luna`を拒否するため、`luna`系列の最新の完全なIDを指定する。

## 再委譲する場合に守る事項

委譲を受けた担当がさらに委譲する時は、本書が委譲元に定める規定を自分に適用する。

- 上位から受けた取り決め（作業場所、書き込みの主体、出力の言語、プロセスの所有、取り消せない操作の権限と許可の範囲）を、同じ意味で下位へ継がせる。
- 下位の担当の完了本文は自分で受け取り、自分の完了報告に含める。
- 委譲先の役割を自分で巻き取る時は、その役割の`<役割名>.subagent.md`が定める義務（判断の責任の主体、加工せずに伝える対象、収束の条件）を自分に適用する。

## 返す報告に含める項目

起動した担当ごとの`session_id`か識別子・`mode`・モデルと、`agents_server`以外を使った時の理由、見送った起動とその不足の項目、分担と並行の形を決めた根拠を返す。

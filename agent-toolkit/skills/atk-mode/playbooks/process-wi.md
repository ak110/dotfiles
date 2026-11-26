# AWIの処理

目的: エージェントがユーザーの登録したAWIを選定から公開まで自律で処理し、ユーザーが判断の要る事項だけに関わって成果を受け取れるようにする。
`/atk-mode process-wi [single-lane|all-repos] [追加指示]`で始める、対象リポジトリのAWIを選定から計画、実装、レビュー、統合、公開まで処理する作業の入口である。
この作業は、ユーザーの入力か`atk run-skill`・`atk wi process-loop`の起動入力が名指しした時だけ選び、実行中は自律モードで動く。
登録リポジトリを継続して処理する時は、`agent-toolkit:atk-mode/playbooks/process-loop.md` から対象ごとの本手順へ進む。
モードを選び、「条件と手順書」「不変条件」「メインが読む対象の境界」を確認する。
その後は選んだモードと実際に進む工程の資料を読む。
採否と公開を終え、「終了」に従って報告するか委譲元へ返す。

## モードの選択

引数の最初の語を確かめ、共通の工程を始める前にモードを1つ選ぶ。
先頭の語が完全一致する時だけモードとして消費し、残りを追加指示として保つ（先頭の空白と語の区切りには空白・タブ・改行を使える）。
一致しない場合と引数なしは複数レーンの処理とし、引数全体を追加指示として扱う。
複数レーンを扱う時は「実行順」に従う。
all-reposではリポジトリごとにモードを決めた時点で、必要な実行順へ進む。

| 最初の語 | 処理 | 手順書 |
| --- | --- | --- |
| `single-lane` | メインが主作業ツリーで1つの流れとして処理する | `agent-toolkit:atk-mode/playbooks/process-wi-single-lane.md` |
| `all-repos` | 登録全体を統括し、対象リポジトリごとに処理する | `agent-toolkit:atk-mode/playbooks/process-wi-all-repos.md` |
| それ以外か引数なし | 選定したAWIをレーンへ分けて処理する | 本書「実行順」 |

どのモードでも、選定を担当するメインは実行開始時刻とタイムゾーンを既存の作業記録へ残し、all-reposでは統括の巡回開始時刻を各リポジトリの担当へ引き継ぐ。
キュー、対象リポジトリ、Gitと`atk`のCLI、担当の起動と終了結果の受領がそろわない環境では、提示されたWIから要求、依存関係、確認事項を整理した下書きを`agent-basics:writing-standards`に従って返す。
キューの状態の更新、実装、検証、統合、公開は、必要な機能がそろったClaude CodeかCodexで行う。

## 条件と手順書

| 条件 | 手順書 |
| --- | --- |
| WIの選定を始める時、明示された目標から実行する時 | `agent-toolkit:atk-mode/playbooks/process-wi-selection.md` |
| レーン分け、所要時間の配分、内部担当の同時数と巨大WIを判定する時 | `agent-toolkit:atk-mode/playbooks/process-wi-selection-allocation.md` |
| 選定YAMLを生成する時 | `agent-toolkit:atk-mode/references/process-wi-selection-format.md` |
| 生成済みの選定YAMLや再開記録を読む時 | `agent-toolkit:atk-mode/references/process-wi-selection-format.md`「欄の意味」 |
| 所要時間の見込み、初回配分の算定根拠、予測補正を扱う時 | `agent-toolkit:atk-mode/references/process-wi-selection-estimates.md` |
| 含めた理由、除外した理由、選定の構成を尋ねられた時 | `agent-toolkit:atk-mode/playbooks/process-wi-selection-explanation.md` |
| 対象がGitHub上にあり、選定の前に監査の要否を決める時 | `agent-toolkit:atk-mode/playbooks/process-wi-review-audit.md` |
| レーンの専用worktreeを作成・再利用・回収する時、後段レーンの先行調査をする時 | `agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md` |
| レーンを起動し、統合を許可・受領し、失敗から再開し、完了させる時 | `agent-toolkit:atk-mode/playbooks/process-wi-lanes.md` |
| 検証対象を選ぶ時（単一レーン・複数レーン共通） | 本書「検証環境」に従う |
| 担当として計画、実装、実行レビュー、統合準備を進める時 | `agent-toolkit:atk-mode/playbooks/process-wi-execution.md` |
| 統合の引数、manifest、計画の最終化、AWIの終了を扱う時 | `agent-toolkit:atk-mode/references/lane-integration-format.md` |
| 不良・是正要求・改善を観測か受領した時、ユーザーが時機として「即時対応」と指示した時、30分ごとの点検と公開工程の開始前 | `agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md` |
| 処理対象WIを加える時、必須是正レーンを足す時、同一主題の追加指示を受けた時、上流へ投入する時 | `agent-toolkit:atk-mode/playbooks/process-wi-additions.md` |
| 修正commitが必要なCI失敗か全体検証の失敗を扱う時 | `agent-toolkit:atk-mode/playbooks/process-wi-ci-fix.md` |
| 公開工程を始める時 | `agent-toolkit:atk-mode/playbooks/process-wi-publishing.md` |
| frontmatterに`merge_branch`を持つマージ用AWIを処理する時 | `agent-toolkit:atk-mode/playbooks/implement.md`「マージ用AWIの処理」 |
| 延期adopt、反映後の観測、報告前の保存状態の確認をする時 | `agent-toolkit:atk-mode/playbooks/process-wi-observation.md` |
| 作業契約を作成・更新する時、工程境界で要求と成果を確かめる時 | `agent-toolkit:atk-mode/references/work-contract.md` |
| WIから受入までの工程の責務と受け渡しを確かめる時 | `agent-toolkit:atk-mode/references/workflow-phases.md` |
| ユーザーが明示的に一時停止を求めた時 | 本書「一時停止」 |

## 用語

- **メイン**: メインエージェント。
- **選定工程、レーン工程、公開工程**: この作業の3つの主な工程。
  公開工程の定義は`agent-toolkit:atk-mode/playbooks/publish.md`の冒頭にあり、この作業では公開担当が担う。
- **処理対象WI**: メインが選定で確定した、1回の実行で処理するAWIの集合。
  ユーザーの明示の追加、明示された目標から具体化した必要作業、即時対応で今回追加と判定した正式改善AWIも含む。
- **レーン**: 処理対象を割り当てる作業の単位で、1つの専用branchと専用worktreeを持つ。
- **レーン担当**: 各レーンの計画、実装、レビュー指摘の修正、履歴の統合、主作業ツリーへの統合を同じスレッドで担う担当。
  担当の種別にはレーン担当、レビュー修正担当、CI修正担当がある（区分は`agent-toolkit:atk-mode/roles/exec.subagent.md`の操作区分）。
- **主作業ツリー**: マージ先のbranchをcheckoutしている作業ツリー。
  巡回では、統括が対象リポジトリと系統ごとに準備した作業ツリーを指し、統括自身の起動cwdとは区別する。
  この作業ツリーはマージ先のbranchの代わりに、それを追跡する専用branch（`worktree-`で始まる名前）をcheckoutしている。
  `agent-toolkit:atk-mode/playbooks/publish.md`「公開状態の4項目」のうちbranch名の項目は、`# branch.upstream`がマージ先のbranchの追跡refであることで確かめる。
  公開用に別のbranch名が必要な場合もHEADを切り替えずに`git push <remote> HEAD:refs/heads/<公開用branch>`で送り、中間commitの検証などで切り替えた場合は工程の終わりに専用branchへ戻す。
- **公開担当**: 公開工程（push、CI、検証の失敗の修正、プロジェクト固有の公開後の操作、延期adoptなど）を担う委譲先で、委譲の範囲は`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「公開担当の起動と並行工程」にある。
- **即時対応**: 現在の影響と先送り条件に基づいて、直接修正と正式対応の時機を判定して実施すること（`agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md`「即時対応」）。

## 不変条件

- 計画ファイルの作成要否は、`agent-toolkit:atk-mode/playbooks/write-plan.md`「計画ファイルの作成要否」の定義を取得してからAWIごとに本文と現物で判定する。
  計画が必要な項目は実装前に起草し、追加分は確定済みの計画から分ける。
  計画が不要ならWIを実装とレビューの基準にする。
- 計画の事前レビューは置かず、実装の後に各計画を最終の実装HEADで、要件と外部仕様の水準の実行レビューで確かめる。
- 専用worktreeへの書き込みはレーン担当が持ち（メインが`agent-toolkit:atk-mode/playbooks/process-wi-additions.md`「同一主題の追加指示」で引き取った場合を除く）、主作業ツリーへの書き込みは統合の時に限る。
  担当の範囲の一部を委譲する場合は`agent-toolkit:atk-mode/playbooks/delegation.md`「作業ツリーの保護」に従い、計画、検収、commitの対応の記録、統合、メインへの返却はレーン担当が責任を持つ。
- ユーザー由来の要求の全部か一部を不採用にする場合は、先にユーザーの確認かUWIの回答を得てから採否を確定する。
- 選定で確定した処理対象WIを最後まで処理し、ready一覧は途中で取り直さない。
  追加は`agent-toolkit:atk-mode/playbooks/process-wi-additions.md`「処理対象WIの追加」の対象に限り、元の選定結果へ追記してレーンへ渡す。
- 対象リポジトリへpushするのは公開担当だけとする。
  前回の公開の後に同じセッションで新しい成果をベースブランチへ統合した場合は、追加の差分で公開担当を起動し直して最新HEADのpushとCIの通過を検収する（`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「公開状態の検収と再起動」）。
- 計画ファイルとレビュー指摘管理表は、参照する全ての工程を終えてから片付ける。
  レーンの専用branchと専用worktreeは、`agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md`「レーンworktreeの扱い」の時点で、所有登録とGit状態を確かめ直してから回収する。
- メインの作業ディレクトリが実行環境の隔離worktree（Claude Codeの`EnterWorktree`など）の中にある場合は、専用worktreeの作成と回収が対象リポジトリのGit共通ディレクトリを変えるため、隔離を解いてから次へ進む。
  巡回セッションが用意した統合先worktreeは、レーンの隔離解除の対象から外れる。

## 実行順

複数レーンの処理は次の順に進め、各レーンの担当は`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`の契約で計画から統合まで完遂する。
all-reposの統括からリポジトリ担当として候補を受け取った場合は、`agent-toolkit:atk-mode/playbooks/process-wi-all-repos.md`「取得済み候補の受領」の受け渡しの契約を確かめてから、対象worktreeで同じ順に進める。

1. `agent-toolkit:atk-mode/playbooks/process-wi-selection.md`「直前の同期結果の検分」と「処理対象の決定」の開始時のCI点検で、このセッションで処理を完遂できるかを判定する。
   手動起動でも統括からの受領でも、実装に入る前に公開対象とベースブランチのCIの状態を確かめる。
2. 同書「自動コードレビュー監査」で監査の要否を判定して必要な監査を起動し、その終了を待たずにメイン自身が対象を選定して、集合を確定した直後に処理を始める。
   統括から受け取ったリポジトリ担当は初回の候補を使い、選定を別の担当へ委譲することは避ける。
   監査の結果が一部の候補の判断に必要な場合は、その判断だけを待ち、独立した候補の選定と開始できるレーンの起動を進める。
3. `agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「レーンと資源」に従い、最新の選定要約の着手可能なレーンと起動結果を照合する。
   初回に加え、選定への追加や先行成果の統合で着手条件が変わったときも、担当がいないレーンの資源を準備し、担当を起動する。
   frontmatterに`merge_branch`を持つマージ用AWIはレーンを割り当てず、`agent-toolkit:atk-mode/playbooks/implement.md`「マージ用AWIの処理」に従ってメインが直接取り込み、公開工程へ合流する。
   Claude Codeでは、各段階の最初の起動を全て終えてから、同書「各段階の全初期通常レーン起動後の会話圧縮」へ進む。
4. メインは各レーンからのユーザー判断の通知を中継し、レーン間の書き込み競合と依存を調停する。
5. `統合準備完了`を返したレーンには、`agent-toolkit:atk-mode/playbooks/lane-receipt.md`「統合の許可」に従い、選定結果の`先行レーン`が全て統合済みなら`統合許可`を出す。
   各レーンは`atk lane integrate`で統合から計画の最終化、AWIの終了、選定結果への統合済みの記録までを終え、メインは返された`統合完了`を同書「統合の指示と受領」の受領条件で検収する。
6. 処理中に加えた処理対象WIも、`agent-toolkit:atk-mode/playbooks/process-wi-additions.md`「処理中に確定した必須是正レーン」で既存のレーンへ割り当て、同じレーン担当のスレッドで収束させて統合する。
7. 全てのレーンが終わり、起動した監査の処置も確定したら、`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`に従って公開とセッションの終了を完遂する。

## 検証環境

処理担当は検証の対象を選ぶ前に、`repositories`の対象リポジトリの`platforms`を確かめる。
実行中のOSが含まれなければ、ローカルではそのOSで行えるコンパイルなどの検査だけを行い、テストの合否は登録されたOSのCIで判定する。
含まれるOSでは従来どおりローカルで検証する。
OSの指定が無い登録はLinuxとWindowsの両方を対象とし、必要な検証を省く理由に使うことは避ける。

## 系統を登録したリポジトリ

`atk config repos list`で`branches`を持つリポジトリは、継続して作業が入るoriginのブランチ（系統）ごとに処理する。
処理中の系統は、all-reposの統括から受け取った対象では対象要素の`target_branch`、手動起動では起動した作業ツリーが追跡するブランチである。
規範の「ベースブランチ」は処理中の系統のブランチを指し、候補の取得、レーンの作成と統合、push、CIの確認、公開状態の検収はそのブランチを対象にする。
系統ごとの作業ツリーではローカルのブランチ名がリモートのブランチ名と異なるため、pushは`git push --dry-run --porcelain origin HEAD:refs/heads/<系統>`で到達先を確定し、同じrefspecで実行する。
公開状態の4項目のうちブランチ名の項目は、`# branch.upstream`が`origin/<系統>`であることで確かめる。
処理中に投入するUWIとAWIは`atk wi add --target-branch <系統>`で処理中の系統を宛先にし、両方の系統に必要な修正のAWIは`agent-toolkit:atk-mode/playbooks/wi-saving.md`「投入と取得」に従って取り込み元の系統を宛先にする。
GitHub CopilotのレビューとDependabotアラートの監査はリポジトリ単位の作業のため、`default_branch`の系統（系統を省略したWIの宛先）の処理だけが行う。

## メインが読む対象の境界

1. メインは選定の時にAWIとUWIの原文を読み、選定の後は構造化した出力、計画が列挙したWIの本文、工程境界の出力、レビュー指摘管理表の状態、統合結果を主に読む。
   他の節や実装差分は、エスカレーション、ユーザーへの対応、レビューの収束、メイン自身の調査・修正に必要な時に読み、レーン担当とレビュー担当の判断を同じ本文から組み立て直すことは避ける（努力目標。判断の重複を防ぐ）。
   `変更したエージェント向け文書`の取得し直しは`agent-toolkit:atk-mode/roles/exec.parent.md`と`agent-toolkit:atk-mode/playbooks/lane-receipt.md`に従う。
2. 処理対象WIに含めた理由、除外した理由、選定の構成を尋ねられた時は、`agent-toolkit:atk-mode/playbooks/process-wi-selection-explanation.md`に従い、保存済みの選定結果と判断の内容から根拠と適用の限界を説明する。

## 一時停止

ユーザーが明示的に一時停止を求めた時だけ使う手順であり、通常の待機や工程の区切りとは別の状態として扱う。
「続けて」「寝るので進めて」など継続を求める発話では止めない。

1. 安全な区切りで止める。
   実行中の原子的な工程は終えるか取り消し、新しい工程と担当は起動しない。
   起動済みの担当にも一時停止を伝え、終了結果を回収してから解放する（`stop`は終了結果も破棄するため）。
2. 一時停止のためだけに、取り消しの難しい操作（push、PRの作成、WIの状態の変更、資源の回収）は行わない。
3. 作業を失わない形で残す。
   未統合の差分は各担当のworktreeとbranchに残し、選定YAML、計画、引き継ぎ記録の保存状態を確かめる。
4. 再開の記録を会話の外へ書く。
   作業契約（`agent-toolkit:atk-mode/references/work-contract.md`）の目的・制約・原本の所在を保つ。
   工程・検証結果・再開位置は、担当の引き継ぎ記録・計画・WIの該当する原本に残す。
5. ユーザーへ、工程の中の現在位置、保存済みのものと会話の中にしか無いもの（パスで示す）、作ったcommitと作業ツリーがcleanかどうか、再開の最初の操作を伝える。
   これは一時停止の連絡であり、作業完了報告とは別に扱う。

## 終了

工程の区切りは確認の理由から外し、ユーザーの判断が本当に必要な事項だけを確認待ちにして、その判断を待たずに進められる工程は続ける。
回答がすぐに得られない確認事項は`agent-toolkit:atk-mode/playbooks/confirmation.md`「未確定判断の保留と暫定判断」に従ってUWIへ移し、`processing`の元項目も同じ手順でそのUWIへの依存を保存して`inbox`へ戻す。
作業完了の報告を所有するメインは、この作業で作ったcommitを公開してから`agent-toolkit:atk-mode/playbooks/report-completion.md`に従って報告する。
明示的に委譲されたリポジトリ担当は役割の契約に従って委譲元へ返し、担当から外れるために同書やtermination-evidenceを取得することと、`omit-completion`の記録は避ける。
反映が必要な担当は`agent-toolkit:atk-mode/playbooks/report-completion.md`「常時稼働サーバーへの反映」を使い、反映と稼働確認の結果を返す。
公開工程の承認スコープは、選定工程で判別した結果を`agent-toolkit:atk-mode/playbooks/process-wi-selection.md`「公開工程の承認スコープ」に従って使う。

返す報告に含める項目: 処理対象WIごとの採否と公開のcommit、確かめた範囲、未達と見送りの理由と再開条件、ユーザーの判断が必要な事項、適用した手順書。

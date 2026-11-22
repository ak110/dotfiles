# dotfiles環境の振り返り観点

`/atk-mode review-session`の振り返りで、メインが問題の原因と対策を確定する時と、所要時間を分析する時に適用する。
振り返りの`atk run-script session-review-prepare`が出力するJSONの`reference_document`が、本文書の絶対パスを示す。

## セッションの所要時間目標

次の目標値と計測範囲は、`github.com/ak110/dotfiles`を処理の対象に含む`agent-toolkit:atk-mode`の作業`process-wi`のセッションへ適用する。
本リポジトリで同スキルを起動したセッションと、`atk wi process-loop`が別のディレクトリで起動する統括のセッションのうち本リポジトリを対象に含むものが該当し、それ以外の作業は目標値の適用から除く。

- 1セッションの所要時間は、セッションの最初の記録からそのセッションが終了する時点までとする。
  振り返り、公開工程とセッション終了工程、`agent-toolkit:atk-mode/playbooks/report-completion.md`および`atk agents-exit-session`に要する時間も含める。
- 処理したAWIの件数によらず、セッション振り返りの完了を含めて3時間以内を目標とする。

起動方法と目標超過の有無によらず、毎セッションの内訳から品質を保った高速化を検討する。

- 目標内なら記録の照会結果で提案の要否を判断する。
- 超過時は、律速区間へ作用する再発防止策の採否と必要な処置を確定する。
  所要時間の超過はC・Dの問題であり、`agent-basics:bugfix/references/prevention.md`「害と費用の比べ方」で扱う。
  超過の区間に品質を下げた手戻りなどQに影響する問題が含まれる場合も、その問題を同節で扱う。
- 適切に並列化されていれば全体の処理件数が所要時間へ与える影響は小さいという前提で、並列化の成立条件と律速区間を調べ、件数の増加自体を超過の原因や許容の理由にしない。
  過去の対策との比較は`agent-toolkit:atk-mode/playbooks/review-session-analysis.md`「原因と対策」に従う。

巡回の終了条件は、dotfilesリポジトリの`AGENTS.md`と`.claude/skills/develop-dotfiles/references/process-wi-duration.md`「所要時間と終了条件」に従う。
毎回、次回の候補全体の180分以内の見込みを根拠とともに検収するまで、必要な改善の実装と公開を続ける。

## 再発の集計

batchでは、対象にdotfilesの作業が含まれる場合に、再発の記録を原因ごとに数える。
2026年11月22日以降の記録は、`git -C <dotfilesの作業ツリー> log -E --grep='^再発: ' --format='%h%n%b'`で列挙する。
各行が指す元の対策のcommitを原因の単位とする。
それより前の記録は、削除前の障害事例集の`再発`行である。
削除したcommitを`git -C <dotfilesの作業ツリー> log -1 --format=%H --diff-filter=D -- docs/development/incidents.md`で得る。
`git -C <dotfilesの作業ツリー> grep -n -E '^ +再発（[0-9]{4}年' <そのcommit>^ -- 'docs/development/incidents*.md'`で事例の再発行だけを列挙し（書式例の`YYYY`の行は一致しない）、各行が属する事例を原因の単位とする。
新しい行の元の対策が削除前の事例の対策と同じなら、同じ原因として数える。
新旧の記録は削除の時点で分かれ、同じ再発が両方に書かれることは無い。
2回以上再発した事例を分析候補にし、`agent-basics:bugfix/references/recurrence.md`「対策の種類を変える」に従って、同じ条文や仕組みを維持・修正・撤去する案を比べる。
効果確認が`insufficient`の採用済みAWIは件数に加えず、各`再発`行が指す是正AWIと元のAWIの対応を確かめる材料にだけ使う（同じ再発を二重に数えないため）。
今回の分析で新たに再発と判定した不具合は、`.claude/skills/edit-dotfiles-norms/references/recurrence-record.md`に従って記録する。

## pyfltr

pyfltrを使った場合は、検索・置換・自動チェックの結果と復旧案内が判断と修正を支援したかを、成功時の表示、警告、エラーも含めて評価する。
個別の操作ミスを一般化せず、CLI自体の改善で同じ失敗を防げる観測事象がある場合だけ候補にする。

## dotfiles配布物

dotfilesまたはその配布物では、原本と配布先、Claude CodeとCodex、POSIXとWindowsの間の同期と、plugin・rules・skills・agents・hooks・settings・生成物の責務を点検する。
読込時点に合う参照の配置と、不要な状態・フック・リンクの整理、配布・更新・旧資産の回収・版数・生成物の追随を評価する。
規範の反映先は、プロジェクト固有か全エンドユーザー向けかの適用範囲に合わせる。
対象が別リポジトリの場合は、観測事象がdotfilesの配布物に起因すると裏付けられた候補だけをdotfiles向けの提案とし、対象プロジェクトの改善提案と混同しない。

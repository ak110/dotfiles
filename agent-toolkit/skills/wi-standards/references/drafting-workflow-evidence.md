# WIの調査・起草・保存の共通手順：出所と観測の取得

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| セッション記録を復元・進捗・操作の観測へ使う前 | `../../review-session/references/session-records-conversation.md` |
| 依頼の`再発の経過を問う要求`が`該当`の場合に加え、起草者が逐語本文・趣旨から失敗・誤り・指摘の反復、頻度、悪化、いつからか守られなくなったことへの言及と判断した要求で、原因を確定する前 | `agent-basics:bugfix/references/root-cause-severity-quality.md`と`agent-basics:bugfix/references/root-cause-scenarios-recurrence.md`を全文読む |
| 原因分析の書式を使う前 | `agent-toolkit:wi-standards/references/analysis-records.md` |
| 原因分析の処置を記録する前 | `agent-basics:bugfix/references/root-cause-actions-hierarchy.md` |
| 報告用UWIの確認・保存手順へ切り替える前 | `../../report-completion/references/report-formats-uwi.md` |
| 複数リポジトリの共通本文を参照するAWIを読む前 | `cross-repository-submission.md` |
| 保存する本文の共通品質を判定する前 | `agent-toolkit:delegation/references/writing.md` |
| エージェント向け文書を追加・変更するAWIを起草する前 | `agent-basics:writing-standards/references/agent-documents-additions.md` |

## 出所と観測の取得

1. 前後の発話と操作の位置を探す場合は、`../../review-session/references/session-records-conversation.md`「会話の少量取得」を使う。
   要求・判断・認可の確定は、下記の`--user-events`と必要な`--detail`で原文と直前の提案を比較して行う。

2. 認可や解釈の判断に出所の再取得が必要な場合は、以下の公開操作コマンドを利用できる。
   すでに取得済みの証拠は再抽出を省く。
   専念型メインは、ユーザーから直接受け取った発話と自身の記録を用い、ホスト環境が公開している自身のセッション識別子を指定する。
   `agents_server`の要求別担当エージェントおよび付随型担当エージェントは、`--delegation-record root`を指定してユーザー発話を保持する最上位の会話を解決する。

   ```sh
   atk run-script session-review-evidence -- --delegation-record root --user-events
   atk run-script session-review-evidence -- --claude-session-id <対象となるセッションID> --user-events
   atk run-script session-review-evidence -- --codex-thread-id <対象となるthread ID> --user-events
   atk run-script session-review-evidence -- --transcript <対象となる記録の絶対パス> --user-events
   atk run-script session-review-evidence -- --user-events-file <元の保存ファイルの絶対パス> --user-event-at <record>:<line>
   ```

   通常の原文転記では、会話ログの探索のやり直しを省略できる。

3. 取得元の指定は、すでに取得済みの出所ファイルと`record`・`line`、要求が明示した識別子または記録パス、委譲関係から解決した記録の優先順位で選択する。
   専念型メインは自身の記録を明示的に指定する。
   識別子の指定形式は対象ホストに応じたものを選択し、取得済みの指定パラメーターは`--user-events-file`と`--user-event-at`を用いて読み込む。
   `--delegation-record root`は、セッション登録簿よりも実際の起動結果を優先する共通の親子判定ロジックにより、最上位の会話を解決する。
   一方、`--owner-session --user-events`は、実行環境の環境変数`AGENT_TOOLKIT_OWNER_SESSION`が示す所有会話を読み込む別の指定であり、必ずしも直接の委譲元を指すとは限らない。
   また、担当エージェント自身の`CLAUDE_CODE_SESSION_ID`や`CODEX_THREAD_ID`は委譲先自身の記録を指しているため、委譲元の識別子としての代用対象から除く。
   解決に失敗して終了コード2となった場合は、委譲元から対象となる会話のセッションID、スレッドID、または記録ファイルの絶対パスを受け取り、対応する明示的なオプションで再実行する。
   なお、`--owner-session`は未設定の場合や、`mcp-`で始まる値の場合、あるいは記録が0件または複数件存在する場合にも終了コード2となり、環境変数の値自体は出力されない。
   `Agent`ツールなど解決動作を確認していない起動方法の場合も、明示的な識別子指定を用いる。
   記録の取得を必要としない要求の起草は、そのまま作業を継続する。
   対象の逐語内容、時点、文脈と一致するイベントの全フィールドを読み込み、出所を「元の保存ファイルの絶対パス」およびJSONレコードの`record`と`line`によって確定する。
   `line`は元のセッション記録における行位置であり、保存出力ファイルの物理行番号とは区別する。
   `<record>:<line>`の指定は末尾のコロンのみで区切る。
   同一の文面が繰り返されている場合であっても、内容の一致だけで特定せず、元の記録における位置を一意に確定する。
   取得不能、不一致、または特定に必要な入力情報の不足が発生した場合は、「続行できない理由」として報告する。
   構造化された確認質問については、質問、全選択肢、選択された回答、および自由記述の対応関係を正確に保持し、ユーザーの回答として採用する範囲は`wi-standards-origin.md`「由来と承認」に従う。
   この出所情報は、引用の一致確認、ならびに対象、除外事項、認可の由来確認に用いる。
   複数の候補が一致している照会結果は、証拠の一意性の確認やユーザー由来の判定の根拠から除く。

4. 委譲先エージェントに必須となる入力の欠落は、`agents_server`の`start`コマンドが起動時に検証するため、受け取った入力のまま作業に着手する。
   委譲先が入力の欠落を観測した場合は、`rules-subagent.md`「委譲時の厳守事項」に従う。
   投入先リポジトリは、ユーザーの指定または委譲入力で指定された値に固定する。
   調査の過程で投入先リポジトリの外部にも原因機構を共有する対象が見つかった場合は、`agent-basics:bugfix/references/root-cause-severity-impact.md`「QとC・Dの区別」に従って処置の要否を判定する。
   委譲先は、該当対象の所在、所有リポジトリ、根拠、処置の要否を委譲元へ即座に通知し、AWIの`## 反映内容と反映先`に「投入先リポジトリの外部であること」および「委譲元へ通知済みであり投入先の確定は委譲元が行うこと」を記録する。
   投入先リポジトリ内の原稿や、回答を待たずに確定できる成果物は、`agent-toolkit/share/rules/rules-subagent.md`「返却形式の受け渡し」に従って完成させる。
   付随型は委譲元へ返却し、専念型は検収と保存まで完了させる。
   専念型メインは、共通の確認要否判定に従って投入先を確定する。
   処置不要と判断した対象については、根拠となる現物ファイルの所在とリビジョン（版）を、AWIの`## 反映内容と反映先`の除外事項、もしくはUWIの成立性の証拠に残す。
   認可を超える不可逆な操作や、ユーザーの回答次第で内容が変動する部分については、操作する前に所定の確認先へ提示する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 根本原因と重大度：QとC・Dの区別の基準を適用する前 | `agent-basics:bugfix/references/root-cause-severity-impact.md` |
| AWIとUWIの共通規範：由来と承認の基準を適用する前 | `wi-standards-origin.md` |

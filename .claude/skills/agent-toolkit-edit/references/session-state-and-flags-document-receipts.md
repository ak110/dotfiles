# セッション状態ファイルとフラグ：確認・操作スキル系

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 各状態の共通設計・生存期間・排他・継承を適用する前 | `session-state-and-flags-common.md` |
| 呼び出し主体の判別方法を確かめる前 | `agent-basics:writing-standards/references/claude-hooks.md` |
| run-skillの再開入力と寿命を変更・調査する前 | `agents-server-shared-state-run-skill.md` |

## 確認・操作スキル系

- `user_confirmation_skill_pending`: 各文脈における`agent-toolkit:user-confirmation-and-report`の起動案内が配送待ちであるかを示す真偽値
  `agent-toolkit/agent_toolkit/_hooks/rules_context.py`が、SessionStartの`source`が`startup`、`clear`、`compact`であり、かつ委譲先エージェントではない場合に真にする（`resume`や`fork`の場合は変更しない）。
  `agent-toolkit/agent_toolkit/_hooks/user_prompt_submit.py`が人間の実際の発話を受けたときにこの値を参照し、真であれば同スキルの起動を促す注記を返す（機械的に注入されたターンでは参照も変更も行わない）。
  注記を返した時点で偽に戻す。
  また、PostToolUse(Skill)が同スキルの起動を観測した場合や、UserPromptSubmitがユーザーによる同スキルのスラッシュコマンド実行を観測した場合も偽に戻す。
  生存期間はセッション状態ファイルと同一とする。
- `operation_skill_delivered_agents`: 資料識別子から受信主体へのマッピング。
  `received-document:<JSON>`は論理資料・実体パス・必要範囲・受信版・範囲digestを保持する。
  分割受信は同じ主体・論理資料・版の行範囲を集約し、必要範囲が完成した時点で取得済みにする。
  部分記録は更新案内の根拠から除く。
  `document_reads.acquisition_state`が取得済み・取得後の更新・未取得を区別する。
  同じ必要本文を別実体から受信した場合も再利用する。
  規範はファイル全体の受信版を照合し、規範以外は必要な確認範囲を照合する。
  旧形式の`read-trigger:<絶対パス>#sha256:<本文hash>`も、受信時の版のまま保持する。
  主体はpayloadの`agent_id`、無い場合は`main`とする。
  `read-update:<受信資料識別子>`は未受信の現在版の更新案内を実配送した主体を保持する。
  更新だけの場合は再書込を許可する。
  `read-trigger-skill:<絶対パス>#sha256:<本文hash>`はSkill起動の専用証拠とし、PostToolUseのSkill成功時に記録する。
  名指し参照のRead・Bashで得た本文取得と区別し、後続のスキル起動要求には専用証拠を求める。
  案内済みのキーも`read-guidance:read-trigger-skill:<絶対パス>#sha256:<本文hash>`で区別する。
  寿命・主体別の管理とclear・compactによるリセットは既存の取得状態と同じとする。
  同じ主体が取得済みの本文をEdit・MultiEdit・Writeで変更した成功では、編集応答の`originalFile`から変更前後の本文を確かめ、変更前に存在した種類の取得証拠だけを新hashへ引き継ぐ。
  参照取得で継承するのは本文取得の証拠に限る。
  `originalFile`が欠落・null、`staged`や`userModified`、編集失敗、別主体・外部変更・Bashの書換えは継承の対象外とする。
  ホスト応答の観測と再検証手段は`docs/development/audit-records.md`「.claude/skills/agent-toolkit-edit/references/session-state-and-flags.md：自己編集の取得継承：2026年11月2日」にある。
  `selected-read-target:<JSON>`は選択済みパス・見出しと選択元の`declarations`配列（`source`・`operations`・`kind`・`selector`）を保持し、持越し時に現行宣言へ接続する。
  対応の無い旧選択は保存したパス・見出しを解決し、失われた見出しを除く。
  `modified-agent-document:<絶対パス>`は変更文書、`review-reference:<絶対パス>`はprepareの参照資料、`read-trigger-role:<絶対パス>`は役割を保持する。
  選択情報は圧縮後も保持し、取得済み状態のみリセットする。
  `compact`では取得済みだった選択資料と必要範囲を候補にし、現在必要な範囲を予算内で一度案内する。
  `read-guidance-carried:<絶対パス>`は持越し候補を操作ごとの案内と遮断から外し、現在の操作が再選択したときに解除する。
  取得証拠は主体ごとにリセットする。
  `startup-input-target:<JSON>`は起動入力のパス・見出し・selectors・原資料のhashと配送本文の`body_sha256`を保持し、圧縮後の再配送に使う。
  `review-output:<絶対パス>`は未取得の保存JSON、`review-output-parsed:<資料識別子>`は解析済み版を示す。
  JSONから参照資料を登録した後は未取得の保存JSON登録を解除する。
  本文編集の検知対象は、編集ツール、Bashの静的なヒアドキュメント（heredoc）やヒアストリング（here-string）、インプレース置換、およびPythonスクリプトによる書き込みとし、生成出力の保存や単なるファイル複製は除外する。
  managed-temp配下の原稿に対してもこれと同じ基準を適用する。
  委譲先自身が使用する引き継ぎ記録ファイルについては、`AGENT_TOOLKIT_HANDOFF_PATH`が示す1件の絶対パスのみを、本文編集時のwriting-standards取得要求の対象から除外する。
  この除外後も取得状態は元の値を保つ。
  同一ディレクトリにある別ファイルや、環境変数が未設定の入力、引き継ぎ記録先以外を含む複数ファイルの編集については、通常の取得状態に基づいて判定する。
  `resolved-project-skill:<絶対パス>`は実際の取得入力から確定したproject skillの宣言元パスを保持する。
  起動時は正式入力の識別と実配送本文のdigestを確認し、受信した版を記録する。
  後日の更新は取得後の更新として区別し、取得済みの根拠は識別情報と本文の両方に求める。
  入力の識別情報と宣言元は会話圧縮時にも保持される。
  `startup-context-observed`は、正式な起動入力の取得先と役割を登録した主体を保持し、圧縮後に過去の履歴本文が二重計上されるのを防ぐ。
  `startup-context-reset`は、最初の操作より前に発生したclearやcompactについても記録する。
  これにより、同じ記録で本文取得の前提が誤って復元されるのを防ぎ、実体指定の再取得へつなげる。
  `read-guidance:<取得識別子>#heading:<対象範囲>`は、初回の未取得案内を実配送した主体を保持する。
  予約は最終出力へ採用した場合だけ確定する。
  未取得が続いても同じ案内を反復せず、再書込は許可する。
  取得証拠は同じ主体が実際に受信した本文だけから記録する。
  純粋Read・静的Bash読取・Skill起動では依存案内と未確定条件の候補通知を保留し、次の関連する非取得操作で必要範囲を再評価する。
  候補は役割・選択済み資料・同じ主体が取得した宣言元へ限定し、同じ条件と必要範囲を統合する。
  受信判定は`document_reads.acquisition_state`を再利用する。
  Claudeは実tool_response、Codexは後続PreToolUse時点の外側配送本文を観測する。
  取得の記録は、応答に実際に含まれた必要範囲に限る。
  ClaudeメインのSkill起動要求、名指し参照の内容取得、WebFetchのURL別出典確認は分離する。
  正式なstartup-documentは識別と本文を意味上で確認して記録する。
  選択は`ReadTrigger.condition`の自然文と役割を保持する。
  他役割の行を除き、判断契機では`read:`の一致だけで自然文条件を確定しない。
  `confirmation`に一致する確認ツール呼出しと本文で識別したWI保存種別は、その操作で確かめられる条件だけを確定する。
  `AskUserQuestion`・`request_user_input`・`request_user_input_async`の呼出しは確認本文・選択肢の起草契機を観測したものとして扱う。
  他の未確定な自然文条件は確認候補とする。
  同一パスの参照を集約し、宣言修復後の選択は現行宣言へ接続する。
  圧縮前の候補は現在の必読案内と遮断の根拠から除外する。
- `document_read_candidates`: PreToolUseで準備した、主体ごとの既知資料・必要範囲・受信前の版とdigest。
  Readの明示対象も含め、選択前の取得と観測前の外部更新に対応する。
  保存はdigestなどのメタデータに限定する。
  compact時はメインの候補を除く。
- `document_read_pending`: Codexの要求位置・生産出力上の位置・版・行digest・必要範囲の主体別候補。
  同じ外側配送の全ブロックで受信行と回数を確かめ、対応する要求位置だけを取得記録へ反映する。
  別位置の同文候補を区別できなければ取得済みにせず、対応しない候補を次の読取へ流用しない。
  本文は保存せず、compact時はメインの候補を除く。
- `document_read_spools`: PostToolUseが保存先の版・元資料の要求位置とdigestを主体別に保持し、保存先の実読取で原本の位置へ対応付ける。
  退避だけでは取得にせず、別主体・保存本文の変更後には流用しない。
  本文は保存せず、compact時はメインの来歴を除く。
- `document_read_offsets`: 主体とrolloutの絶対パスから、観測済みのバイト位置への対応。
  Codexの後続PreToolUseが完成した外側出力行だけを差分走査して進める。
  compact時はメインの位置を末尾へ進め、以後に配送された本文を観測する。
  寿命はセッション状態と同じ。
- `result_read_pending`: `result_reads.py`が、Codexの内側で回収した結果の識別digestと判断資料を主体別に保持する。
  後続PreToolUseで同じ結果の外側配送を確認したときだけ消費し、選択資料へ接続する。
  結果本文を保存せず、compactでメインの未配送候補を除く。
- `result_read_files`: 結果回収の公開応答が示した本文ファイルと判断資料の主体別対応。
  保存先を提示しただけでは資料を選択せず、同じ主体の完全受領を確認したときに選択する。
  セッション状態と同じ寿命で保持する。

- `termination_evidence`: `agent-toolkit/agent_toolkit/_hooks/termination_evidence.py`が、終了工程に必要な証拠を作業単位で管理・保持する。
  保持する内容には、作業ごとの成果作業の種別、report-completionの起動証拠、ツールの呼び出し履歴、判断の記録、遅れて返ってくる応答を元の作業に対応付けるための未完了の呼び出し、ならびに判断の根拠となった人間の入力が含まれる。
  PreToolUse、PostToolUse、UserPromptSubmitがツール呼び出しと人間の入力を逐次更新し、Stopの`termination_order_advisor`が成果作業と起動証拠の対応関係を読み取る。
  報告本文、完了の自己申告、起動済みを示す独自フラグなどは保持しない。
  解決済みの呼び出しや入力は更新のたびに除外し、バージョンや形式が合致しない値は破棄してStop判定ログに診断情報を残す。
  生存期間と継承の扱いはセッション状態ファイルと同一とし、別の会話から継承した値は`session_id`の一致を確認してから使用する。
  - 成果作業と起動証拠: 成功した対象スキルの起動、リポジトリに対する自身の編集作業、計画ファイルの保存、WIの保存、commitやpushなどを`deliverables`へ記録する。
    成功したreport-completionスキルの起動（Claude Code）や、外側で必要本文の配送を確認した`SKILL.md`の読み取り（Codex）は、`completion_report`へ記録する。
    Codexの成功した読取は`pending_reads`に所有plugin・実体・版・元の呼出しを保留し、共通の取得記録で本文受信を確認して開始証拠へ変える。
    compactで保留を除く。
    起動証拠を取得済みの作業に対して、新しい人間の入力があった後にさらに成果作業が発生した場合は、別の新しい作業として管理を開始する
  - 待機の許可: 成果作業が待機している非同期処理の実行中に、Stop hookが待機を許可する条件は`agent-toolkit:report-completion/references/claude-hooks-stop.md`「終了工程の証拠」に定める。
    本キーは作業の開始位置（UserPromptSubmitが記録したtranscriptのサイズ）を保持し、Stopがその判定時に参照する
  - 成果作業の対象: 観測されたリポジトリのルートパスを`repositories`に保持する。
    直接実行されたGit操作については`-C`などの引数を含めて対象リポジトリを取得し、シェルのディレクトリ移動や環境変数の前置代入によって対象を取得できない操作については`null`を保持する。
    対象が単一のリポジトリであると確定できる作業に限り、プロジェクト規範に基づく終了工程の省略指定を受理する
  - 委譲先に対するwait判定: `async_targets`とその所有者を確認し、観測義務とは独立して有効なCLI待機所有権や対象登録の有無を共通の読み取り処理で確認する。
    `pending_observation`が偽であっても待機が有効であれば状態を保持し、両方の処理が完了した段階で証拠不足の判定へ戻す
  - 自身の編集作業: 成功したEdit、Write、`apply_patch`、および既存の静的解析で確定したBashによる書き込み先を、作業ごとの`edits`にリポジトリおよび相対パスとして記録する。
    Claude Codeの協調モードにおけるメインエージェントでは、Stop hookがこの編集集合と終了時のGit状態を比較し、公開範囲が確定していない編集が含まれている場合は遮断通知に添えて提示し、他主体による差分を誤って取り込まないようにする
  - 公開範囲: メインエージェントが元の指示入力または適用済みの認可に基づいて判断した公開範囲を`publish_scope`に記録する。
    記録手順は`agent-toolkit:user-confirmation-and-report`の`references/publish-scope.md`に従う
  - プロジェクト規範による省略: メインエージェントが`omit-completion`で指定したエージェント向け文書の絶対パス、記録処理がそのパスから読み取った全文、対象リポジトリ、および人間の入力識別子を、作業ごとの`omit_completion`に保持する。
    `input_id`が省略されている場合は、現在の会話における直近の人間の入力に対応付ける。
    記録時には、観測された成果作業が単一のリポジトリに対するものであること、規範ファイルの所在、現在のメインエージェント・会話・入力の対応関係を確認し、明示された引用文（`quote`）が規範の全文と一致しているかも確認する。
    Stop hookにおいても規範ファイルの実体と入力の対応を確認する。
    有効な省略情報を持つ作業に限り、起動不足の判定を免除する。
    この免除は次回の判断時に解除され、新しい人間の入力後に発生した成果作業は新規作業として扱われるため、省略設定は引き継がれない。
    エージェント向け文書の変更や消失が確認された場合は、免除を無効化する。
    具体的な手順は`agent-toolkit:report-completion`の`references/termination-evidence.md`に従う
  - 元の入力と再突入: `last_human_input`には直近の人間の入力を保持し、自動通知による再開後であっても元の依頼への帰属関係を維持する。
    同じターンIDを持つ通知は別の入力として保存し、人間の入力の本文と由来を上書きしない。
    ターン途中に届いた入力（Claude Codeのセッション記録の`attachment.type=queued_command`）は、`transcript_path`に保持した記録ファイルを`queued_offset`の読取位置から読んで取り込む。
    取り込んだ入力のうち`origin.kind=human`のものだけを人間の入力として扱う。
    記録ファイルが変わったら読取位置を先頭へ戻す。
    `--context`は、人間の入力の有無にかかわらず現在の会話の`session_id`と作業の`work_id`を返し、その入力を観測できた場合のみ`input_id`と`quote`を添え、観測できていなければ両者を`null`とする。
    証拠が存在しない場合、別会話や旧バージョンの値である場合、現在のメインエージェントとして特定できない場合、あるいは作業も人間の入力も存在しない場合は取得を拒否する。
    取得しても判断に必要な証拠条件は緩まず、人間の元の入力、実際の失敗、所有する委譲先の待機状態の検証は、各actionごとの記録処理において厳格に行われる。
    生存期間と継承の扱いは本キー全体と同一であり、公開範囲、起動証拠、および現在のGit状態から不要な判定を除外する

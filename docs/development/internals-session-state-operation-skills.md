# セッション状態ファイルとフラグ：確認・操作スキル系

各状態の共通の設計、生存期間、排他、継承は`docs/development/internals-session-state-common.md`にある。

## 確認・操作スキル系

- `verification_bugfix`: 検証・CIの失敗を観測した主体ごとに、最後の失敗のtool_use_idとbugfix未起動の印だけを保持する。
  PostToolUseが失敗を記録し、同じ主体の`Skill`によるbugfixの起動成功で印を解除する。
  PreToolUseは修正・commit・検証の再実行で未起動を警告し、遮断しない。
  同じ失敗の再配送では印を戻さず、新しい失敗では改めて起動を求める。
  圧縮で未対処の印を消さず、clearと期限切れ回収はセッション状態の共通寿命に従う。
- `operation_skills_notified`: `pretooluse/operation_skills.py`が、主体ごとに、操作の直前に起動を案内したスキルと、Claude Codeの`Skill`で起動を観測したスキルの完全名を保持する。
  対象は表`OPERATION_SKILLS`の4件（search、bugfix、writing-standards、check-execution）である。
  案内は最終出力へ採用した時に記録し、記録済みのスキルはその主体へ再び案内しない。
  会話圧縮・clearのSessionStartで`forget_subject`がその主体の記録を消し、次の該当する操作で再び案内する。
  軽量の委譲先には案内せず、一時領域（managed-tempとClaude Codeの一時領域）だけへの書込はwriting-standardsの案内の対象から外す。
- `termination_evidence`: `agent-toolkit/agent_toolkit/_hooks/termination_evidence.py`が、終了工程に必要な証拠を作業単位で管理・保持する。
  保持する内容には、作業ごとの成果作業の種別、report-completionの起動証拠、ツールの呼び出し履歴、判断の記録、遅れて返ってくる応答を元の作業に対応付けるための未完了の呼び出し、ならびに判断の根拠となった人間の入力が含まれる。
  PreToolUse、PostToolUse、UserPromptSubmitがツール呼び出しと人間の入力を逐次更新し、Stopの`termination_order_advisor`が成果作業と起動証拠の対応関係を読み取る。
  報告本文、完了の自己申告、起動済みを示す独自フラグなどは保持しない。
  解決済みの呼び出しや入力は更新のたびに除外し、バージョンや形式が合致しない値は破棄してStop判定ログに診断情報を残す。
  生存期間と継承の扱いはセッション状態ファイルと同一とし、別の会話から継承した値は`session_id`の一致を確認してから使用する。
  - 成果作業と起動証拠: 成功した対象スキルの起動、リポジトリに対する自身の編集作業、計画ファイルの保存、WIの保存、commitやpushなどを`deliverables`へ記録する。
    完了報告の手順書`skills/atk-mode/playbooks/report-completion.md`の起動証拠は、`completion_report`へ記録する。
    Claude Codeは`atk-mode`の引数`report-completion`での起動か、応答の`file.content`が現在の全文と一致した`Read`を記録する。
    Codex・Antigravityは、出力をリダイレクトしない本文表示か1行目から始まる`sed -n`で、結果が現在の全文を含むコマンドの読取を記録する。
    メインの起動証拠を記録した時は、手順書の現在の版（SHA-256）を`completion_context`へ保つ。
    Stopはこの版が現在の版と一致する時だけ、メインの文脈に手順書があるとみなす。
    会話圧縮・clearのSessionStartが`completion_context`を消す。
    起動証拠を取得済みの作業に対して、新しい人間の入力があった後にさらに成果作業が発生した場合は、別の新しい作業として管理を開始する
  - 待機の許可: 成果作業が待機している非同期処理の実行中に、Stop hookが待機を許可する条件は`docs/development/design-hooks-termination-evidence.md`「終了工程の証拠」に定める。
    本キーは作業の開始位置（UserPromptSubmitが記録したtranscriptのサイズ）を保持し、Stopがその判定時に参照する
  - 成果作業の対象: 観測されたリポジトリのルートパスを`repositories`に保持する。
    直接実行されたGit操作については`-C`などの引数を含めて対象リポジトリを取得し、シェルのディレクトリ移動や環境変数の前置代入によって対象を取得できない操作については`null`を保持する。
    対象が単一のリポジトリであると確定できる作業に限り、プロジェクト規範に基づく終了工程の省略指定を受理する
  - 委譲先に対するwait判定: `async_targets`とその所有者を確認し、観測義務とは独立して有効なCLI待機所有権や対象登録の有無を共通の読み取り処理で確認する。
    `pending_observation`が偽であっても待機が有効であれば状態を保持し、両方の処理が完了した段階で証拠不足の判定へ戻す
  - 自身の編集作業: 成功したEdit、Write、`apply_patch`、および既存の静的解析で確定したBashによる書き込み先を、作業ごとの`edits`にリポジトリおよび相対パスとして記録する。
    Claude Codeの協調モードにおけるメインエージェントでは、Stop hookがこの編集集合と終了時のGit状態を比較し、公開範囲が確定していない編集が含まれている場合は遮断通知に添えて提示し、他主体による差分を誤って取り込まないようにする
  - 公開範囲: メインエージェントが元の指示入力または適用済みの認可に基づいて判断した公開範囲を`publish_scope`に記録する。
    記録手順は`agent-toolkit:atk-mode/playbooks/confirmation.md`に従う
  - プロジェクト規範による省略: メインエージェントが`omit-completion`で指定したエージェント向け文書の絶対パス、記録処理がそのパスから読み取った全文、対象リポジトリ、および人間の入力識別子を、作業ごとの`omit_completion`に保持する。
    `input_id`が省略されている場合は、現在の会話における直近の人間の入力に対応付ける。
    記録時には、観測された成果作業が単一のリポジトリに対するものであること、規範ファイルの所在、現在のメインエージェント・会話・入力の対応関係を確認し、明示された引用文（`quote`）が規範の全文と一致しているかも確認する。
    Stop hookにおいても規範ファイルの実体と入力の対応を確認する。
    有効な省略情報を持つ作業に限り、起動不足の判定を免除する。
    この免除は次回の判断時に解除され、新しい人間の入力後に発生した成果作業は新規作業として扱われるため、省略設定は引き継がれない。
    エージェント向け文書の変更や消失が確認された場合は、免除を無効化する。
    具体的な手順は`agent-toolkit:atk-mode/references/termination-evidence.md`に従う
  - 元の入力と再突入: `last_human_input`には直近の人間の入力を保持し、自動通知による再開後であっても元の依頼への帰属関係を維持する。
    同じターンIDを持つ通知は別の入力として保存し、人間の入力の本文と由来を上書きしない。
    ターン途中に届いた入力（Claude Codeのセッション記録の`attachment.type=queued_command`）は`transcript_path`に保持した記録ファイルを`queued_offset`の読取位置から読んで取り込む。
    取り込んだ入力のうち`origin.kind=human`のものだけを人間の入力として扱う。
    記録ファイルが変わったら読取位置を先頭へ戻す。
    `--context`は人間の入力の有無にかかわらず現在の会話の`session_id`と作業の`work_id`を返し、その入力を観測できた場合のみ`input_id`と`quote`を添え、観測できていなければ両者を`null`とする。
    証拠が存在しない場合、別会話や旧バージョンの値である場合、現在のメインエージェントとして特定できない場合、あるいは作業も人間の入力も存在しない場合は取得を拒否する。
    取得しても判断に必要な証拠条件は緩まず、人間の元の入力、実際の失敗、所有する委譲先の待機状態の検証は、各actionごとの記録処理において厳格に行われる。
    生存期間と継承の扱いは本キー全体と同一であり、公開範囲、起動証拠、および現在のGit状態から不要な判定を除外する

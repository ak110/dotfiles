# 監査記録: 規範の文体と配置

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## .chezmoi-source/dot_claude/docs/session-review-dotfiles.md：dotfiles配布物：2026年11月18日

同じpyfltrの3件を、規範が少なかった9月30日版、比較当時の現行版、簡易な実行環境の3構成で処理した。
9月30日版の2回は現行版の1.40〜1.74倍の生成トークンと1.61〜1.67倍の所要時間（処理開始から最後の採用まで）を要した。
独立した成果物評価では3構成とも重大欠陥は見つからなかった。
9月30日版はテストが最も厚く、依頼の解釈は現行版が最も指示に沿っていた。
簡易な構成は速かったが、公開せずキューも更新していないため、同じ完了状態の速度比較には使えない。
比較したのは個別作業の成果物と所要であり、長い会話や圧縮直後の手順遵守を計測したものではない。
当時の構成全体を比べており、規範の分量だけの効果は分からない。
計測と成果物評価の一次記録はリポジトリ外にある。

## agent-basics/skills/writing-standards/references/agent-documents-basics.md：語調と現行規則の表現：2026年9月16日

条文は2026年10月6日まで`llm-characteristics.md`「知識の想起」にあり、同ファイルの撤去に伴って`agent-documents-basics.md`「語調と現行規則の表現」の理由へ移った。

2026年9月16日、本リポジトリのHEAD `2ee94027`でコーパス分析とA/B実験を実施した。

コーパス分析の対象は、`agent-toolkit/rules/`、`agent-toolkit/skills/`、`agent-toolkit/share/`、`.chezmoi-source/dot_claude/`、`.claude/skills/`配下のMarkdownと`AGENTS.md`とする。
現行本文137ファイル・9850文では、否定形で終わる文は1481文（15.0%）だった。
`scripts/check_agent_doc_tone.py`の`_SUBJECT_WORD`が表す指示語は1393回（1000文あたり141回）現れた。
由来の区分は`git log --format='%H%x09%an%x09%(trailers:key=Co-Authored-By,valueonly)'`で全3578commitのトレーラーを取って行った。
`Codex`を含むものをCodex由来（107件、いずれも2026年8月）、`Claude`を含むものをClaude由来とした。
Codexが委譲先として書いた変更はClaude名義で記録されるため、Codex由来の件数は下限である。
追加文の抽出は各commitの`git show --format= --unified=0 -- '*.md'`の追加行から行い、コードブロック・表・見出しを除いて「」で分割した。
追加文1000文あたりの推移は、Codex利用前のClaude由来、2026年8月のCodex由来、2026年9月のClaude由来の順に次のとおりであった。
`_SUBJECT_WORD`の対象語23→55→163、「`主体`」0.6→7.6→17.5、「`正本`」0.4→10.4→35.4、「`契約`」0.2→8.3→11.0。
「`終端`」0.5→2.5→36.7、「`厳守規定`」0.8→5.6→18.2、「〜を根拠にしない」型2.9→7.8→10.2。
「〜だけを…しない」型0.7→6.8→6.3、否定形終端文の割合5.0〜8.0%→10.9%→15.7%。

A/B実験では、`agent-toolkit/rules/01-agent.md`の「判断指針」と「完遂と先送り」（112行）を条件Aの抜粋とし、同じ内容を平易な肯定形へ書き換えた99行を条件Bの抜粋とし、抜粋を読ませない条件Cを対照とした。
抜粋の否定形終端率はA19.5%・B12.1%、`_SUBJECT_WORD`の対象語はA34回・B0回、「厳守規定」はA7回・B0回である。
課題はスキルの1節の起草（10〜20文）、バグ修正方針の指示文（300〜600字）、ユーザーへの完了報告（200〜400字）の3件とし、各条件3回ずつClaude Opusの委譲先へ与え、条件を隠した匿名のファイル名で別の委譲先へ採点させた。
修正方針の課題では、範囲外の追加要素がA4.67件・B2.00件・C1.67件、過剰設計度がA3.00点・B2.00点・C2.33点であった。
成果物の否定形終端率は、スキル起草の課題でA17.9%・B9.0%・C22.9%、修正方針の課題でA30.5%・B16.5%・C27.6%であった。
反復は3回であり、統計的な検定は行っていない。
完了報告の課題では条件間の差は現れなかった。

再検証は`uv run --frozen python scripts/check_agent_doc_tone.py --report <対象ファイル>`で現行本文の指標を取得し、上記の値と比べる。
再検証の契機は、規範文書の一括改訂と、文体の閾値の見直しとする。

2026年10月14日、`scripts/check_agent_doc_tone.py`の文境界を、句点とMarkdown構文解析が返す本文ブロックの末尾へ変更した。
同じブロックの折返しは連結し、文末の物理行と抜粋を保持する。
frontmatter、HTMLコメント、フェンス付きコード、見出しと表の除外、インラインコードの置換語、8%・3%・20文の判定条件は維持した。
字下げコードも保存データとして分子・分母の共通対象から除外し、引用内の本文段落は引き続き数える。
入力WIの基準期間は次の方法で再測定した。
最初のCodex由来commit 2026年8月2日「fix(agent-toolkit): プロジェクト規範を実態より優先してコミット帰属を統一する」、`4cef410f8`より前のClaude由来787commitについて、Markdown追加行をhunkごとに数えた。
同じ集合で、句点だけの数え方は1400/16182文（8.65%）、ブロック末も数える方法は1879/22925文（8.20%）だった。
この再測定値は入力WIの観測に由来し、2026年9月16日の区分ごとの記録と集計単位が異なるため、数え方による差だけを閾値維持の根拠とする。
再検証では前掲の`--report`を使い、段落、句点の無い箇条の項目、引用内の段落を個別に数え、同じブロックの折返しを1文として数えることを確かめる。
規範の一括改訂後は`uv run --frozen pyfltr run-for-agent --commands=agent-doc-tone`をパス指定なしで実行し、全対象への到達と密度超過の診断の不在を確認する。
上記の旧値と比べる場合は数え方と集計単位をそろえる。

## agent-basics/skills/writing-standards/references/agent-documents-basics.md：置き場所と配送：2026年10月6日

配送範囲表のAntigravityの2列、作業ディレクトリのプロジェクト規範の行、agent-toolkitのスキルの行、Codexの列の`~/.codex/AGENTS.md`の配置条件の根拠である。

2026年10月6日、`agents_server`の`start`でツールを使わせない委譲先を起動し、初期コンテキストにある文書を答えさせて、各見出しの直後の1文の逐語引用を現物と比べた。
版はClaude Code 2.1.291（claude-agent-sdk 0.2.163）、codex-cli 0.160.1、agy 1.3.0である。
Claudeの`explore`は`rules/`配下だけが届き、`rules-subagent.md`、作業ディレクトリの`AGENTS.md`、スキル（`skills=[]`）は届かなかった。
Claudeの`delegate`は`rules/`配下、`rules-subagent.md`と`rules-subagent.claude-code.md`、作業ディレクトリの`AGENTS.md`、スキルが届いた。
Codexの`explore`は`~/.codex/AGENTS.md`経由で`rules/`配下と`rules-main.codex.md`、およびスキルが届き、`rules-subagent.md`と作業ディレクトリの`AGENTS.md`は届かなかった。
`delegate`はこれらに加えて`rules-subagent.md`と作業ディレクトリの`AGENTS.md`が届いた。
Antigravityの`explore`と`delegate`はどちらも作業ディレクトリの`AGENTS.md`が届き、`rules/`配下は届かなかった。
`rules-subagent.md`は`delegate`だけに届き、`delegate`のスキル一覧は作業ディレクトリの`.agents/skills`配下などだけでagent-toolkitのスキルを含まなかった。
`explore`のスキルは確かめていない。
実装の根拠は次の3つである。
`_agents_server/claude.py`の`_build_options`は軽量起動で`setting_sources=["user"]`と`skills=[]`を渡す。
`codex.py`の`_base_thread_config`は軽量起動で`project_doc_max_bytes=0`を渡す。
`antigravity.py`の`_system_prompt`は起動区分ごとの指示を本文の先頭へ置くだけで、読込範囲を変えない。
`~/.codex/AGENTS.md`は`scripts/sync_codex_agents.py`が生成し、Codex単体のインストーラーは配置しない（`docs/guide/codex-guide.md`冒頭）。
`Agent`ツールのサブエージェントとCodexの組み込み委譲先のスキルの列は観測していないため「未確認」とした。

ユーザーがモデル比較を指示した再検証では、`start`の`model_type`に比較するモデル、`model_override`に`user_instruction`を指定する。
`model_override_evidence`には`agent-toolkit:atk-mode/references/model-settings.md`「本人指定による上書き」で定める原文・回答の所在を渡す。
ツールを使わない指示とともに同じ問いを渡す。
問いは初期コンテキストの文書一覧と、次の各見出しの直後の1文である。
見出しは`01-agent.md`「QCDと3段判定」、`02-agent-operations.md`「ツール・コマンド運用」、`rules-subagent.md`「確認事項の即時通知」である。
ほかに`rules-main.codex.md`「メインエージェント向け規範」と作業ディレクトリの`AGENTS.md`「記録の所在」を使う。

## agent-basics/skills/writing-standards/references/agent-documents-basics.md：語調と現行規則の表現：2026年10月31日

2026年10月31日、[Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)をHTTP 200で取得した。
対象版はClaude Opus 5.5であり、実モデルの比較実験は行っていない。
同ガイドの「Calibrate effort」はeffortを主な調整手段とし、`xhigh`・`max`は品質向上を測定した作業へ留め、思考を減らす場合はeffortを先に下げることを推奨する。
「Thinking instructions in chat system prompts」は熟考を促す行の削除を検討するよう案内する。
「Safeguard refusals」は内部推論の再現要求が`reasoning_extraction`で拒否されうる一方、短い説明や実施内容の要約は求められると説明する。
費用面の推奨を共通規範の努力目標へ採ることは技術判断であり、全モデルの効果を実証した記録ではない。
再検証は同じURLの対象モデルと上記3節を取得し、effortとプロンプト指示の関係、最上位effortの選定条件、内部推論と短い説明の区別を確認する。
モデル更新時の点検では、[Claudeの共通ガイド](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)や[OpenAIのガイド](https://developers.openai.com/api/docs/guides/prompt-engineering)から使用モデルの資料へ進む。
両URLも同日にHTTP 200を確認し、後者は旧platform URLから上記URLへの転送を確認した。

## agent-basics/skills/writing-standards/references/agent-documents-basics.md：条件と範囲の記述：2026年11月11日

2026年11月11日、[Prompting Claude Fable 5.2](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-2)の公式Markdownを取得した（取得は成功し、本文全体を読んだ）。
対象版はClaude Fable 5.2とClaude Mythos 5.2であり、実モデルの比較実験は行っていない。
同ガイドの「Approvals」は、承認の規則が対象とする操作を正確に名指しするよう案内する。
承認者のいない模擬課題でのAnthropicのテストでは、「この会話の外部の人にメッセージを送信すること」を要承認に挙げた規則のもとで、モデルが日常的な依頼のおよそ半分を控えたと記す。
「Keep caution from piling up」は、不要になった注意書きの削除、承認の要らない操作の名指し、レビュー担当が付ける条件の確認、例外を規則自体へ書くことを案内する。
同節によると承認済みの手順でも「never」の規則のもとでは実行が約10回に1回で、別の行に置いた許可では大きく変わらず、規則自体に事前の承認を得る旨を書くと約10回中9回になった。
承認の要らない操作として再試行を名指しすると再試行が増え、より自由に行動してよいという一般的な指示では変わらなかったこと、再試行の名指しは繰り返しても安全な呼び出しに限ることも記す。
これらを規範の書き方の努力目標へ採ることは技術判断であり、dotfilesの規範での効果を実証した記録ではない。
再検証は同じURLの上記2節を取得し、承認の規則の対象の特定、例外を規則の文へ書くこと、承認の要らない操作の名指しの3点の記述を確認する。

## agent-toolkit/skills/atk-mode/SKILL.md：作業と手順書：2026年11月27日

`ca7975548^`（`a5f218742`）と `f13095c07` の追跡対象を比較した。
スキル名は両版とも同じ13件で、起動の移設や改名は見つからなかった。
起動方法と役割文書の配置、代表場面の判断を別に検収した。

| スキル | 初動 | 条件付きの資料・境界 | 旧版からの変更と維持 |
| --- | --- | --- | --- |
| `adversarial-review` | 通常のレビューか敵対的レビューかを分ける | 重要・異論のある設計で複数モデルを使う | 本文は同一 |
| `bugfix` | 期待と観測を比べる | 原因、再発、類似、CIの条件から個別資料へ進む | 5問を7問に分け、類似見直しを `spread.md` から `similar-review.md` に変更、原因・再発・検証は維持 |
| `check-execution` | 比較元と直接影響を定める | 診断、受入、性能は該当する操作だけで参照する | 目的を追加、実行前の条件と4つの相対参照は同じ |
| `explain` | 読者と問われた仕組みを確かめる | 不具合の診断は `bugfix` に渡す | 本文は同一 |
| `fetch-sources` | 外部資料の原本を取得する | 資料種別と取得失敗で次の方法を選ぶ | 切り詰めを未取得と明記、資料種別の選択と既存の参照を維持 |
| `formal-verification` | 害と通常テストで届く範囲を比べる | 並行・認可など対象の性質から検証器を選ぶ | 判定先を運用規範で選ぶよう変更、害・検証範囲・費用による比較は維持 |
| `harden-from-history` | 履歴から繰り返した失敗を探す | 1件の修正は `bugfix` に渡す | 選択の優先順を効果・費用の比較へ変更、`prevention.md` は維持 |
| `review-standards` | 原要求と役割から初動を分ける | 実行レビューは `reviewer-execution.md`、指摘の採否は `reviewee.md` | `reviewer.md`、`reviewer-execution.md`、`reviewee.md` を索引から役割別の初動へ移設 |
| `search` | 母集団と検索手段を決める | 不在・件数は完全性の基準を使う | 目的を追加、検索手段と完全性の条件は同じ |
| `secret-files` | 作業ツリー内外と秘匿対象を区別する | 作業ツリー外の読取は値を出力しない | 本文は同一 |
| `verify-skill` | 既存の検証手順を確かめる | 実行できる手順がないか、既存手順に不備がある時に生成・保守する | WebとネイティブGUIの証拠を区別、既存手段を先に探す条件は同じ |
| `writing-standards` | 作業をコードと文章に分ける | 初回のコード編集は `implementation-time.md`、個別の言語と操作は索引 | `implementation-time.md` と `writing.md` を索引から初動へ移設、UIと版数判断の参照を追加 |
| `atk-mode` | 公開の作業名から `playbooks/`、委譲の役割から `roles/` へ進む | 共通操作は次の索引、実行レビューとWI起草は役割文書 | 作業名・起動方法・親子の役割を維持、使用案内を `usage-guidance.md` に分離、実ホストの配送は未確認 |

同じ入力を版ごとに別セッションへ渡し、Codex CLI 0.172.0とGPT-6.2 Sol / Mediumで各条件を1回ずつ比較した。
合否は取得結果に出た本文と、判断で使った基準から判定した。

| 場面 | 旧版・現行版で観測した判断 | ツール結果の件数（旧 → 現行） | 結果本文のバイト数（旧 → 現行） |
| --- | --- | ---: | ---: |
| 初回編集 | 説明だけのコメントを省き、実行系が読む PEP 723 は残す | 11 → 9 | 194364 → 183139 |
| 実行レビュー | 基点版の検証は現行HEADの完成条件を証明しない | 17 → 14 | 234586 → 202866 |
| 指摘の採否 | 返却を原要求と比べ、対象集合を縮めない | 19 → 12 | 315921 → 282514 |
| WI起草 | 改修せず、ユーザーが検証結果の範囲を判断できるAWIを起草する | 18 → 19 | 443854 → 306018 |
| 既読・条件不成立 | 同じ版を取り直さず、関係のない資料を読まない | 0 → 0 | 0 → 0 |

結果本文には規範以外の検索結果や診断も含む。
モデルの入力トークンではない。
13件の `SKILL.md` のUTF-8全文は111749バイトから118113バイトに変わった。
スキル一覧が配る説明の元になる `description` の抽出量は両版7239バイトで、実ホストが配送したバイト数は未計測である。
両版とも代表4場面で目的資料に届いたため、過去の未到達が減ったという結論は得られない。
今回の比較だけを理由に、新しい読込監視やスキルの変更は加えない。

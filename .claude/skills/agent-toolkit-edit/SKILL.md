---
name: agent-toolkit-edit
user-invocable: false
description: >
  `agent-toolkit/`・`agent-basics/`配下のプラグイン（スキル・サブエージェント・フックスクリプト・marketplace記述）、
  `agent-toolkit/rules/`配下のルールファイル（配布先`~/.claude/rules/agent-toolkit/`）、
  `.claude-plugin/marketplace.json`を編集するときに使う。
  版数更新・marketplace管理・セッション状態フラグの扱いを含む。
---

# agent-toolkit（Agent Plugins・Claude Code・Codex・Antigravity）

本スキルは、`agent-toolkit/`配下の配布物や`.claude-plugin/marketplace.json`を編集するエージェントに向けて、ファイル構成、参照関係、付帯作業の扱い、バージョン更新、配布および同期に関する規約を提供する。
エージェント向け文書の改訂全般に共通する手順（編集前に確認すべき記録や、規範を削除・縮小する際の消失確認など）は、`dotfiles-norm-edit`で扱う。

## 名指し参照の読込方法

本文の各参照の適用条件を確認し、選んだ資料の指定範囲を取得してから、その基準に基づく具体的な判断へ進む。起動時の行で指定された資料は、起動時の本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../dotfiles-norm-edit/SKILL.md` | 判断 | `read:*/skills/dotfiles-norm-edit/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../dotfiles-repo-layout/SKILL.md#「ディレクトリ構造に関する注意点」` | 判断 | `section:*/skills/dotfiles-repo-layout/SKILL.md#「ディレクトリ構造に関する注意点」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/agent-documents-additions.md#「規範追記時の判定」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/agent-documents-additions.md#「規範追記時の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/agent-documents-basics.md#「責務と構成」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/agent-documents-basics.md#「責務と構成」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:review-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/review-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:writing-standards/references/investigation.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/investigation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/agent-skills.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-skills.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/agent-documents-basics.md#「定義の所有者」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/agent-documents-basics.md#「定義の所有者」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:plan-mode/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/plan-mode/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../dotfiles-development/SKILL.md#「開発手順」` | 判断 | `section:*/skills/dotfiles-development/SKILL.md#「開発手順」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:writing-standards/references/delivery-scope.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/delivery-scope.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:writing-standards/references/session-state-and-flags.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/session-state-and-flags.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:writing-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/SKILL.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| WI処理の工程や運用を担うスキル、`share/`配下の`<役割名>.subagent.md`、または`atk wi`の実装を編集する前 | `agent-toolkit:workflow-overview`を起動する。ユーザーの判断、操作、観測結果が変わる場合のみ、対応する`docs/guide/`節も読む | 判断 | `read:agent-toolkit:*/skills/workflow-overview/SKILL.md` | |
| `agent-toolkit/agent_toolkit/agents_server_mcp.py`、`agent-toolkit/agent_toolkit/_agents_server/`配下、または`rust/claude-statusline/src/agents_server.rs`の実装を変更・調査する前 | `references/agents-server-shared-state.md` | 判断 | `read:*/skills/agent-toolkit-edit/references/agents-server-shared-state.md` | |
| `agents_server`が起動した委譲先が動作しない事象（起動失敗、初期化未到達、委譲先の無応答、結果の欠落）を調査する前 | `references/agents-server-investigation.md` | 判断 | `read:*/skills/agent-toolkit-edit/references/agents-server-investigation.md` | |
| `agent-toolkit/agent_toolkit/_atk/serve/static/`配下のCSS・HTML・JavaScriptを変更する前 | `references/atk-serve-static.md` | 判断 | `read:*/skills/agent-toolkit-edit/references/atk-serve-static.md` | |
| 次のいずれかを変更する前、および作業ツリーで改訂した配布物の挙動を確認する前: `agent-toolkit/agent_toolkit/`・`agent-toolkit/scripts/`の配置とインポート、`atk`のサブコマンドと出力、MCPサーバー識別子とツール名、フックの実装と登録、プラグイン内の実行時パス、権限設定、marketplaceと配布の手順 | `references/distribution-and-hooks.md` | 判断 | `read:*/skills/agent-toolkit-edit/references/distribution-and-hooks.md` | |
| `agent-toolkit/`配下を変更対象に含む計画を起草する前 | `references/version-bump.md`の「plan modeでの取り扱い」節 | 判断 | `section:*/skills/agent-toolkit-edit/references/version-bump.md#「plan modeでの取り扱い」` | |
| 全レーンの完了後にバージョンを更新する時点、および`references/version-bump.md`の手順へ入る前 | `agent-toolkit/skills/process-wi/references/finish-session.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session.md` | |

## ファイル構成と参照方向

- `agent-toolkit/`: Agent Plugins、Claude Code、Codexが共有するプラグインルート。
- `agent-basics/`: 成果物の品質スキルを独立して配布するプラグインルート。toolkitからbasicsへの一方向依存とする。
- `agent-toolkit/rules/`: ルールファイルの配置場所（`01-agent.md`は基本原則、`02-agent-operations.md`は製品横断の実行運用ルール）。メイン、サブエージェント、委譲先のすべてに適用する条文のみを置き、サブディレクトリは作成しない（`scripts/gen_install_files.py`は直下の`*.md`のみを配布一覧へ列挙する）。配布先や同期手順は`dotfiles-repo-layout`「ディレクトリ構造に関する注意点」が扱う。
- `agent-toolkit/share/rules-main.md`・`rules-main.claude-code.md`・`rules-main.codex.md`: メイン向けの共通規範および各ホスト固有の規範。
- `agent-toolkit/share/rules-common.codex.md`: Codexの全エージェント向けの規範。
- `agent-toolkit/share/rules-subagent.md`・`rules-subagent.claude-code.md`: 委譲先向けの共通規範およびClaude Code固有の規範（Codex固有の差分が必要な場合は`rules-subagent.codex.md`を追加する）。振り分け基準は`agent-basics:writing-standards`の`references/agent-documents-additions.md`「規範追記時の判定」に従う。
- 環境変数の命名規則として、配布物内で完結するものは`AGENT_TOOLKIT_<PURPOSE>`、個人環境で完結するものは`DOTFILES_`を接頭辞とする。一覧は`<plugin root>/skills/writing-standards/references/claude-hooks.md`にある。

参照方向は「dotfilesリポジトリ → プラグイン」および「プラグイン ↔ ルールファイル」を許容する。
`pytools/`は同一リポジトリ内のagent-toolkitをeditableインストールして解決するため、`_`で始まるプライベートなサブパッケージもインポートしてよい。プライベートなモジュールを移動または改名する場合は、同一の変更で`pytools/`側のインポートも揃える。
記述の配置場所は、読み込まれる時点と成立に必要な前提条件に基づいて決定する（`agent-basics:writing-standards`の`references/agent-documents-basics.md`「責務と構成」を参照）。特定のツール、データ、命名、CI、運用形態などを前提とする内容はプロジェクト側に置き、それらの固有要素を同種の別要素へ置き換えても判定基準・工程順・終了条件が成立する汎用的な内容のみを配布物の対象とする。

## 付帯作業の扱い

`agent-toolkit/rules/`、`agent-toolkit/skills/`、`agent-toolkit/share/`配下のエージェント向け文書は、エンドユーザーへ配布する成果物そのものである。対象にはルールファイル、`SKILL.md`、`references/`配下の文書、`<役割名>.parent.md`、`<役割名>.subagent.md`が含まれる。
これらの改訂自体を目的とするタスクは主作業として扱い、変更の目的ごとにコミットとWIを分ける。他の機能開発に伴って必要となった文書の改訂や整理は、`agent-toolkit/rules/01-agent.md`で定める付帯作業に該当するため、関連する開発と同一の計画、WI、コミットで完了させる。開発完了後の振り返りによって独立して見つかった作業のみ、別作業として切り出す。

## agents_serverの共有状態

共有状態ごとの保存場所や読み書きの担当モジュールは、`references/agents-server-shared-state.md`に記録されている。保存先、更新する主体、または状態ディレクトリ配下のファイル構成を変更する場合は、同一の変更単位で同文書も更新する。

## agents_serverの委譲不具合の調査

`references/agents-server-investigation.md`は、ログ記録の所在、切り分けの順序、外部プロセスでの再現手順をまとめている。ログの所在、診断項目、または委譲先CLIへ渡す引数を変更する場合は、同一の変更単位で同文書も更新する。

## 配布物としての記述方針

本プラグインはdotfilesユーザー以外にも配布されるため、環境によって異なる可能性のある設定値やディレクトリ構成などの前提は、条件付きで記述する。

- 本文、docstring、コメントには配布物自身の挙動や仕様を記述し、個人用フックとの連携などエンドユーザー環境側の固有設計は含めない。
- agent-toolkit同梱スキルは、`agent-toolkit:review-standards`のようにプラグイン名で修飾した完全名で記述する（努力目標）。`.claude/skills/`のプロジェクトローカルスキルは単体のスキル名とし、サブエージェント名は省略しない完全名称で記述する。
- 配布物が参照する単一情報源（SSOT）は配布物内に配置し、dotfiles固有のファイルや非配布ファイルを参照先に指定しない。例外として、実測値を根拠とする条文が参照する監査記録`docs/development/audit-records.md`は配布物外に置く（失効判定でのみ参照するため）。記録先は`agent-toolkit:writing-standards`の`references/investigation.md`で定めている。
- ファイル編集時に`pytools/claude_hook/pretooluse.py`の固有名チェックが個人環境固有の識別子を検出した場合は、汎用的な表現へ置き換える。
- 配布物スキルの本文では、フックの挙動をエンドユーザーが観測できる結果として示し、ハッシュ値の比較や状態フラグの更新といった内部実装の詳細は記載しない（努力目標）。ただし、状態フラグやフック間連携の仕様をSSOTとして集約している`<plugin root>/skills/writing-standards/references/session-state-and-flags.md`などは例外とする。

スキルやサブエージェントを編集する際は、次の事項を守る。

- 名前付きサブエージェントの定義や委譲プロンプトには、`agent-basics:writing-standards`の`references/agent-skills.md`を適用する。
- 委譲元での起動・受領契約とサブエージェント定義はセットで更新する。単一の呼び出し元からのみ使われる契約はその工程の規範へ統合し、複数の呼び出し元で共有する起動契約は`<役割名>.parent.md`として分離できる。起動設定は`<役割名>.subagent.md`のfrontmatterで管理し、委譲元が起動準備のために定義本文を取得しなくても済む構造を保つ。
- 独立して読み込まれる文書間で内容が重複する場合の採否は、`agent-basics:writing-standards`の`references/agent-documents-basics.md`「定義の所有者」に従う。
- 実行レビューの契約を変更する場合は、`agent-toolkit:process-wi`側と、`agent-toolkit:plan-mode`でメインが委譲元となる側の双方を同一の変更単位で更新する（両系統は共通化されていない）。

## スキル間の連携

スキル間の依存関係は、`single-lane-process`側が共通契約を参照して上書き内容を定義する一方向依存とする。共通側の改訂負担を減らすため、`agent-toolkit/skills/single-lane-process/`以外の`agent-toolkit/`配下の文書では`single-lane-process`を名指しで参照しない（努力目標）。ただし、起動コマンドを案内する`docs/`配下の解説や方針記録はこの対象外とする。

## バージョン更新

本節は`agent-toolkit/`配下（agent-toolkitプラグイン配布物）のみを対象とする。詳細は`references/version-bump.md`を参照する。
バージョン番号を更新しないままでは、`claude plugin update`を実行しても「最新です」と応答されて更新が配信されない。そのため、エンドユーザーに届く変更はプッシュ前に必ずバージョンを更新する。

- バージョン番号は`scripts/agent_toolkit_bump.py {patch|minor|major}`で更新する。更新区分の判断は`references/version-bump.md`「判定基準」を、実行主体とタイミングは同文書「plan modeでの取り扱い」に従う（`agent-toolkit:process-wi`の各レーンでは計画に区分を記録するのみとし、終端担当が全レーンのマージ後に1回だけ実行する）。rebaseやmergeの競合は同文書「競合解決と統合後の確認」に従って解決する。
- `version`と`description`は2箇所の定義元で常に一致させる（`description`は手動で同期する）。定義元は、`agent-toolkit/.claude-plugin/plugin.json`と、`.claude-plugin/marketplace.json`の`plugins[]`内にある`name == "agent-toolkit"`のエントリである。この整合性は`manifest_ssot_invariant_test.py`によって検証される。
- Agent Plugins向けの`plugin.json`・`mcp.json`やCodex向けマニフェストは、上記2ファイルと`agent-toolkit/.mcp.json`をもとに`scripts/sync_codex_plugin_manifests.py`が生成する。Antigravity向けの`agent-toolkit-agy/`および`agent-basics-agy/`は`scripts/sync_antigravity_plugin.py`が生成し、`post_apply`が`~/.gemini/config/plugins.json`へ登録する。`--plugin agent-basics`を指定すると品質プラグインのみを生成できる。元の名前空間と品質本文を保持したまま、両配布物の長文スキルを同一の短いSKILL.md・全文資料生成処理へ渡す。これらの生成ファイルは手動で編集せず、生成元ファイルを編集した上で再生成する。
- 動作検証は、`dotfiles-development`「開発手順」に示された特定ファイル限定の実行形式を用い、SSOTテストを含めて実施する。

## 同期先ドキュメント

- engineを追加する際は、委譲先と対話ホストのそれぞれへの導入要否を判断し、採用した配布範囲と導入を見送った理由を構成・設計記録に残す。
- 複数ファイルにまたがる機構や委譲構造を新設・変更する場合は、`docs/development/design.md`の索引から該当する主題の本文を選び、目的、構造を採用した理由、知識境界、却下した代替案を追記または更新する（努力目標）。新たな主題は主題別ファイルを作成して索引テーブルに行を追加し、索引ファイル自体には詳細な見出しや機構の解説を書かない。
- `agent-toolkit/skills/workflow-overview/SKILL.md`はエージェントの作業工程に関する契約を定義している。ユーザーの運用形態、WIの登録方法、または回答操作が変わる場合は、`docs/guide/claude-code-guide.md`「推奨ワークフロー」も同一の変更単位で揃える。
- `docs/guide/claude-code-guide.md`「設定確認」は、ユーザーが実行する確認コマンド、期待される結果、または復旧手順が変わる場合に更新する。
- `install-claude.sh`の`FILES`、`install-claude.ps1`の`$files`、および`agent-toolkit/rules/`配下のMarkdownファイル一覧は完全一致を保つ。この整合性は`install_script_ssot_invariant_test.py`で検証し、`uv run python scripts/sync_generated_files.py`で同期する。
- 配布物スキルの外部インターフェース（判定区分、出力フォーマット、後始末コマンドの分岐、サマリー表現など）を追加・削除・変更する場合は、呼び出し元を検索し、連携先の記述も同一の計画内で更新する。
- `agent-toolkit/rules/01-agent.md`・`02-agent-operations.md`および`agent-toolkit/share/rules-common.codex.md`は、`.chezmoi-source/dot_codex/AGENTS.md`の生成元ファイルである。同期は`uv run python scripts/sync_generated_files.py`で行い、計画への記載は`references/version-bump.md`「plan modeでの取り扱い」の派生物の規則に従う。
- `agent-toolkit/share/rules-main*.md`や`rules-subagent*.md`の編集はファイルの自動生成や配布一覧の変更を伴わないが、プラグインのバージョン更新は必要である。各起動区分へどの規範が届くかは、`agent-toolkit:writing-standards`の`references/delivery-scope.md`にある配送範囲表を参照する。軽量なagents_server委譲先へ`rules-subagent.md`を配信しない境界条件はテストで保持されている。
- 計画ファイルの見出し、固定H3、表の行名は、`agent-toolkit/agent_toolkit/_plan/structure/constants.py`の構造定数で定義されている。これらを改訂する場合は構造定数を変更し、同一名称を使用している以下の文書も同一の変更単位で揃える。
  - `agent-toolkit/skills/plan-mode/references/plan-file-standards.md`
  - `agent-toolkit/share/`配下の`<役割名>.subagent.md`
  - `docs/development/`配下の該当設計資料および`concepts.md`
  ※ユーザーの操作や画面の観測結果が変わる場合のみ、対応するガイドを更新する。改訂前の名称は読み取り互換用の構造定数として残し、新規作成や改訂のバリデーション処理でのみエラーとする。
- 構造定数を持たず、`plan-file-standards.md`のみで必須とされている見出しについては、同ファイルの記述に従う。稼働中の計画が自動チェックで不合格になるのを防ぐためにこの形式を採用した場合は、その理由を計画ファイルへ記録する

## セッション状態フラグ

`agent-toolkit`プラグインで管理する全フラグ一覧のSSOTは、`agent-toolkit:writing-standards`の`references/session-state-and-flags.md`である。フラグを追加・変更する際は、必ず同ファイルを更新する。
フックの実装・編集やセッション状態の設計・変更する際は、`agent-toolkit:writing-standards`を起動して参照する（フック実装の基準を含む。エージェントの行動規範自体は役割別ルールまたは作業別スキルに置く）。

## agent-basicsとの責務境界

成果物の品質基準は`agent-basics/skills/`へ配置し、toolkit側からは品質スキルと自身の運用契約を参照する。basics側には常駐フック、MCP、ホームディレクトリ向けの運用規範を含めない。basicsのオリジナルマニフェストは`agent-basics/.claude-plugin/plugin.json`であり、掲載原本は同一marketplace内のagent-basicsエントリである。独立したバージョン番号と説明文（description）の2箇所を揃え、ルートマニフェストおよびCodexマニフェストは既存の生成スクリプトで再生成する。toolkitのClaude依存関係の配列を、Agent Pluginsのルートマニフェストへ複製することは避ける。

agent-toolkitのリソースを編集する前は`agent-toolkit:writing-standards`も適用し、同スキルの読込表から配送、状態、起動、記録に関する運用条件を確認する。すでに取得済みの品質に関する記述は再利用する。

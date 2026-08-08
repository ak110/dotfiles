# バージョン更新の詳細手順

`.claude/skills/agent-toolkit-edit/SKILL.md`「バージョン更新」節の詳細手順を集約する。
本節のバージョン更新規定は`agent-toolkit/`配下（agent-toolkitプラグイン配布物）のみを対象とする。
`.chezmoi-source/`配下のchezmoi配布物・`bin/`配下のCLIラッパー・`scripts/`配下のヘルパースクリプトは
本規定の対象外とし、`agent_toolkit_bump.py`も更新しない。

## 判定基準

利用者に届く振る舞いが変わるものは必ずbumpし、判断に迷う場合はbumpする
（pre-1.0であれば頻繁にMINORを更新しても問題ない）。
`git commit`時に`agent-toolkit/`配下の変更を含みつつ`plugin.json`の`version`未変更の場合、
`agent-toolkit/scripts/pretooluse.py`の検知フックが`warn`を返す。bump不要に該当する場合は警告を無視して進める。
以下いずれにも該当しない場合はbumpしない
（例: コメント・docstringのみ／`*_test.py`のみ／入出力が不変なリファクタリング／誤字・スタイル調整）。

- PATCH（`+0.0.1`）: 軽微な修正（フックスクリプト・entry pointロジック変更／
  軽微な検出パターン追加・除外パターン追加（新規checkが検出範囲を大幅に広げる場合はMINOR）／
  `hooks/hooks.json`の`matcher`・`command`変更／依存・実行環境要件の変更／
  軽微なallowlist追加・削除（allowlist方針の抜本変更はMINOR）／
  スキル・ルールファイルへの1件あたり数行〜1節相当の分量の規範文追記・条件補強・例示追加
  （新設見出しを伴わない限り、追記が複数節に跨っても本区分に含める。MINOR例示を参照）／
  メッセージ変更／バグ修正／検出漏れの修正）。
  軽微／大幅の判定は、検出パターン追加時は影響を受ける利用者範囲
  （該当checkが新規に検出する差分件数・対象ツール数・対象ファイル種別数など具体的な数値尺度）、
  allowlist変更時は方針の抜本変更に該当するかで判断する
- MINOR（`+0.1.0`）: 機能追加・検出範囲の大幅拡大・descriptionが変わる規模など、規模の大きい変更に限定
  （description文言変更・トリガーキーワード追加・節新設、または単一ファイル内で複数の新設見出しに
  跨る規範改訂）。既存の見出し配下への規範文追記・条件補強・例示追加を複数追加するだけの変更は、
  跨る節数によらずPATCH判定とする。
  複数ファイルへそれぞれ単一節分の追記をする変更は、各ファイル単位でPATCH判定の対象とする
- MAJOR（`+1.0.0`）: ユーザーからの明示的な指示がない限り行わない

## 競合解決

rebase・merge時に`version`フィールドが競合した場合、次の手順で解決する。

1. 競合した各候補の`version`値を`(major, minor, patch)`の数値タプルとして比較する
2. タプルが大きい方を採用する
   （MINOR更新は`minor`を増分し`patch`を0へ戻すため、同一の基準版から派生した候補である限り、
   数値タプル比較だけで更新区分が上位の候補が自動的に選ばれる。個別に更新区分を判定し
   基準版を特定する手順は不要である）。採用した候補が既にリモートへpush済みの公開版であり、
   かつ自分側の未公開コミットが利用者振る舞い変更を含む場合、採用値をそのまま使うと当該変更が
   version未変更のまま配信対象から漏れる。この場合は「未プッシュ範囲での統合」節の
   「`git push`実行後の追加commitは新たな未プッシュ範囲の開始として扱う」規定に従い、
   採用値からさらに「判定基準」節に基づく追加のbumpを実施する
3. 採用した値（前項の追加bumpを実施した場合はbump後の値）を、Claude Code向け正本2ファイル
   （`agent-toolkit/.claude-plugin/plugin.json`・`.claude-plugin/marketplace.json`）へ反映する
4. 正本2ファイルの反映後、`scripts/sync_codex_plugin_manifests.py`で
   Codex向け派生manifest（`agent-toolkit/.codex-plugin/plugin.json`）を同期する
5. 同期後、対象3ファイル（正本2ファイルとCodex向け派生manifest）の`version`値が
   一致することを`grep -n version`等で確認する

## 統合後の同値確認

rebaseまたはmerge後はGit競合の有無にかかわらず、公開済みの統合先と現在の正本の`version`値を比較する。
自分の未公開コミットに利用者の振る舞いを変えるplugin変更が残り、両者の値が同じ場合は、
統合後の版を基準として「判定基準」節に従うbumpを再実行する。
この確認により、別の作業が同じ版へ先にbumpし、Gitが非競合として統合した場合も配信漏れを防ぐ。

再bump後はClaude Code向け正本2ファイルを確認し、`scripts/sync_codex_plugin_manifests.py`で
Codex向け派生manifestを同期する。
対象3ファイルの`version`値が一致することを確認してから後続の検証へ進む。

## 未プッシュ範囲での統合

未プッシュコミットが既に1回以上bumpを含む場合、後続編集ごとに追加でbumpしない。
`scripts/agent_toolkit_bump.py`は既存bump以下の指定をno-op扱い、
既存bumpがPATCHで後続編集がMINOR相当なら`agent_toolkit_bump.py minor`で上書き格上げする。
レビュー判定も未プッシュコミット範囲の累積bumpで対応済みを判定する。
計画でMINOR bumpを宣言していても当該コミット単体ではversion変更が無いケースがある。

`git push`実行後の追加commitは新たな未プッシュ範囲の開始として扱う。
当該追加commitが利用者振る舞い変更を含む場合はbumpを再度実施する。
push済みコミット範囲の既往bumpは判定対象に含めない。

同一push cycle内で同種以上のbumpが未pushで累積している場合、
`scripts/agent_toolkit_bump.py`はno-op出力で正常終了する。
複数計画の直列消化中はこれが期待動作であり、bump欠落扱いにしない。

既往bumpの自動検出に使う基準版は、上流ブランチ（`@{u}`）、次に`origin/HEAD`の順で解決する。
作業用の複製で追跡先が失われている場合も`origin/HEAD`から取得できる。
いずれからも解決できない場合は増分せず非ゼロ終了するため、未検出のまま重複増分することはない。

## Codex導入後のroot再照合

Codexプラグインの導入版が変わった場合、保持済みのplugin rootを再利用してはならない。
次の手順で現行rootを再照合してから、`atk.py`や`check_plan_file.py`などの後続スクリプトを実行する。

1. `codex plugin list --json`の出力をJSONとして構造解析する
2. `pluginId == "agent-toolkit@ak110-dotfiles"`の完全一致項目を1件取得し、導入版を確認する
3. 導入版が単一のdirectory名であり、path separatorまたはdot segmentを含まないことを確認する
4. 導入版が保持済みの版と同じ場合は、保持済みrootを再解決しない
5. 導入版が変わった場合は、保持済みrootを破棄する
6. 保持していた導入rootの親plugin cache directoryと`pluginId`のmarketplace名・plugin名から、新versionの兄弟cache directoryを組み立てる
7. 候補pathをstrict resolveし、保持済みrootの親plugin cache directory配下であることを確認する
8. 再解決したroot配下の`.codex-plugin/plugin.json`、`scripts/atk.py`、`skills/plan-mode/scripts/check_plan_file.py`の実在を確認する
9. plugin manifestの`version`と導入版が一致することを確認してから、再解決したrootを後続スクリプトへ渡す
10. writerは完了報告へ導入版、再解決したrootの絶対パス、plugin manifestと必要スクリプトの確認結果を含める

`source.path`は配布元を示す値であり、導入cache rootの更新には使用しない。
完全一致項目、導入版、directory名検証、cache directory、親directory配下の確認、必要ファイル、plugin manifestのいずれかを確認できない場合は、旧rootへフォールバックせず未完了として扱う。

## plan modeでの取り扱い

計画フェーズではbump要否や既存bumpとの差分を調査せず、種別（PATCH／MINOR／MAJOR）と
「判定基準」節に基づく種別選定根拠を実装者向け領域へ記述する。
具体的なversion数値（`x.y.z`形式）は書かず`scripts/agent_toolkit_bump.py`の実行結果に従う。
判定は計画段階で`### 対象ファイル一覧`と実装者向け領域から目視照合する。
実装フェーズで`scripts/agent_toolkit_bump.py {種別}`を実行する
（既存bumpとの統合はツール側が吸収するため`git log`確認は不要）。
`agent-toolkit/scripts/pretooluse.py`の`agent-toolkit/`配下変更検知フックが`plugin.json`版未変更をwarnで返す。
補完照合の対象は`agent-toolkit/`配下に限定し、`.chezmoi-source/`・`bin/`・`scripts/`配下は対象外。
計画ファイルの実装者向け領域には検証より前に
`scripts/agent_toolkit_bump.py {patch|minor|major}`を実行する順序を含める。
bump不要の場合は、同領域へ`bump不要`と根拠を記載する。
version bumpを伴う計画では、Claude Code向け正本2ファイルを`### 対象ファイル一覧`へ必ず含める。
正本は`agent-toolkit/.claude-plugin/plugin.json`と`.claude-plugin/marketplace.json`である。
Codex向け派生manifestも`### 対象ファイル一覧`へ含める。
Codex向けmanifestは`agent_toolkit_bump.py`の直接更新対象に含めない。
Claude Code向け正本2ファイルを更新した後、`scripts/sync_codex_plugin_manifests.py`で反映する。

## 新規CLI公開時の疎通経路確認

配布物プラグインで新規CLI・新規コマンド・新規ラッパースクリプトを公開する変更を対象とする計画では、
計画段階の`### 対象ファイル一覧`に利用者環境での疎通経路を含める。
対象経路はインストールスクリプト・post-apply処理・PATH配置手法・bash補完登録・Windowsペアファイル同期を指す。
判断基準は、プラグイン単体利用者がPATH追加・環境変数の設定以外の追加設定なしで新CLIを起動できるかとする。

配布物プラグインが`bin/`配下でCLIを提供する場合の実配置先は次のパスとなる。
`~/.claude/plugins/cache/<marketplace>/<plugin-name>/<version>/bin/<cli>`で`<version>`は更新ごとに変わる。
dotfiles配布利用者は`.chezmoi-source/dot_bashrc`のPATH追加で吸収する。
プラグイン単体利用者は`install-claude.sh`/`install-claude.ps1`側で動的解決ラッパーを`~/.local/bin/<cli>`に配置する。
ラッパーは`ls -d ~/.claude/plugins/cache/*/<plugin-name>/*/bin | sort -V | tail -1`で最新バージョンを実行時解決する。

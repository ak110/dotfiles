---
# 同期注記: 「判定」列の`bump不要`ラベル定義は
# `agent-toolkit/skills/plan-mode/references/integrity-checks.md`
# 「編集対象スキル固有規定の事前適用」節と意図的に重複する（改訂時は両ファイルを同時更新する）。
---

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
  スキル・ルールファイルへの数行〜1節規模の規範文追記・条件補強・例示追加／メッセージ変更／バグ修正／検出漏れの修正）。
  軽微／大幅の判定は、検出パターン追加時は影響を受ける利用者範囲
  （該当checkが新規に検出する差分件数・対象ツール数・対象ファイル種別数など具体的な数値尺度）、
  allowlist変更時は方針の抜本変更に該当するかで判断する
- MINOR（`+0.1.0`）: 機能追加・検出範囲の大幅拡大・descriptionが変わる規模など、規模の大きい変更に限定
  （description文言変更・トリガーキーワード追加・節新設・単一ファイル内で複数節に跨る規範改訂）。
  複数ファイルへそれぞれ単一節分の追記をする変更は、各ファイル単位でPATCH判定の対象とする
- MAJOR（`+1.0.0`）: ユーザーからの明示的な指示がない限り行わない

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

## plan modeでの取り扱い

計画フェーズではbump要否や既存bumpとの差分を調査せず、種別（PATCH／MINOR／MAJOR）と
「判定基準」節に基づく種別選定根拠を`### エージェント判断`欄へ記述する。
対象ファイル×H2/H3節数マトリクスも同欄へ並記する。
書式は`agent-toolkit/skills/plan-mode/references/integrity-checks.md`「編集対象スキル固有規定の事前適用」節に従う。
具体的なversion数値（`x.y.z`形式）は書かず`scripts/agent_toolkit_bump.py`の実行結果に従う。
判定は計画段階で対象ファイル一覧と変更内容から目視照合する。
実装フェーズで`scripts/agent_toolkit_bump.py {種別}`を実行する
（既存bumpとの統合はツール側が吸収するため`git log`確認は不要）。
`agent-toolkit/scripts/pretooluse.py`の`agent-toolkit/`配下変更検知フックが`plugin.json`版未変更をwarnで返す。
補完照合の対象は`agent-toolkit/`配下に限定し、`.chezmoi-source/`・`bin/`・`scripts/`配下は対象外。
計画ファイル本文の`## 実行方法`には検証ステップの手前へ
`scripts/agent_toolkit_bump.py {patch|minor|major}`の実行ステップを必ず含める（bump不要時のみ省略可）。
bump不要と判定した計画では、`## 対応方針`配下の`### エージェント判断`節に版更新マトリクス（5列表）を配置し「判定」列の全行に`bump不要`を記載する。
この場合`_plan_format.py`のSSOT関数がbumpステップ記載を求める警告とmanifest記載欠落の警告を抑止する。
マトリクス自体が欠落している場合の警告は現行どおり維持する。
version bumpを伴う計画では、Claude Code向け正本2ファイルを`## 変更内容`の対象ファイル一覧へ必ず含める。
正本は`agent-toolkit/.claude-plugin/plugin.json`と`.claude-plugin/marketplace.json`である。
Codex向け派生manifestも対象ファイル一覧へ含める。
Codex向けmanifestは`agent_toolkit_bump.py`の直接更新対象に含めない。
Claude Code向け正本2ファイルを更新した後、`scripts/sync_codex_plugin_manifests.py`で反映する。

## 新規CLI公開時の疎通経路確認

配布物プラグインで新規CLI・新規コマンド・新規ラッパースクリプトを公開する変更を対象とする計画では、
計画段階の対象ファイル一覧に利用者環境での疎通経路を含める。
対象経路はインストールスクリプト・post-apply処理・PATH配置手法・bash補完登録・Windowsペアファイル同期を指す。
判断基準は、プラグイン単体利用者がPATH追加・環境変数の設定以外の追加設定なしで新CLIを起動できるかとする。

配布物プラグインが`bin/`配下でCLIを提供する場合の実配置先は次のパスとなる。
`~/.claude/plugins/cache/<marketplace>/<plugin-name>/<version>/bin/<cli>`で`<version>`は更新ごとに変わる。
dotfiles配布利用者は`.chezmoi-source/dot_bashrc`のPATH追加で吸収する。
プラグイン単体利用者は`install-claude.sh`/`install-claude.ps1`側で動的解決ラッパーを`~/.local/bin/<cli>`に配置する。
ラッパーは`ls -d ~/.claude/plugins/cache/*/<plugin-name>/*/bin | sort -V | tail -1`で最新バージョンを実行時解決する。

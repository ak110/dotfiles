# バージョン更新の詳細手順

`.claude/skills/agent-toolkit-edit/SKILL.md`「バージョン更新」節の詳細を集約する。
`.chezmoi-source/`配下のchezmoi配布物、`bin/`配下のCLIラッパー、`scripts/`配下のヘルパースクリプトだけの変更は対象外とし、版数を据え置く。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:commit/references/publish.md#「版数と生成物」` | 判断 | `section:agent-toolkit:*/skills/commit/references/publish.md#「版数と生成物」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |

## 判定基準

エンドユーザーに届く振る舞いが変わるものは必ずbumpし、迷う場合もbumpする。
コメント・docstringのみ、`*_test.py`のみ、入出力が不変なリファクタリング、誤字・スタイル調整は版数を据え置く。

- PATCH（`+0.0.1`）: 軽微な修正（不具合・フック・検出・文言、既存見出し内の規範の補強など）
- MINOR（`+0.1.0`）: 機能追加・検出範囲の大幅拡大・description変更・節新設など、規模の大きい変更
- MAJOR（`+1.0.0`）: ユーザーの明示指示がある場合だけ（規定は`agent-toolkit:commit`の`references/publish.md`「版数と生成物」）
- 現行版が`major.minor.patch`の数値3要素で表せない場合は、プロジェクト固有の対応表かユーザーの明示指定がある場合だけ区分を適用し、無ければbumpせず判定不能として報告する

## 競合解決と統合後の確認

rebase・merge時に`version`が競合した場合は、`(major, minor, patch)`の数値タプルが大きい方を採用する。採用した値は定義元2ファイル（`agent-toolkit/.claude-plugin/plugin.json`・`.claude-plugin/marketplace.json`）へ反映する。続けて`scripts/sync_codex_plugin_manifests.py`で派生manifestを同期する。整合は`scripts/sync_codex_plugin_manifests.py --check`（最新なら0、不整合なら1、引数誤用なら2）で確かめ、同スクリプトの判定に一本化する。

rebase・merge後は競合の有無にかかわらず、公開済みの統合先と定義元の`version`を比較する。未公開のcommitにエンドユーザーの振る舞いを変える変更が残り値が同じなら、統合後の版を基準に再bumpする（別の作業が同じ版へ先にbumpし、非競合で統合されると配信されないため）。

## 未プッシュ範囲での統合

`scripts/agent_toolkit_bump.py`は未プッシュ範囲の既存bump以下の種別指定をno-opにするため、後続編集ごとの追加bumpは不要で、格上げが要る場合だけ上位種別を指定する。`git push`後の追加commitは新たな未プッシュ範囲として扱う。

## Codex導入後のrootの再確認

Codexプラグインの導入版が変わった場合は、保持済みのplugin rootを破棄して再解決する。
`codex plugin list --json`の結果をプログラム内で取得し、`installed`の`pluginId == "agent-toolkit@ak110-dotfiles"`を一意に選別して、識別子・marketplace・version・有効状態とrootの解決結果だけをモデルへ返す。
取得失敗、対象なし、対象が一意でない場合、無効化、実体の欠落を区別して診断し、全一覧は返さない。
plugin cache directory配下の新versionのrootを解決し、そのrootの`skills/`、plugin manifestの`version`と導入版の一致、使うスクリプトの実在を確かめてから使う。
確かめられなければ未完了とし、旧rootへ戻さない（`source.path`は配布元を示す値であり、cache rootの解決には新versionのrootを使う）。
起動通知が示した変更前のplugin rootは、導入版を変更する前の処理に使う。

## plan modeでの取り扱い

単一のworktreeで実装する計画には本節の一般則を、`agent-toolkit:process-wi`のレーンには末尾の特則を適用する。

- 計画の`## 要件・外部仕様`へ種別（PATCH／MINOR／MAJORまたは`bump不要`）と「判定基準」に基づく根拠を書く。具体的な版数と既存bumpとの統合は`scripts/agent_toolkit_bump.py`へ委ねる。bumpする実装では検証前に`scripts/agent_toolkit_bump.py {種別}`を実行する
- bumpを伴う計画では、差分の有無を問わず定義元2ファイルを変更説明へ含める。生成器が保証する派生物（派生manifest、`agent-toolkit/rules/`から生成する`.chezmoi-source/dot_codex/AGENTS.md`など）は変更説明へ列挙せず、正式な生成コマンドと生成器出力との一致確認を`## 検証`へ書く。派生manifestは定義元の更新後に`scripts/sync_codex_plugin_manifests.py`で反映し、`--check`で確かめる
- bumpの完了条件は、公開済み基準（`git push`済みの最新版のplugin manifest）に対して要求種別以上のbumpが含まれ、定義元2ファイルと派生manifestの`version`が一致することとする。既存の未プッシュbumpで足り、`scripts/agent_toolkit_bump.py`が無変更・終了コード0で終わる場合も完了とする

`agent-toolkit:process-wi`の特則:

- 各レーンはbump種別（`bump不要`を含む）と根拠、MAJORなら認可根拠を計画へ記録し、版数更新コマンドを実行しない
- AWI本文の版数区分は起草時にその要求だけを見た判定である。メインは同じ実行で統合した変更全体へ「判定基準」を適用し、上位の区分があればそれを`bump種別`として終端担当へ渡す。終端担当が全レーンのマージ後に`scripts/agent_toolkit_bump.py`を1回実行する
- 版数更新を完成条件に持つAWIは`agent-toolkit/share/lane-integration.subagent.md`の延期`adopt`に従い、終端担当が版数、派生manifest、公開結果を検収した後に終端する
- 統合ブランチのpush前に上流の進行でrebaseした場合は、「競合解決と統合後の確認」の基準で再比較し、必要なら終端担当が再bumpとmanifest同期を行う

## 新規CLI公開時の疎通確認

配布物プラグインで新規CLI・コマンド・ラッパースクリプトを公開する計画では、`## 要件・外部仕様`にエンドユーザー環境で起動できるまでの導入手順（インストールスクリプト、post-apply処理、PATH配置、bash補完登録、Windowsペアファイル）を含める。基準はプラグイン単体のエンドユーザーがPATH追加・環境変数設定以外の追加設定なしで起動できることである。
プラグインの`bin/`の実配置先はバージョン付きcache directoryになる。dotfilesのエンドユーザーは`.bashrc`のPATH追加、プラグイン単体のエンドユーザーは`install-claude.sh`/`install-claude.ps1`が置く動的解決ラッパーでこれを吸収する。

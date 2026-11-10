# dotfilesの検証で使う値：push前のチェックとCIだけが実行するチェック

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:commit/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/commit/SKILL.md` |
| テストとCIの所要基準を使う前 | `../../../../agent-basics/skills/writing-standards/references/testing-speed.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing-speed.md` |
| テストとCIの所要基準を使う前 | `agent-basics:check-execution/references/diagnostics-results.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/diagnostics-results.md` |
| 共有実行枠のコマンドをシェルから起動する前 | `agent-toolkit:/share/check-execution-operations-launch.md` | 判断 | `read:agent-toolkit:*/share/check-execution-operations-launch.md` |

## push前のチェックとCIだけが実行するチェック

公開前は、`agent-toolkit:commit`の「検証とCI」に従い、変更箇所と関連テスト・lint、およびCIが実行しないチェックを確認する。
本リポジトリの全体の試験と静的検査はCIが担う。
本リポジトリでは以下の手順を実行する。

- 最初に環境を同期する: `make sync`を実行する。
  仮想環境を`uv.lock`および`.python-version`の内容に合わせ、CIのクリーンな実行環境と同様の条件で後続のチェックを動かす
- 後続の検査より先に、生成型を前提とする`function-hooks-types`を単独で実行する: `uv run --frozen pyfltr run --commands=function-hooks-types`
  - 型が未展開で終了2になった場合は、`verification-values-scope.md`「変更範囲の検証の値」のFunction hooksモジュールの項に従ってplugin-authoringで型を準備し、`FUNCTION_HOOKS_TYPES`で明示してから同じコマンドを再実行する
  - 成功した結果は同じ型・同じ対象の検査として後段でも再利用し、重複実行を省く
- CIのpyfltrで無効化されている残りのチェックを実行する。
  `uv run --frozen pyfltr run --commands=claude-plugin-validate,statusline-version,plugin-version --enable=statusline-version,plugin-version`

CIの`python-lint (3.15)`は、Pylintとその他のlintの両jobの成功を集約する。
Pylintは`pyfltr ci --commands=pylint`で実行する。
その他は`pyfltr ci --disable=pylint,pytest,claude-plugin-validate,statusline-version,plugin-version,function-hooks-types`で実行する。
Python 3.15のpytestは直下の`pytest (3.15)`と、toolkitを3分割した`pytest-toolkit (3.15, 1/3)`〜`(3.15, 3/3)`が独立して担当する。
必須checkの`pytest-toolkit (3.15)`が全分割の成功を集約する。
以下のジョブはローカルの`make test`の実行対象外である。

- `test-windows`ジョブ: Windows実機環境でのchezmoi適用、Windows固有のテスト、および公開ランチャーの動作確認。
  Windows固有のテストは`windows` markerを付けた試験が選ばれるため、Windows実機で確かめる試験を加えるときはCI定義ではなく試験側へmarkerを付ける
- `update-dotfiles-upgrade (windows)`ジョブ: Windows環境における旧バージョンからのupdate-dotfilesによるアップデート検証
- `test-linux`ジョブ: `install.sh`およびchezmoiの実際の適用確認
- `pytest (3.15)`・`pytest-toolkit (3.15, N/3)`ジョブ: Python 3.15・最低Git 2.34.1で直下とtoolkitのテストを各1回実行し、skills配下と横断不変条件も含める。
  toolkitの試験は`--test-shard`で3分割の各jobへちょうど1回ずつ割り当てる
- `rust-lint`ジョブ: `rust/claude-statusline/`のcargoによる検証
- `plugin-version`ジョブ: `develop`へのpushで、push前の公開済みcommitとagent-toolkit・agent-basicsの配布物の差分と版数を比べる検証
- `browser-e2e`ジョブ: 実際のブラウザーを用いたE2Eテスト（ローカル環境では`make test-browser`で実行可能）

Linux上のOS判定値の模擬は、`../../../../agent-basics/skills/writing-standards/references/testing-platforms.md`「プラットフォーム分岐の検証」に従って実Windowsの保証と分ける。
共有モジュールを変更するfixtureの影響、ランチャーの引数・待機・終了状態の受け渡し、パスAPIの戻り値と実体同一性、起動時の依存解決・環境作成は、ローカルでも先に前提を反証できる。
実WindowsのAPIによる変換や権限・ファイル共有の副作用は実OSの観測と対応付け、準備済み開発環境の成功で配布環境の初回起動を保証しない。
現行CIでは候補branchへのpushによるWindows検証も選べる。
必要性、公開操作の認可、既存結果との重複費用を確認し、追加CIの要否を変更ごとに判定する。
未観測の境界は検証記録へ残し、タイムアウトの内部段階が未確定なら特定原因へ帰属させない。

通常branchのpushではdevelop、PRではPRのbaseとの共通祖先から変更領域を判定する。
`common-ci-owner`の選択結果と理由を基に、Rust・fixtureは`rust-lint`、Python・serveは`browser-e2e`を実行する。
文書だけの変更では両検査を省略できる。
共有設定・未知の変更・比較不能・初回pushとmaster/develop pushは両方を実行する。
選択しない成功と判定の失敗・出力欠落を区別し、必須check名と失敗伝播を保つ。
Pythonのpytest・lint、完全導入と更新検証には領域選択を適用しない。
CIを変更した担当は、同条件の保存結果から実時間とrunner実行量を比較し、公開前に観測できない実CIの比較は公開担当へ渡す。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| dotfilesの検証で使う値：変更範囲の検証の値の基準を適用する前 | `verification-values-scope.md` | 判断 | `read:*/.claude/skills/develop-dotfiles/references/verification-values-scope.md` |
| dotfilesの検証で使う値：検証コマンドの実行の基準を適用する前 | `verification-values-execution.md` | 判断 | `read:*/.claude/skills/develop-dotfiles/references/verification-values-execution.md` |

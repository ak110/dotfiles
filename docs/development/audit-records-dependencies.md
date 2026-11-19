# 監査記録: 依存管理

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-basics/skills/writing-standards/references/dependency-management.md：公開待機設定：2026年9月16日

2026年9月16日、公開直後の新バージョンを待つ目安1日の典拠を確認した。
`.chezmoi-source/dot_config/uv/uv.toml`は`exclude-newer = "1 day"`を持ち、同ファイルのコメントが公開後24時間未満のパッケージを除外する目的を示す。
再検証は同ファイルから`exclude-newer`の値を取得する。
この設定値が変わったときに再検証する。

## agent-basics/skills/writing-standards/references/dependency-management.md：pnpm：2026年9月16日

2026年9月16日、pnpm 11.25.0とnpm 11.19.0で検証した。
環境変数`NPM_CONFIG_REGISTRY`へ`https://example.invalid/`を与えて`pnpm config get registry`を実行すると`https://registry.npmjs.org/`を返した。
同じ環境変数で`npm config get registry`を実行すると`https://example.invalid/`を返した。
再検証はpnpm 11.25.0とnpm 11.19.0に同じ環境変数を与える。
両ツールから実効のレジストリー設定を取得し、環境変数が反映されるか比べる。
再検証の契機はpnpmまたはnpmの更改とする。

## agent-basics/skills/writing-standards/references/dependency-management.md：pnpm：2026年11月6日

- 観測日: 2026年11月6日。
- 観測した版数: pnpm 12.16.1、切替先12.15.0・12.18.0、Node.js 24.22.2、Linux x64。
  HOME・XDG設定とキャッシュ・npm prefix・PNPM_HOMEを隔離し、Corepackを経由せずnpmで初回導入した。
- 再検証手段: [導入](https://pnpm.io/installation)、[lockfile](https://pnpm.io/lockfile)、[self-update](https://pnpm.io/cli/self-update)の公式仕様を対象版と照合する。
  隔離環境で`npm install -g pnpm@12.16.1`を実行し、`packageManager`を固定したプロジェクトでinstallと`pnpm self-update <版>`によるpin・boot文書の同時更新を確認する。
  取得済みの切替先キャッシュを除き、boot文書の対象pnpmパッケージの`integrity`を改ざんして`pnpm install --frozen-lockfile`を実行すると、`ERR_PNPM_PNPM_ENGINE_IDENTITY_MISMATCH`で終了コード1になる。
  通常の`pnpm --version`は検証できない版を実行せず導入済みの版へ戻るため、改ざんの拒否はfrozen installで確認する。
  正しいlockfileを戻して同じ初期状態で成功する対照も確認する。
  公開から4000分未満の版に`pnpm --config.minimum-release-age=4000 self-update <版>`を非対話で実行すると、`ERR_PNPM_NO_MATURE_MATCHING_VERSION`で終了コード1になる。
  再検証時は待機条件内の公開版を選ぶ。
  `packageManager`の`+sha512.<hash>`を除去する挙動は[pnpm 12.18.0のパーサー](https://github.com/pnpm/pnpm/blob/v12.18.0/pnpm/crates/cli/src/cli_args/package_manager.rs)と[pin処理](https://github.com/pnpm/pnpm/blob/v12.18.0/pnpm/crates/cli/src/engine_pm/pin.rs)を照合する。
  pnpmの版管理・署名検証・公開待機の仕様が変わったときに再検証する。

## agent-basics/skills/writing-standards/references/dependency-management.md：Python対応版の追加：2026年10月10日

2026年10月10日の入力WIが保持する観測では、[pytilpackのCI run 38024857168・job 114133431911](https://github.com/ak110/pytilpack/actions/runs/38024857168/job/114133431911)が依存同期の終了1を記録した。
停止した条件はcommit `1de178d2e7fb7e7cbf12135db690ae4a4c31b797`、CPython 3.15.0rc3、pydantic-core 2.46.5のsdistビルドとリンカー`cc`不在である。
使用イメージは`ghcr.io/ak110/pyfltr`のdigest `sha256:224003f281050887c053eb24f14137970df5225b1b2c99af61b960fab43d8103`である。
PyYAML 6.0.3のビルドは成功しており、ビルド発生だけを失敗と判定できない。
同入力の更新commit `80d4be7`は2026年10月10日の以下の件名を持つ。

> fix: 3.15用wheelのあるpydantic-coreへ依存を更新し、CIの3.15でソースビルドが走らないようにする

同commitではpydantic-core 2.50.0にcp315 wheelがある。
この過去のCIの観測は入力WIに由来し、本レーンのローカル成功とは区別する。

[uvのビルド失敗の公式説明](https://docs.astral.sh/uv/reference/troubleshooting/build-failures/#why-does-uv-build-a-package)では、導入環境に合うwheelが無ければsdistをビルドする。
[CIキャッシュの説明](https://docs.astral.sh/uv/concepts/cache/#caching-in-continuous-integration)では、ソースからビルドしたwheelもキャッシュする。
ローカルでの成功は、別のCI環境のビルド用ツールとキャッシュの成立を保証しない。

再検証では該当jobの全ログとcommitのlockfileを取得し、Python版、OS・アーキテクチャ、イメージdigest、ビルド開始と終了状態を対応付ける。
同じイメージとPython版の隔離環境で、既存wheelキャッシュを使わずにプロジェクトの同期コマンドを実行し、ビルド要件を確認する。
互換wheelを持つ版への更新とCIで必要なビルド用ツールを用意する案を比較し、純Pythonのビルドと環境マーカーにより導入されない依存を区別する。

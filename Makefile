# サプライチェーン攻撃対策としてlockfileを常に尊重する。依存を更新する場合のみ
# `env --unset=UV_FROZEN` で一時的に無効化する（`UV_FROZEN=` の空文字代入はuvがエラー扱い）。
export UV_FROZEN := 1

# pytestを個別に起動するtargetの共通の起動形。`-v`でテストごとの成否を出力し、保存した出力を
# 個別テストの合否の根拠に使えるようにする。`-o addopts=''`はpyproject.tomlの並列実行などの設定を外す。
PYTEST_RUN = uv run pytest -v -o addopts='' -p no:cacheprovider

# CIのpython-lint jobが使うcontainer image、`ネイティブ依存インストール`stepの導入パッケージとmatrixのPython版。
# `ci-deps-check`がこの値でCIの依存導入を再現し、ci_workflow_test.pyがci.yamlとの一致を確かめる。
CI_CONTAINER_IMAGE := ghcr.io/ak110/pyfltr:latest
CI_NATIVE_PACKAGES := poppler-utils libarchive13 zstd
CI_PYTHON_VERSIONS := 3.15

help:
	@cat Makefile

# 依存パッケージをアップグレードし全テスト実行
update:
	env --unset=UV_FROZEN uv sync --upgrade --all-groups --all-extras
	env --unset=UV_FROZEN uv lock --upgrade --project agent-toolkit
	mise exec -- prek autoupdate
	$(MAKE) update-mise-locks
	$(MAKE) update-actions
	$(MAKE) test

# リポジトリ用と配布用のmise lockfileを更新
# rustとdotnetは版を明示指定しており、明示指定した版は`mise lock --bump`が自分自身へ解決して
# 変更しないため、設定ファイルの版を書き換える`mise upgrade --bump`を併せて実行する。
# 配布用設定はGitLabバックエンドの`glab`を含み、トークンのfallbackを設定ファイルで無効化する。
update-mise-locks:
	mise upgrade --bump rust
	mise lock --bump --platform linux-x64,windows-x64
	MISE_CONFIG_DIR="$(CURDIR)/.chezmoi-source/dot_config/mise" mise upgrade --bump dotnet
	MISE_CONFIG_DIR="$(CURDIR)/.chezmoi-source/dot_config/mise" mise lock --global --bump --platform linux-x64,windows-x64
	# miseが生成する`.mise/locks`をchezmoiの`dot_mise/locks`へ移し、配布先の参照先へ届くようにする。
	rm -rf .chezmoi-source/dot_config/mise/dot_mise/locks
	mv .chezmoi-source/dot_config/mise/.mise/locks .chezmoi-source/dot_config/mise/dot_mise/locks
	rmdir .chezmoi-source/dot_config/mise/.mise
	# 各ツールのロック用ディレクトリへ`exact_`を付け、版の更新で配布元から消えた旧版を配布先からも除く。
	for dir in .chezmoi-source/dot_config/mise/dot_mise/locks/*/; do \
		[ -d "$$dir" ] || continue; \
		name=$$(basename "$$dir"); \
		mv "$$dir" ".chezmoi-source/dot_config/mise/dot_mise/locks/exact_$$name"; \
	done

# GitHub Actionsのアクションをハッシュピンで最新化（mise未導入時はスキップ）
update-actions:
	@command -v mise >/dev/null 2>&1 || { echo "mise未検出、スキップ。Actionsの更新が不要なら対応不要です。Linux/macOSは 'curl -fsSL https://mise.run | sh'、Windowsは 'winget install jdx.mise' で導入し、miseをPATHへ加えてmake update-actionsを再実行してください。"; exit 0; }; \
	GITHUB_TOKEN=$$(gh auth token) mise exec -- pinact run --update --min-age=1

# 開発環境のセットアップ
setup:
	mise run bootstrap
	@command -v pwsh >/dev/null 2>&1 || echo "警告: pwsh が未導入。PowerShell スクリプトの検証がスキップされる。Ubuntu/Debian なら 'make setup-pwsh' で一括導入可能"
	@command -v chezmoi >/dev/null 2>&1 || echo "警告: chezmoi が未導入。template 検証がスキップされる可能性あり。mise install chezmoiで導入してください。"

# ChromiumとLinuxのシステム依存を初期環境へ導入
# ルートとagent-toolkitは別々のuv.lockを持ちplaywrightの版が異なり得るため、両方の環境の版に対応するChromiumを導入する
setup-browser:
	uv run playwright install --with-deps chromium
	uv run --project agent-toolkit playwright install chromium

# Ubuntu/Debian へ pwsh + PSScriptAnalyzer を一括インストールする。
# prek の PSScriptAnalyzer / chezmoi template check (.ps1.tmpl) を
# ローカルでも実行可能にするための開発者向けターゲット。
setup-pwsh:
	sudo apt-get update
	sudo apt-get install --yes wget
	. /etc/os-release && \
	    wget --quiet "https://packages.microsoft.com/config/$$ID/$$VERSION_ID/packages-microsoft-prod.deb" && \
	    sudo dpkg --install packages-microsoft-prod.deb && \
	    rm packages-microsoft-prod.deb
	sudo apt-get update
	sudo apt-get install --yes powershell
	pwsh -NoProfile -Command "Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force -SkipPublisherCheck"

# リポジトリ直下とagent-toolkitの仮想環境を、ロックファイルが選ぶパッケージ集合とCIの基準Python版へそろえる。
# `uv sync`は余分なパッケージを除き、`--python`は版の異なる既存の仮想環境を削除して指定の版で構築する。
# `uv run`の自動同期は余分なパッケージを残し既存の仮想環境のPython版も保つため、この同期に代えない。
sync:
	uv sync --all-groups --all-extras --python "$$(cat .python-version)"
	uv sync --project agent-toolkit --all-groups --all-extras --python "$$(cat .python-version)"

# CIのcontainer jobと同じimageの中で、CIと同じ依存導入と各Python版の`uv sync`を実行する。
# 作業ツリーは読み取り専用でマウントし、container内へ仮想環境とビルド生成物を除いて複製してから同期する。
# 書込みマウントで同期すると、container内のrootが作業ツリーの所有者を書き換えるためである。
ci-deps-check:
	docker run --rm --user root --entrypoint bash --volume "$(CURDIR):/src:ro" --env UV_FROZEN=1 \
		"$(CI_CONTAINER_IMAGE)" -euo pipefail -c '\
		apt-get update; \
		apt-get install -y --no-install-recommends $(CI_NATIVE_PACKAGES); \
		for version in $(CI_PYTHON_VERSIONS); do \
			rm -rf /work; mkdir /work; \
			tar -C /src --exclude=.venv --exclude=node_modules --exclude=target -cf - . | tar -C /work -xf -; \
			echo "uv sync: Python $$version"; \
			(cd /work && UV_PYTHON=$$version uv sync --all-groups --all-extras); \
		done'

# フォーマット + 軽量lint（開発時の手動実行用。自動修正あり）
format: sync
	uv run --frozen pyfltr fast

# 全チェック実行（これを通過すればコミット可能）
# `--no-fix`はlinterの自動修正段を抑止する。コミット可否を判定するゲートが判定対象の作業ツリーを
# 書き換えないようにし、担当範囲外のファイルへ差分が出ないようにする。
# 自動修正が必要な場合は`make format`を使う。
test: sync
	uv run --frozen pyfltr run --no-fix

# 実ブラウザーテストを日常実行
test-browser:
	uv run playwright install chromium
	uv run --project agent-toolkit playwright install chromium
	$(PYTEST_RUN) -m browser agent-toolkit/agent_toolkit/_atk/serve

# agents_serverの実backendを使うライブ一体テスト
test-agents-live:
	$(PYTEST_RUN) -m live_agents agent-toolkit/agent_toolkit/agents_server_live_test.py

.PHONY: help update update-mise-locks update-actions setup setup-browser setup-pwsh sync ci-deps-check format test test-browser test-agents-live

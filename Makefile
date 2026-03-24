help:
	@cat Makefile

# 開発環境セットアップ（uv tool installでpre-commitをインストールし、git hookを設定）
setup:
	uv tool install pre-commit
	uvx pre-commit install

# 全チェック実行
check:
	uvx pre-commit run --all-files

.PHONY: help setup check

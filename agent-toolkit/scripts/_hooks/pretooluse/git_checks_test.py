# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=unused-import,unused-wildcard-import,wildcard-import,wrong-import-position,undefined-variable
"""agent-toolkit/scripts/_hooks/pretooluse.py のテスト。

subprocessで起動しexit code・stderr・stdoutを検証する。
"""

import ast
import json
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import textwrap
import time
from collections.abc import Callable

import _managed_temp
import hook
import pytest
from _testing import fork_runner as _fork_runner
from _testing.helpers import SESSION_STATE_FILENAME_TEMPLATE
from pyfltr.colloquial import check as _colloquial_check


pytest_plugins = ["_hooks.pretooluse.test_support_test"]
from _hooks.pretooluse import git_checks as pretooluse
from _hooks.pretooluse.test_support_test import *  # noqa: F403


class TestManifestSsot:
    """Claude Code向け正本manifest間のSSOT整合性。

    version / description / nameを2箇所で重複管理しているため、
    片方だけ更新して配布されない事故を防ぐためのハードチェック。
    Codex向け派生manifestはsync_codex_plugin_manifests.pyが検証する。
    """

    def test_plugin_manifest_matches_marketplace(self):
        plugin_manifest = json.loads(_PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        marketplace = json.loads(_MARKETPLACE_MANIFEST.read_text(encoding="utf-8"))

        entries = [p for p in marketplace["plugins"] if p["name"] == plugin_manifest["name"]]
        assert len(entries) == 1, f"marketplace.json に {plugin_manifest['name']} のエントリが 1 件ではない"
        entry = entries[0]

        # SSOTの3フィールドが完全一致することを要求する。
        # 不一致が出たらagent-toolkit/.claude-plugin/plugin.jsonと
        # .claude-plugin/marketplace.json（plugins[]内name == "agent-toolkit"のエントリ）の
        # version／description／nameを両側で揃えること。
        assert entry["version"] == plugin_manifest["version"], (
            f"version 不一致: plugin.json={plugin_manifest['version']} marketplace.json={entry['version']}"
        )
        assert entry["description"] == plugin_manifest["description"], (
            "description 不一致: plugin.json と marketplace.json を揃えること"
        )
        assert entry["name"] == plugin_manifest["name"]

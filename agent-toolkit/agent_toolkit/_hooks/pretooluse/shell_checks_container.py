"""コンテナーの書込可能なroot bind mountの検査。"""

from __future__ import annotations

import pathlib
import re

lazy from agent_toolkit._common.bash_invocations import STATIC_UNKNOWN, extract_bash_invocations
lazy from agent_toolkit._hooks.notice import _WARN_TAG
lazy from agent_toolkit._hooks.pretooluse.notices import _llm_notice


def _warn_container_root_bind_mount(command: str) -> str | None:
    """root所有の生成物をホストへ残し得る起動を警告し、正当なroot実行は継続する。

    イメージのUSER属性は照会せず、起動時のユーザー未指定も警告する。composeの定義とイメージ以降の
    コマンド引数は判定しない。変数を含むホストパスは、展開前の表記でbind mountと判定する。
    """
    for invocation in extract_bash_invocations(command):
        tokens = invocation.static_tokens
        if not tokens or pathlib.PurePosixPath(tokens[0]).name not in {"docker", "podman"}:
            continue
        index = 1
        while index < len(tokens) and tokens[index].startswith("-"):
            option = tokens[index]
            index += (
                2
                if option
                in {
                    "-H",
                    "--host",
                    "-c",
                    "--context",
                    "--connection",
                    "--url",
                    "--root",
                    "--runroot",
                    "--storage-driver",
                    "--log-level",
                    "--config",
                }
                else 1
            )
        if tokens[index : index + 1] == ("container",):
            index += 1
        if index >= len(tokens) or tokens[index] not in {"run", "create"}:
            continue
        user: str | None = None
        writable = False
        index += 1
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if token == "--" or not token.startswith("-"):
                break
            # pflagの結合短形式は、値付きオプション以降をその値として消費する。
            short = re.fullmatch(r"-[ditPq]*([acehlmpuvw])(.*)", token)
            if short:
                token = "-" + short[1] + short[2]
            option, separator, value = token.partition("=")
            if option in {"--user", "--volume", "--mount", "-u", "-v"}:
                if not separator:
                    if index >= len(tokens):
                        break
                    value = tokens[index]
                    index += 1
            elif token.startswith(("-u", "-v")) and not token.startswith("--"):
                option, value = token[:2], token[2:]
            else:
                # オプションの値とコンテナー内の実行語は、起動オプションの解析から除く。
                if not separator and option in {
                    "-e",
                    "--env",
                    "--env-file",
                    "-w",
                    "--workdir",
                    "--entrypoint",
                    "--name",
                    "--hostname",
                    "-h",
                    "--label",
                    "-l",
                    "--network",
                    "--net",
                    "-p",
                    "--publish",
                    "--expose",
                    "--device",
                    "--cap-add",
                    "--cap-drop",
                    "--security-opt",
                    "--group-add",
                    "--platform",
                    "--pull",
                    "--restart",
                    "--runtime",
                    "--memory",
                    "-m",
                    "--cpus",
                    "--cpuset-cpus",
                    "--tmpfs",
                    "--volume-driver",
                    "--volumes-from",
                    "--userns",
                    "--pid",
                    "--ipc",
                    "--ulimit",
                    "--shm-size",
                    "--log-driver",
                    "--log-opt",
                    "--cidfile",
                    "--stop-signal",
                    "--stop-timeout",
                    "--health-cmd",
                    "--health-interval",
                    "--health-timeout",
                    "--health-start-period",
                    "--health-start-interval",
                    "--health-retries",
                    "--add-host",
                    "--annotation",
                    "-a",
                    "--attach",
                    "--blkio-weight",
                    "--blkio-weight-device",
                    "--cgroup-parent",
                    "--cgroupns",
                    "--cpu-period",
                    "--cpu-quota",
                    "--cpu-rt-period",
                    "--cpu-rt-runtime",
                    "-c",
                    "--cpu-shares",
                    "--cpuset-mems",
                    "--detach-keys",
                    "--device-cgroup-rule",
                    "--device-read-bps",
                    "--device-read-iops",
                    "--device-write-bps",
                    "--device-write-iops",
                    "--dns",
                    "--dns-option",
                    "--dns-search",
                    "--domainname",
                    "--gpus",
                    "--ip",
                    "--ip6",
                    "--isolation",
                    "--kernel-memory",
                    "--label-file",
                    "--link",
                    "--link-local-ip",
                    "--mac-address",
                    "--memory-reservation",
                    "--memory-swap",
                    "--memory-swappiness",
                    "--network-alias",
                    "--oom-score-adj",
                    "--pids-limit",
                    "--storage-opt",
                    "--sysctl",
                    "--uts",
                }:
                    index += 1
                continue
            if option in {"--user", "-u"}:
                user = value
            elif option in {"--volume", "-v"}:
                parts = value.split(":")
                writable |= (
                    len(parts) >= 2
                    and value.startswith(("/", ".", "~", "$", STATIC_UNKNOWN))
                    and not (len(parts) >= 3 and {"ro", "readonly"}.intersection(parts[-1].split(",")))
                )
            else:
                fields = {key: item for key, _, item in (part.partition("=") for part in value.split(","))}
                readonly = any(key in fields and fields[key] not in {"false", "0"} for key in ("readonly", "ro"))
                writable |= fields.get("type") == "bind" and not readonly
        if not writable or user is not None and user.split(":", 1)[0] not in {"root", "0"}:
            continue
        return _llm_notice(
            "`root`またはユーザー未指定のコンテナーへ、書き込み可能なホストのバインドマウントを渡している。"
            "この形では生成物が`root`所有でホストに残り、ホストで実行中のアカウントで削除できなくなった事例がある。",
            tag=_WARN_TAG,
            subject=command,
            removable_cause=True,
            fix=(
                '元の起動のマウントを`--mount "type=bind,src=$PWD,dst=/source,readonly" '
                "--mount type=volume,dst=/work`にし、コンテナー内で`cp -a /source/. /work/`として専用領域で作業せよ。"
                'ホストへ書く必要があれば`--user "$(id -u):$(id -g)"`で起動せよ。'
                "正当な`root`実行では、所有者と回収方法を確認した上で警告を受けたまま実行できる。"
            ),
        )
    return None

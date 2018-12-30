
#$XONSH_COLOR_STYLE = 'native'

$INDENT = "    "
$COMPLETIONS_CONFIRM = True
$HISTCONTROL = ('ignoredups')
$AUTO_CD = True
$XONSH_SHOW_TRACEBACK = False

$PROMPT = "{user}@{hostname}:{INTENSE_BLUE}{cwd}{GREEN}$ "
$RIGHT_PROMPT = "* {INTENSE_RED}{curr_branch}"
$TITLE = '{hostname}'

@events.on_postcommand
def _on_postcommand(cmd: str, rtn: int, out: str or None, ts: list) -> None:
    """終了コードと実行時間の表示。"""
    outputs = []

    if rtn != 0:
        outputs.append(f'ExitCode={rtn}')

    elapsed = ts[1] - ts[0]
    if elapsed >= 3:
        outputs.append(f'Elapsed={elapsed:.3f}[secs]')

    if len(outputs) > 0:
        print('')
        print('{', ', '.join(outputs), '}')

@events.on_chdir
def _on_chdir(olddir, newdir, **kwargs):
    ll

aliases['ls'] = 'ls --color=auto'
aliases['grep'] = 'grep --color=auto'
aliases['fgrep'] = 'fgrep --color=auto'
aliases['egrep'] = 'egrep --color=auto'
aliases['ll'] = 'ls -l --classify --group-directories-first'
aliases['la'] = 'ls -l --classify --group-directories-first --all'
aliases['l'] = 'ls -C --classify --group-directories-first'
aliases['rm'] = 'rm -i'
aliases['mv'] = 'mv -i'
aliases['cp'] = 'cp -i'
aliases['reload-shell'] = 'exec xonsh'
aliases['gpuwatch'] = 'watch "top -b | head ; nvidia-smi"'


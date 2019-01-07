
#xontrib load apt_tabcomplete docker_tabcomplete readable-traceback

#$XONSH_COLOR_STYLE = 'native'

$INDENT = "    "
$COMPLETIONS_CONFIRM = True
$HISTCONTROL = ('ignoredups')
$AUTO_CD = True
$XONSH_SHOW_TRACEBACK = True

$PROMPT = "{user}@{hostname}:{INTENSE_BLUE}{cwd}{GREEN}$ "
$RIGHT_PROMPT = "* {INTENSE_RED}{curr_branch} "
$TITLE = '{hostname}'

@events.on_postcommand
def _on_postcommand(cmd: str, rtn: int, out: str or None, ts: list) -> None:
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

aliases['grep'] = 'grep --color=auto'
aliases['fgrep'] = 'fgrep --color=auto'
aliases['egrep'] = 'egrep --color=auto'
aliases['ls'] = 'ls --color=auto --group-directories-first -v'
aliases['ll'] = 'ls -l --classify --group-directories-first -v'
aliases['la'] = 'ls -l --classify --group-directories-first -v --all'
aliases['l'] = 'ls -C --classify --group-directories-first -v'
aliases['rm'] = 'rm -i'
aliases['mv'] = 'mv -i'
aliases['cp'] = 'cp -i'
aliases['reload-shell'] = 'exec xonsh'
aliases['gpuwatch'] = 'watch "top -b | head ; nvidia-smi"'


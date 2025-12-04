# ~/.bashrc: executed by bash(1) for non-login shells.
# see /usr/share/doc/bash/examples/startup-files (in the package bash-doc)
# for examples

# 環境変数(非interactiveでも)
export PYTHONDONTWRITEBYTECODE=1
export MPLBACKEND=Agg
# ~/.envがあれば読み込む
if [ -e ~/.env ] ; then
    set -a
    eval "$(cat ~/.env <(echo) <(declare -x))"
    set +a
fi

# If not running interactively, don't do anything
case $- in
    *i*) ;;
      *) return;;
esac

# don't put duplicate lines or lines starting with space in the history.
# See bash(1) for more options
HISTCONTROL=ignoreboth

# append to the history file, don't overwrite it
shopt -s histappend

# for setting history length see HISTSIZE and HISTFILESIZE in bash(1)
HISTSIZE=1000
HISTFILESIZE=2000

# check the window size after each command and, if necessary,
# update the values of LINES and COLUMNS.
shopt -s checkwinsize

# If set, the pattern "**" used in a pathname expansion context will
# match all files and zero or more directories and subdirectories.
#shopt -s globstar

# make less more friendly for non-text input files, see lesspipe(1)
[ -x /usr/bin/lesspipe ] && eval "$(SHELL=/bin/sh lesspipe)"

# set variable identifying the chroot you work in (used in the prompt below)
if [ -z "${debian_chroot:-}" ] && [ -r /etc/debian_chroot ]; then
    debian_chroot=$(cat /etc/debian_chroot)
fi

# set a fancy prompt (non-color, unless we know we "want" color)
case "$TERM" in
    xterm-color|*-256color) color_prompt=yes;;
esac

# uncomment for a colored prompt, if the terminal has the capability; turned
# off by default to not distract the user: the focus in a terminal window
# should be on the output of commands, not on the prompt
#force_color_prompt=yes

if [ -n "$force_color_prompt" ]; then
    if [ -x /usr/bin/tput ] && tput setaf 1 >&/dev/null; then
	# We have color support; assume it's compliant with Ecma-48
	# (ISO/IEC-6429). (Lack of such support is extremely rare, and such
	# a case would tend to support setf rather than setaf.)
	color_prompt=yes
    else
	color_prompt=
    fi
fi

if [ "$color_prompt" = yes ]; then
    PS1='${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '
else
    PS1='${debian_chroot:+($debian_chroot)}\u@\h:\w\$ '
fi
unset color_prompt force_color_prompt

# If this is an xterm set the title to user@host:dir
# => host:dirに変更
case "$TERM" in
xterm*|rxvt*)
    #PS1="\[\e]0;${debian_chroot:+($debian_chroot)}\u@\h: \w\a\]$PS1"
    PS1="\[\e]0;${debian_chroot:+($debian_chroot)}\h: \w\a\]$PS1"
    ;;
*)
    ;;
esac

# enable color support of ls and also add handy aliases
if [ -x /usr/bin/dircolors ]; then
    test -r ~/.dircolors && eval "$(dircolors -b ~/.dircolors)" || eval "$(dircolors -b)"
    alias ls='ls --color=auto'
    #alias dir='dir --color=auto'
    #alias vdir='vdir --color=auto'

    alias grep='grep --color=auto'
    alias fgrep='grep -F --color=auto'
    alias egrep='grep -E --color=auto'
fi

# colored GCC warnings and errors
#export GCC_COLORS='error=01;31:warning=01;35:note=01;36:caret=01;32:locus=01:quote=01'

# some more ls aliases
alias ll='ls -l --classify --group-directories-first'
alias la='ls -l --classify --group-directories-first --all'
alias l='ls -C --classify --group-directories-first'

# Add an "alert" alias for long running commands.  Use like so:
#   sleep 10; alert
alias alert='notify-send --urgency=low -i "$([ $? = 0 ] && echo terminal || echo error)" "$(history|tail -n1|sed -e '\''s/^\s*[0-9]\+\s*//;s/[;&|]\s*alert$//'\'')"'

# Alias definitions.
# You may want to put all your additions into a separate file like
# ~/.bash_aliases, instead of adding them here directly.
# See /usr/share/doc/bash-doc/examples in the bash-doc package.

if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi

# enable programmable completion features (you don't need to enable
# this, if it's already enabled in /etc/bash.bashrc and /etc/profile
# sources /etc/bash.bashrc).
if ! shopt -oq posix; then
  if [ -f /usr/share/bash-completion/bash_completion ]; then
    . /usr/share/bash-completion/bash_completion
  elif [ -f /etc/bash_completion ]; then
    . /etc/bash_completion
  fi
fi

# 環境変数
test -d /usr/local/cuda/bin     && export PATH=/usr/local/cuda/bin:$PATH
test -d $HOME/.cargo/bin        && export PATH=$HOME/.cargo/bin:$PATH
test -d $HOME/.local/bin        && export PATH=$HOME/.local/bin:$PATH
test -d $HOME/dotfiles/bin      && export PATH=$HOME/dotfiles/bin:$PATH
test -d $HOME/bin               && export PATH=$HOME/bin:$PATH
export ENV=$HOME/.bashrc
export EDITOR=vim
export LESS="--LONG-PROMPT --RAW-CONTROL-CHARS --quit-if-one-screen --no-init"
export MYPY_CACHE_DIR=$HOME/.cache/mypy
#export NODE_TLS_REJECT_UNAUTHORIZED=0  # oco用

# エイリアス
alias rm='rm -i'
alias mv='mv -i'
alias cp='cp -i'
function cd() {
    builtin cd "$@" && (
        ll
        if [ -e .git ] ; then
            git status
        fi
    )
}
alias ..='cd ..'
alias reload-shell='exec $SHELL'

# プロンプトのカスタマイズ
function _show_status() {
    # 終了コード表示
    local status=${PIPESTATUS[@]}
    local color=""  # 白
    local s
    for s in $status ; do
        if [ ${s} -gt 100 ]; then
            color="\\033[1;31m"  # 赤
            break
        elif [ ${s} -gt 0 ]; then
            color="\\033[1;33m"  # 黄
        fi
    done
    if [ -n "${color}" ] ; then
        echo -en ${color}
        echo "Exit code: ${status}"
        echo -en "\\033[0;39m"
    fi
    # ウィンドウタイトルをホスト名にする(念のため毎回)
    echo -en "\\e]2;$(hostname)\\a"
}
PROMPT_COMMAND=${PROMPT_COMMAND//history -a;/}
PROMPT_COMMAND=${PROMPT_COMMAND//_show_status;/}
PROMPT_COMMAND="history -a;_show_status;${PROMPT_COMMAND}"

# pyenv
# 手動で有効化
function enable-pyenv() {
    if [ ! -d "$HOME/.pyenv" ] ; then
        curl -sL https://pyenv.run | bash
    fi
    export PATH="$HOME/.pyenv/bin:$PATH"
    eval "$(pyenv init -)"
    eval "$(pyenv virtualenv-init -)"
    pyenv --version
    pyenv versions
}
# 既に存在したら自動で有効化しちゃうことにしてみる
# ただし、VIRTUAL_ENVがすでに有効ならスキップ
if [ -d "$HOME/.pyenv" -a ! -n "$VIRTUAL_ENV" ] ; then
    enable-pyenv
fi

# Claude Code
if [ -e ~/.claude/local/claude ] ; then
    alias claude=~/.claude/local/claude
fi
if [ -e ~/.local/bin/env ] ; then
    . ~/.local/bin/env
fi

# ~/.localbashrc
if [ -e ~/.localbashrc ] ; then
    source ~/.localbashrc
fi

#xonsh_path=$(which xonsh)
#if [ -x $xonsh_path ] ; then
#    exec $xonsh_path
#fi

# exit code: 0
:

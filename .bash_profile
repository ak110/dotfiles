
# 環境変数
test -d /usr/local/cuda/bin     && export PATH=/usr/local/cuda/bin:$PATH
test -d $HOME/conda/bin         && export PATH=$HOME/conda/bin:$PATH
test -d $HOME/.local/bin        && export PATH=$HOME/.local/bin:$PATH
test -d $HOME/dotfiles/bin      && export PATH=$HOME/dotfiles/bin:$PATH
test -d $HOME/bin               && export PATH=$HOME/bin:$PATH
export ENV=$HOME/.bashrc
export HISTCONTROL="ignoredups"  # ignorespace, ignoredups or ignoreboth
export EDITOR=vim
export PYTHONDONTWRITEBYTECODE=1
export MPLBACKEND=Agg

# .bashrc
test -f ~/.bashrc && . ~/.bashrc


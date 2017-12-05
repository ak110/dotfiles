test -f ~/.bashrc && . ~/.bashrc

# 環境変数
export ENV=$HOME/.bashrc
test -d /usr/local/cuda/bin && export PATH=/usr/local/cuda/bin:$PATH
test -d $HOME/anaconda3/bin && export PATH=$HOME/anaconda3/bin:$PATH
test -d $HOME/bin && export PATH=$HOME/bin:$PATH
export PYTHONDONTWRITEBYTECODE=1
export HISTCONTROL="ignoredups"  # ignorespace, ignoredups or ignoreboth


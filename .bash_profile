
# .bashrc
test -f ~/.bashrc && . ~/.bashrc

# poetry
if [ -d "$HOME/.poetry/bin" ] ; then
    export PATH="$HOME/.poetry/bin:$PATH"
fi

:

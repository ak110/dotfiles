#!/bin/bash
set -eux
sh -c "$(curl -fsLS get.chezmoi.io)" -- -b ~/.local/bin
export PATH="$HOME/.local/bin:$PATH"
if [ ! -d ~/dotfiles ]; then
    git clone https://github.com/ak110/dotfiles.git ~/dotfiles
fi
chezmoi init --source ~/dotfiles --apply

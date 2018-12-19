#!/bin/bash
set -eux
DOT_DIR=~/dotfiles

cd $DOT_DIR
git pull --no-recurse-submodules
git submodule update --init --recursive
./deploy.sh


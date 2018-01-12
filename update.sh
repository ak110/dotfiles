#!/bin/bash
set -eux
DOT_DIR=~/dotfiles

cd $DOT_DIR
git pull
./deploy.sh


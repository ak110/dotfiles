# dotfiles

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install.sh | bash
```

## VSCode例 (`~/.vscode-server/data/Machine/settings.json`)

```json
{
    "python.linting.pylintArgs": [
        "--rcfile=~/dotfiles/share/vscode/pylintrc"
    ]
}
```

## ipython

```bash
pip install -r ~/dotfiles/requirements.txt
ipython --profile=ipy
```

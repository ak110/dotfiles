# dotfilesの導入の補足

miseの自動導入に失敗した場合の手順を示す。

## インストール

### miseの手動導入

`update-dotfiles`はmiseが見つからない場合にmise本体を自動で導入する。
ネットワーク制限などで自動導入に失敗した場合は、次の手順で導入してから`update-dotfiles`を再実行する。

Linuxの場合は以下を実行する。

```bash
curl -fsSL https://mise.run | MISE_INSTALL_PATH="$HOME/.local/bin/mise" sh
```

Windows（PowerShell）の場合は以下を実行する。

```powershell
winget install jdx.mise
```

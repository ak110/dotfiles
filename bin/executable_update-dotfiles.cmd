@echo off
chezmoi update --verbose --source "%USERPROFILE%\dotfiles"
REM �ݒ�e���v���[�g�̕ύX�𔽉f�ichezmoi update�ł͍Đ�������Ȃ��j
chezmoi init --verbose --source "%USERPROFILE%\dotfiles"

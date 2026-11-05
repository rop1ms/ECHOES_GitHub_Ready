# Publish ECHOES on GitHub / Публикация ECHOES на GitHub

## Русский

Эта папка подготовлена для отдельного репозитория **ECHOES**: исходники, сборочные скрипты, установщик, две версии README, логотип, настоящие демонстрационные скриншоты, MIT и уведомления о сторонних лицензиях.

1. Выбери один из двух способов: GitHub Desktop или команды ниже.
2. В GitHub Desktop выбери **File → Add local repository** и эту папку. Если локальный репозиторий ещё не создан, используй предложенное **Create a repository here**, затем **Commit to main → Publish repository**. При публикации задай имя `ECHOES`; заранее создавать репозиторий на сайте для этого способа не нужно.
3. Для командного способа сначала создай на GitHub пустой репозиторий `ECHOES` без дополнительного README, `.gitignore` и лицензии, затем выполни команды ниже из этой папки, заменив `YOUR_ACCOUNT` своим логином. Публикацию выполняешь ты; эти команды здесь приведены как инструкция.

```powershell
git init -b main
git add .
git commit -m "Initial ECHOES release"
git remote add origin https://github.com/YOUR_ACCOUNT/ECHOES.git
git push -u origin main
```

Для этой поставки удобнее GitHub Desktop или Git: нативная среда и лаунчер содержат файлы больше 25 МиБ. Загрузка через сайт ограничена 25 МиБ на файл; обычный Git допускает до 100 МиБ. В подготовленной папке файлов больше 100 МиБ нет. [Официальные ограничения GitHub](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository).

Для пользователей можно дополнительно создать **Release** и приложить ZIP всей папки: установщик должен находиться рядом с `app/` и `setup/`. Не публикуй свою папку `.neon_player`, резервные копии профиля, базы чатов, ключи, `.env`, кэши, личные треки и модели. Подготовленная копия не содержит профиль пользователя.

Перед распространением бинарной сборки сохрани `THIRD_PARTY_NOTICES.md` и `app/montage_native/licenses/`: авторский код имеет MIT, но PyQt6 и другие зависимости сохраняют свои лицензии.

## English

This folder is prepared for a separate repository named **ECHOES**. It contains source, native build scripts, setup scripts, English/Russian READMEs, the logo, real demo screenshots, an MIT license, and third-party notices.

Choose one method. In GitHub Desktop, add this local folder, create a local repository there if prompted, commit to `main`, then publish it as `ECHOES`; Desktop creates the remote repository. For the command-line method, first create an empty `ECHOES` repository on GitHub without another README/license, then use the commands above, replacing `YOUR_ACCOUNT` with your GitHub username.

Use Git or GitHub Desktop: this package includes runtime/launcher files over the browser's 25 MiB upload limit. Every included file is below GitHub's 100 MiB Git file limit. See [GitHub's documentation](https://docs.github.com/en/repositories/working-with-files/managing-files/adding-a-file-to-a-repository).

For an installable download, attach a ZIP of the entire folder to a GitHub Release. Keep `app/` and `setup/` together. Never add a personal `.neon_player` directory, profile backups, chat databases, keys, `.env` files, private music, caches, or local models. Retain all third-party notices when distributing binaries.

## Подписанные обновления и файлы для скачивания

После коммита и push новой версии выполните `python tools/release.py stable --notes "Что нового"`. Ключ автора остаётся на вашем компьютере. Push `release/stable.json` запускает Publish downloads: проверяет подпись, собирает архив точного коммита, сверяет файлы и публикует GitHub Release с лаунчером, ECHOES_Setup.zip и SHA256SUMS.txt. Обычный push кода не выпускает обновление. Повторить публикацию можно через Actions → Publish downloads → Run workflow.

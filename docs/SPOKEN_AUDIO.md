# Audiobooks and podcasts

**For You & Trends** now includes two native C++ / Qt sections:

- **Audiobooks:** complete LibriVox recordings hosted by Internet Archive. Search by title or author, choose English or Russian, open a book and select a chapter.
- **Podcasts:** Apple Podcasts directory search with playable episodes from publishers' RSS / Atom feeds. The selector chooses the English / Russian directory region; the publisher determines each episode's spoken language.

Double-click a chapter or episode, or use **Play selected**, to listen in ECHOES. Audiobook playback turns shuffle off so chapters continue in order. No account is required. Availability depends on the source and network connection. Metadata is cached for six hours; a previous cached response can still be used when the service is unavailable.

The interface follows the app's Russian / English setting immediately. Search and catalogue-language choices survive rebuilding the For You page during the current session.

The UI, asynchronous networking, catalogue parsing and RSS handling are in `tools/native_ui/spoken_ui.cpp` and `spoken_catalog.cpp`. `app/native_spoken.py` only connects native requests to the existing player. Build `echoes_ui.dll` with `tools/native_ui/build.ps1` using the matching Qt SDK, including QtNetwork.

# Аудиокниги и подкасты

В **«Для вас и тренды»** появились **«Аудио Книги»** и **«Подкасты»**. У каждой строки есть поиск и выбор RU / EN. Откройте карточку, выберите главу или выпуск и нажмите **«Слушать выбранное»** — либо дважды щёлкните по записи.

Книги — полные записи LibriVox из Internet Archive. Подкасты ищутся в Apple Podcasts, а выпуски загружаются из RSS издателей. Для подкастов выбор RU / EN задаёт регион каталога, а язык самого выпуска зависит от издателя. При запуске аудиокниги перемешивание отключается, чтобы главы шли по порядку.

Интерфейс переключается между русским и английским вместе с ECHOES. Аккаунт не нужен; для загрузки каталога и прослушивания записей требуется доступ к источнику. Настройки языка лаунчера и загрузка собственного перевода описаны в [Launcher languages](LAUNCHER_TRANSLATIONS.md).

# 2026.11.01-3

## English

- Native-resolution vector flames originate on the track card’s rounded outline, with flowing ribbons, local glow and embers. No enlarged raster aura or rectangular effect boundary. Fixed champion streak counting.
- Public radio station branding: independent station name and bounded custom avatar in room state, friend presence and signed public-directory announcements.
- Independent on-air schedule: up to 100 queued tracks, live edits/reordering, pause/resume, next, direct selection and repeat. Personal host playback cannot overwrite the broadcast. The schedule advances in the network thread, even with the station page hidden.
- Liquid Glass station editor and listener queue, in English and Russian. Small position packets preserve profile/queue by revision; periodic full refresh and joining guests receive full metadata. Position-only updates retain the visible queue widgets.
- Validation limited to 19 feature-specific tests and the radio/fire UI walkthrough in both languages. No full regression suite run for this release.

## Русский

- Векторное пламя в родном разрешении исходит от контура карточки: плавные языки, локальное свечение и искры, без растянутой текстуры и прямоугольной границы эффекта. Исправлен подсчёт серии побед чемпиона.
- Название и аватар радиостанции передаются в комнату, друзьям и в подписанные объявления публичного каталога.
- Независимое расписание эфира: до 100 треков, редактирование и сортировка на ходу, пауза/продолжение, следующий трек, выбор из очереди и повтор. Личный плеер ведущего больше не перезаписывает эфир. Очередь переключается в сетевом потоке, даже когда страница станции скрыта.
- Редактор станции и очередь слушателей в Liquid Glass на русском и английском. Позиционные обновления компактны; профиль и очередь обновляются по версии, при входе и периодически. Виджеты очереди не пересоздаются при каждом обновлении позиции.
- Только 19 проверок этих функций и отдельный проход интерфейса радио/огня на двух языках. Полный набор регрессионных тестов в этой версии не запускался.

# 2026.11.01-2

## English

- Chorus-first duel previews enabled by default, offline background analysis, persistent cache, and filters for genre, artist, album, playlist, favorites, and duration.
- Win-tier spectral fire: procedural smoke, luminous contours, filaments, and embers with configurable detail and a bounded raster cache.
- New Performance category with presets, FPS/background controls, actual cache budgets, animated-image controls, preview limits, and live RAM counters.
- Shared byte-bounded row cache, bounded button cache, hidden-resource release, small-batch chorus FFT processing, and preview image cleanup.
- Private reports to verified staff, including non-friends: durable offline queue, acknowledgments, retry cancellation, and a staff inbox.
- All new controls have English and Russian text; updated screenshots, tests, and documentation.

## Русский

- Дуэли начинают превью с припева по умолчанию; добавлены фоновый анализ, кэш и фильтры по жанру, автору, альбому, плейлисту, избранному и длительности.
- Аура за победы: дым, светящийся контур, волокна и искры; настройка детализации и лимита памяти.
- Раздел «Производительность»: пресеты, FPS и фон, бюджеты кэшей, GIF, пределы предпросмотров и счётчики памяти.
- Общий кэш строк по байтам, ограниченный кэш кнопок, освобождение скрытых ресурсов, анализ припева маленькими пачками и очистка изображений после закрытия.
- Личные репорты подтверждённым администраторам/модераторам без дружбы: очередь, подтверждения, отмена повторов и входящие.
- Английские и русские надписи нового интерфейса, обновлённые скриншоты, тесты и документация.

# 2026.11.01-1

## English

- Expanded English UI coverage, including dynamically drawn captions, native Montage, and launcher labels. A new installation uses English; existing profile language is preserved.
- Added English install/update/uninstall scripts alongside the Russian entry points.
- Fixed a fullscreen minimize/restore race: interrupted resize/maximize transitions are cancelled, stale queued callbacks are ignored, and repaint/opacity are restored.
- English/Russian README, nine real demo screenshots, original demo cover art, native build sources, MIT license, third-party notices, and GitHub publishing instructions.
- Includes prior improvements to voice recognition/scenarios, drawn UI symbols, resizable media previews, click/scroll performance, independent panel rounding, appearance settings, and duel drawing caches.

## Русский

- Расширен английский перевод, включая рисуемые надписи, нативный Montage и лаунчер. Новый профиль использует English, язык существующего профиля сохраняется.
- Добавлены английские установщик, обновление и удаление; русские варианты сохранены.
- Исправлено чёрное окно после сворачивания из полноэкранного режима: прерванные переходы завершаются, устаревшие отложенные действия игнорируются, перерисовка и непрозрачность восстанавливаются.
- README на двух языках, девять настоящих демонстрационных скриншотов, оригинальные обложки, исходники нативных модулей, MIT, лицензии зависимостей и инструкция публикации.
- Включены предыдущие улучшения голосовых сценариев, символов интерфейса, растягиваемых вложений, отзывчивости, скругления панелей, оформления и отрисовки дуэлей.

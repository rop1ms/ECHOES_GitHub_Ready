# Release 2026.11.02-5 — expanded startup, collection and duel diagnosis

See [the diagnostic report](diagnostics/2026.11.02-5.md) for the scenario matrix, confirmed failures, fixes and limitations. The final isolated real-app run passed **317 state invariants over 223 music and 35 Echoes.NET transitions, with zero uncaught exceptions**. Fifty focused persistence/collection/duel tests and two real recovery startups passed. Real user/friend data, external services and audio playback were not exercised. The friend's exact screenshot cause was not confirmed; no claim of all conceivable scenarios or measured live-display FPS is made.

Library/settings JSON now uses BOM-tolerant reads, atomic same-directory replacement and a bounded last-good backup. Bad library records are handled without changing healthy dictionaries or modifying the source on read. Optional analysis and first-build failures cannot permanently hide a healthy collection; failed navigation restores the actual page, buttons and page history. Late duel search results no longer restart unrelated games; persistence/serialization failures and malformed saved/search/title data retain responsive controls and coherent state.

# Release 2026.11.02-4 — first-open geometry and Echoes.NET motion

Cold pages could be captured before nested height-for-width layouts, item grids and scrollbars had settled. A first-use tier row changed from 76 to 122 logical pixels after the old snapshot; the profile acquired a horizontal scrollbar afterwards. Incoming capture now runs behind a queued GUI-turn barrier and resolves only the visible page's nested layout requests and item-view layout. It does not spin the event loop or drain layout events for unrelated windows. Pending transitions follow initial layout resizes, and cancellation during rendering cannot show a cancelled overlay again. Embedded track sections also update their visible viewport when preceding headers move them without scrolling/resizing.

Echoes.NET now uses the shared transition driver for outer navigation and its internal tabs. Ancestor/descendant transitions cancel each other to prevent nested frozen overlays. The slide has a larger directional displacement, while sheets use fractional positions instead of rounded whole-pixel steps. Animation frames blend bounded prepared snapshots; they do not relayout live page trees each frame. Existing motion/eco/reduced settings remain effective. Initial microphone/speaker enumeration runs once per network page in a bounded, hidden helper process; native imports/initialization cannot hold the parent interpreter's lock. Results populate live combo boxes on the GUI thread without changing saved device IDs or emitting selection changes. Long device names keep stable control width; closed controls/pages are ignored safely. Cancellation, timeout and interpreter exit terminate outstanding helpers.

Focused verification: **19 navigation/motion cases, 2 track-section move cases, 9 network navigation/device cases and 7 isolated-device/codec-loading cases passed**. Four new transition lifecycle cases also passed with actual Qt scale factor 1.5. Disposable real MainWindow probes covered only the first queue/albums/profile/tierlist visits and first friends/network-settings/room/threads visits. In all eight checked pages, captured visible geometry remained unchanged in the following GUI event turn(s). The settings geometry was rechecked after moving native discovery into its helper. See [music-page report](performance/first-page-geometry-2026.11.02-4.json) and [network-page report](performance/net-first-open-2026.11.02-4.json). Profiles were temporary, socket connections blocked, and no audio or real contacts were used. The full player scenario and unrelated tests were not run. These geometry and lifecycle checks do not measure live-display FPS or promise zero frame cost on every PC.

An additional scoped diagnostic disabling discovery exposed a **1.7-second synchronous import of `net_voice` and its codec dependencies in `_voice_card`**. That import was removed from settings construction; the card describes automatic codec selection without loading voice DSP/PyAV merely to display it. `VoiceEngine.devices()` and the helper now share lightweight discovery code, preserving PortAudio indices/order/name deduplication. Native-library loading/discovery is isolated from the main interpreter. In a separate actual-device probe, discovery completed in 362.311 ms while a 10 ms Qt GUI heartbeat delivered 36 ticks (p95 interval 10.284 ms, max 10.389 ms), with zero helpers remaining. See [device responsiveness report](performance/device-discovery-2026.11.02-4.json). It enumerated devices only; no microphone recording or playback occurred. This heartbeat is not a rendering-FPS measurement. The two snapshot raster operations already execute in Qt/C++; no new rendering engine or dependency was added without a demonstrated benefit.

RU: исправлены скачки первого открытия из-за незавершённой раскладки, позднего появления полос прокрутки и скрытого после перемещения списка песен. Переходы добавлены в Echoes.NET; вложенные анимации не перекрывают друг друга. Опрос аудиоустройств изолирован в процессе, а загрузка голосового движка/кодеков убрана из показа настроек. Пройдены 37 профильных проверок и узкие первые открытия восьми реальных страниц с временными данными. При опросе устройств GUI-таймер с шагом 10 мс продолжал работать (максимальный интервал 10,389 мс). Полный сценарий и остальные тесты не запускались; реальный FPS экрана не измерялся.

# Release 2026.11.02-3 — navigation, cover loading and friend presence

Removed repeated queue widget rebuilding, moved every lazy cover decode/file lookup out of the GUI thread, and deduplicated pending-cover repaint subscriptions. Incoming transition captures are prepared once on a shared frame after the last navigation request in an event burst. Interrupted transitions continue from the visible blend; navigation clicks outside the page no longer discard that image first. Transparent page captures now include cropped ancestor backgrounds, preserving the actual liquid glass without rendering unrelated sibling controls.

**35 targeted cases passed:** 15 navigation/motion, 6 queue reuse/signature, 4 asynchronous cover-loading and 10 friend-presence/shutdown cases. Three new background/coalescing/continuity cases also passed at actual Qt scale factor 1.5. No broad regression suite, playback scenario, real contacts or live relay tests were run.

An isolated real MainWindow/YaShell probe with 2,500 synthetic tracks and a 20-track queue measured warmed collection→queue preparation at **107.294 ms median with forced rebuilding versus 24.560 ms with reuse (4.37× less elapsed time)**. This isolates queue reuse in the current code, rather than comparing entire releases. Sixteen rapid navigation requests left one overlay; incoming capture was prepared for the final page. Two empty glass-background pixels matched the real window crop exactly. These are offscreen navigation measurements, **not live display FPS, a weak-PC benchmark or a measured Spotify comparison**. See [the measurement report](performance/navigation-2026.11.02-3.json) and `qa/profile_queue_navigation.py`. The probe's cleanup was corrected and rechecked separately after the navigation measurement.

Presence now distinguishes outbound relay mailboxes from confirmed inbound activity. Duplicate, stale, reordered and invalid heartbeats cannot renew online status; TTL retains signed-event age and uses a monotonic expiry clock. Offline wins same-second ties, but cannot override newer authenticated activity. Online alerts require an actually observed absence. Full application exit attempts urgent offline once with a total 0.75-second deadline; it blocks a racing late online heartbeat. Process termination/unreachable relays fall back to a 210-second relay activity TTL. Closing to the tray still keeps the application running. Relay clients with clocks more than 210 seconds behind cannot establish live presence from heartbeat history; direct/UDP activity remains independent of that clock skew.

The implementation follows Qt's [rendering/background rules](https://doc.qt.io/qt-6/qwidget.html#render) and [animation performance guidance](https://doc.qt.io/qtcreator/qtquick-production-quality-animation.html): avoid blocking GUI work and keep processing within the frame budget. These are engineering references, not evidence that Echoes matches another application's FPS.

RU: исправлены потеря стеклянного фона в переходе, лишняя пересборка очереди и синхронная загрузка обложек во время прокрутки. Быстрые переключения объединяют подготовку входящего снимка до последней выбранной страницы. Пройдены только 35 профильных проверок. Изолированный переход в очередь с 2500 синтетических треков: 107,294 → 24,560 мс; это не замер FPS экрана. Повторы старых сообщений больше не оживляют друзей, отправка сама по себе не считается online; полный выход отправляет offline, а аварийное завершение покрывается TTL. Работа в трее сохраняется. Реальные аккаунты и полный сценарий не использовались.

# Release 2026.11.02-2 — focused navigation checks

Fixed the reduced-page transition regression from 2026.11.02-1. A bounded snapshot with DPR below one was drawn at its raster extent instead of the page extent. Snapshots now use explicit physical-source/logical-target rectangles. Normal transitions retain page size and brightness, without zoom, dark fade-through or spring overshoot. Standard pages retain logical pixel resolution up to 2.1 million pixels per snapshot; eco mode is capped at 900,000 pixels. Page/sheet/morph transitions use the shared frame clock and release their snapshots/timers on completion, new input, resize or hide. Morphs no longer drain global layout events. Wheel reversal discards the old pending distance, and scrollbar dragging cancels smoothing.

Only `qa/test_navigation_motion.py` was run: **12 focused cases passed**. These cover full-page pixel extent, brightness/scale through transition frames, sheet endpoints, first-show layouts, input/resize/hide cleanup, elapsed-time frame progression, rapid navigation and overlay bounds, morph transitions, wheel reversal/settling, drag/range/visibility changes, native touchpad handling and horizontal scrolling. The two pixel/brightness cases also passed with actual `QT_SCALE_FACTOR=1.5`. Checks use isolated offscreen Qt widgets; no full player scenario, unrelated suite, personal profile or network was exercised. No claim of measured live-display FPS or guaranteed performance on every PC is made.

Исправлен уменьшенный снимок страницы из предыдущего обновления. Обычные переходы сохраняют масштаб и яркость; работают по общим часам кадров и освобождают снимки при завершении, новом вводе, изменении размера или скрытии. Прокрутка сразу меняет направление и не мешает перетаскиванию полосы. Пройдены только 12 проверок изменённых переходов/прокрутки; две проверки отрисовки дополнительно пройдены при масштабе 150%. Полный сценарий плеера и остальные тесты не запускались. Реальный FPS на экране и работа на слабом ПК не измерялись.

# Release 2026.11.02-1 — not tested

At the user's request, no tests, compilation, application launch or performance benchmarks were run. Embedded track sections now retain document height while limiting the actual item-view viewport to the visible area plus a small overscan. Wheel scrolling uses the shared frame clock, supports horizontal motion, and stops for hidden/minimized pages. Page-transition captures are rendered directly within a pixel budget, without a full-resolution intermediate capture or global posted-event drains.

Fresh profiles use the system UI language (Russian or English; unsupported system languages fall back to English). Saved language choices are retained. In-place language changes retain source captions, update existing controls and invalidate text raster caches without restarting the player. Editable text is preserved. Native editor windows are separate processes and receive the current language when next opened. Actual FPS improvements and runtime behavior have not been measured for this release.

По просьбе пользователя тесты, компиляция, запуск приложения и замеры FPS не выполнялись. Списки внутри страниц рисуют видимую часть с небольшим запасом; прокрутка и снимки переходов ограничивают лишнюю работу. При первом запуске язык берётся из системы (RU/EN, для остальных — EN). Выбранный ранее язык сохраняется; смена языка обновляет существующий интерфейс без перезапуска и остановки музыки. Отдельный нативный редактор получает текущий язык при следующем открытии. Улучшение FPS этой версии не измерялось.

# Release 2026.11.01-5 — not tested

At the user's request, no automated tests, UI runs or live playlist imports were executed for this update. Changes: playback transition/pause guards and repeat restart; public Yandex/VK playlist metadata import and TXT/M3U fallback beside Spotify. Token-free retrieval depends on metadata being publicly accessible; login/CAPTCHA and partial-list responses are reported. The checks below belong to previous releases.

По просьбе пользователя тесты, запуск интерфейса и пробный импорт не выполнялись. Добавлены исправления переключения/повтора, импорт публичных списков Яндекс/VK без ввода токенов и запасной импорт TXT/M3U. Ниже — проверки прошлых версий.

# Release verification — 2026.11.01-4

Verification was limited to the changed radio, fire-level, request and player-effect features.

| Focused check | Result |
| --- | --- |
| Radio studio/auth/export cases (`qa/test_radio_studio.py`) | 19 cases passed; a corrected normal-player fixture was rechecked individually |
| Existing related radio/fire cases (`qa/test_radio_fire.py`) | 19 cases passed with updated level/authority fixtures |
| Voice DSP | Every preset returned bounded finite audio; pitch changed frequency without changing duration; autotune corrected a detuned tone; echo/reverb had measured tails |
| Hosting authorization | Default deny, admin/mod signed grants, rejection of forged/nonstaff grants, revocation/replay/persistence and moderator removal checked with synthetic real Ed25519 identities |
| Support protocol | Private request → approved signed permission → reply → acknowledgment; nonstaff/uncertified approvals rejected; late delivery ACK does not overwrite approval |
| Soundboard | Bounded file decode, send with closed mic gate, and original-file access restricted to station members |
| Fire levels | No effect before 5 wins; all six thresholds and existing native-render/cache behavior checked |
| Focused UI (`qa/check_radio_studio_ui.py`) | 9 checks each in English and Russian, no Python errors |
| Shared controls | Settings and bottom popup shared bass/gain/speed; export read the same state and followed the shared link toggle |
| Actual file export | 8-second generated WAV at 1.25× rendered to a 6.4-second WAV with current EQ/gain |
| Screenshots | Real app captures with a temporary profile, generated artwork/audio, and synthetic staff identities |

No full regression suite, general-page smoke audit, native editor checks or launcher tests were run for this release. No real administrator was messaged and no permission was issued for a real user. Permission and request protocol tests use in-memory peers; actual multi-PC broadcasts, microphones/devices, network latency and perceptual voice quality need a real listening session. Voice effects run before encoding; the station soundboard uses the available radio voice channel. Original-file transfer uses the existing transport limits. Everyone, including approving staff, needs an updated client for the new approval flow.

## Русский

Проверены только изменённые функции: 38 профильных случаев и по 9 проверок интерфейса на русском/английском. Измерены изменение частоты, работа автотюна и хвостов эха/реверба, проверены подписи разрешений и обмен заявками/ответами, доступ к файлам слушателей, уровни огня и общие параметры плеера/экспорта. WAV длиной 8 секунд при 1,25× получил длительность 6,4 секунды. Реальные аккаунты/разрешения не использовались, сообщения сотрудникам не отправлялись. Эфир между реальными ПК и субъективное качество микрофона не проверялись. Полный набор 120+ тестов не запускался.

---

## Historical verification — preceding releases

### Release 2026.11.01-3

This release was checked only for the changed radio and card-fire features, as requested.

| Focused check | Result |
| --- | --- |
| `qa/test_radio_fire.py` | 19 tests passed |
| Station schedule | Advance/repeat/end, pause/resume/next, select, edit/reorder/remove, retain elapsed position |
| Station isolation | Host personal-player updates cannot overwrite radio; ordinary rooms still accept player updates |
| Station transport | Guest receives branding and queue; revision-matched deltas retain both; signed public announcements include branding; closing cancels the scheduler |
| Input and memory bounds | Avatar format/dimensions/bytes, queue length and unique IDs, no local paths on the wire, bounded fire recipe cache |
| Fire | Emitters root on the actual card outline, animation and win colors, high-resolution rendering, champion streak/undo |
| Focused UI walkthrough (`qa/check_radio_fire_ui.py`) | 6 radio/fire checks in English and Russian; no Python errors |
| Native fire recipe cache | 64 KiB accounted while visible; 0 after leaving the page |
| Arena capture, 1600 × 1050, English fixture | 13.96 ms mean / 15.33 ms p95; offscreen capture workload, not a live display FPS measurement |
| New screenshots | Actual application captures using generated demo artwork and a temporary profile |

The UI check opens only the duel and station interfaces, exercises editor add/reorder/save, host queue, guest visibility, public branding, and retention of widgets on position updates. Network access is disabled. Protocol tests use simulated peers, not real listeners or public relays. Live multi-device connectivity and hardware-specific FPS were not measured. All participants need the updated app to display station branding/queue.

No full regression suite, general UI smoke walkthrough, launcher test or native-editor test was run for this release. The following section records the preceding release’s checks and memory measurements.

## Русский

Для этой версии выполнены только 19 тестов изменённых функций и 6 проверок интерфейса радио/огня на каждом из двух языков. Проверены независимость эфира, очередь, аватар/название, обмен состоянием, освобождение кэша и рисунок огня. Реальные соединения между компьютерами и реальный FPS экрана не измерялись; сетевые проверки используют имитацию участников. Для отображения нового профиля/очереди станции всем участникам нужна обновлённая версия. Полный набор 120+ тестов не запускался.

---

## Historical verification — preceding release 2026.11.01-2



Verified on Windows with an isolated demonstration profile:

| Check | Result |
| --- | --- |
| Python regression suite | 125 tests passed |
| Regression suite in the prepared GitHub checkout | 125 tests passed |
| English UI smoke audit | 54 checks; no Python errors |
| Russian UI smoke audit | 54 checks; no Python errors |
| Visible English label/button audit across the smoke walkthrough | 0 remaining Cyrillic UI captions |
| Interrupted fullscreen transition and minimize/restore on the Windows Qt platform | 4 language/restore tests passed |
| Native Montage model round-trip and render-plan self-test | Passed |
| Native Montage English editor smoke test | Passed |
| Built launcher on the Windows Qt platform | Opened and exited successfully |
| English and Russian setup scripts | PowerShell syntax validated |
| New feature integration | Chorus detection/start, filter persistence, performance presets, queued report and staff inbox passed offline |
| Chorus preview fixture | Detected 15.65 seconds; actual player start at the explicit 16-second chorus marker verified |
| Aura rendering, 1600 × 1000 window | 4.19 ms mean / 5.12 ms p95 in the isolated capture workload |
| Aura raster memory | 1.49 MiB while visible; 0 MiB after leaving the page |
| README screenshot set | 15 real application captures, including 6 new feature captures |

The UI smoke walkthrough covers music/library/discovery pages, social tabs and populated chats/feed, listening tools, settings categories, resizing, and supported themes. It disables network access and uses a temporary home. Bug-report tests use in-memory peers and an isolated local database; no report was sent to a real administrator. Live provider availability, peer connectivity, individual hardware performance, and optional AI models are separate from this offline audit. Native Montage/launcher binaries are unchanged from the preceding verified release.

## Memory workload

`qa/benchmark_row_memory.py` creates 400 real Qt pixmaps of 1440 × 56 pixels in separate before/after processes. The former 600-item row-cache policy retains 123.05 MiB of raster data; the shared byte-budget policy retains 12 MiB. Measured private-memory growth was 125.39 MiB before and 14.02 MiB after. This isolates one cache and is not a measurement of total application RAM. Results are recorded in `MEMORY_BENCHMARK.json`.

Additional memory changes: no all-frame GIF caching by default; hidden row/button/aura caches are released; closed photo previews drop decoded sources; image preview resolution is bounded; page prewarming is opt-in; chorus FFT work uses small batches instead of a full-song spectrum matrix. The Performance panel exposes the tradeoffs and reports actual working/private memory.

Public package preparation uses an explicit file allowlist, checks hashes, scans source text for credential-like literals and personal home paths, and rejects files of 100 MiB or larger. Profile files, private databases, caches, audit logs, and previous-file backups are excluded. File hashes and package size are recorded in `RELEASE_MANIFEST.json`; `sha256_lf` additionally allows checking text after Git normalizes CRLF/LF line endings.

## Русский

Пройдены 125 регрессионных тестов и по 54 проверки английского/русского интерфейса. Проверены начало с припева, сохранение фильтров, пресеты производительности, локальная очередь репортов и входящие команды. Реальным администраторам тестовые сообщения не отправлялись. В проверенных английских кнопках и подписях русских остатков нет. Для кэша 400 строк измерено 123,05 → 12 МиБ, прирост приватной памяти тестового процесса — 125,39 → 14,02 МиБ. Это не замер всей программы. Кэш ауры занимал 1,49 МиБ и освобождался после ухода со страницы. Проверки проводились на временном профиле без сети; реальные внешние соединения и конкретное оборудование имеют собственные ограничения.

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

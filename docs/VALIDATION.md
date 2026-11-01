# Release verification — 2026.11.01-2

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

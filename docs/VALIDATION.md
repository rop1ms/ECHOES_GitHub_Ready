# Release verification — 2026.11.01-1

Verified on Windows with an isolated demonstration profile:

| Check | Result |
| --- | --- |
| Python regression suite | 99 tests passed |
| Regression suite in the prepared GitHub folder | 99 tests passed |
| English UI smoke audit | 53 checks; no Python errors |
| Russian UI smoke audit | 53 checks; no Python errors |
| Visible English label/button audit across the smoke walkthrough | 0 remaining Cyrillic UI captions |
| Interrupted fullscreen transition and minimize/restore on the Windows Qt platform | 4 language/restore tests passed |
| Native Montage model round-trip and render-plan self-test | Passed |
| Native Montage English editor smoke test | Passed |
| Built launcher on the Windows Qt platform | Opened and exited successfully |
| English and Russian setup scripts | PowerShell syntax validated |
| README screenshot set | 9 real application captures reviewed |

The UI smoke walkthrough covers music/library/discovery pages, social tabs and populated chats/feed, listening tools, settings categories, resizing, and supported themes. It disables network access and uses a temporary home. Live provider availability, peer connectivity, individual hardware performance, and optional AI models are separate from this offline audit.

Public package preparation uses an explicit file allowlist, checks hashes, scans source text for credential-like literals and personal home paths, and rejects files of 100 MiB or larger. Profile files, private databases, caches, audit logs, and previous-file backups are excluded. File hashes and package size are recorded in `RELEASE_MANIFEST.json`.

## Русский

Пройдены 99 регрессионных тестов, 53 проверки английского и 53 проверки русского интерфейса, восстановление полноэкранного окна на Windows, проверки нативного Montage и запуск собранного лаунчера. В проверенных видимых английских надписях кнопок/меток русских остатков не найдено. Девять скриншотов просмотрены вручную. Проверки интерфейса выполнялись без сети на временном профиле; доступность внешних сервисов и производительность конкретного компьютера проверяются отдельно.

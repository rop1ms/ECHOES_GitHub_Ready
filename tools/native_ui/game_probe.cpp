// «Режим геймера» (game_mode.py): что сейчас на переднем плане — дёшево, раз в пару секунд.
//
// eui_foreground_info — процесс окна на переднем плане (путь к .exe, pid), закрывает ли окно весь свой
// монитор (полноэкранная или «оконная без рамки» игра) и что Windows говорит о полноэкранном режиме
// (SHQueryUserNotificationState: 3 — запущено полноэкранное Direct3D-приложение).
// eui_process_alive — жив ли процесс игры (режим держится, пока игра запущена, даже если свернули).
#include <windows.h>
#include <shellapi.h>

#define EUI_API extern "C" __declspec(dllexport)

#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "user32.lib")

EUI_API int eui_foreground_info(wchar_t *exe, int cap, int *pid, int *fullscreen) {
    if (exe && cap > 0) exe[0] = 0;
    if (pid) *pid = 0;
    if (fullscreen) *fullscreen = 0;
    QUERY_USER_NOTIFICATION_STATE st = QUNS_ACCEPTS_NOTIFICATIONS;
    SHQueryUserNotificationState(&st);
    HWND w = GetForegroundWindow();
    if (!w) return int(st);
    DWORD p = 0;
    GetWindowThreadProcessId(w, &p);
    if (pid) *pid = int(p);
    if (exe && cap > 0 && p) {
        HANDLE h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, p);
        if (h) {
            DWORD n = DWORD(cap);
            if (!QueryFullProcessImageNameW(h, 0, exe, &n)) exe[0] = 0;
            CloseHandle(h);
        }
    }
    if (fullscreen) {
        RECT r;
        MONITORINFO mi{sizeof mi};
        HMONITOR m = MonitorFromWindow(w, MONITOR_DEFAULTTONEAREST);
        if (GetWindowRect(w, &r) && m && GetMonitorInfoW(m, &mi)) {
            const RECT &s = mi.rcMonitor;
            *fullscreen = (r.left <= s.left && r.top <= s.top && r.right >= s.right && r.bottom >= s.bottom) ? 1 : 0;
        }
    }
    return int(st);
}

EUI_API int eui_process_alive(int pid) {
    if (pid <= 0) return 0;
    HANDLE h = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, DWORD(pid));
    if (!h) return 0;
    DWORD code = 0;
    BOOL ok = GetExitCodeProcess(h, &code);
    CloseHandle(h);
    return ok && code == STILL_ACTIVE ? 1 : 0;
}

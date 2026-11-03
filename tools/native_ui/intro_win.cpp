// Заставка ECHOES в своём потоке и своём окне (intro_anim.start_native).
//
// Раньше заставка рисовалась в потоке интерфейса: пока плеер загружался, её кадры стояли. Здесь отдельный
// поток создаёт своё окно Win32 (без рамки, поверх окна плеера), рисует кадры QPainter'ом в картинку
// (DIB) и выводит её в ритме экрана (DwmFlush) — ни поток интерфейса, ни GIL Python ей не нужны. Поэтому
// пока идёт заставка, плеер спокойно загружает всё заранее (страницы, первые кадры, GL), а заставка не
// дёргается. Сюжет: из тьмы загорается солнце, лучи по одному вылетают с искрами, вспышка, буква за
// буквой выходит ECHOES с бликом; дальше, пока плеер не готов, солнце медленно вращается, сияние дышит,
// под надписью бежит полоска загрузки. Готов (eui_intro_ready) и сюжет досмотрен — заставка растворяется
// с лёгким «наездом». Клик — не ждать конца сюжета.
#include <windows.h>
#include <dwmapi.h>
#include <QtGui/QColor>
#include <QtGui/QFont>
#include <QtGui/QFontMetricsF>
#include <QtGui/QImage>
#include <QtGui/QLinearGradient>
#include <QtGui/QPainter>
#include <QtGui/QPainterPath>
#include <QtGui/QRadialGradient>
#include <QtCore/QString>
#include <atomic>
#include <chrono>
#include <cmath>
#include <mutex>
#include <thread>

#pragma comment(lib, "dwmapi.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "user32.lib")

#define EUI_API extern "C" __declspec(dllexport)

namespace {

constexpr int RAYS = 16;
constexpr double T_RAYS = 0.30, RAY_STEP = 0.055, RAY_GROW = 0.36;
constexpr double T_FLASH = T_RAYS + RAYS * RAY_STEP + 0.16;
constexpr double T_TEXT = 1.15, LETTER_STEP = 0.075, T_SWEEP = 1.80, T_SEQ = 2.6, FADE = 0.6;

double clamp01(double x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
double out_cubic(double x) { x = clamp01(x); return 1 - (1 - x) * (1 - x) * (1 - x); }
double out_back(double x, double s) { x = clamp01(x) - 1; return 1 + (s + 1) * x * x * x + s * x * x; }
double in_out(double x) { x = clamp01(x); return x < 0.5 ? 4 * x * x * x : 1 - std::pow(-2 * x + 2, 3) / 2; }

struct Intro {
    std::thread th;
    std::atomic<bool> alive{false}, ready{false}, skip{false};
    std::atomic<HWND> owner{nullptr};
    std::atomic<int> frames{0};
    std::mutex mu;
    RECT rect{};
    double dpr = 1.0;
    QColor acc{255, 219, 26};
    QString text = QStringLiteral("ECHOES"), family;
    HWND hwnd = nullptr;
    double fade_t0 = -1;
    double ready_t = -1;

    static LRESULT CALLBACK proc(HWND h, UINT m, WPARAM w, LPARAM l) {
        auto *self = reinterpret_cast<Intro *>(GetWindowLongPtrW(h, GWLP_USERDATA));
        if (m == WM_LBUTTONDOWN && self) { self->skip = true; return 0; }
        if (m == WM_MOUSEACTIVATE) return MA_NOACTIVATE;
        if (m == WM_ERASEBKGND) return 1;
        return DefWindowProcW(h, m, w, l);
    }

    void run();
    void draw(QPainter &p, double W, double H, double t, double out);
};

Intro *g_intro = nullptr;

void Intro::draw(QPainter &p, double W, double H, double t, double out) {
    p.fillRect(QRectF(0, 0, W, H), Qt::black);
    const double alpha = 1.0 - out;
    p.setOpacity(alpha);
    const double cx = W / 2, cy = H / 2 - 40;
    const double zoom = 1.0 + 0.10 * out;
    p.translate(cx, cy + 40);
    p.scale(zoom, zoom);
    p.translate(-cx, -(cy + 40));
    const bool hold = t > T_SEQ;
    const double breathe = hold ? 0.5 + 0.5 * std::sin((t - T_SEQ) * 2.4) : 0.0;
    // сияние
    {
        double k = out_cubic(t / 1.0);
        double pulse = std::exp(-std::pow((t - T_FLASH) / 0.18, 2));
        double r = 120 + 260 * k + 60 * pulse + 26 * breathe;
        QRadialGradient g(QPointF(cx, cy), r);
        QColor c0 = acc; c0.setAlpha(int(70 * k + 70 * pulse + 18 * breathe));
        QColor c1 = acc; c1.setAlpha(int(18 * k));
        QColor c2 = acc; c2.setAlpha(0);
        g.setColorAt(0, c0); g.setColorAt(0.35, c1); g.setColorAt(1, c2);
        p.setPen(Qt::NoPen); p.setBrush(g);
        p.drawEllipse(QPointF(cx, cy), r, r);
    }
    // солнце, лучи, искры, ударная волна
    {
        double core = out_back(t / 0.6, 1.8);
        double pulse = std::exp(-std::pow((t - T_FLASH) / 0.14, 2));
        double R = 34 * core * (1 + 0.10 * pulse + 0.04 * breathe);
        double rot = 30 * out_cubic(t / 1.4) + 6 * t;
        QColor hot = acc.lighter(135);
        p.save();
        p.translate(cx, cy);
        p.rotate(rot);
        for (int i = 0; i < RAYS; ++i) {
            double t0 = T_RAYS + i * RAY_STEP;
            double k = (t - t0) / RAY_GROW;
            if (k <= 0) continue;
            double ang = 360.0 * i / RAYS - 90;
            bool longr = i % 2 == 0;
            double L = (longr ? 88 : 60) * (1 + 0.06 * pulse + 0.05 * breathe);
            double grow = out_back(k, 2.6);
            p.save();
            p.rotate(ang);
            double base = R * 0.82, tip = base + L * grow;
            double w = (longr ? 11 : 8) * std::min(1.0, grow + 0.2);
            QPainterPath path;
            path.moveTo(base, -w / 2); path.lineTo(tip, 0); path.lineTo(base, w / 2); path.closeSubpath();
            QLinearGradient lg(QPointF(base, 0), QPointF(tip, 0));
            lg.setColorAt(0, hot); lg.setColorAt(1, acc);
            p.setPen(Qt::NoPen); p.setBrush(lg); p.drawPath(path);
            double sk = (t - t0) / 0.6;
            if (sk > 0 && sk < 1) {
                double d = base + L + 130 * out_cubic(sk), a = 1 - sk;
                QColor sc = hot; sc.setAlpha(int(230 * a));
                QLinearGradient trail(QPointF(d - 46 * a, 0), QPointF(d, 0));
                QColor tc = acc; tc.setAlpha(0);
                trail.setColorAt(0, tc); trail.setColorAt(1, sc);
                p.setPen(QPen(QBrush(trail), 2.2 * a + 0.4, Qt::SolidLine, Qt::RoundCap));
                p.drawLine(QPointF(d - 46 * a, 0), QPointF(d, 0));
                p.setPen(Qt::NoPen); p.setBrush(sc);
                p.drawEllipse(QPointF(d, 0), 2.6 * a + 0.6, 2.6 * a + 0.6);
            }
            p.restore();
        }
        if (R > 0.5) {
            QRadialGradient g(QPointF(-R * 0.3, -R * 0.3), R * 1.4);
            g.setColorAt(0, QColor(255, 255, 245)); g.setColorAt(0.35, hot); g.setColorAt(1, acc);
            p.setPen(Qt::NoPen); p.setBrush(g);
            p.drawEllipse(QPointF(0, 0), R, R);
        }
        double ring = (t - 0.15) / 0.8;
        if (ring > 0 && ring < 1) {
            QColor rc = acc; rc.setAlpha(int(150 * (1 - ring)));
            p.setPen(QPen(rc, 2.0 * (1 - ring) + 0.3)); p.setBrush(Qt::NoBrush);
            double rr = 30 + 200 * out_cubic(ring);
            p.drawEllipse(QPointF(0, 0), rr, rr);
        }
        double ring2 = (t - T_FLASH) / 0.9;                       // вторая волна — от вспышки
        if (ring2 > 0 && ring2 < 1) {
            QColor rc = acc.lighter(130); rc.setAlpha(int(110 * (1 - ring2)));
            p.setPen(QPen(rc, 1.6 * (1 - ring2) + 0.3)); p.setBrush(Qt::NoBrush);
            double rr = 50 + 320 * out_cubic(ring2);
            p.drawEllipse(QPointF(0, 0), rr, rr);
        }
        p.restore();
    }
    // надпись
    if (t >= T_TEXT) {
        QFont f(family.isEmpty() ? QStringLiteral("Segoe UI") : family);
        f.setPixelSize(66);
        f.setWeight(QFont::Black);
        QFontMetricsF fm(f);
        const double base_y = cy + 182;
        double spread = 1 - out_cubic((t - T_TEXT) / 0.65);
        double gap = 6 + 30 * spread;
        QList<double> widths;
        double total = 0;
        for (QChar ch : text) { widths.append(fm.horizontalAdvance(ch)); total += widths.last(); }
        total += gap * (text.size() - 1);
        double x = cx - total / 2;
        p.setFont(f);
        for (int j = 0; j < text.size(); ++j) {
            double k = out_cubic((t - T_TEXT - j * LETTER_STEP) / 0.4);
            if (k > 0) {
                double dy = 22 * (1 - k);
                int v = int(40 + 215 * k);
                QColor col(v, v, v); col.setAlpha(int(255 * std::min(1.0, k * 1.3)));
                p.setPen(col);
                p.drawText(QPointF(x, base_y + dy), QString(text[j]));
            }
            x += widths[j] + gap;
        }
        double sweep = (t - T_SWEEP) / 0.5;
        if (sweep > 0 && sweep < 1) {
            double c = cx - total / 2 - 80 + (total + 160) * sweep;
            QLinearGradient band(QPointF(c - 60, 0), QPointF(c + 60, 0));
            QColor c0 = acc; c0.setAlpha(0);
            QColor c1 = acc.lighter(140); c1.setAlpha(255);
            band.setColorAt(0, c0); band.setColorAt(0.5, c1); band.setColorAt(1, c0);
            QPainterPath path;
            double xx = cx - total / 2;
            for (int j = 0; j < text.size(); ++j) { path.addText(QPointF(xx, base_y), f, QString(text[j])); xx += widths[j] + gap; }
            p.save(); p.setClipPath(path);
            p.fillRect(QRectF(cx - total / 2 - 10, base_y - fm.ascent() - 10, total + 20, fm.height() + 20), band);
            p.restore();
        }
        double u = out_cubic((t - (T_TEXT + 0.35)) / 0.45);
        if (u > 0) {                                              // полоска акцента; в ожидании — бегущий блик
            QColor uc = acc; uc.setAlpha(int(200 * u));
            double w = 120 * u;
            p.fillRect(QRectF(cx - w / 2, base_y + 22, w, 3), uc);
            if (hold) {
                double ph = std::fmod((t - T_SEQ) / 1.1, 1.0);
                double bx = cx - w / 2 + (w + 40) * ph - 20;
                QLinearGradient gl(QPointF(bx - 20, 0), QPointF(bx + 20, 0));
                QColor a0(255, 255, 255, 0), a1(255, 255, 255, 220);
                gl.setColorAt(0, a0); gl.setColorAt(0.5, a1); gl.setColorAt(1, a0);
                p.save(); p.setClipRect(QRectF(cx - w / 2, base_y + 21, w, 5));
                p.fillRect(QRectF(bx - 20, base_y + 21, 40, 5), gl);
                p.restore();
            }
        }
    }
}

void Intro::run() {
    HINSTANCE inst = GetModuleHandleW(nullptr);
    WNDCLASSW wc{};
    wc.lpfnWndProc = &Intro::proc;
    wc.hInstance = inst;
    wc.lpszClassName = L"EchoesIntroWindow";
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    RegisterClassW(&wc);
    RECT r;
    { std::lock_guard<std::mutex> g(mu); r = rect; }
    hwnd = CreateWindowExW(WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE, wc.lpszClassName, L"ECHOES",
                           WS_POPUP, r.left, r.top, r.right - r.left, r.bottom - r.top, nullptr, nullptr, inst, nullptr);
    if (!hwnd) { alive = false; return; }
    SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(this));
    SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA);
    ShowWindow(hwnd, SW_SHOWNOACTIVATE);
    HDC wdc = GetDC(hwnd);
    HDC mdc = CreateCompatibleDC(wdc);
    HBITMAP bmp = nullptr;
    void *bits = nullptr;
    int bw = 0, bh = 0;
    auto start = std::chrono::steady_clock::now();
    for (;;) {
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
        double t = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        HWND own = owner.load();
        if (own && IsWindow(own)) {                               // окно плеера уже есть — встать ровно поверх него
            // Только читаем, где окно плеера. Владельцем его НЕ делаем: у «владеемого» окна смена порядка
            // синхронизируется с потоком владельца, и пока поток интерфейса загружает плеер, заставка стояла.
            RECT cr; POINT tl{0, 0};
            if (!IsIconic(own) && GetClientRect(own, &cr) && ClientToScreen(own, &tl) && cr.right > 10 && cr.bottom > 10) {
                RECT want{tl.x, tl.y, tl.x + cr.right, tl.y + cr.bottom};
                if (want.left != r.left || want.top != r.top || want.right != r.right || want.bottom != r.bottom) {
                    r = want;
                    SetWindowPos(hwnd, nullptr, r.left, r.top, r.right - r.left, r.bottom - r.top, SWP_NOACTIVATE | SWP_NOZORDER);
                }
                UINT dpi = GetDpiForWindow(own);
                if (dpi) dpr = dpi / 96.0;
            }
        }
        if (ready && ready_t < 0) ready_t = t;
        double seq_end = skip ? std::min(T_SEQ, t) : T_SEQ;
        if (fade_t0 < 0 && ready && t >= seq_end) fade_t0 = t;
        double out = fade_t0 < 0 ? 0.0 : in_out((t - fade_t0) / FADE);
        const int W = std::max(1, int(r.right - r.left)), H = std::max(1, int(r.bottom - r.top));
        if (W != bw || H != bh || !bmp) {
            if (bmp) DeleteObject(bmp);
            BITMAPINFO bi{};
            bi.bmiHeader.biSize = sizeof bi.bmiHeader;
            bi.bmiHeader.biWidth = W;
            bi.bmiHeader.biHeight = -H;                           // сверху вниз, как QImage
            bi.bmiHeader.biPlanes = 1;
            bi.bmiHeader.biBitCount = 32;
            bi.bmiHeader.biCompression = BI_RGB;
            bmp = CreateDIBSection(mdc, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
            SelectObject(mdc, bmp);
            bw = W; bh = H;
        }
        if (bits) {
            QImage img(static_cast<uchar *>(bits), W, H, W * 4, QImage::Format_RGB32);
            QPainter p(&img);
            p.setRenderHint(QPainter::Antialiasing);
            p.setRenderHint(QPainter::TextAntialiasing);
            p.scale(dpr, dpr);
            draw(p, W / dpr, H / dpr, t, out);
            p.end();
            GdiFlush();
            BitBlt(wdc, 0, 0, W, H, mdc, 0, 0, SRCCOPY);
            SetLayeredWindowAttributes(hwnd, 0, BYTE(255 * (1.0 - out)), LWA_ALPHA);
            ++frames;
        }
        if (fade_t0 >= 0 && t - fade_t0 >= FADE) break;
        if (FAILED(DwmFlush())) Sleep(8);                         // кадр — в ритме экрана
    }
    if (bmp) DeleteObject(bmp);
    DeleteDC(mdc);
    ReleaseDC(hwnd, wdc);
    DestroyWindow(hwnd);
    hwnd = nullptr;
    alive = false;
}

}  // namespace

// Запустить заставку (один раз): прямоугольник на экране (пиксели устройства), dpr, акцент, надпись, шрифт.
EUI_API int eui_intro_start(int x, int y, int w, int h, double dpr, int r, int g, int b, const wchar_t *text,
                            const wchar_t *family) {
    if (g_intro) return 0;
    g_intro = new Intro;
    g_intro->rect = RECT{x, y, x + w, y + h};
    g_intro->dpr = dpr > 0 ? dpr : 1.0;
    g_intro->acc = QColor(r, g, b);
    if (text && *text) g_intro->text = QString::fromWCharArray(text);
    if (family) g_intro->family = QString::fromWCharArray(family);
    g_intro->alive = true;
    g_intro->th = std::thread([] { g_intro->run(); });
    g_intro->th.detach();
    return 1;
}

// окно плеера показано — заставка встаёт ровно поверх его клиентской области и держится над ним
EUI_API void eui_intro_follow(intptr_t hwnd) { if (g_intro) g_intro->owner = reinterpret_cast<HWND>(hwnd); }
// плеер всё загрузил — досмотреть сюжет и раствориться
EUI_API void eui_intro_ready() { if (g_intro) g_intro->ready = true; }
EUI_API int eui_intro_alive() { return g_intro && g_intro->alive ? 1 : 0; }
// сколько кадров показано (проверка: заставка идёт, пока поток интерфейса занят)
EUI_API int eui_intro_frames() { return g_intro ? g_intro->frames.load() : 0; }

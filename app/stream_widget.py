"""Стример-тул: локальная страница «сейчас играет» (обложка, трек, визуализатор)
для OBS / TikTok LIVE Studio (источник «Браузер» → http://127.0.0.1:PORT/).

Главное окно раз в ~50 мс кладёт снимок в update(); HTTP-поток только отдаёт готовые данные."""
import json
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import numpy as np

PORT = 17845
_state = {"title": "", "artist": "", "pos": 0.0, "dur": 0.0, "playing": False, "cover": "", "v": 0}
_bands = [0.0] * 48
_lock = threading.Lock()
_server = None

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>ECHOES now playing</title>
<style>
html,body{margin:0;background:transparent;font-family:'Segoe UI',Arial,sans-serif;color:#fff;overflow:hidden}
#w{display:flex;align-items:center;gap:16px;padding:14px 18px;margin:10px;border-radius:18px;
background:rgba(12,12,18,.72);width:max-content;min-width:420px;max-width:760px}
#c{width:96px;height:96px;border-radius:12px;object-fit:cover;background:#222;flex:none}
#i{display:flex;flex-direction:column;min-width:0;flex:1}
#t{font-size:22px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#a{font-size:16px;opacity:.75;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
#v{width:100%;height:46px;margin-top:8px}
#p{height:4px;border-radius:2px;background:rgba(255,255,255,.18);margin-top:6px;overflow:hidden}
#pf{height:100%;width:0;background:#fff}
</style></head><body><div id="w"><img id="c"><div id="i"><div id="t"></div><div id="a"></div>
<canvas id="v"></canvas><div id="p"><div id="pf"></div></div></div></div>
<script>
const q=new URLSearchParams(location.search),acc=q.get('color')||'#ffffff';
if(q.get('bg')==='0')document.getElementById('w').style.background='transparent';
document.getElementById('pf').style.background=acc;
let st={},cv=-1,bands=[],sm=[];const cvs=document.getElementById('v'),g=cvs.getContext('2d');
async function now(){try{st=await (await fetch('/now')).json();
document.getElementById('t').textContent=st.title||'';document.getElementById('a').textContent=st.artist||'';
if(st.v!==cv){cv=st.v;const c=document.getElementById('c');c.src='/cover?'+cv;c.style.display=st.cover?'':'none';}
document.getElementById('pf').style.width=(st.dur?100*st.pos/st.dur:0)+'%';}catch(e){}}
async function spec(){try{bands=await (await fetch('/bands')).json();}catch(e){}}
function draw(){const w=cvs.clientWidth,h=cvs.clientHeight;cvs.width=w;cvs.height=h;g.clearRect(0,0,w,h);
const n=bands.length;if(n){const bw=w/n;g.fillStyle=acc;for(let i=0;i<n;i++){sm[i]=Math.max(bands[i],(sm[i]||0)*0.85);
const bh=Math.max(2,sm[i]*h);g.globalAlpha=.55+.45*sm[i];g.fillRect(i*bw+1,h-bh,bw-2,bh);}}requestAnimationFrame(draw);}
setInterval(now,500);setInterval(spec,50);now();draw();
</script></body></html>"""


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        with _lock:
            s, b = dict(_state), list(_bands)
        if p == "/now":
            self._send(json.dumps(s).encode(), "application/json")
        elif p == "/bands":
            self._send(json.dumps([round(x, 3) for x in b]).encode(), "application/json")
        elif p == "/cover":
            c = s.get("cover") or ""
            try:
                data = Path(c).read_bytes() if c else b""
            except OSError:
                data = b""
            self._send(data, "image/png" if c.lower().endswith(".png") else "image/jpeg")
        else:
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")


def url() -> str:
    return f"http://127.0.0.1:{PORT}/"


def running() -> bool:
    return _server is not None


def start() -> str:
    global _server
    if _server is None:
        _server = ThreadingHTTPServer(("127.0.0.1", PORT), _H)
        _server.daemon_threads = True
        threading.Thread(target=_server.serve_forever, daemon=True, name="stream_widget").start()
    return url()


def update(state: dict, samples):
    """state — из _cover_state(); samples — моно float32 (или None)."""
    b = None
    if samples is not None:
        samples = np.asarray(samples, dtype=float)
        if samples.ndim > 1:
            samples = samples.mean(axis=-1)
        samples = np.clip(np.nan_to_num(samples.reshape(-1), nan=0.0, posinf=0.0, neginf=0.0), -1.0, 1.0)
    if samples is not None and len(samples) >= 4 and state.get("playing"):
        spec = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))[1:]
        edges = np.unique(np.geomspace(1, len(spec), len(_bands) + 1).astype(int))
        vals = [spec[a:max(a + 1, z)].mean() for a, z in zip(edges[:-1], edges[1:])]
        b = np.clip(np.log10(1 + np.asarray(vals)) / 2.2, 0, 1).tolist()
    with _lock:
        if state.get("cover") != _state.get("cover") or state.get("title") != _state.get("title"):
            _state["v"] += 1
        for k in ("title", "artist", "pos", "dur", "playing", "cover"):
            _state[k] = state.get(k, _state[k])
        _bands[:] = (b + [0.0] * len(_bands))[:len(_bands)] if b else [0.0] * len(_bands)


# ── отдельное окно ECHOES (OBS: «Захват окна», TikTok Studio: «Окно») ──
from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import QPainter, QColor, QFont, QPixmap, QPainterPath
from PyQt6.QtWidgets import QWidget


class NowPlayingWindow(QWidget):
    def __init__(self, win):
        super().__init__(None, Qt.WindowType.Window)
        self.win = win
        self.setWindowTitle("ECHOES — Сейчас играет")
        from stream_designer import default_design
        design = win.settings.get('stream_design') or default_design()
        self.apply_design()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda _: self.open_designer())
        self.setMinimumSize(320, 100)
        self._st, self._cover_path, self._pix = {}, None, None
        self._bands = np.zeros(48)
        self._t = QTimer(self)                        # свой таймер: не встаёт, когда плеер не в фокусе
        self._t.timeout.connect(self._tick)
        self._t.start(33)

    def _tick(self):
        try:
            st = self.win._cover_state()
            smp = self.win.engine.get_visual_samples(2048) if st["playing"] else None
        except Exception:                             # noqa: BLE001
            return
        update(st, smp)
        with _lock:
            b = np.asarray(_bands, float)
        self._bands = np.maximum(b, self._bands * 0.85)
        if st.get("cover") != self._cover_path:
            self._cover_path = st.get("cover")
            try:
                import img_load
                self._pix = img_load.load_pixmap(self._cover_path) if self._cover_path else None
            except Exception:                         # noqa: BLE001
                self._pix = QPixmap(self._cover_path) if self._cover_path else None
        self._st = st
        self.update()

    def apply_design(self):
        from stream_designer import default_design
        design = self.win.settings.get('stream_design') or default_design()
        used = {item.get('file', '') for item in design['items'] if item.get('kind') == 'image'}
        for key, asset in list(getattr(self, '_asset_cache', {}).items()):
            if key not in used:
                if hasattr(asset, 'stop'):
                    asset.stop()
                    asset.deleteLater()
                del self._asset_cache[key]
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, bool(design.get('frameless')))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, bool(design.get('frameless')))
        self.resize(design['width'], design['height'])
        if visible:
            self.show()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.windowHandle():
            self.windowHandle().startSystemMove()
        super().mousePressEvent(event)

    def open_designer(self):
        from stream_designer import StreamEditor
        StreamEditor(self.win).exec()

    def showEvent(self, event):
        super().showEvent(event)
        self._t.start(33)
        for asset in getattr(self, '_asset_cache', {}).values():
            if hasattr(asset, 'setPaused'):
                asset.setPaused(False)

    def hideEvent(self, event):
        self._t.stop()
        for asset in getattr(self, '_asset_cache', {}).values():
            if hasattr(asset, 'setPaused'):
                asset.setPaused(True)
        super().hideEvent(event)

    def paintEvent(self, _):
        from stream_designer import paint_scene, default_design
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_scene(self, p, self.win.settings.get('stream_design') or default_design(),
                    self._st, self._bands, self._pix)
        p.end()

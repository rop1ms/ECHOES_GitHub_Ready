# net_tor.py
"""
Tor для Echoes.NET: свой tor.exe, onion-адрес вместо сервера.

Откуда берётся tor.exe (по порядку):
  1. путь из настроек сети (можно указать tor.exe из Tor Browser);
  2. ~/Echoes/net/tor_bin — Tor Expert Bundle, который плеер скачивает сам
     с dist.torproject.org по кнопке «Скачать Tor» (~20 МБ, один раз);
  3. установленный Tor Browser (Рабочий стол / Program Files / AppData).

Как подключается (режим «auto»): сначала напрямую; если за TIMEOUT_DIRECT секунд
не дошли до 100 % — перезапуск со встроенными мостами (snowflake, obfs4 из pt_config.json
комплекта) — в России Tor напрямую часто заблокирован. Режим «bridges» — мосты
пользователя (строки «obfs4 …», «webtunnel …» из @GetBridgesBot / bridges.torproject.org).

Управление — по ControlPort (cookie), onion-сервис — ADD_ONION с НАШИМ ключом ed25519:
адрес = ключ личности, поэтому его нельзя подделать (net_core.onion_from_pub).
__OwningControllerProcess = pid плеера: плеер закрылся (даже аварийно) — tor завершается сам.
Модуль без Qt; всё сетевое — asyncio в потоке NetNode.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from pathlib import Path

TIMEOUT_DIRECT = 75          # сек: прямое подключение не дошло до 100 % — пробуем мосты
TIMEOUT_BRIDGES = 150
LINE_LIMIT = 4 * 2 ** 20     # одна строка протокола (синхронизация ленты) — не больше 4 МБ
DOWNLOAD_PAGES = ("https://www.torproject.org/download/tor/", "https://dist.torproject.org/torbrowser/")
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TorError(Exception):
    pass


class TorManager:
    def __init__(self, base: Path, settings: dict, log=print):
        self.base = Path(base)
        self.bin_dir = self.base / "tor_bin"
        self.data_dir = self.base / "tor_data"
        self.settings = settings
        self.log = log
        self.proc = None
        self.socks_port = 0
        self.control_port = 0
        self._ctl = None                    # (reader, writer) — держим открытым: onion живёт, пока жив контроль
        self.mode_used = ""

    # ── где tor.exe ── #

    def find_tor(self) -> Path | None:
        cands = []
        if self.settings.get("tor_path"):
            cands.append(Path(self.settings["tor_path"]))
        cands.append(self.bin_dir / "tor" / "tor.exe")
        home = Path.home()
        for root in (home / "Desktop", home / "OneDrive" / "Desktop", home / "AppData" / "Local",
                     Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
                     Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))):
            cands.append(root / "Tor Browser" / "Browser" / "TorBrowser" / "Tor" / "tor.exe")
        for c in cands:
            try:
                if c.is_file():
                    return c
            except OSError:
                pass
        return None

    def _pt_dir(self, tor: Path) -> Path | None:
        for d in (tor.parent / "pluggable_transports", tor.parent / "PluggableTransports"):
            if d.is_dir():
                return d
        return None

    def _geoip(self, tor: Path):
        for d in (tor.parent.parent / "data", tor.parent.parent / "Data" / "Tor", tor.parent / "data"):
            if (d / "geoip").is_file():
                return d / "geoip", d / "geoip6"
        return None, None

    # ── скачивание Tor Expert Bundle ── #

    def download(self, progress=None) -> Path:
        """Скачать и распаковать Tor Expert Bundle (синхронно, звать из фонового потока)."""
        import requests
        url = None
        for page in DOWNLOAD_PAGES:
            try:
                html = requests.get(page, timeout=20).text
            except Exception as e:                           # noqa: BLE001
                self.log(f"[net] tor page {page}: {e}")
                continue
            links = re.findall(r"https://[^\"'\s]*tor-expert-bundle-windows-x86_64-[0-9.]+\.tar\.gz", html)
            if links:
                url = links[0]
                break
            vers = re.findall(r'href="(\d+\.\d+(?:\.\d+)?)/"', html)         # листинг каталога: только стабильные
            if vers:
                v = sorted(vers, key=lambda s: tuple(int(x) for x in s.split(".")))[-1]
                url = f"{page}{v}/tor-expert-bundle-windows-x86_64-{v}.tar.gz"
                break
        if not url:
            raise TorError("Сайт Tor недоступен. Скачайте Tor Browser и укажите его tor.exe в настройках сети.")
        self.log(f"[net] tor download {url}")
        tmp = self.base / "tor_bundle.tar.gz.part"
        self.base.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            got = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(256 * 1024):
                    f.write(chunk)
                    got += len(chunk)
                    if progress and total:
                        progress(got / total)
        out = self.bin_dir.with_name("tor_bin.new")
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        with tarfile.open(tmp, "r:gz") as tf:
            for m in tf.getmembers():                        # только обычные файлы внутри папки
                name = m.name.replace("\\", "/")
                if not (m.isfile() or m.isdir()) or name.startswith("/") or ".." in name.split("/"):
                    continue
                tf.extract(m, out)
        tmp.unlink(missing_ok=True)
        if not (out / "tor" / "tor.exe").is_file():
            raise TorError("В архиве Tor нет tor.exe")
        shutil.rmtree(self.bin_dir, ignore_errors=True)
        out.rename(self.bin_dir)
        return self.bin_dir / "tor" / "tor.exe"

    # ── запуск ── #

    def _builtin_bridges(self, pt: Path | None) -> list[str]:
        try:
            cfg = json.loads((pt / "pt_config.json").read_text(encoding="utf-8"))
            br = cfg.get("bridges", {})
            return list(br.get("snowflake", []))[:2] + list(br.get("obfs4", []))[:6]
        except Exception:                                    # noqa: BLE001
            return []

    def _transport_lines(self, pt: Path | None) -> list[str]:
        if pt is None:
            return []
        try:
            cfg = json.loads((pt / "pt_config.json").read_text(encoding="utf-8"))
            lines = []
            for line in cfg.get("pluggableTransports", {}).values():
                lines.append(line.replace("${pt_path}", str(pt) + os.sep))
            return lines
        except Exception:                                    # noqa: BLE001
            lyre = pt / "lyrebird.exe"
            return [f"ClientTransportPlugin meek_lite,obfs4,webtunnel,snowflake exec {lyre}"] if lyre.exists() else []

    def _write_torrc(self, tor: Path, bridges: list[str]) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.socks_port, self.control_port = _free_port(), _free_port()
        g4, g6 = self._geoip(tor)
        lines = [f"SocksPort 127.0.0.1:{self.socks_port}",
                 f"ControlPort 127.0.0.1:{self.control_port}",
                 "CookieAuthentication 1",
                 f"DataDirectory {self.data_dir}",
                 f"__OwningControllerProcess {os.getpid()}",
                 "ClientOnly 1", "AvoidDiskWrites 1",
                 f"Log notice file {self.data_dir / 'tor.log'}"]
        if g4:
            lines += [f"GeoIPFile {g4}", f"GeoIPv6File {g6}"]
        if bridges:
            lines.append("UseBridges 1")
            lines += self._transport_lines(self._pt_dir(tor))
            lines += [("Bridge " + b.strip()) for b in bridges if b.strip()]
        rc = self.data_dir / "torrc"
        rc.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return rc

    async def start(self, on_progress) -> int:
        """Запустить tor и дождаться 100 %. on_progress(pct, text). Возвращает SOCKS-порт."""
        tor = self.find_tor()
        if tor is None:
            raise TorError("no_tor")
        mode = self.settings.get("mode", "auto")
        user_br = [b for b in re.split(r"[\r\n]+", self.settings.get("bridges", "")) if b.strip()]
        user_br = [re.sub(r"^\s*Bridge\s+", "", b) for b in user_br]
        plans = []
        if mode == "bridges" and user_br:
            plans = [("bridges", user_br, TIMEOUT_BRIDGES)]
        elif mode == "direct":
            plans = [("direct", [], TIMEOUT_BRIDGES)]
        else:
            builtin = self._builtin_bridges(self._pt_dir(tor))
            plans = [("direct", [], TIMEOUT_DIRECT)]
            if user_br:
                plans.append(("bridges", user_br, TIMEOUT_BRIDGES))
            if builtin:
                plans.append(("builtin", builtin, TIMEOUT_BRIDGES))
            if self.settings.get("last") in ("bridges", "builtin"):     # в прошлый раз помогли мосты — с них
                plans.sort(key=lambda p: p[0] != self.settings["last"])
        last_err = "Tor не подключился"
        for name, bridges, timeout in plans:
            self.stop()
            label = {"direct": "напрямую", "bridges": "через ваши мосты", "builtin": "через встроенные мосты"}[name]
            on_progress(0, f"Tor: подключение {label}…")
            rc = self._write_torrc(tor, bridges)
            try:
                self.proc = subprocess.Popen([str(tor), "-f", str(rc)], cwd=str(tor.parent),
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                             creationflags=_NO_WINDOW)
                await self._control_connect()
                t0 = time.time()
                while time.time() - t0 < timeout:
                    if self.proc.poll() is not None:
                        raise TorError(f"tor завершился (код {self.proc.returncode})")
                    pct, summary = await self._bootstrap()
                    on_progress(pct, f"Tor {label}: {pct}% · {summary}")
                    if pct >= 100:
                        self.mode_used = name
                        self.settings["last"] = name
                        return self.socks_port
                    await asyncio.sleep(1.0)
                last_err = f"Tor {label}: не подключился за {timeout} с"
            except (OSError, TorError, asyncio.TimeoutError) as e:
                last_err = str(e) or last_err
            self.log(f"[net] {last_err}")
        self.stop()
        raise TorError(last_err)

    # ── ControlPort ── #

    async def _control_connect(self):
        cookie_f = self.data_dir / "control_auth_cookie"
        for _ in range(100):                                 # tor пишет cookie после старта
            try:
                r, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", self.control_port), 2)
                if cookie_f.exists():
                    break
                w.close()
            except (OSError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(0.2)
        else:
            raise TorError("нет связи с tor (ControlPort)")
        self._ctl = (r, w)
        cookie = cookie_f.read_bytes().hex()
        code, lines = await self._cmd(f"AUTHENTICATE {cookie}")
        if code != "250":
            raise TorError("tor: ошибка авторизации " + " ".join(lines))
        self._ctl_lock = asyncio.Lock()

    async def _cmd(self, line: str):
        r, w = self._ctl
        w.write((line + "\r\n").encode())
        await w.drain()
        out = []
        while True:
            raw = await asyncio.wait_for(r.readline(), 30)
            if not raw:
                raise TorError("tor закрыл ControlPort")
            s = raw.decode("utf-8", "replace").rstrip("\r\n")
            out.append(s[4:])
            if len(s) >= 4 and s[3] == " ":
                return s[:3], out

    async def _bootstrap(self):
        async with self._ctl_lock:
            code, lines = await self._cmd("GETINFO status/bootstrap-phase")
        txt = " ".join(lines)
        m = re.search(r"PROGRESS=(\d+)", txt)
        s = re.search(r'SUMMARY="([^"]*)"', txt)
        return (int(m.group(1)) if m else 0), (s.group(1) if s else "")

    async def add_onion(self, expanded_key_b64: str, local_port: int, virt_port: int = 80) -> str:
        async with self._ctl_lock:
            code, lines = await self._cmd(f"ADD_ONION ED25519-V3:{expanded_key_b64} Flags=DiscardPK "
                                          f"Port={virt_port},127.0.0.1:{local_port}")
        if code != "250":
            raise TorError("tor: не удалось открыть onion-адрес: " + " ".join(lines))
        for ln in lines:
            if ln.startswith("ServiceID="):
                return ln.split("=", 1)[1].strip()
        raise TorError("tor: нет ServiceID")

    def stop(self):
        if self._ctl:
            try:
                self._ctl[1].close()
            except Exception:                                # noqa: BLE001
                pass
            self._ctl = None
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(5)
            except Exception:                                # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:                            # noqa: BLE001
                    pass
        self.proc = None


async def socks_connect(socks_port: int, host: str, port: int, timeout=90.0):
    """TCP через SOCKS5 Tor к host:port (onion-имя резолвит сам tor). Возвращает (reader, writer)."""
    async def go():
        r, w = await asyncio.open_connection("127.0.0.1", socks_port, limit=LINE_LIMIT)
        w.write(b"\x05\x01\x00")
        await w.drain()
        if (await r.readexactly(2)) != b"\x05\x00":
            raise TorError("SOCKS: отказ")
        hb = host.encode()
        w.write(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + port.to_bytes(2, "big"))
        await w.drain()
        head = await r.readexactly(4)
        if head[1] != 0:
            w.close()
            raise TorError(f"SOCKS: код {head[1]} (собеседник не в сети)")
        atyp = head[3]
        n = 4 if atyp == 1 else 16 if atyp == 4 else (await r.readexactly(1))[0]
        await r.readexactly(n + 2)
        return r, w
    return await asyncio.wait_for(go(), timeout)

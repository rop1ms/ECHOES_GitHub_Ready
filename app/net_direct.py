# net_direct.py
"""
Echoes.NET: прямые соединения без Tor — быстрее (голос, файлы), но друг видит ваш IP.
Поэтому адреса получают только друзья (в «hi»), а личность всё равно проверяется подписью.

  • LAN — плееры в одной сети находят друг друга широковещательным «маячком» (UDP 47474) —
    работает даже без интернета;
  • IPv6 — у многих провайдеров глобальный IPv6 без NAT: соединение напрямую, без настроек;
  • UPnP — роутер сам открывает порт (AddPortMapping), если это разрешено в его настройках;
    внешний IPv4 берём у роутера (GetExternalIPAddress).
Модуль без Qt; блокирующие операции (UPnP по HTTP) зовутся из потоков-исполнителей.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import time
from urllib.parse import urljoin

LAN_PORT = 47474
SSDP = ("239.255.255.250", 1900)


def lan_ipv4() -> list[str]:
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))                             # пакет не уходит — так узнаём свой адрес в сети
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = ai[4][0]
            if ip not in out and not ip.startswith("127.") and not ip.startswith("169.254."):
                out.append(ip)
    except OSError:
        pass
    return out


def global_ipv6() -> list[str]:
    out = []
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        s.connect(("2001:4860:4860::8888", 80))
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    good = []
    for ip in out:
        try:
            a = ipaddress.IPv6Address(ip.split("%")[0])
            if a.is_global:
                good.append(str(a))
        except ValueError:
            pass
    return good


def is_private(ip) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


# ── UPnP IGD ── #

def _soap(url, service, action, args: dict) -> str:
    import requests
    body = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    env = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
           f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
           f'<u:{action} xmlns:u="{service}">{body}</u:{action}></s:Body></s:Envelope>')
    r = requests.post(url, data=env.encode(), timeout=6,
                      headers={"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{service}#{action}"'})
    r.raise_for_status()
    return r.text


def upnp_find():
    """Найти роутер с UPnP: (controlURL, тип сервиса) или None."""
    import requests
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\n"
           "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n").encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    locs = []
    try:
        s.sendto(msg, SSDP)
        t0 = time.time()
        while time.time() - t0 < 3:
            try:
                data, _ = s.recvfrom(4096)
            except socket.timeout:
                break
            m = re.search(rb"(?i)location:\s*(\S+)", data)
            if m and m.group(1).decode() not in locs:
                locs.append(m.group(1).decode())
    finally:
        s.close()
    for loc in locs:
        try:
            xml = requests.get(loc, timeout=5).text
        except Exception:                                      # noqa: BLE001
            continue
        for svc in ("urn:schemas-upnp-org:service:WANIPConnection:1", "urn:schemas-upnp-org:service:WANIPConnection:2",
                    "urn:schemas-upnp-org:service:WANPPPConnection:1"):
            i = xml.find(svc)
            if i < 0:
                continue
            m = re.search(r"<controlURL>\s*([^<]+)\s*</controlURL>", xml[i:])
            if m:
                return urljoin(loc, m.group(1).strip()), svc
    return None


def upnp_map(port, lan_ip):
    """Открыть TCP-порт на роутере. Возвращает (внешний IP, порт, controlURL, сервис) или None."""
    found = upnp_find()
    if not found:
        return None
    url, svc = found
    _soap(url, svc, "AddPortMapping", {"NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "TCP",
                                       "NewInternalPort": port, "NewInternalClient": lan_ip, "NewEnabled": 1,
                                       "NewPortMappingDescription": "ECHOES Echoes.NET", "NewLeaseDuration": 7200})
    txt = _soap(url, svc, "GetExternalIPAddress", {})
    m = re.search(r"<NewExternalIPAddress>([^<]+)</NewExternalIPAddress>", txt)
    ext = m.group(1).strip() if m else ""
    if not ext or is_private(ext):                             # роутер за ещё одним NAT провайдера — снаружи не достучаться
        return None
    return ext, port, url, svc


def upnp_unmap(url, svc, port):
    try:
        _soap(url, svc, "DeletePortMapping", {"NewRemoteHost": "", "NewExternalPort": port, "NewProtocol": "TCP"})
    except Exception:                                          # noqa: BLE001
        pass


# ── LAN-маячок ── #

class LanBeacon(asyncio.DatagramProtocol):
    """Раз в 15 с: «я — такой-то onion, жду на таком-то порту». Слышим чужие — сообщаем узлу."""

    def __init__(self, onion, port, on_peer):
        self.onion, self.port, self.on_peer = onion, port, on_peer
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        try:
            d = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if d.get("e") != "echoes" or d.get("o") == self.onion:
            return
        try:
            self.on_peer(str(d.get("o", ""))[:56], addr[0], int(d.get("p")))
        except (TypeError, ValueError):
            pass

    def announce(self):
        if self.transport is None:
            return
        msg = json.dumps({"e": "echoes", "o": self.onion, "p": self.port}).encode()
        try:
            self.transport.sendto(msg, ("255.255.255.255", LAN_PORT))
        except OSError:
            pass


async def start_beacon(loop, onion, port, on_peer):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", LAN_PORT))
    _tr, proto = await loop.create_datagram_endpoint(lambda: LanBeacon(onion, port, on_peer), sock=sock)
    return proto

"""Public playlist metadata only: no OAuth, browser cookies or protected audio retrieval.

Modern Yandex playlist shapes: github.com/fgsfds1/yt-dlp-yandex-music-plugin.
The resulting metadata is matched by ECHOES' existing music importer.
"""
import html
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit,urljoin,unquote,quote
import requests

LIMIT=5000
MAX_BYTES=12*2**20
UA={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'}

def text(value,limit=300):
    return html.unescape(re.sub(r'<[^>]*>','',str(value or ''))).strip()[:limit]

def allowed(url,provider):
    p=urlsplit(url);host=(p.hostname or '').lower()
    valid=bool(re.fullmatch(r'music\.yandex\.(ru|com|kz|by|uz|com\.tr)',host)) if provider=='yandex' else host in ('vk.com','vk.ru','m.vk.com','m.vk.ru','www.vk.com','www.vk.ru','vk.cc')
    if provider=='yandex' and host in ('api.music.yandex.net','api.music.yandex.ru'):valid=True
    if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or not valid:raise ValueError('invalid_link')

def read(session,url,provider,**kwargs):
    for _ in range(5):
        allowed(url,provider)
        with session.get(url,headers=UA,timeout=(8,18),stream=True,allow_redirects=False,**kwargs) as response:
            if response.is_redirect:
                url=urljoin(url,response.headers.get('Location',''));continue
            response.raise_for_status();chunks=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>MAX_BYTES:raise ValueError('page_too_large')
                chunks.append(chunk)
            raw=b''.join(chunks)
            encoding=response.encoding if response.encoding and response.encoding.lower()!='iso-8859-1' else 'utf-8'
            return raw.decode(encoding,errors='replace'),url
    raise ValueError('too_many_redirects')

class PageData(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.scripts=[];self.script=None;self.audio=[];self.playlists=[];self.title='';self.in_title=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='script':self.script=[]
        if tag=='title':self.in_title=True
        if tag=='meta' and attrs.get('property')=='og:title':self.title=attrs.get('content','')
        for key,target in [('data-audio',self.audio),('data-playlist',self.playlists)]:
            if attrs.get(key):
                try:target.append(json.loads(attrs[key]))
                except (ValueError,TypeError):pass
    def handle_data(self,data):
        if self.script is not None:self.script.append(data)
        if self.in_title:self.title+=data
    def handle_endtag(self,tag):
        if tag=='script' and self.script is not None:self.scripts.append(''.join(self.script));self.script=None
        if tag=='title':self.in_title=False

def objects(page):
    decoder=json.JSONDecoder();scripts=list(page.scripts);decoded=[];preloaded=[]
    for script in page.scripts:
        for match in re.finditer(r'__next_f\.push\s*\(',script):
            try:
                packet,_=decoder.raw_decode(script[match.end():].lstrip())
                if isinstance(packet,list):scripts.extend(x for x in packet if isinstance(x,str))
            except ValueError:pass
    for script in scripts:
        try:decoded.append(json.loads(script))
        except ValueError:pass
        for match in re.finditer(r'"(?:preloadedPlaylistByUuid|preloadedPlaylist|preloadedAlbum)"\s*:\s*',script):
            try:preloaded.append(decoder.raw_decode(script[match.end():])[0])
            except ValueError:pass
    yield from (preloaded or decoded)

def containers(root):
    pending=[root];visited=0
    while pending and visited<60000:
        value=pending.pop();visited+=1
        if isinstance(value,dict):
            if any(isinstance(value.get(key),list) for key in ('tracks','list','audios','volumes')):yield value
            pending.extend(v for v in value.values() if isinstance(v,(dict,list)))
        elif isinstance(value,list):pending.extend(v for v in value if isinstance(v,(dict,list)))

def track(raw,provider):
    if provider=='vk' and isinstance(raw,list) and len(raw)>=6 and isinstance(raw[0],(int,str)) and isinstance(raw[3],str) and isinstance(raw[4],str):
        raw={'title':raw[3],'artist':raw[4],'duration':raw[5]}
    if not isinstance(raw,dict):return None
    raw=raw.get('track') if isinstance(raw.get('track'),dict) else raw
    title=text(raw.get('title') or raw.get('name'))
    artists=raw.get('artists') or []
    artist=', '.join(text(a.get('name') if isinstance(a,dict) else a,200) for a in artists) if isinstance(artists,list) else ''
    artist=artist or text(raw.get('artist') or raw.get('performer'),200)
    if not title or not artist:return None
    try:duration=max(0.,min(36000.,float(raw.get('durationMs',0))/1000 if raw.get('durationMs') else float(raw.get('duration') or 0)))
    except (ValueError,TypeError,OverflowError):duration=0.
    albums=raw.get('albums') or [];album=albums[0] if isinstance(albums,list) and albums else raw.get('album','')
    return {'title':title,'artist':artist,'duration':duration,'album':text(album.get('title') if isinstance(album,dict) else album,200)}

def selection(root,provider):
    best=None
    for playlist in containers(root):
        rows=playlist.get('tracks') or playlist.get('list') or playlist.get('audios') or []
        if not rows and isinstance(playlist.get('volumes'),list):rows=[t for volume in playlist['volumes'] if isinstance(volume,list) for t in volume]
        parsed=[t for row in rows[:LIMIT] if (t:=track(row,provider))]
        if parsed and (best is None or len(parsed)>len(best[1])):
            try:expected=max(len(rows),int(playlist.get('trackCount') or playlist.get('totalCount') or playlist.get('count') or 0))
            except (ValueError,TypeError):expected=len(rows)
            if playlist.get('hasMore') or playlist.get('has_more'):expected=max(expected,len(parsed)+1)
            elif provider=='vk' and not any(key in playlist for key in ('trackCount','totalCount','count')):expected=-1
            best=(text(playlist.get('title') or playlist.get('name'),80),parsed,expected)
    return best

def fetch(provider,url,language='en'):
    ru=language=='ru'
    def message(a,b):return a if ru else b
    url=url.strip()
    if not url.startswith(('http://','https://')):url='https://'+url
    try:allowed(url,provider)
    except ValueError:raise ValueError(message('Вставьте HTTPS-ссылку на музыкальный плейлист выбранного сервиса.','Enter an HTTPS music-playlist link for the selected service.'))
    best=None;page=None
    def take(candidate):
        nonlocal best
        if candidate and (best is None or len(candidate[1])>len(best[1])):best=candidate
    with requests.Session() as session:
        try:
            body,final=read(session,url,provider);page=PageData();page.feed(body)
            for value in list(page.playlists)+list(objects(page)):take(selection(value,provider))
            if provider=='vk' and page.audio:take(selection({'title':page.title,'list':page.audio},provider))
            url=final
        except (requests.RequestException,ValueError):pass
        if provider=='yandex':
            path=urlsplit(url).path.rstrip('/');api_path=None
            if re.fullmatch(r'/playlists/[A-Za-z0-9.-]+',path):api_path='/playlist/'+quote(path.rsplit('/',1)[1],safe='.')
            elif re.fullmatch(r'/users/[^/]+/playlists/[A-Za-z0-9.-]+',path):api_path=path
            elif re.fullmatch(r'/album/\d+',path):api_path=path.replace('/album/','/albums/')+'/with-tracks'
            if api_path and (best is None or len(best[1])<best[2]):
                for host in ('api.music.yandex.ru','api.music.yandex.net'):
                    try:
                        raw,_=read(session,'https://'+host+api_path,provider,params={'rich-tracks':'true','richTracks':'true','page-size':LIMIT})
                        take(selection(json.loads(raw),provider))
                    except (ValueError,requests.RequestException):continue
                    if best and len(best[1])>=best[2]:break
        else:
            decoded=unquote(url)
            match=re.search(r'(?:audio_playlist|music/(?:playlist|album)/)(-?\d+)_(\d+)(?:_([A-Za-z0-9]+))?',decoded)
            if match and (best is None or best[2]<0 or len(best[1])<best[2]):
                owner,pid,access=match.groups()
                mobile='https://m.vk.com/audio'
                try:
                    raw,_=read(session,mobile,provider,params={'act':'audio_playlist','owner_id':owner,'playlist_id':pid,'access_hash':access or ''})
                    mobile_page=PageData();mobile_page.feed(raw)
                    for value in list(mobile_page.playlists)+list(objects(mobile_page)):take(selection(value,provider))
                    if mobile_page.audio:take(selection({'title':mobile_page.title,'list':mobile_page.audio},provider))
                except (ValueError,requests.RequestException):pass
    if best is None:
        raise RuntimeError(message('Сервис не отдал публичный список треков. Возможны вход, капча или закрытый плейлист. Откройте доступ либо загрузите TXT/M3U со списком — токен не нужен.',
            'The service did not expose a public track list. It may require login, a CAPTCHA, or playlist access. Make the playlist public or load a TXT/M3U track list; no token is needed.'))
    name,tracks,expected=best
    warning=''
    if expected<0:
        warning=message('Полнота публичного списка не подтверждена сервисом. Если не хватает треков, используйте файл со списком.',
                        'The service did not confirm that this public list is complete. Use a track-list file if songs are missing.')
    elif len(tracks)<expected:
        warning=message(f'Получено {len(tracks)} из {expected} треков или больше. Сервис отдал неполный список; для полного переноса используйте файл.',
                        f'Received {len(tracks)} of at least {expected} tracks. The service returned a partial list; use a file for a complete transfer.')
    return name or ('Яндекс Музыка' if provider=='yandex' and ru else 'Yandex Music' if provider=='yandex' else 'VK Music'),{'tracks':tracks,'warning':warning}

# net_store.py
"""
Echoes.NET: всё, что сеть помнит, — на ЭТОМ компьютере, в ~/.neon_player/net/net.db (SQLite).

  contacts  — друзья и заявки (onion-адрес = личность), имя, что слушает, когда был в сети;
  messages  — переписка (delivered = дошло до собеседника; пока нет — уйдёт при следующей встрече);
  posts     — свои посты и копии постов друзей (подписаны автором ed25519 — подделать нельзя);
  comments, votes — треды и апвоуты; хранит их АВТОР поста и раздаёт друзьям вместе с постом;
  outbox    — голоса/комментарии/ответы на заявки, которые ждут, когда собеседник появится.

Очистка: чат целиком, вся переписка, кэш ленты, всё сразу. Ключ личности — отдельно (identity.key).
Сервера нет, поэтому то, что уже ушло друзьям, остаётся и у них — это честно сказано в интерфейсе.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    onion TEXT PRIMARY KEY, name TEXT DEFAULT '', bio TEXT DEFAULT '', status TEXT DEFAULT 'friend',
    added REAL DEFAULT 0, last_seen REAL DEFAULT 0, now TEXT DEFAULT '', room TEXT DEFAULT '', note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, peer TEXT, dir TEXT, ts REAL, text TEXT, track TEXT DEFAULT '',
    delivered INTEGER DEFAULT 0, read INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS msg_peer ON messages(peer, ts);
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY, author TEXT, ts REAL, text TEXT, track TEXT DEFAULT '', sig TEXT, raw TEXT
);
CREATE INDEX IF NOT EXISTS post_ts ON posts(ts);
CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY, post TEXT, parent TEXT DEFAULT '', author TEXT, ts REAL, text TEXT, raw TEXT
);
CREATE INDEX IF NOT EXISTS com_post ON comments(post, ts);
CREATE TABLE IF NOT EXISTS votes (
    post TEXT, voter TEXT, val INTEGER, ts REAL, raw TEXT, PRIMARY KEY (post, voter)
);
CREATE TABLE IF NOT EXISTS reacts (
    post TEXT, who TEXT, kind TEXT, val TEXT, ts REAL, raw TEXT, PRIMARY KEY (post, who, kind)
);
CREATE TABLE IF NOT EXISTS relay (
    id TEXT PRIMARY KEY, dest TEXT, env TEXT, ts REAL
);
CREATE INDEX IF NOT EXISTS relay_dest ON relay(dest);
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY, raw TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS files (
    fid TEXT PRIMARY KEY, owner TEXT, state TEXT DEFAULT '', path TEXT DEFAULT '', allow TEXT DEFAULT '',
    meta TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS reports (
    target TEXT, reporter TEXT, reason TEXT, ts REAL, PRIMARY KEY (target, reporter)
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT, peer TEXT, payload TEXT, ts REAL
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA secure_delete=ON")
            self.db.executescript(SCHEMA)
            for table, col in (("messages", "extra"), ("contacts", "taste"),   # базы прошлых версий
                               ("contacts", "profile"), ("contacts", "direct"), ("contacts", "npk"),
                               ("contacts", "game")):
                try:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT DEFAULT ''")
                except sqlite3.OperationalError:
                    pass

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args)

    # ── контакты ── #

    def contact(self, onion):
        r = self.q("SELECT * FROM contacts WHERE onion=?", (onion,))
        return r[0] if r else None

    def contacts(self, status=None):
        if status:
            return self.q("SELECT * FROM contacts WHERE status=? ORDER BY last_seen DESC", (status,))
        return self.q("SELECT * FROM contacts ORDER BY last_seen DESC")

    def upsert_contact(self, onion, **f):
        with self.lock:
            if self.contact(onion) is None:
                self.x("INSERT INTO contacts(onion, added) VALUES(?,?)", (onion, time.time()))
            for k, v in f.items():
                if k in ("name", "bio", "status", "last_seen", "now", "room", "note", "taste", "profile", "direct", "npk", "game"):
                    self.x(f"UPDATE contacts SET {k}=? WHERE onion=?", (v, onion))

    def remove_contact(self, onion):
        self.x("DELETE FROM contacts WHERE onion=?", (onion,))
        self.x("DELETE FROM outbox WHERE peer=?", (onion,))

    # ── сообщения ── #

    def add_message(self, mid, peer, dir_, ts, text, track=None, delivered=0, read=0, extra=None):
        self.x("INSERT OR IGNORE INTO messages(id, peer, dir, ts, text, track, delivered, read, extra) "
               "VALUES(?,?,?,?,?,?,?,?,?)",
               (mid, peer, dir_, ts, text, json.dumps(track, ensure_ascii=False) if track else "", delivered, read,
                json.dumps(extra, ensure_ascii=False) if extra else ""))

    @staticmethod
    def _msg_row(r):
        r["track"] = json.loads(r["track"]) if r["track"] else None
        r["extra"] = json.loads(r["extra"]) if r.get("extra") else {}
        return r

    def messages(self, peer, limit=300):
        rows = self.q("SELECT * FROM messages WHERE peer=? ORDER BY ts DESC LIMIT ?", (peer, limit))
        rows.reverse()
        return [self._msg_row(r) for r in rows]

    def chat_tracks(self, peer):
        """Все треки, которыми обменялись в чате (по порядку) — «плейлист чата»."""
        return [r["track"] for r in self.messages(peer, 2000) if r["track"]]

    def last_message(self, peer):
        r = self.q("SELECT * FROM messages WHERE peer=? ORDER BY ts DESC LIMIT 1", (peer,))
        return self._msg_row(r[0]) if r else None

    def undelivered(self, peer):
        # delivered=2 — отдано общему другу: всё равно повторить напрямую (дубль получатель отбросит и подтвердит)
        rows = self.q("SELECT * FROM messages WHERE peer=? AND dir='out' AND delivered<>1 ORDER BY ts", (peer,))
        return [self._msg_row(r) for r in rows]

    def mark_delivered(self, mid):
        self.x("UPDATE messages SET delivered=1 WHERE id=?", (mid,))

    def mark_read(self, peer):
        self.x("UPDATE messages SET read=1 WHERE peer=? AND dir='in' AND read=0", (peer,))

    def unread(self) -> dict:
        return {r["peer"]: r["n"] for r in
                self.q("SELECT peer, COUNT(*) n FROM messages WHERE dir='in' AND read=0 GROUP BY peer")}

    def clear_chat(self, peer):
        self.x("DELETE FROM messages WHERE peer=?", (peer,))

    # ── посты, треды, голоса ── #

    def put_post(self, p: dict):
        self.x("INSERT OR REPLACE INTO posts(id, author, ts, text, track, sig, raw) VALUES(?,?,?,?,?,?,?)",
               (p["id"], p["author"], p["ts"], p.get("text", ""),
                json.dumps(p.get("track"), ensure_ascii=False) if p.get("track") else "", p.get("sig", ""),
                json.dumps(p, ensure_ascii=False)))

    def post(self, pid):
        r = self.q("SELECT raw FROM posts WHERE id=?", (pid,))
        return json.loads(r[0]["raw"]) if r else None

    def posts(self, author=None, limit=200):
        if author:
            rows = self.q("SELECT raw FROM posts WHERE author=? ORDER BY ts DESC LIMIT ?", (author, limit))
        else:
            rows = self.q("SELECT raw FROM posts ORDER BY ts DESC LIMIT ?", (limit,))
        return [json.loads(r["raw"]) for r in rows]

    def post_activity(self, ids):
        """Scores/comment counts for a feed in two indexed queries, without decoding comments."""
        ids = tuple(dict.fromkeys(ids))
        if not ids:
            return {}
        # Feed queries are capped at 300 posts, below SQLite's parameter limit.
        marks = ",".join("?" for _ in ids)
        out = {pid: [0, 0] for pid in ids}
        for r in self.q(f"SELECT post, SUM(CASE WHEN val>0 THEN 1 WHEN val<0 THEN -1 ELSE 0 END) n "
                        f"FROM votes WHERE post IN ({marks}) GROUP BY post", ids):
            out[r["post"]][0] = r["n"]
        for r in self.q(f"SELECT post, COUNT(*) n FROM comments WHERE post IN ({marks}) GROUP BY post", ids):
            out[r["post"]][1] = r["n"]
        return out

    def delete_post(self, pid):
        self.x("DELETE FROM posts WHERE id=?", (pid,))
        self.x("DELETE FROM comments WHERE post=?", (pid,))
        self.x("DELETE FROM votes WHERE post=?", (pid,))
        self.x("DELETE FROM reacts WHERE post=?", (pid,))

    def put_react(self, r: dict):
        if not r.get("val"):
            self.x("DELETE FROM reacts WHERE post=? AND who=? AND kind=?", (r["post"], r["who"], r["kind"]))
        else:
            self.x("INSERT OR REPLACE INTO reacts(post, who, kind, val, ts, raw) VALUES(?,?,?,?,?,?)",
                   (r["post"], r["who"], r["kind"], str(r["val"]), r["ts"], json.dumps(r, ensure_ascii=False)))

    def reacts(self, pid):
        return [json.loads(r["raw"]) for r in self.q("SELECT raw FROM reacts WHERE post=?", (pid,))]

    def react_counts(self, pid, kind) -> dict:
        return {r["val"]: r["n"] for r in
                self.q("SELECT val, COUNT(*) n FROM reacts WHERE post=? AND kind=? GROUP BY val", (pid, kind))}

    def my_react(self, pid, me, kind) -> str:
        r = self.q("SELECT val FROM reacts WHERE post=? AND who=? AND kind=?", (pid, me, kind))
        return r[0]["val"] if r else ""

    def put_comment(self, c: dict):
        self.x("INSERT OR REPLACE INTO comments(id, post, parent, author, ts, text, raw) VALUES(?,?,?,?,?,?,?)",
               (c["id"], c["post"], c.get("parent", ""), c["author"], c["ts"], c.get("text", ""),
                json.dumps(c, ensure_ascii=False)))

    def comments(self, pid):
        return [json.loads(r["raw"]) for r in self.q("SELECT raw FROM comments WHERE post=? ORDER BY ts", (pid,))]

    def put_vote(self, v: dict):
        if int(v.get("val", 0)) == 0:
            self.x("DELETE FROM votes WHERE post=? AND voter=?", (v["post"], v["voter"]))
        else:
            self.x("INSERT OR REPLACE INTO votes(post, voter, val, ts, raw) VALUES(?,?,?,?,?)",
                   (v["post"], v["voter"], int(v["val"]), v["ts"], json.dumps(v, ensure_ascii=False)))

    def votes(self, pid):
        return [json.loads(r["raw"]) for r in self.q("SELECT raw FROM votes WHERE post=?", (pid,))]

    def score(self, pid):
        r = self.q("SELECT COALESCE(SUM(CASE WHEN val>0 THEN 1 ELSE 0 END),0) up, "
                   "COALESCE(SUM(CASE WHEN val<0 THEN 1 ELSE 0 END),0) dn FROM votes WHERE post=?", (pid,))[0]
        return r["up"], r["dn"]

    def my_vote(self, pid, me):
        r = self.q("SELECT val FROM votes WHERE post=? AND voter=?", (pid, me))
        return r[0]["val"] if r else 0

    def replace_thread(self, pid, comments, votes, reacts=()):
        """Автор прислал актуальный тред — он главный: заменить комментарии, голоса и реакции целиком."""
        with self.lock:
            self.db.execute("SAVEPOINT replace_thread")
            try:
                self.x("DELETE FROM comments WHERE post=?", (pid,))
                self.x("DELETE FROM votes WHERE post=?", (pid,))
                self.x("DELETE FROM reacts WHERE post=?", (pid,))
                for c in comments:
                    self.put_comment(c)
                for v in votes:
                    self.put_vote(v)
                for r in reacts:
                    self.put_react(r)
            except BaseException:
                self.db.execute("ROLLBACK TO SAVEPOINT replace_thread")
                raise
            finally:
                self.db.execute("RELEASE SAVEPOINT replace_thread")

    def has_message(self, mid) -> bool:
        return bool(self.q("SELECT 1 FROM messages WHERE id=?", (mid,)))

    # ── доставка через общего друга: конверты, которые мы несём другим ── #

    def relay_put(self, env: dict, limit=200):
        dest = env["to"]
        self.x("INSERT OR IGNORE INTO relay(id, dest, env, ts) VALUES(?,?,?,?)",
               (env["id"], dest, json.dumps(env, ensure_ascii=False), time.time()))
        self.x("DELETE FROM relay WHERE dest=? AND id NOT IN (SELECT id FROM relay WHERE dest=? ORDER BY ts DESC "
               "LIMIT ?)", (dest, dest, limit))
        self.x("DELETE FROM relay WHERE ts<?", (time.time() - 7 * 86400,))

    def relay_take(self, dest) -> list:
        rows = self.q("SELECT id, env FROM relay WHERE dest=? ORDER BY ts", (dest,))
        for r in rows:
            self.x("DELETE FROM relay WHERE id=?", (r["id"],))
        return [json.loads(r["env"]) for r in rows]

    # ── файлы: свои (state='have', allow — кому можно отдавать) и чужие (part → have) ── #

    def file(self, fid):
        r = self.q("SELECT * FROM files WHERE fid=?", (fid,))
        if not r:
            return None
        r = r[0]
        r["meta"] = json.loads(r["meta"]) if r["meta"] else {}
        r["allow"] = json.loads(r["allow"]) if r["allow"] else []
        return r

    def put_file(self, fid, owner, meta, state="", path="", allow=None):
        self.x("INSERT OR REPLACE INTO files(fid, owner, state, path, allow, meta, ts) VALUES(?,?,?,?,?,?,?)",
               (fid, owner, state, path, json.dumps(allow or [], ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False), time.time()))

    def set_file(self, fid, **f):
        for k, v in f.items():
            if k in ("state", "path"):
                self.x(f"UPDATE files SET {k}=? WHERE fid=?", (v, fid))

    def pending_files(self, owner):
        return [r["fid"] for r in self.q("SELECT fid FROM files WHERE owner=? AND state='want'", (owner,))]

    # ── группы ── #

    def put_group(self, g: dict):
        self.x("INSERT OR REPLACE INTO groups(id, raw, ts) VALUES(?,?,?)",
               (g["id"], json.dumps(g, ensure_ascii=False), float(g.get("ts") or 0)))

    def group(self, gid):
        r = self.q("SELECT raw FROM groups WHERE id=?", (gid,))
        return json.loads(r[0]["raw"]) if r else None

    def groups(self):
        return [json.loads(r["raw"]) for r in self.q("SELECT raw FROM groups ORDER BY ts DESC")]

    def delete_group(self, gid):
        self.x("DELETE FROM groups WHERE id=?", (gid,))
        self.x("DELETE FROM messages WHERE peer=?", ("g:" + gid,))

    # ── очередь отправки ── #

    def outbox_add(self, peer, payload: dict):
        self.x("INSERT INTO outbox(peer, payload, ts) VALUES(?,?,?)",
               (peer, json.dumps(payload, ensure_ascii=False), time.time()))

    def outbox(self, peer):
        return [(r["id"], json.loads(r["payload"])) for r in
                self.q("SELECT id, payload FROM outbox WHERE peer=? ORDER BY id", (peer,))]

    def outbox_done(self, oid):
        self.x("DELETE FROM outbox WHERE id=?", (oid,))

    # ── очистка ── #

    def clear(self, what: str, me: str = ""):
        with self.lock:
            if what in ("messages", "all"):
                self.x("DELETE FROM messages")
            if what in ("feed", "all"):                       # кэш чужих постов (свои — только при «всё»)
                if what == "all":
                    self.x("DELETE FROM posts"); self.x("DELETE FROM comments"); self.x("DELETE FROM votes")
                    self.x("DELETE FROM reacts")
                else:
                    ids = [r["id"] for r in self.q("SELECT id FROM posts WHERE author<>?", (me,))]
                    for pid in ids:
                        self.delete_post(pid)
            if what == "all":
                self.x("DELETE FROM contacts"); self.x("DELETE FROM outbox")
                self.x("DELETE FROM relay"); self.x("DELETE FROM groups"); self.x("DELETE FROM files")
            self.db.execute("VACUUM")

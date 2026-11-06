"""Database-backed dialog state; uses the same PostgreSQL/SQLite adapter as the bot."""
import json
from aiogram.fsm.storage.base import BaseStorage


class PersistentStorage(BaseStorage):
    def __init__(self, connect):
        self.connect = connect

    def key(self, key):
        return json.dumps([key.bot_id, key.chat_id, key.user_id, key.thread_id, key.destiny], ensure_ascii=False)

    def get(self, key):
        conn = self.connect()
        try:
            row = conn.execute('SELECT state, data FROM dialog_state WHERE storage_key=?', (self.key(key),)).fetchone()
            return (row['state'], json.loads(row['data'])) if row else (None, {})
        finally:
            conn.close()

    async def set_state(self, key, state=None):
        value = state.state if hasattr(state, 'state') else state
        conn = self.connect()
        try:
            conn.execute("INSERT INTO dialog_state(storage_key,state,data) VALUES (?,?,'{}') ON CONFLICT(storage_key) DO UPDATE SET state=excluded.state", (self.key(key), value))
            conn.commit()
        finally:
            conn.close()

    async def get_state(self, key):
        return self.get(key)[0]

    async def set_data(self, key, data):
        conn = self.connect()
        try:
            conn.execute("INSERT INTO dialog_state(storage_key,state,data) VALUES (?,NULL,?) ON CONFLICT(storage_key) DO UPDATE SET data=excluded.data", (self.key(key), json.dumps(data, ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()

    async def get_data(self, key):
        return self.get(key)[1]

    async def close(self):
        pass

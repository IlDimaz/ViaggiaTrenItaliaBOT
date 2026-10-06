import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage as stmod
from storage import Storage
import trenitalia.viaggiatreno as vt
from bot import notifications


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append((chat_id, text))


class FakeApp:
    def __init__(self, storage):
        self.bot_data = {"storage": storage}
        self.bot = FakeBot()


async def main():
    number = int(sys.argv[1]) if len(sys.argv) > 1 else 22470
    tmp = Path(tempfile.gettempdir()) / "track_smoke.sqlite3"
    if tmp.exists():
        tmp.unlink()
    stmod.DB_PATH = tmp
    store = Storage()
    await store.connect()
    refs = await vt.find_train(number)
    if not refs:
        print("train not running today")
        await store.close()
        return
    ref = refs[0]
    app = FakeApp(store)
    status = await vt.train_status(ref)
    desc = "None" if status is None else (str(status.number) + " delay=" + str(status.delay) + " last=" + str(status.last_detected_station))
    print("initial status:", desc)
    await store.add_tracked(1, ref.number, ref.origin_code, ref.midnight_ms)
    if status is not None:
        await store.set_tracked_sig(1, ref.number, ref.origin_code, notifications.status_signature(status))
    print("tracked count:", len(await store.all_tracked()))
    await notifications.poll_tracked_once(app)
    print("msgs after poll 1 (expect 0):", len(app.bot.messages))
    await store.set_tracked_sig(1, ref.number, ref.origin_code, "BOGUS")
    app.bot.messages.clear()
    await notifications.poll_tracked_once(app)
    print("msgs after poll 2 (expect 1 if live):", len(app.bot.messages))
    for m in app.bot.messages:
        print("  ->", m[1][:70].replace(chr(10), " "))
    await store.close()
    await vt.close()


asyncio.run(main())

"""Accept the configured operator's invitation to an explicitly opened Telegram portal."""

import re

from twisted.internet.defer import DeferredLock

ADMISSION = "org.matrixconstellation.telegram.admission"
PEER = re.compile(r"(user|chat|channel):([1-9][0-9]{0,18})")


class TelegramAdmission:
    def __init__(self, config, api):
        self.api = api
        self.owner = config["owner"]
        self.domain = self.owner.split(":", 1)[1]
        self.bot = f"@telegrambot:{self.domain}"
        self.lock = DeferredLock()
        api.register_third_party_rules_callbacks(on_new_event=self.on_new_event)

    async def on_new_event(self, event, _state):
        if event.type != ADMISSION or event.sender != self.bot or event.state_key != '' or event.content.get('operator') != self.owner:
            return
        peer = event.content.get("peer")
        if isinstance(peer, str) and PEER.fullmatch(peer):
            self.api.run_as_background_process("constellation_telegram_admit", self.admit, event.room_id, peer)

    async def admit(self, room, peer):
        async with self.lock:
            state = await self.api.get_room_state(room)
            marker = state.get((ADMISSION, ""))
            invite = state.get(("m.room.member", self.owner))
            creation = state.get(("m.room.create", ""))
            rules = state.get(("m.room.join_rules", ""))
            encryption = state.get(("m.room.encryption", ""))
            if (not marker or marker.sender != self.bot or marker.content.get("peer") != peer or
                    marker.content.get("operator") != self.owner or
                    not invite or invite.content.get("membership") not in ("invite", "join") or
                    not creation or creation.content.get("m.federate") is not False or
                    not rules or rules.content.get("join_rule") != "invite" or
                    not encryption or encryption.content.get("algorithm") != "m.megolm.v1.aes-sha2" or
                    ("m.room.tombstone", "") in state):
                return
            ghost = f"@telegram_{peer.partition(':')[2]}:{self.domain}"
            if invite.content.get("membership") == "invite" and invite.sender not in (self.bot, ghost):
                return
            bridge = next((event for (kind, _), event in state.items() if kind == "m.bridge" and
                           event.sender == self.bot and event.content.get("bridgebot") == self.bot and
                           event.content.get("channel", {}).get("id") == peer), None)
            if not bridge:
                return
            if invite.content["membership"] == "invite":
                await self.api.update_room_membership(self.owner, self.owner, room, "join")
            direct = await self.api.account_data_manager.get_global(self.owner, "m.direct") or {}
            recipient = ghost if peer.startswith("user:") else self.bot
            existing = direct.get(recipient, [])
            if isinstance(existing, list) and existing.count(room) == 1 and all(
                    room not in rooms for key, rooms in direct.items() if key != recipient and isinstance(rooms, list)):
                return
            updated = {key: [item for item in rooms if item != room] if isinstance(rooms, list) else rooms
                       for key, rooms in direct.items()}
            updated.setdefault(recipient, [])
            if not isinstance(updated[recipient], list):
                updated[recipient] = []
            updated[recipient].append(room)
            await self.api.account_data_manager.put_global(self.owner, "m.direct", updated)

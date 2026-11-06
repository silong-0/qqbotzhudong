import asyncio
import json
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from quart import jsonify, request

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register

PLUGIN_NAME = "astrbot_plugin_proactive_chat"
DATA_DIR = Path(__file__).resolve().parents[2]
PLUGIN_DIR = DATA_DIR / "plugin_data" / PLUGIN_NAME
STATE_PATH = PLUGIN_DIR / "state.json"
MEMORY_DB = DATA_DIR / "plugin_data" / "memory_diary" / "memory.db"

DEFAULTS = {
    "enabled": False,
    "whitelist": [],
    "min_minutes": 90,
    "max_minutes": 240,
    "active_start": "09:00",
    "active_end": "23:30",
    "read_context": True,
    "context_limit": 8,
    "read_memory": True,
    "memory_limit": 3,
    "max_chars": 220,
    "prompt": "像平时在群里随手说话一样，1到2句，不要解释，不要提日记或系统提示。",
}


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def _parse_hm(value: str):
    try:
        h, m = str(value).split(":", 1)
        return int(h), int(m)
    except Exception:
        return 9, 0


@register(PLUGIN_NAME, "local", "主动随机消息", "1.0.0", "白名单随机主动发言")
class ProactiveChat(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._task = None
        self._register_web_apis()
        try:
            self._task = asyncio.create_task(self._loop())
        except RuntimeError:
            pass

    def _config_path(self):
        return DATA_DIR / "config" / "astrbot_plugin_proactive_chat_config.json"

    def _cfg(self):
        data = _load_json(self._config_path(), {})
        cfg = dict(DEFAULTS)
        if isinstance(data, dict):
            cfg.update(data)
        return cfg

    def _state(self):
        data = _load_json(STATE_PATH, {})
        if not isinstance(data, dict):
            data = {}
        data.setdefault("next_run", {})
        data.setdefault("sent", {})
        if not isinstance(data["next_run"], dict):
            data["next_run"] = {}
        if not isinstance(data["sent"], dict):
            data["sent"] = {}
        return data

    def _save_state(self, state):
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _interval_seconds(self, cfg):
        try:
            low = max(1, int(cfg.get("min_minutes", 90)))
            high = max(low, int(cfg.get("max_minutes", 240)))
        except Exception:
            low, high = 90, 240
        return random.uniform(low * 60, high * 60)

    def _next_time(self, cfg, now=None):
        now = now or datetime.now()
        return now.timestamp() + self._interval_seconds(cfg)

    def _in_window(self, cfg, now=None):
        now = now or datetime.now()
        sh, sm = _parse_hm(cfg.get("active_start", "09:00"))
        eh, em = _parse_hm(cfg.get("active_end", "23:30"))
        start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        if start == end:
            return True
        if start < end:
            return start <= now <= end
        return now >= start or now <= end

    def _until_window(self, cfg, now=None):
        now = now or datetime.now()
        sh, sm = _parse_hm(cfg.get("active_start", "09:00"))
        eh, em = _parse_hm(cfg.get("active_end", "23:30"))
        start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        if start < end:
            target = start if now < start else start + timedelta(days=1)
        else:
            if now <= end:
                return 0
            target = start if now < start else start + timedelta(days=1)
        return max(0, (target - now).total_seconds())

    def _persona(self, umo: str):
        persona_id = ""
        custom_name = ""
        try:
            con = sqlite3.connect(f"file:{DATA_DIR / 'data_v4.db'}?mode=ro", uri=True)
            row = con.execute(
                "SELECT value FROM preferences WHERE scope='umo' AND scope_id=? "
                "AND key='session_service_config'",
                (umo,),
            ).fetchone()
            if row and row[0]:
                data = json.loads(row[0]).get("val", {})
                persona_id = str(data.get("persona_id") or "")
                custom_name = str(data.get("custom_name") or "")
            con.close()
        except Exception:
            pass

        if not persona_id:
            try:
                cfg = _load_json(DATA_DIR / "cmd_config.json", {})
                persona_id = str(
                    cfg.get("agent_runner", {})
                    .get("config", {})
                    .get("persona", {})
                    .get("persona_id")
                    or "default"
                )
            except Exception:
                persona_id = "default"

        prompt = ""
        if persona_id and persona_id != "default":
            try:
                con = sqlite3.connect(f"file:{DATA_DIR / 'data_v4.db'}?mode=ro", uri=True)
                row = con.execute(
                    "SELECT system_prompt FROM personas WHERE persona_id=?",
                    (persona_id,),
                ).fetchone()
                if row and row[0]:
                    prompt = str(row[0]).strip()
                con.close()
            except Exception:
                pass
        return persona_id, prompt, custom_name

    def _memories(self, umo: str, cfg):
        if not cfg.get("read_memory", True):
            return []
        try:
            limit = max(0, min(int(cfg.get("memory_limit", 3)), 10))
        except Exception:
            limit = 3
        if not limit or not MEMORY_DB.exists():
            return []
        try:
            con = sqlite3.connect(f"file:{MEMORY_DB}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT day, content FROM diaries WHERE umo=? "
                "ORDER BY day DESC, id DESC LIMIT ?",
                (umo, limit),
            ).fetchall()
            con.close()
            return [(str(day), str(content)) for day, content in rows]
        except Exception:
            return []

    def _conversation_context(self, umo: str, cfg):
        if not cfg.get("read_context", True):
            return []
        try:
            limit = max(2, min(int(cfg.get("context_limit", 8)), 30))
        except Exception:
            limit = 8
        rows = []
        try:
            con = sqlite3.connect(f"file:{DATA_DIR / 'data_v4.db'}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT json_extract(value, '$.role') AS role, "
                "json_extract(value, '$.content') AS content "
                "FROM conversations, json_each(conversations.content) "
                "WHERE conversations.user_id=? "
                "ORDER BY json_each.key DESC LIMIT ?",
                (umo, limit * 4),
            ).fetchall()
            con.close()
        except Exception:
            rows = []

        if not rows:
            # 旧数据库没有 JSON1 时退回整段解析
            try:
                con = sqlite3.connect(f"file:{DATA_DIR / 'data_v4.db'}?mode=ro", uri=True)
                row = con.execute(
                    "SELECT content FROM conversations WHERE user_id=? "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (umo,),
                ).fetchone()
                con.close()
                history = json.loads(row[0]) if row and row[0] else []
                if isinstance(history, list):
                    rows = [
                        (item.get("role"), json.dumps(item.get("content"), ensure_ascii=False))
                        for item in history
                        if isinstance(item, dict)
                    ][-limit * 4 :]
            except Exception:
                return []

        messages = []
        for role, parts_raw in reversed(rows):
            role = str(role or "")
            if role not in {"user", "assistant"}:
                continue
            try:
                parts = json.loads(parts_raw) if isinstance(parts_raw, str) else parts_raw
            except Exception:
                parts = parts_raw
            texts = []
            if isinstance(parts, str):
                texts.append(parts)
            elif isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict) and part.get("type") == "text":
                        value = str(part.get("text") or "")
                        if "<system_reminder>" in value:
                            continue
                        texts.append(value)
            text = " ".join(value.strip() for value in texts if value.strip()).strip()
            if not text:
                continue
            messages.append((role, text[:400]))
        return messages[-limit:]

    @staticmethod
    def _append_sent_state(state, umo: str, text: str):
        sent = state["sent"].get(umo, [])
        if not isinstance(sent, list):
            sent = []
        sent.append(text[:300])
        state["sent"][umo] = sent[-8:]

    def _remember_sent(self, umo: str, text: str):
        state = self._state()
        self._append_sent_state(state, umo, text)
        self._save_state(state)

    async def _generate(self, umo: str, cfg):
        persona_id, persona_prompt, custom_name = self._persona(umo)
        memories = self._memories(umo, cfg)
        context = self._conversation_context(umo, cfg)
        sent_before = self._state()["sent"].get(umo, [])
        start = cfg.get("active_start", "09:00")
        end = cfg.get("active_end", "23:30")
        kind = "群聊" if "GroupMessage" in umo else "私聊"
        name_line = f"你在会话里使用的名字是「{custom_name}」。\n" if custom_name else ""

        context_block = ""
        if context:
            lines = []
            for role, text in context:
                lines.append(f"{'你' if role == 'assistant' else '对方'}：{text}")
            context_block = (
                "你们最近的对话如下，只用于保持连贯，不要逐句复述，"
                "更不要重复你最近已经说过的话：\n"
                + "\n".join(lines)
                + "\n\n"
            )

        sent_block = ""
        if isinstance(sent_before, list) and sent_before:
            lines = [f"- {text[:160]}" for text in sent_before[-5:] if str(text).strip()]
            if lines:
                sent_block = (
                    "你最近主动发过这些话，这次换一个话题或说法，不要重复：\n"
                    + "\n".join(lines)
                    + "\n\n"
                )

        memory_block = ""
        if memories:
            lines = [f"- {day}：{content[:900]}" for day, content in memories]
            memory_block = (
                "你记得这个会话的一些旧日记。可以自然地接着旧话题，"
                "但不要逐条复述，也不要直接说“日记里写着”：\n"
                + "\n".join(lines)
                + "\n\n"
            )

        system_prompt = persona_prompt or (
            "你是大肥鱼，一只蓝发小鲸鱼娘，18岁，群里的普通群友。"
            "说话短、口语化，不要自称AI，不要客服腔。"
        )
        instruction = (
            f"现在是 {datetime.now():%Y-%m-%d %H:%M}，你在一个{kind}会话里。\n"
            f"{name_line}"
            f"允许主动发言的时间段是 {start} 到 {end}。\n"
            f"{context_block}"
            f"{sent_block}"
            f"{memory_block}"
            "请主动发一条消息，像你突然想起什么、看到时间、或者想找大家说话。"
            "不要回复某个具体的人，不要提到“主动消息”“记忆”“日记”“系统提示”。\n"
            f"{cfg.get('prompt', DEFAULTS['prompt'])}\n"
            "只输出要发送的正文，不要加引号、不要加“大肥鱼：”。"
        )
        provider = await self.context.get_using_provider_async(umo)
        if provider is None:
            raise RuntimeError("没有可用的聊天模型")
        resp = await provider.text_chat(
            prompt=instruction,
            system_prompt=system_prompt,
            session_id=f"proactive:{umo}",
        )
        text = (getattr(resp, "completion_text", "") or "").strip()
        text = text.strip(" \t\r\n\"'“”‘’")
        text = re.sub(r"^(大肥鱼|助手|AI)[：:]\s*", "", text)
        if not text:
            return ""
        try:
            max_chars = max(20, min(int(cfg.get("max_chars", 220)), 500))
        except Exception:
            max_chars = 220
        return text[:max_chars]

    def _register_web_apis(self):
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/overview",
            self._api_overview,
            ["GET"],
            "获取主动消息配置与会话列表",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/save",
            self._api_save,
            ["POST"],
            "保存主动消息配置",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/test",
            self._api_test,
            ["POST"],
            "立即为某个会话生成一条主动消息",
        )

    @staticmethod
    def _kind(umo: str):
        if "GroupMessage" in umo:
            return "群聊"
        if "FriendMessage" in umo:
            return "私聊"
        return "其他"

    def _known_sessions(self, cfg):
        aliases = {}
        try:
            con = sqlite3.connect(f"file:{DATA_DIR / 'data_v4.db'}?mode=ro", uri=True)
            cols = {row[1] for row in con.execute("PRAGMA table_info(umo_aliases)")}
            if "display_name" in cols:
                expr = "display_name"
            elif "user_alias" in cols and "auto_name" in cols:
                expr = "COALESCE(NULLIF(user_alias, ''), NULLIF(auto_name, ''), umo)"
            elif "auto_name" in cols:
                expr = "COALESCE(NULLIF(auto_name, ''), umo)"
            else:
                expr = "umo"
            for umo, name in con.execute(f"SELECT umo, {expr} FROM umo_aliases"):
                if umo:
                    aliases[str(umo)] = str(name or "")
            con.close()
        except Exception:
            pass

        raw = cfg.get("whitelist", [])
        if isinstance(raw, str):
            raw = [raw]
        whitelist = {str(x).strip() for x in raw or [] if str(x).strip()}
        state = self._state()
        next_run = state.get("next_run", {})

        sessions = []
        seen = set()
        for umo in list(aliases) + [u for u in whitelist if u not in aliases]:
            if umo in seen or ("GroupMessage" not in umo and "FriendMessage" not in umo):
                continue
            seen.add(umo)
            ts = next_run.get(umo)
            next_text = ""
            if ts:
                try:
                    next_text = datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    next_text = ""
            sessions.append(
                {
                    "umo": umo,
                    "display_name": aliases.get(umo) or "手动添加",
                    "kind": self._kind(umo),
                    "persona_id": self._persona(umo)[0],
                    "selected": umo in whitelist,
                    "next_run": next_text,
                }
            )
        sessions.sort(key=lambda x: (not x["selected"], x["display_name"], x["umo"]))
        return sessions

    async def _api_overview(self):
        cfg = self._cfg()
        return jsonify({"config": cfg, "sessions": self._known_sessions(cfg)})

    @staticmethod
    def _as_int(value, default, low, high):
        try:
            return max(low, min(int(value), high))
        except Exception:
            return default

    async def _api_save(self):
        data = await request.get_json(silent=True) or {}
        incoming = data.get("config") if isinstance(data.get("config"), dict) else data
        cfg = self._cfg()

        if "enabled" in incoming:
            cfg["enabled"] = bool(incoming.get("enabled"))
        if "whitelist" in incoming:
            raw = incoming.get("whitelist") or []
            if isinstance(raw, str):
                raw = [raw]
            cleaned = []
            for item in raw:
                value = str(item or "").strip()
                if value and value not in cleaned:
                    cleaned.append(value)
            cfg["whitelist"] = cleaned
        if "min_minutes" in incoming:
            cfg["min_minutes"] = self._as_int(incoming.get("min_minutes"), 90, 1, 1440)
        if "max_minutes" in incoming:
            cfg["max_minutes"] = self._as_int(incoming.get("max_minutes"), 240, 1, 2880)
        if cfg["max_minutes"] < cfg["min_minutes"]:
            cfg["max_minutes"] = cfg["min_minutes"]
        if "active_start" in incoming:
            value = str(incoming.get("active_start") or "")
            if re.fullmatch(r"\d{1,2}:\d{2}", value):
                cfg["active_start"] = value
        if "active_end" in incoming:
            value = str(incoming.get("active_end") or "")
            if re.fullmatch(r"\d{1,2}:\d{2}", value):
                cfg["active_end"] = value
        if "read_context" in incoming:
            cfg["read_context"] = bool(incoming.get("read_context"))
        if "context_limit" in incoming:
            cfg["context_limit"] = self._as_int(incoming.get("context_limit"), 8, 2, 30)
        if "read_memory" in incoming:
            cfg["read_memory"] = bool(incoming.get("read_memory"))
        if "memory_limit" in incoming:
            cfg["memory_limit"] = self._as_int(incoming.get("memory_limit"), 3, 0, 10)
        if "max_chars" in incoming:
            cfg["max_chars"] = self._as_int(incoming.get("max_chars"), 220, 20, 500)
        if "prompt" in incoming:
            cfg["prompt"] = str(incoming.get("prompt") or "")[:2000]

        path = self._config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return jsonify({"ok": True, "config": cfg, "sessions": self._known_sessions(cfg)})

    async def _api_test(self):
        data = await request.get_json(silent=True) or {}
        umo = str(data.get("umo", "")).strip()
        if not umo:
            return jsonify({"ok": False, "error": "umo is required"}), 400
        try:
            text = await self._send_one(umo, self._cfg())
            if text:
                self._remember_sent(umo, text)
        except Exception as exc:
            logger.exception("[proactive_chat] manual test failed")
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": bool(text), "text": text})

    async def _send_one(self, umo: str, cfg):
        text = await self._generate(umo, cfg)
        if not text:
            logger.warning(f"[proactive_chat] empty generation for {umo}")
            return ""
        await self.context.send_message(umo, MessageChain([Plain(text)]))
        logger.info(f"[proactive_chat] sent to {umo}: {text[:80]}")
        return text

    async def _loop(self):
        await asyncio.sleep(20)
        while True:
            try:
                cfg = self._cfg()
                if not cfg.get("enabled"):
                    await asyncio.sleep(30)
                    continue
                whitelist = []
                raw = cfg.get("whitelist", [])
                if isinstance(raw, str):
                    raw = [raw]
                for item in raw or []:
                    value = str(item or "").strip()
                    if value and value not in whitelist:
                        whitelist.append(value)

                state = self._state()
                next_run = state["next_run"]
                for stale in [u for u in next_run if u not in whitelist]:
                    next_run.pop(stale, None)

                now = time.time()
                for umo in whitelist:
                    if umo not in next_run:
                        next_run[umo] = self._next_time(cfg)
                        continue
                    if now < float(next_run.get(umo, 0)):
                        continue
                    if not self._in_window(cfg):
                        next_run[umo] = now + self._until_window(cfg) + random.uniform(60, 300)
                        continue
                    try:
                        sent_text = await self._send_one(umo, cfg)
                        if sent_text:
                            self._append_sent_state(state, umo, sent_text)
                    except Exception:
                        logger.exception(f"[proactive_chat] send failed for {umo}")
                    next_run[umo] = self._next_time(cfg)
                self._save_state(state)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[proactive_chat] scheduler loop failed")
            await asyncio.sleep(30)

    async def terminate(self):
        if self._task:
            self._task.cancel()
            self._task = None

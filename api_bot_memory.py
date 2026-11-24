"""Api mixin — what a bot keeps in its own folder besides its memory file (settings.experimental_bots).

js_api (the bot editor's Skills section, web/bots.js): bots_own_skills, bots_skill_new,
        bots_skill_copy, bots_skill_delete, bots_skill_open.
sfctl: bots-self (_bots_self: a bot reads, and rewrites, its own 執掌).
Hook main.py calls once the window is up: _bot_memory_start (the thread that asks an open bot to
save its memory after a quiet spell).

The rules are in bot_skills.py (a bot's own skills) and bot_memory.py (when to save); this only
checks the name is a bot of this app's and hands the result to the page.
"""

import json
import os
import subprocess
import sys
import threading
import time

import bot_memory
import bot_skills
import bots
import sf_lang
from api_host import main
from sf_log import _dlog, _swallow

_ = sf_lang.pick


class BotMemoryApiMixin:
    # ── a bot's own skills ──────────────────────────────────────────────
    @staticmethod
    def _own_skill_bot(role: str):
        """None when `role` is a bot that has a folder of its own (not an outside one), else the refusal."""
        entry = (main.load_config().get("agent_roster") or {}).get(role)
        if not bots.is_bot(entry):
            return {"success": False, "code": "no_bot", "message": _(f'"{role}" is not a bot', f"「{role}」不是 bot")}
        if bots.is_external(entry):
            return {"success": False, "code": "external", "message": _(
                "A bot outside ShellBot keeps its skills where it lives", "外部的 bot，skill 放在它自己那邊")}
        return None

    def bots_own_skills(self, role: str) -> str:
        """{"skills": [{"name", "folder", "description", "path", "shadows"}], "dir"}: the skills only this bot gets."""
        refused = self._own_skill_bot(role)
        return json.dumps(refused or bot_skills.list_own(role), ensure_ascii=False)

    def bots_skill_new(self, role: str, name: str, description: str = "") -> str:
        """A blank skill (a SKILL.md to fill in) in the bot's own folder."""
        refused = self._own_skill_bot(role)
        return json.dumps(refused or bot_skills.create_blank(role, name, description), ensure_ascii=False)

    def bots_skill_copy(self, role: str, shared_name: str) -> str:
        """A copy of a shared skill (by its name in bots_skill_catalog) as one of the bot's own."""
        refused = self._own_skill_bot(role)
        return json.dumps(refused or bot_skills.copy_shared(role, shared_name), ensure_ascii=False)

    def bots_skill_delete(self, role: str, folder: str) -> str:
        """Remove one of the bot's own skills. The page asks first (SFDialog.confirm)."""
        refused = self._own_skill_bot(role)
        return json.dumps(refused or bot_skills.delete(role, folder), ensure_ascii=False)

    def bots_skill_open(self, role: str, folder: str = "") -> str:
        """Show the bot's skills folder (or one skill's) in Finder / Explorer."""
        refused = self._own_skill_bot(role)
        if refused:
            return json.dumps(refused, ensure_ascii=False)
        path = bot_skills.folder_to_open(role, folder)
        if path is None:
            return json.dumps({"success": False, "code": "missing", "message": _("No such skill folder", "沒有這個 skill 資料夾")},
                              ensure_ascii=False)
        try:
            opener = "open" if sys.platform == "darwin" else ("explorer" if os.name == "nt" else "xdg-open")
            subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            return json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)
        return json.dumps({"success": True, "message": str(path)}, ensure_ascii=False)

    # ── a bot's own 執掌, from its own tab (`sfctl bots-self`) ────────────
    def _bots_self(self, args: dict) -> dict:
        """`sfctl bots-self [--job "…"]`, run in a bot's own tab ($SF_SID): its name, 執掌 and skills; with
        --job its 執掌 is rewritten (only that: not its name, CLI or face, and no other bot's), the old one
        added to role-history.md (bots.log_job_change). The once-a-day look of bots.review_step uses it."""
        if not self._bots_enabled():
            return {"success": False, "message": _("Bots are experimental: turn them on in Settings first (experimental_bots)",
                                                  "Bots 是實驗功能，請先在設定裡打開（experimental_bots）")}
        sid, cfg = str(args.get("from_sid") or "").strip(), main.load_config()
        role = self._bots_role_of(sid, cfg) if sid in self.sessions else ""
        if not role:
            return {"success": False, "code": "not_bot", "message": _(
                "Only a bot can do this, in its own tab", "只有 bot 能用，而且要在它自己的分頁裡跑")}
        entry = (cfg.get("agent_roster") or {}).get(role) or {}
        old = " ".join(str(entry.get("responsibility") or "").split())
        if args.get("job") is None:
            own = [k["name"] for k in bots.own_skill_catalog(role)]
            lines = [_("Bot: ", "Bot：") + bots.label_of(role, entry),
                     _("Job: ", "執掌：") + (old or _("(not written yet)", "（還沒寫）")),
                     _("Shared skills: ", "共用 skill：") + ("、".join(bots.normalize_bot(entry.get("bot") or {}).get("skills") or []) or "-"),
                     _("Its own skills: ", "自己的 skill：") + ("、".join(own) or "-") + f"  ({bots.skills_dir(role)})",
                     _("Changes so far: ", "改過的紀錄：") + str(bots.role_history(role))]
            return {"success": True, "message": "\n".join(lines), "role": role, "job": old, "own_skills": own}
        new = " ".join(str(args.get("job") or "").split())
        err = bots.job_error(new)
        if err:
            return {"success": False, "code": "bad_job", "message": err}
        if new == old:
            return {"success": True, "changed": False, "message": _("Same as now: nothing changed", "跟現在一樣，沒有改")}

        def _mut(c):
            e = (c.get("agent_roster") or {}).get(role)
            if not isinstance(e, dict) or not bots.is_bot(e):
                raise ValueError(_(f'"{role}" is no longer a bot', f"「{role}」已經不是 bot"))
            e["responsibility"] = new
        try:
            main.update_config(_mut)
        except ValueError as e:
            return {"success": False, "message": str(e)}
        bots.log_job_change(role, old, new, by=sid)
        _dlog("bots", f"bots-self sid={sid} role={role!r} job {len(old)}->{len(new)} chars")
        self._bots_notify_ui()
        return {"success": True, "changed": True, "message": _(
            f"Job of {bots.label_of(role, entry)} updated; the old one is in {bots.role_history(role)}. Your next wake carries it.",
            f"{bots.label_of(role, entry)} 的執掌已更新，舊的記在 {bots.role_history(role)}；下次叫醒時生效。")}

    # ── asking an open bot to save its memory ───────────────────────────
    def _bot_memory_start(self):
        """Start the thread that looks at the open bots once a minute (bot_memory.tick). Once."""
        if self.__dict__.get("_bot_memory_started"):
            return
        self._bot_memory_started = True

        def _loop():
            time.sleep(bot_memory.TICK_SEC)          # the tabs have been restored by the first look
            while True:
                try:
                    for sid, role, why in bot_memory.tick(self, main.load_config()):
                        if why == "asked":
                            _dlog("bots", f"autosave asked sid={sid} role={role!r}")
                except Exception:
                    _swallow("bot_memory:tick")
                time.sleep(bot_memory.TICK_SEC)

        threading.Thread(target=_loop, daemon=True, name="bot-memory").start()

    # ── a bot's persona on its launch command (bots.with_persona) ─────────
    def _bots_tab_launch_cmd(self, sid: str, cmd: str) -> str:
        """The command an existing tab's CLI is started again with (a relaunch, a restore after the
        computer restarted): a bot's tab gets its persona, as a bot woken now does; anything else as it was."""
        try:
            role = self._bots_role_of(sid)
            entry = (main.load_config().get("agent_roster") or {}).get(role) if role else None
            return self._bots_launch_cmd(role, entry, cmd) if entry else cmd
        except Exception:
            _swallow("bots:tab-persona")
            return cmd

    def _bots_launch_cmd(self, role: str, entry: dict, cmd: str = "") -> str:
        """The command a bot's tab starts with: its persona file added to the CLI's system prompt where the
        CLI takes one (bots.with_persona), else the command as it was. Never fails a wake."""
        cmd = cmd or str((entry or {}).get("cmd") or "claude")
        try:
            if self._bots_enabled() and bots.is_bot(entry) and not bots.is_external(entry):
                return bots.with_persona(cmd, role, entry)
        except Exception:
            _swallow("bots:persona")
        return cmd

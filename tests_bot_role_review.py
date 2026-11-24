#!/usr/bin/env python3
"""Once a day a bot looks at its own job (執掌) and skills (settings.bot_role_review, bots.review_step).

Reported: bots nobody wrote a job for stay blank, and a written one drifts from what the bot does. So the
first save request of a day (at close or after a quiet spell) has one more thing to do: look at the job
and the skills from the work the user gave it, rewrite the job with `sfctl bots-self --job` (its own only;
the old one kept in role-history.md) and its own skills as files. Checked here:
  - the setting (on by default) and the once-a-day mark; a request that did not go in is asked again
  - the request text: the same as before when not due; when due, a third thing, worded for a bot with
    no job yet and for one with a job, "nothing else" widened, the first lines unchanged
  - bots-self: only from a bot's own tab, its job only, too long refused (not cut), history kept
  - sfctl has the command and sends the tab it runs in

Demo data only. Run: .venv/bin/python tests_bot_role_review.py
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-role-review-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
sys.modules["webview"] = MagicMock()
bt = MagicMock()
bt.load_user_instructions.return_value = ""
sys.modules["bridge_telegram"] = bt
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")
import bot_memory as bm  # noqa: E402
import bots  # noqa: E402
import main  # noqa: E402
import tab_delivery as td  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {str(detail)[:400]}")


ROSTER = {
    "alpha": {"label": "示範工程師", "cmd": "claude", "responsibility": "寫程式、跑測試。也整理發版紀錄。",
              "bot": {"memory": True, "skills": ["demo-release"]}},
    "blank": {"label": "示範新人", "cmd": "claude", "responsibility": "", "bot": {"memory": True}},
    "other": {"label": "示範研究員", "cmd": "codex", "responsibility": "查資料", "bot": {"memory": True}},
    "plain": {"label": "一般角色", "cmd": "claude", "responsibility": "不是 bot"},
}
SESSIONS = {"s1": {"bot": "alpha"}, "s2": {"bot": "blank"}}


def write_config(settings=None):
    st = {"experimental_bots": True}
    st.update(settings or {})
    main.CONFIG_FILE.write_text(json.dumps({"settings": st, "agent_roster": json.loads(json.dumps(ROSTER)),
                                            "bots_sessions": SESSIONS}, ensure_ascii=False), encoding="utf-8")


def cfg():
    return json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))


DAY = 1795000000.0          # a fixed moment; the next day is DAY + 86400

print("1. the setting and the once-a-day mark")
check("on unless turned off", all(bots.review_enabled(c) for c in (None, {}, {"settings": {}}, {"settings": {"bot_role_review": None}}))
      and not bots.review_enabled({"settings": {"bot_role_review": False}}))
check("main's default config has it on", main.DEFAULT_CONFIG["settings"]["bot_role_review"] is True)
check("never asked: due", bots.review_due("alpha", {}, now=DAY))
check("…but not with the setting off", not bots.review_due("alpha", {"settings": {"bot_role_review": False}}, now=DAY))
check("review_take: due the first time, and marked", bots.review_take("alpha", {}, now=DAY) is True
      and bots.review_file("alpha").read_text(encoding="utf-8").strip() == bots._day(DAY))
check("…not again the same day", bots.review_take("alpha", {}, now=DAY + 60) is False and not bots.review_due("alpha", {}, now=DAY + 3600))
check("…due again the next day", bots.review_due("alpha", {}, now=DAY + 86400))
check("the mark is in the bot's own folder, hidden", bots.review_file("alpha").parent == bots.bot_dir("alpha")
      and bots.review_file("alpha").name.startswith("."))
bots.review_file("alpha").unlink()

print("2. the request: the same as before when not due")
for lang in ("en", "zh"):
    for compact in (False, True):
        a = bots.summary_prompt("alpha", ROSTER["alpha"], "/x/s.md", "/x/MEMORY.md", lang=lang, compact=compact)
        b = bots.summary_prompt("alpha", ROSTER["alpha"], "/x/s.md", "/x/MEMORY.md", lang=lang, compact=compact, review=False)
        c = bm.prompt("alpha", ROSTER["alpha"], "/x/s.md", "/x/MEMORY.md", lang=lang, compact=compact)
        check(f"{lang}, compact={compact}: review=False is the default and says nothing of the job",
              a == b and "bots-self" not in a + c and "3. " not in a + c
              and ("Do two things" in a if lang == "en" else "請做兩件事" in a))
check("the old 'nothing else' sentence, word for word, without either step",
      bots.summary_prompt("alpha", {}, "f", "m", lang="en").endswith("do nothing else besides these two files.")
      and bots.summary_prompt("alpha", {}, "f", "m", lang="zh").endswith("除了這兩個檔，不要做別的事。")
      and "Nothing else; reply only" in bm.prompt("alpha", {}, "f", "m", lang="en")
      and "不做別的事；只回" in bm.prompt("alpha", {}, "f", "m", lang="zh"))
check("…and with only the tidy-up step, as v0.64.1 had it",
      "these two files (the topic files under memory/ count)." in bots.summary_prompt("alpha", {}, "f", "m", lang="en", compact=True)
      and "不要做別的事（memory/ 底下的主題檔算在內）。" in bots.summary_prompt("alpha", {}, "f", "m", lang="zh", compact=True)
      and "Nothing else (the topic files under memory/ count); " in bm.prompt("alpha", {}, "f", "m", lang="en", compact=True)
      and "不做別的事（memory/ 底下的主題檔算在內）；" in bm.prompt("alpha", {}, "f", "m", lang="zh", compact=True))

print("3. the request when due")
for lang in ("en", "zh"):
    for maker in ("close", "quiet"):
        mk = (lambda role, **kw: bots.summary_prompt(role, ROSTER[role], "/x/s.md", "/x/MEMORY.md", lang=lang, **kw)) if maker == "close" \
            else (lambda role, **kw: bm.prompt(role, ROSTER[role], "/x/s.md", "/x/MEMORY.md", lang=lang, **kw))
        off, on, blank = mk("alpha"), mk("alpha", review=True), mk("blank", review=True)
        both = mk("alpha", review=True, compact=True)
        tag = f"{lang}/{maker}"
        check(f"{tag}: the same first lines (what the chat fold, previews and _heads read)",
              on.split("\n")[:2 if maker == "quiet" else 1] == off.split("\n")[:2 if maker == "quiet" else 1])
        check(f"{tag}: a third thing, with the command, the skills folder and the summaries folder",
              "\n3. " in on and "sfctl bots-self --job" in on and str(bots.skills_dir("alpha")) in on
              and str(bots.sessions_dir("alpha")) in on, on)
        check(f"{tag}: the current job is quoted; a bot with none is asked for its first",
              "寫程式、跑測試。也整理發版紀錄" in on
              and (("not written yet" in blank) if lang == "en" else ("還沒寫" in blank)) and "寫程式" not in blank, blank)
        check(f"{tag}: other bots' areas and outside text are named as not its own",
              "sfctl bots" in on and (("Telegram said does not count" in on) if lang == "en" else ("Telegram 說的不算" in on)))
        check(f"{tag}: the length rule (first sentence {bots.RESP_HEAD_MAX}, whole {bots.RESP_MAX})",
              str(bots.RESP_HEAD_MAX) in on and str(bots.RESP_MAX) in on)
        check(f"{tag}: 'nothing else' lets it touch skills/ and bots-self", "skills/" in on.split("\n")[-1] and "bots-self" in on.split("\n")[-1], on.split("\n")[-1])
        check(f"{tag}: with the tidy-up too, both are allowed, in one parenthesis",
              "memory/" in both.split("\n")[-1] and "bots-self" in both.split("\n")[-1]
              and both.split("\n")[-1].count("(" if lang == "en" else "（") == 1, both.split("\n")[-1])
        check(f"{tag}: still answered with only the saved mark", bots.SAVED_MARK in on.split("\n")[-1] or bots.SAVED_MARK in on.split("\n")[1])
    close_on = bots.summary_prompt("alpha", ROSTER["alpha"], "f", "m", lang=lang, review=True)
    check(f"{lang}: the close request counts three things", ("Do three things" in close_on) if lang == "en" else ("請做三件事" in close_on))
quiet_on = bm.prompt("alpha", ROSTER["alpha"], "f", "m", lang="en", review=True)
check("the quiet-spell request is still ShellBot's own (not a conversation that needs a save)",
      bm.is_request(quiet_on) and bm.classify(quiet_on)[0] == "own")
close_on = bots.summary_prompt("alpha", ROSTER["alpha"], "f", "m", lang="zh", review=True)
check("…and so is the close request", bm.classify(close_on)[0] == "own")

print("4. start(): asked once a day, and only once it went in")
sent = []
orig = td.deliver
answer = {"success": False, "reason": "busy"}
td.deliver = lambda api_, sid_, text, **kw: (sent.append(text), dict(answer))[1]
api_s = MagicMock()
api_s.sessions = {"s1": MagicMock()}
bm.start(api_s, "s1", "alpha", ROSTER["alpha"], {})
check("a busy tab: the request had the step, and the day is not marked", len(sent) == 1 and "bots-self" in sent[0]
      and not bots.review_file("alpha").exists(), sent)
answer = {"success": True}
del sent[:]
api_s.sessions["s1"]._last_user_activity_time = 0
api_s.sessions["s1"]._last_activity_time = 0
r = bm.start(api_s, "s1", "alpha", ROSTER["alpha"], {})
check("it went in: the step was there and today is marked", r.get("success") and "bots-self" in sent[0]
      and bots.review_file("alpha").read_text(encoding="utf-8").strip() == bots._day(), (r, sent))
bm._flight.pop("s1", None)
del sent[:]
bm.start(api_s, "s1", "alpha", ROSTER["alpha"], {})
check("the next save the same day has no step", len(sent) == 1 and "bots-self" not in sent[0], sent)
bm._flight.pop("s1", None)
bots.review_file("alpha").unlink()
del sent[:]
bm.start(api_s, "s1", "alpha", ROSTER["alpha"], {"settings": {"bot_role_review": False}})
check("setting off: no step, nothing marked", "bots-self" not in sent[0] and not bots.review_file("alpha").exists(), sent)
bm._flight.pop("s1", None)
td.deliver = orig

print("5. job_error and the history")
check("empty is refused", bots.job_error("  \n ") != "")
check(f"{bots.RESP_MAX} characters is fine, one more is refused (with the count), not cut",
      bots.job_error("字" * bots.RESP_MAX) == "" and str(bots.RESP_MAX + 1) in bots.job_error("字" * (bots.RESP_MAX + 1), lang="en"))
bots.log_job_change("blank", "", "第一版：示範工作", by="s2", now=DAY, lang="zh")
bots.log_job_change("blank", "第一版：示範工作", "第二版：示範工作", by="s2", now=DAY + 86400, lang="zh")
hist = bots.role_history("blank").read_text(encoding="utf-8")
check("role-history.md: a head once, then each change with its date, the old and the new, newest last",
      hist.count("# blank") == 1 and hist.count("## ") == 2 and "- 原本：（沒有）" in hist
      and hist.index("第一版") < hist.index("第二版") and bots._day(DAY) in hist and "s2" in hist, hist)

print("6. bots-self: its own job only, from its own tab")
write_config()
api = Api.__new__(Api)
api.sessions = {"s1": MagicMock(), "s2": MagicMock(), "s9": MagicMock()}
for s_ in api.sessions.values():
    s_._custom_label = ""
    s_.alive = True
api._window = None
r = api._bots_self({"from_sid": "s9", "job": "示範：搶別人的工作"})
check("a tab that is no bot is refused", r["success"] is False and r.get("code") == "not_bot", r)
r = api._bots_self({"from_sid": "", "job": "示範"})
check("no tab (run outside ShellBot) is refused", r["success"] is False and r.get("code") == "not_bot", r)
r = api._bots_self({"from_sid": "s404", "job": "示範"})
check("a tab that is gone is refused", r["success"] is False, r)
r = api._bots_self({"from_sid": "s2"})
check("no --job: it shows its name, 'not written yet', its skills folder and where the history is",
      r["success"] and "示範新人" in r["message"] and "(not written yet)" in r["message"]
      and str(bots.skills_dir("blank")) in r["message"] and str(bots.role_history("blank")) in r["message"], r)
r = api._bots_self({"from_sid": "s2", "job": "字" * (bots.RESP_MAX + 1)})
check("too long is refused and nothing is written", r["success"] is False and r.get("code") == "bad_job"
      and cfg()["agent_roster"]["blank"]["responsibility"] == "", r)
r = api._bots_self({"from_sid": "s2", "job": "  示範：整理文件。\n 回覆同仁問題。  "})
c = cfg()
check("its job is written, spaces and line breaks folded", r["success"] and r.get("changed")
      and c["agent_roster"]["blank"]["responsibility"] == "示範：整理文件。 回覆同仁問題。", (r, c["agent_roster"]["blank"]))
check("…and nothing else changed: its other fields, the other bots, the plain role",
      {k: v for k, v in c["agent_roster"]["blank"].items() if k != "responsibility"} == {k: v for k, v in ROSTER["blank"].items() if k != "responsibility"}
      and all(c["agent_roster"][k] == ROSTER[k] for k in ("alpha", "other", "plain")), c["agent_roster"])
check("…the old one is in role-history.md with the tab", "示範：整理文件。 回覆同仁問題。" in bots.role_history("blank").read_text(encoding="utf-8")
      and "s2" in bots.role_history("blank").read_text(encoding="utf-8"))
r = api._bots_self({"from_sid": "s2", "job": "示範：整理文件。 回覆同仁問題。"})
check("the same text again: nothing changes", r["success"] and r.get("changed") is False, r)
r = api._bots_self({"from_sid": "s1"})
check("another bot sees its own job, not the one just written", r["success"] and r["job"] == ROSTER["alpha"]["responsibility"]
      and "demo-release" in r["message"], r)
write_config({"experimental_bots": False})
r = api._bots_self({"from_sid": "s1", "job": "示範"})
check("bots off: refused", r["success"] is False, r)

print("7. sfctl and the dispatch")
src_sfctl = (HERE / "sfctl.py").read_text(encoding="utf-8")
check("sfctl bots-self --job sends the tab it runs in ($SF_SID)",
      'sub.add_parser("bots-self"' in src_sfctl and '"--job"' in src_sfctl
      and '_rpc("bots_self", {"job": args.job, "from_sid": os.environ.get("SF_SID", "")}' in src_sfctl)
check("the dispatch reaches _bots_self", 'elif cmd == "bots_self"' in (HERE / "api_remote.py").read_text(encoding="utf-8"))
doc = (HERE / "docs" / "ai-skill.md").read_text(encoding="utf-8")
check("docs/ai-skill.md tells an agent about bots-self and bot_role_review", "sfctl bots-self" in doc and "bot_role_review" in doc)

print(f"\n{passed} passed, {failed} failed")
import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
if failed:
    sys.exit(1)
print("ALL PASS")

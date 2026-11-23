#!/usr/bin/env python3
"""用量不同步（v0.64）：沒綁帳號的分頁，頂列膠囊與帳號面板要看同一份讀數。

回報的現象：帳號面板「目前 SESSION：未登入」、下一個 SESSION 那個帳號列 4% / 92%，
同時膠囊卻是 ⚠ 0% / 85%（沿用舊資料）。分頁沒綁帳號 → 跑的是 ~/.claude 的全域登入。

驗：
  1. 對得到：全域登入與某個已登記帳號嚴格同一人（email＋組織＋方案）→ 膠囊與面板共用
     一份快取與冷卻；面板查新 → 膠囊拿到新值、不再 stale；之後輪詢零額外查詢
  2. 對不到／不同人：不共用（email 同但組織不同、方案不同、憑證與帳號資料對不上、有兩個
     候選），各用各的 token
  3. 「目前 SESSION」：沒綁帳號顯示實際在用的全域帳號（標「全域」）；真的沒登入才是 None
  4. 重新登入：auth 失敗的冷卻在憑證換掉後清除一次；429／網路失敗不清；沒換就不清

全部離線：urlopen、鑰匙圈、設定檔都是假的，不打真 API、不讀真鑰匙圈。
跑法：.venv/bin/python tests_usage_sync.py
"""
import json
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
sys.modules.setdefault("webview", MagicMock())
sys.modules.setdefault("bridge_telegram", MagicMock())
sys.path.insert(0, str(HERE))

import account_manager as AM  # noqa: E402
import api_host  # noqa: E402
import usage_probe as U  # noqa: E402
from api_accounts import AccountsApiMixin  # noqa: E402
from tests_usage_probe import (  # noqa: E402
    FakeClock, GOOD_API_PAYLOAD, TokenAwareUrlopen, http_error, probe_env)

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


NEW_PAYLOAD = {
    "five_hour": {"utilization": 4.0, "resets_at": "2026-07-03T18:00:00+08:00"},
    "seven_day": {"utilization": 92.0, "resets_at": "2026-07-07T09:00:00+08:00"},
}

DEMO = "demo@example.test"
FAR_FUTURE_MS = 4_000_000_000_000


class World:
    """一台假機器：~/.claude.json、全域鑰匙圈、profile 鑰匙圈、設定檔。"""

    def __init__(self, email=DEMO, org="Demo Org", plan="team", token="tok-global",
                 tier_ok=True, signed_in=True):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.email, self.org, self.plan = email, org, plan
        self.token, self.expires = token, 1
        self.tier_ok = tier_ok
        self.signed_in = signed_in
        self.profile_tokens = {}      # CLAUDE_CONFIG_DIR -> token
        self.cfg = {}
        self.write_claude_json()
        self.am = AM.AccountManager(
            root=self.home / "profiles", home=self.home,
            keychain_getter=self.keychain,
            profile_keychain_reader=lambda d: {"claudeAiOauth": {
                "accessToken": self.profile_tokens.get(str(d), ""), "expiresAt": FAR_FUTURE_MS}})

    def write_claude_json(self):
        data = {}
        if self.signed_in:
            data = {"oauthAccount": {
                "emailAddress": self.email, "organizationName": self.org,
                "organizationRateLimitTier": "tier-a", "userRateLimitTier": "tier-a"}}
        (self.home / ".claude.json").write_text(json.dumps(data))

    def keychain(self):
        if not self.signed_in:
            return {}
        return {"claudeAiOauth": {
            "accessToken": self.token, "refreshToken": "r-" + self.token,
            "expiresAt": self.expires, "subscriptionType": self.plan,
            "rateLimitTier": "tier-a" if self.tier_ok else "tier-other"}}

    def oauth(self, env=None):
        d = (env or {}).get("CLAUDE_CONFIG_DIR")
        if d:
            return {"accessToken": self.profile_tokens.get(d, ""), "expiresAt": FAR_FUTURE_MS}
        return self.keychain().get("claudeAiOauth") or {}

    def close(self):
        self.tmp.cleanup()


class Host(AccountsApiMixin):
    def __init__(self, world):
        self.sessions = {}
        self.world = world

    @staticmethod
    def _single_account_state(provider):
        return {"current": None, "global": None, "accounts": [], "logged_in": False,
                "single_account": True}

    def _install_info(self, provider):
        return {}


saved_fakes = {}


def make(world, clock, net):
    """把 usage_probe 與 api_host 接到假世界。回傳 (host, ctx)。"""
    ctx = probe_env(clock, net)
    ctx.__enter__()
    U._claude_oauth = world.oauth
    # 不碰真的鑰匙圈與 ~/.claude.json：profile 鑰匙圈與全域帳號標籤都換成假的
    saved_fakes.update(kc=AM.read_claude_profile_keychain, acct=U._claude_account)
    AM.read_claude_profile_keychain = lambda d: {"claudeAiOauth": world.oauth({"CLAUDE_CONFIG_DIR": str(d)})}
    U._claude_account = lambda: f"{world.email} · Team"
    U.PROVIDERS = ("claude",)
    ns = {
        "ACCOUNT_MANAGER": world.am, "account_manager": AM, "usage_probe": U,
        "load_config": lambda: world.cfg, "save_config": lambda c: None,
        "IS_WIN": False, "_swallow": lambda *a, **k: None,
    }
    api_host.bind(ns)
    host = Host(world)
    host.sessions["s1"] = types.SimpleNamespace(cmd="claude", account_refs={})
    return host, ctx


_ORIG_PROVIDERS = U.PROVIDERS


def done(ctx, world):
    AM.read_claude_profile_keychain = saved_fakes["kc"]
    U._claude_account = saved_fakes["acct"]
    U.PROVIDERS = _ORIG_PROVIDERS
    ctx.__exit__(None, None, None)
    world.close()


def brief(host, sid="s1"):
    return json.loads(host.tab_usage_brief(sid))


# ── 1. 對得到：共用 ───────────────────────────────────────────────────────
print("1. 沒綁帳號的分頁與面板對到同一個帳號 → 共用快取")
world = World()
clock = FakeClock()
net = TokenAwareUrlopen({"tok-global": http_error(429)})
host, ctx = make(world, clock, net)
try:
    b0 = brief(host)                      # 登入前／被擋：膠囊查不到
    check("起點：膠囊沒有數字（429、無舊資料）", not b0.get("five_hr") and not b0.get("week"), b0)
    ref = host.sessions["s1"] and host._global_login("claude", host._account_config())[0]
    check("全域登入對到已登記的 profile（email＋組織＋方案相同）", bool(ref), ref)
    # 之前有一次好讀數，後來被擋 → 膠囊沿用舊值 + stale
    net.outcomes["tok-global"] = GOOD_API_PAYLOAD
    clock.now += 100
    b1 = brief(host)
    check("好讀數進來：膠囊 18% / 20%", b1["five_hr"]["pct"] == 18 and not b1["stale"], b1)
    net.outcomes["tok-global"] = http_error(429)
    clock.now += 100
    b2 = brief(host)
    check("再被擋：膠囊沿用舊值並標 stale（⚠）", b2["stale"] and b2["five_hr"]["pct"] == 18, b2)
    check("膠囊結果帶 account_ref", b2.get("account_ref") == ref, b2.get("account_ref"))
    # 面板用同一個帳號再查，這次成功（冷卻期過後）
    net.outcomes["tok-global"] = NEW_PAYLOAD
    clock.now += 100
    panel = json.loads(host.account_usage_all(True))["providers"]["claude"]
    row = panel[ref]
    check("面板那一列拿到新值 4% / 92%", row["five_hr"]["pct"] == 4 and row["week"]["pct"] == 92, row)
    check("面板那一列標為目前登入", row["is_current_login"] is True)
    calls = len(net.calls)
    b3 = brief(host)
    check("面板查新之後，膠囊直接拿到新值、不再 stale", b3["five_hr"]["pct"] == 4
          and b3["week"]["pct"] == 92 and not b3["stale"], b3)
    check("…而且是同一份快取：膠囊沒有再打 API", len(net.calls) == calls, net.calls)
    # 輪詢次數：45s TTL 內連續輪詢與開關面板都不增加查詢
    for _ in range(5):
        brief(host)
        host.account_usage_all(False)
    check("正常輪詢（膠囊×5＋面板×5）零額外查詢", len(net.calls) == calls, net.calls)
finally:
    done(ctx, world)

print("1b. 反方向：膠囊查到新值 → 面板那一列直接用，不再查")
world = World()
clock = FakeClock()
net = TokenAwareUrlopen({"tok-global": NEW_PAYLOAD})
host, ctx = make(world, clock, net)
try:
    b = brief(host)
    ref = b["account_ref"]
    n = len(net.calls)
    row = json.loads(host.account_usage_all(False))["providers"]["claude"][ref]
    check("面板那一列 = 膠囊剛查到的值", row["five_hr"]["pct"] == 4 and row["week"]["pct"] == 92, row)
    check("面板沒有另外打 API", len(net.calls) == n, net.calls)
finally:
    done(ctx, world)

print("1c. 分頁綁的就是全域登入的那個帳號 → 同樣共用（不再各打一次 profile token）")
world = World()
clock = FakeClock()
net = TokenAwareUrlopen({"tok-global": NEW_PAYLOAD, "tok-profile": GOOD_API_PAYLOAD})
host, ctx = make(world, clock, net)
try:
    cfg = host._account_config()
    ref = host._global_login("claude", cfg)[0]
    world.profile_tokens[str(world.am._profile_dir("claude", ref))] = "tok-profile"
    host.sessions["s1"].account_refs = {"claude": ref}
    b = brief(host)
    check("綁定且是全域帳號：走共用快取（global token，4%）", b["five_hr"]["pct"] == 4, b)
    check("沒有用 profile token 另打", "tok-profile" not in net.calls, net.calls)
finally:
    done(ctx, world)

# ── 2. 不同人不共用 ───────────────────────────────────────────────────────
print("2. 不同帳號不共用快取")
world = World()
try:
    prof = {"id": "claude-x", "email": "Demo@Example.test ", "organization": "demo  org", "plan": "TEAM"}
    cfg = {"accounts": {"profiles": {"claude": [prof]}}}
    disc = {"email": DEMO, "organization": "Demo Org", "plan": "team"}
    check("大小寫／空白差異仍視為同一人", world.am.match_profile(cfg, "claude", disc) == "claude-x")
    check("email 相同、組織不同 → 不是同一人",
          world.am.match_profile(cfg, "claude", {**disc, "organization": "Other Org"}) is None)
    check("email 相同、方案不同（team vs pro）→ 不是同一人",
          world.am.match_profile(cfg, "claude", {**disc, "plan": "pro"}) is None)
    check("email 不同 → 不是同一人",
          world.am.match_profile(cfg, "claude", {**disc, "email": "other@example.test"}) is None)
    check("憑證與帳號資料對不上（mismatch）→ 不比對",
          world.am.match_profile(cfg, "claude", {**disc, "mismatch": {"x": 1}}) is None)
    check("沒有 email → 不比對", world.am.match_profile(cfg, "claude", {**disc, "email": ""}) is None)
    cfg2 = {"accounts": {"profiles": {"claude": [prof, {**prof, "id": "claude-y"}]}}}
    check("兩個候選（有歧義）→ 不比對", world.am.match_profile(cfg2, "claude", disc) is None)
finally:
    world.close()

print("2b. 全域登入對不到任何 profile：膠囊維持全域鍵，面板別的帳號用自己的 token")
world = World(tier_ok=False)          # 憑證與帳號資料對不上 → ensure() 不登記、不比對
clock = FakeClock()
net = TokenAwareUrlopen({"tok-global": NEW_PAYLOAD, "tok-b": GOOD_API_PAYLOAD})
host, ctx = make(world, clock, net)
try:
    cfg = host._account_config()
    other = {"id": "claude-b", "email": "b@example.test", "organization": "B Org", "plan": "pro", "label": "b@example.test"}
    cfg.setdefault("accounts", {}).setdefault("profiles", {}).setdefault("claude", []).append(other)
    world.am._profile_dir("claude", "claude-b").mkdir(parents=True, exist_ok=True)
    world.profile_tokens[str(world.am._profile_dir("claude", "claude-b"))] = "tok-b"
    b = brief(host)
    check("膠囊用全域 token（4%），沒有 account_ref", b["five_hr"]["pct"] == 4 and not b.get("account_ref"), b)
    panel = json.loads(host.account_usage_all(True))["providers"]["claude"]
    check("別的帳號列用自己的 token（18%），沒被膠囊的數字蓋掉",
          panel["claude-b"]["five_hr"]["pct"] == 18, panel.get("claude-b"))
    check("它不是目前登入", panel["claude-b"]["is_current_login"] is False)
    check("膠囊沒被別的帳號的數字污染", brief(host)["five_hr"]["pct"] == 4)
finally:
    done(ctx, world)

# ── 3. 目前 SESSION ───────────────────────────────────────────────────────
print("3. 「目前 SESSION」：沒綁帳號顯示實際在用的全域帳號")
world = World()
clock = FakeClock()
net = TokenAwareUrlopen({"tok-global": NEW_PAYLOAD})
host, ctx = make(world, clock, net)
try:
    st = json.loads(host.account_state("s1"))["providers"]["claude"]
    check("current 是全域登入的帳號", (st["current"] or {}).get("email") == DEMO, st["current"])
    check("標成全域登入", st.get("current_source") == "global_login", st.get("current_source"))
    check("current.id 與帳號列的 id 相同（那一列會標目前）", st["current"]["id"]
          in [a["id"] for a in st["accounts"]], st["current"])
    host.sessions["s1"].account_refs = {"claude": None}
    st = json.loads(host.account_state("s1"))["providers"]["claude"]
    check("account_refs 是 None 一樣顯示全域帳號", (st["current"] or {}).get("email") == DEMO)
    ref = st["current"]["id"]
    host.sessions["s1"].account_refs = {"claude": ref}
    st = json.loads(host.account_state("s1"))["providers"]["claude"]
    check("有綁帳號時維持原樣（不標全域）", st["current"]["id"] == ref and not st.get("current_source"), st)
    check("沒有分頁（sid 空）維持原樣", "current_source" not in
          json.loads(host.account_state(""))["providers"]["claude"])
finally:
    done(ctx, world)

world = World(signed_in=False)
clock = FakeClock()
net = TokenAwareUrlopen({})
host, ctx = make(world, clock, net)
try:
    st = json.loads(host.account_state("s1"))["providers"]["claude"]
    check("真的查不到登入 → current 為空（面板才顯示未登入）", st["current"] is None, st["current"])
finally:
    done(ctx, world)

# ── 4. 重新登入清掉失敗冷卻 ───────────────────────────────────────────────
print("4. 重新登入後，auth 失敗的冷卻被清掉")
world = World(token="tok-old")
clock = FakeClock()
net = TokenAwareUrlopen({"tok-old": http_error(401), "tok-new": NEW_PAYLOAD})
host, ctx = make(world, clock, net)
try:
    b = brief(host)
    check("登入過期：401，查不到", not b.get("five_hr"), b)
    n = len(net.calls)
    clock.now += 10
    brief(host)
    check("憑證沒變：冷卻期內不重查", len(net.calls) == n, net.calls)
    world.token, world.expires = "tok-new", 2          # 使用者重新 /login
    clock.now += 10                                     # 仍在 60 秒冷卻內
    b = brief(host)
    check("重新登入後：冷卻被清掉，馬上用新憑證重查（4% / 92%）",
          (b.get("five_hr") or {}).get("pct") == 4 and (b.get("week") or {}).get("pct") == 92
          and not b.get("stale"), b)
    check("只多一次查詢", len(net.calls) == n + 1, net.calls)
finally:
    done(ctx, world)

print("4b. 429 不清冷卻（429 很敏感）；憑證自己更新也不會讓 429 的帳號被連打")
world = World(token="tok-a")
clock = FakeClock()
net = TokenAwareUrlopen({"tok-a": http_error(429), "tok-b": NEW_PAYLOAD})
host, ctx = make(world, clock, net)
try:
    brief(host)
    n = len(net.calls)
    world.token, world.expires = "tok-b", 5
    clock.now += 10
    brief(host)
    check("429 之後憑證換了也照樣冷卻", len(net.calls) == n, net.calls)
    clock.now += 100
    b = brief(host)
    check("冷卻過了才重查", (b.get("five_hr") or {}).get("pct") == 4 and len(net.calls) == n + 1, (b, net.calls))
finally:
    done(ctx, world)

print("4c. 沒有憑證 → 之後登入也算重新登入")
world = World(signed_in=False)
clock = FakeClock()
net = TokenAwareUrlopen({"tok-global": NEW_PAYLOAD})
host, ctx = make(world, clock, net)
try:
    brief(host)
    check("沒登入：沒有打 API", net.calls == [], net.calls)
    world.signed_in = True
    world.write_claude_json()
    clock.now += 5
    b = brief(host)
    check("登入後 5 秒內就讀得到（沒有被 60 秒冷卻擋住）", b.get("five_hr", {}).get("pct") == 4, b)
finally:
    done(ctx, world)

print("4d. 面板（每帳號快取）同樣：auth 失敗後憑證換掉就清冷卻")
world = World(token="tok-global")
clock = FakeClock()
net = TokenAwareUrlopen({"tok-p": http_error(401), "tok-p2": NEW_PAYLOAD})
host, ctx = make(world, clock, net)
try:
    cfg = host._account_config()
    other = {"id": "claude-b", "email": "b@example.test", "organization": "B Org", "plan": "pro", "label": "b@example.test"}
    cfg["accounts"]["profiles"]["claude"].append(other)
    d = world.am._profile_dir("claude", "claude-b")
    d.mkdir(parents=True, exist_ok=True)
    world.profile_tokens[str(d)] = "tok-p"
    row = json.loads(host.account_usage_all(True))["providers"]["claude"]["claude-b"]
    check("帳號 B：401 → 查不到", not row["five_hr"], row)
    n = len(net.calls)
    clock.now += 5
    host.account_usage_all(True)
    check("憑證沒變：連按「重新查用量」也不重打", len(net.calls) == n, net.calls)
    world.profile_tokens[str(d)] = "tok-p2"
    clock.now += 5
    row = json.loads(host.account_usage_all(True))["providers"]["claude"]["claude-b"]
    check("帳號 B 重新登入後馬上重查成功", row["five_hr"]["pct"] == 4, row)
finally:
    done(ctx, world)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")

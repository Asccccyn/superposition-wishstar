"""Step8 冒烟测试：自建隔离服务 + 全新临时库，通过 HTTP 走完所有核心流程。

F04 修复：本脚本默认自己启动一个随机端口的临时服务、使用唯一的临时数据库，
绝不连接 127.0.0.1:8321 或任何真实数据，也不再依赖预先运行的服务器。
如显式设置 SUPERPOSITION_TEST_BASE_URL 指向外部地址，目标必须是带
SUPERPOSITION_TEST_INSTANCE=1 标记的隔离实例，否则拒绝运行。

第二轮修订：纪念日章节按"0 点冻结快照"测试——用 set_business_clock 注入
时钟（与服务器同进程），隐藏星写在纪念日的前一天，纪念日当天发起/确认。
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from _util import ROOT  # noqa: F401


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def call(base, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def check(name, cond, extra=""):
    tag = "PASS" if cond else "FAIL"
    line = f"  [{tag}] {name}"
    if not cond and extra:
        line += f"  -> {extra}"
    print(line)
    assert cond, name


EXTERNAL_BASE = os.environ.get("SUPERPOSITION_TEST_BASE_URL", "").rstrip("/")
server = None
tmpdir = None
set_business_clock = None
SH = None

if EXTERNAL_BASE:
    # 显式外部地址：拒绝默认生产端口，且必须是带测试实例标记的隔离服务
    assert ":8321" not in EXTERNAL_BASE, "拒绝把冒烟测试指向默认生产端口 8321"
    s, health = call(EXTERNAL_BASE, "GET", "/health")
    assert s == 200 and health.get("test_instance") is True, (
        "外部目标必须是以 SUPERPOSITION_TEST_INSTANCE=1 启动的隔离实例"
    )
    BASE = EXTERNAL_BASE
    Q_TOKEN = os.environ["SUPERPOSITION_TOKEN_HUMAN"]
    J_TOKEN = os.environ["SUPERPOSITION_TOKEN_COMPANION"]

    def set_business_clock(_when=None):
        """外部实例是独立进程，无法注入时钟；纪念日章节断言只适用于自建服务。"""
        return
else:
    tmpdir = tempfile.mkdtemp(prefix="superposition_step8_")
    port = _free_port()
    # 关键顺序：先设置 SUPERPOSITION_DB_PATH 等环境变量，再 import app.*——
    # app.config 在 import 时固化 DB_PATH，任何提前的 app.* import 都会把
    # 测试服务连到生产库（曾经发生过一次事故，勿再犯）。
    os.environ["SUPERPOSITION_DB_PATH"] = os.path.join(tmpdir, "step8.db")
    os.environ["SUPERPOSITION_TOKEN_HUMAN"] = "step8-human-test-token-0000000001"
    os.environ["SUPERPOSITION_TOKEN_COMPANION"] = "step8-companion-test-token-00000001"
    os.environ["SUPERPOSITION_TEST_INSTANCE"] = "1"
    Q_TOKEN = os.environ["SUPERPOSITION_TOKEN_HUMAN"]
    J_TOKEN = os.environ["SUPERPOSITION_TOKEN_COMPANION"]

    from app.common import set_business_clock  # noqa: E402  env 已就位，此时才允许 import app.*

    SH = ZoneInfo("Asia/Shanghai")

    import uvicorn
    from app.main import app

    # 隔离保险丝：测试服务绝不允许连到临时目录之外的任何库
    assert str(tmpdir) in str(app.state.db.path), (
        f"隔离失败：测试服务连到了非临时库 {app.state.db.path}"
    )
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning",
    ))
    threading.Thread(target=server.run, daemon=True).start()
    BASE = f"http://127.0.0.1:{port}/api"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            s, health = call(BASE, "GET", "/health")
            if s == 200:
                break
        except Exception:
            pass
        time.sleep(0.15)
    else:
        raise AssertionError("隔离测试服务没有启动成功")

H_HDR = {"Authorization": f"Bearer {Q_TOKEN}"}
C_HDR = {"Authorization": f"Bearer {J_TOKEN}"}

try:
    # 开源版种子不含纪念日：先经 API 自建示例纪念日（3.14）
    s, _d = call(BASE, "POST", "/special-dates",
                 {"name": "3.14", "month": 3, "day": 14, "type": "anniversary"}, H_HDR)
    check("自建纪念日 3.14", s == 200 and _d["type"] == "anniversary", str(_d))

    # 业务时钟注入（与服务同进程）：隐藏星写在纪念日 3.14 的前一天
    set_business_clock(datetime(2026, 3, 13, 20, 0, 0, tzinfo=SH))

    # 1. 健康 + 鉴权（F20：health 附带 db_ok / version / test_instance）
    s, b = call(BASE, "GET", "/health")
    check("health", s == 200 and b["status"] == "ok" and b["db_ok"] is True
          and b["test_instance"] is True and b["version"])
    s, b = call(BASE, "GET", "/bottles/counts")
    check("缺少 Bearer 凭证返回 401", s == 401)
    s, b = call(BASE, "GET", "/bottles/counts", headers={"X-Actor-Id": "human"})
    check("伪造旧 X-Actor-Id 不能认证", s == 401)
    s, b = call(BASE, "GET", "/bottles/counts", headers={"Authorization": "Bearer invalid-invalid-invalid-invalid-invalid"})
    check("错误 Bearer 凭证返回 401", s == 401)
    s, b = call(BASE, "GET", "/bottles/counts", headers=H_HDR)
    check("合法 Bearer 凭证识别对方", s == 200 and b["my_name"] == "人类")

    # 2. 可见星：写入即进我们的瓶子，对方收到轻提示
    s, v = call(BASE, "POST", "/stars", {"content": "今天你回来啦，我看到你说“回来啦”的时候突然很开心。",
                                         "visibility": "visible", "mood_type": "开心"}, H_HDR)
    check("对方写可见星 -> SHARED", s == 200 and v["state"] == "SHARED")
    s, notes = call(BASE, "GET", "/notifications", headers=C_HDR)
    check("伴侣收到新可见星通知", any(n["type"] == "NEW_VISIBLE_STAR" for n in notes["items"]))
    check("通知不含正文", all("回来啦" not in (n["title"] + (n["body"] or "")) for n in notes["items"]))

    # 3. 隐藏星（F02：数量提示合并为一条、不暴露时间）
    s, h1 = call(BASE, "POST", "/stars", {"content": "伴侣的隐藏星A", "visibility": "hidden"}, C_HDR)
    s, h2 = call(BASE, "POST", "/stars", {"content": "伴侣的隐藏星B", "visibility": "hidden",
                                          "mood_text": "有点想你"}, C_HDR)
    s, q1 = call(BASE, "POST", "/stars", {"content": "对方的隐藏星C", "visibility": "hidden"}, H_HDR)
    s, q_notes = call(BASE, "GET", "/notifications", headers=H_HDR)
    hidden_notes = [n for n in q_notes["items"] if n["type"] == "HIDDEN_STAR_ADDED"]
    check("隐藏星数量通知合并为一条且不泄漏 star_id / 时间",
          len(hidden_notes) == 1 and hidden_notes[0]["star_id"] is None
          and hidden_notes[0]["created_at"] is None)
    s, counts = call(BASE, "GET", "/bottles/counts", headers=H_HDR)
    check("三瓶计数正确", counts["partner_private_count"] == 2 and counts["my_private_count"] == 1
          and counts["shared_count"] == 1, json.dumps(counts, ensure_ascii=False))

    # 4. 方式一：请求 -> 审批 -> 随机 -> 打开（F03：一次只有一个周期）
    s, req = call(BASE, "POST", "/requests", headers=H_HDR)
    check("创建请求不带内部 star_id", s == 200 and "allocated_star_id" not in req)
    s, b = call(BASE, "POST", "/requests", headers=H_HDR)
    check("已有 pending 请求时不能再发起（F03）", s == 400 and b.get("code") == "invalid_state")
    s, b = call(BASE, "POST", f"/requests/{req['id']}/respond", {"decision": "give"}, H_HDR)
    check("请求人自己不能审批", s == 403)
    s, r = call(BASE, "POST", f"/requests/{req['id']}/respond", {"decision": "give"}, C_HDR)
    check("伴侣审批通过但打开前不泄漏随机结果",
          s == 200 and r["request"]["status"] == "approved"
          and "star_id" not in r and "allocated_star_id" not in r["request"])
    s, request_list = call(BASE, "GET", "/requests", headers=H_HDR)
    rq_view = next(x for x in request_list["outgoing"] if x["id"] == req["id"])
    check("请求列表打开前不泄漏 allocated_star_id",
          "allocated_star_id" not in rq_view and "opened_star_id" not in rq_view)
    s, opened = call(BASE, "POST", f"/requests/{req['id']}/open", headers=H_HDR)
    check("对方打开随机抽中的星", s == 200 and opened["state"] == "SHARED"
          and opened["shared_origin"] == "revealed_by_request" and opened["author_id"] == "companion"
          and opened["shared_at"] == opened["opened_at"])
    s, b = call(BASE, "POST", f"/requests/{req['id']}/open", headers=H_HDR)
    check("不能重复打开（不可重抽）", s == 400)
    s, b = call(BASE, "POST", "/requests", headers=H_HDR)
    check("打开后未留话不能再申请（用户规则）", s == 400 and b.get("code") == "invalid_state")
    s, resp0 = call(BASE, "POST", f"/stars/{opened['id']}/responses",
                    {"type": "text", "text": "看到了。"}, H_HDR)
    check("给打开的星留话解除门槛", s == 200 and resp0["type"] == "text")

    # 作者可以编辑自己的星（新 PATCH 路径 + 注释字段）；产品不提供删除
    s, edited = call(BASE, "PATCH", f"/stars/{q1['id']}",
                     {"content": "对方的隐藏星C（改）", "note": "为什么写的注释"}, H_HDR)
    check("作者编辑自己的星并补注释", s == 200 and edited["note"] == "为什么写的注释")
    s, b = call(BASE, "PATCH", f"/stars/{q1['id']}", {"content": "偷改"}, C_HDR)
    check("对方不能编辑（收紧到作者本人）", s == 404)
    s, b = call(BASE, "DELETE", f"/stars/{q1['id']}", None, H_HDR)
    check("产品不提供删除", s in (404, 405))

    # 5. 方式二：伴侣主动挑一颗递给对方（带捎的话）
    # （注意：方式一随机抽走了 h1/h2 中的一颗，所以这里专门写一颗要亲手递的星）
    s, h3 = call(BASE, "POST", "/stars", {"content": "伴侣想亲手递的一颗", "visibility": "hidden"}, C_HDR)
    s, offer = call(BASE, "POST", "/offers", {"star_id": h3["id"], "message": "看看这个"}, C_HDR)
    check("伴侣主动递出指定的一颗并捎话", s == 200 and offer["status"] == "offered"
          and offer["message"] == "看看这个")
    s, offer_list = call(BASE, "GET", "/offers", headers=H_HDR)
    incoming_offer = next(x for x in offer_list["incoming"] if x["id"] == offer["id"])
    check("接收者接住前看不到 offer.star_id（捎的话可见）",
          "star_id" not in incoming_offer and incoming_offer.get("message") == "看看这个")
    s, b = call(BASE, "POST", f"/offers/{offer['id']}/accept", headers=C_HDR)
    check("只有接收者能接住", s == 403)
    s, accepted = call(BASE, "POST", f"/offers/{offer['id']}/accept", headers=H_HDR)
    check("对方接住，正文展开并进入我们的瓶子",
          s == 200 and accepted["state"] == "SHARED" and accepted["content"] == "伴侣想亲手递的一颗"
          and accepted["shared_origin"] == "revealed_by_offer"
          and accepted["shared_at"] == accepted["opened_at"])

    # 6. 回应：让写星的人知道星星被接住了（§20）+ 幂等重试（F10）
    op_key = "step8-retry-response-key-0001"
    s, resp = call(BASE, "POST", f"/stars/{h3['id']}/responses",
                   {"type": "text", "text": "我喜欢这颗。"}, {**H_HDR, "Idempotency-Key": op_key})
    check("文字回应已保存", s == 200 and resp["type"] == "text")
    s, resp_retry = call(BASE, "POST", f"/stars/{h3['id']}/responses",
                         {"type": "text", "text": "我喜欢这颗。"},
                         {**H_HDR, "Idempotency-Key": op_key})
    check("同一 Idempotency-Key 的重试不产生第二条回应", s == 200 and resp_retry["id"] == resp["id"])
    s, detail = call(BASE, "GET", f"/stars/shared/{h3['id']}", headers=C_HDR)
    check("我们的瓶子详情含回应且重试没有重复", s == 200 and len(detail["responses"]) == 1
          and detail["responses"][0]["text"] == "我喜欢这颗。")

    # 7. 纪念日互看（第二轮修订：额度 = 纪念日 3.14 当天 00:00 冻结快照）
    # 时钟切到纪念日当天早上；此时双方各自恰有 1 颗 cutoff 前写的隐藏星。
    set_business_clock(datetime(2026, 3, 14, 9, 0, 0, tzinfo=SH))
    s, dates = call(BASE, "GET", "/special-dates", headers=H_HDR)
    d314 = next(d for d in dates["items"] if (d["month"], d["day"]) == (3, 14))
    check("自建纪念日 3.14 类型正确", d314["type"] == "anniversary")
    # 不绑定具体日期的发起必须被拒绝（服务层领域校验，与 MCP 同一规则）
    s, b = call(BASE, "POST", "/sessions", {"type": "anniversary"}, H_HDR)
    check("必须绑定具体 special_date_id",
          s == 400 and "具体" in b.get("message", ""), str(b))
    # 选了纪念日却按 special_day 发起 -> 类型错配被拒
    s, b = call(BASE, "POST", "/sessions",
                {"type": "special_day", "special_date_id": d314["id"]}, H_HDR)
    check("纪念日不能按 special_day 发起",
          s == 400 and "类型" in b.get("message", ""), str(b))
    # 非纪念日当天（4.28）发起被拒
    set_business_clock(datetime(2026, 3, 15, 9, 0, 0, tzinfo=SH))
    s, b = call(BASE, "POST", "/sessions",
                {"type": "anniversary", "special_date_id": d314["id"]}, H_HDR)
    check("非纪念日当天不能发起",
          s == 400 and "当天" in b.get("message", ""), str(b))
    set_business_clock(datetime(2026, 3, 14, 9, 0, 0, tzinfo=SH))

    s, sess = call(BASE, "POST", "/sessions",
                   {"type": "anniversary", "special_date_id": d314["id"]}, H_HDR)
    check("发起互看 -> 等待确认，0 点快照额度 1+1",
          s == 200 and sess["status"] == "waiting_confirmation"
          and sess["type"] == "anniversary" and sess["special_date_id"] == d314["id"]
          and sess["actor_a_count"] == 1 and sess["actor_b_count"] == 1
          and sess["quota_cutoff_at"] == "2026-03-14T00:00:00",
          json.dumps(sess, ensure_ascii=False))
    # 纪念日当天新写的星可以存在，但不占额度、不进本轮候选
    s, late_j = call(BASE, "POST", "/stars",
                     {"content": "伴侣纪念日当天才写的", "visibility": "hidden"}, C_HDR)
    assert s == 200
    s, b = call(BASE, "POST", f"/sessions/{sess['id']}/confirm", headers=H_HDR)
    check("发起者不能自己确认", s == 403)
    s, active = call(BASE, "POST", f"/sessions/{sess['id']}/confirm", headers=C_HDR)
    check("确认 -> active，额度仍是 0 点快照（不按确认时数量）",
          s == 200 and active["status"] == "active"
          and active["actor_a_count"] == 1 and active["actor_b_count"] == 1
          and active["quota_cutoff_at"] == "2026-03-14T00:00:00",
          json.dumps(active, ensure_ascii=False))
    take_key = "step8-take-key-0001"
    s, next1 = call(BASE, "POST", f"/sessions/{sess['id']}/take-next",
                    None, {**C_HDR, "Idempotency-Key": take_key})
    check("伴侣看的是对方写的星（cutoff 前那颗）",
          s == 200 and next1["state"] == "SHARED"
          and next1["author_id"] == "human" and next1["opened_by"] == "companion"
          and next1["content"].startswith("对方的隐藏星C"))
    check("揭晓即首次查看：first_view 已初始化",
          next1["first_view_at"] == next1["opened_at"]
          and next1["first_view_by"] == "companion")
    s, next1_retry = call(BASE, "POST", f"/sessions/{sess['id']}/take-next",
                          None, {**C_HDR, "Idempotency-Key": take_key})
    check("重试同一操作返回同一颗星，不多看一颗（F10）",
          s == 200 and next1_retry["id"] == next1["id"])
    s, b = call(BASE, "POST", f"/sessions/{sess['id']}/take-next", headers=C_HDR)
    check("看了没留话不能继续看（用户规则）", s == 400 and b.get("code") == "invalid_state")
    s, r1 = call(BASE, "POST", f"/stars/{next1['id']}/responses",
                 {"type": "text", "text": "看到了。"}, C_HDR)
    assert s == 200
    s, next2 = call(BASE, "POST", f"/sessions/{sess['id']}/take-next", headers=H_HDR)
    check("对方看的是伴侣 cutoff 前写的星（当天新星不在本轮）",
          s == 200 and next2["state"] == "SHARED"
          and next2["author_id"] == "companion" and next2["opened_by"] == "human"
          and next2["content"] != "伴侣纪念日当天才写的")
    s, r2 = call(BASE, "POST", f"/stars/{next2['id']}/responses",
                 {"type": "text", "text": "我也看到了。"}, H_HDR)
    assert s == 200
    s, b = call(BASE, "POST", f"/sessions/{sess['id']}/take-next", headers=H_HDR)
    check("配额用完提示（纪念日指定文案）",
          s == 400 and b.get("code") == "bottle_empty"
          and b.get("message") == "不可以哦，你的瓶子里面没有星星可以交换了，下次多写点吧。",
          str(b))
    s, fin = call(BASE, "POST", f"/sessions/{sess['id']}/finish", headers=H_HDR)
    check("结束本轮", s == 200 and fin["status"] == "completed")
    # 纪念日当天新写的星仍安全地留在伴侣瓶子里
    s, j_hidden = call(BASE, "GET", "/stars/hidden/mine", headers=C_HDR)
    check("当天新写的星仍在伴侣瓶子里（SEALED）",
          any(x["content"] == "伴侣纪念日当天才写的" and x["state"] == "SEALED"
              for x in j_hidden["items"]))

    # 8. 终态：三个瓶子（伴侣瓶里留有纪念日当天新写的那颗）
    s, counts = call(BASE, "GET", "/bottles/counts", headers=H_HDR)
    check("终态三瓶：对方私人 0 / 伴侣私人 1 / 我们的瓶子 5 颗",
          counts["my_private_count"] == 0
          and counts["partner_private_count"] == 1 and counts["shared_count"] == 5,
          json.dumps(counts, ensure_ascii=False))

    # 私人星详情：作者本人可读且显式查看计足迹；对方一律 404（不泄露存在性）
    s, priv = call(BASE, "GET", f"/stars/hidden/mine/{late_j['id']}", headers=C_HDR)
    check("作者读私人详情并记录首次查看",
          s == 200 and priv["state"] == "SEALED"
          and priv["first_view_at"] and priv["first_view_by"] == "companion"
          and any(v["viewer_id"] == "companion" and v["count"] == 1 for v in priv["views"]))
    s, b = call(BASE, "GET", f"/stars/hidden/mine/{late_j['id']}", headers=H_HDR)
    check("对方读私人详情一律 404", s == 404)
    # 列表刷新不计查看足迹：再拉一次列表，足迹仍是 1
    call(BASE, "GET", "/stars/hidden/mine", headers=C_HDR)
    s, priv2 = call(BASE, "GET", f"/stars/hidden/mine/{late_j['id']}", headers=C_HDR)
    check("列表刷新不计足迹（第二次显式打开累计为 2）",
          s == 200 and sum(v["count"] for v in priv2["views"] if v["viewer_id"] == "companion") == 2
          and priv2["first_view_at"] == priv["first_view_at"], "首次查看时间不被覆盖")

    # 9. 普通用户不能读取内部 audit；共同瓶子来源仍可正常浏览
    s, audit = call(BASE, "GET", "/audit", headers=H_HDR)
    check("普通用户 API 已移除全局 audit", s == 404)
    s, shared = call(BASE, "GET", "/stars/shared", headers=H_HDR)
    origins = {item["shared_origin"] for item in shared["items"]}
    check("我们的瓶子三种来源齐全", {"visible_from_start", "revealed_by_request",
                                     "revealed_by_offer"} <= origins, str(origins))

    s, filtered = call(BASE, "GET", f"/stars/shared?author_id=companion&session_id={sess['id']}", headers=H_HDR)
    check("共同瓶子作者/session 筛选可用",
          s == 200 and len(filtered["items"]) == 1 and filtered["items"][0]["author_id"] == "companion")
    s, sessions_list = call(BASE, "GET", "/sessions", headers=H_HDR)
    check("历史批次列表可用（F11）",
          s == 200 and any(x["id"] == sess["id"] for x in sessions_list["items"]))
    s, view_detail = call(BASE, "GET", f"/stars/shared/{next1['id']}", headers=H_HDR)
    check("查看足迹按人累计（用户规则）",
          s == 200 and any(v["viewer_id"] == "human" and v["count"] >= 1
                           for v in view_detail["views"]))
    check("首次查看只记录第一次且不被后续查看覆盖",
          view_detail["first_view_by"] == "companion"
          and view_detail["first_view_at"] == next1["first_view_at"])

    print("\nSTEP 8 PASS —— REST API 冒烟测试全部通过（自建隔离服务，0 点快照语义）")
finally:
    set_business_clock(None)
    if server is not None:
        server.should_exit = True
        time.sleep(0.3)
    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)

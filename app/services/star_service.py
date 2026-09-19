"""StarService：业务规则与权限检查的唯一入口。

架构文档 §45：Web / MCP 必须共用同一套服务，所有规则在这里统一检查；
§46：推荐的领域服务方法清单。本文件按步骤逐块实现：
    Step3  写星 / 编辑 / 删除 / 三瓶计数 / 列表
    Step4  请求一颗隐藏星 -> 审批 -> 服务端随机 -> 打开
    Step5  作者主动递星 -> 接住
    Step6  回应 / 通知 / 审计
    Step7  特殊日期 / 纪念日拆星 session

权限设计原则（§2 / §44 / §58 第十三）：
    * 服务层根本不提供“读取他人隐藏星列表 / 正文”的方法；
    * 打开类操作逐一校验 actor 是否为合法接收人；
    * 隐藏星对非作者一律表现为“不存在”，避免泄露存在性。
"""
from __future__ import annotations

import json
import re
from datetime import date

from ..common import anniversary_cutoff, business_today, new_id, now_iso
from ..config import (
    MAX_CONTENT_LENGTH,
    MAX_MOOD_LENGTH,
    MAX_NOTE_LENGTH,
    MAX_OFFER_MESSAGE_LENGTH,
    MAX_NAME_LENGTH,
    MAX_OPERATION_ID_LENGTH,
    MAX_RESPONSE_TEXT_LENGTH,
)
from ..db.connection import Database
from ..domain import enums as E
from ..domain.errors import (
    BottleEmptyError,
    DuplicateError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ..domain.state_machine import require_transition
from ..repositories import (
    audit_repo,
    idempotency_repo,
    notification_repo,
    offer_repo,
    request_repo,
    response_repo,
    session_repo,
    special_date_repo,
    star_repo,
    user_repo,
)

# V1 音频回应只做引用保存：必须是明确的 http(s) URL，不接受空白占位（F03）
_AUDIO_URL_RE = re.compile(r"^https?://[^\s]+$")

# 私人瓶子里的星 = 尚未被打开的隐藏星（§3.1 / §39）
PRIVATE_ACTIVE_STATES = (
    E.StarState.SEALED.value,
    E.StarState.OFFERED.value,
    E.StarState.ALLOCATED_RANDOMLY.value,
    E.StarState.SESSION_LOCKED.value,
)

# §17 / §37：在我们的瓶子里如何区分来源
SOURCE_LABELS = {
    E.SharedOrigin.VISIBLE_FROM_START.value: "一开始就放进我们的瓶子",
    E.SharedOrigin.REVEALED_BY_REQUEST.value: "对方请求后拆开",
    E.SharedOrigin.REVEALED_BY_OFFER.value: "作者主动递出后拆开",
    E.SharedOrigin.REVEALED_BY_ANNIVERSARY.value: "纪念日一起拆开",
    E.SharedOrigin.REVEALED_BY_SPECIAL_DAY.value: "特殊日一起拆开",
}

# 纪念日配额耗尽的两类提示必须区分（用户指定文案，逐字保留）：
# 前者 = 我的交换额度用完；后者 = 对方本轮没有可交换候选。
ANNIVERSARY_QUOTA_EXHAUSTED = "不可以哦，你的瓶子里面没有星星可以交换了，下次多写点吧。"
ANNIVERSARY_PARTNER_EMPTY = "对方瓶子里没有本轮可以交换的星星了。"
SPECIAL_DAY_EXHAUSTED = "看完啦，下次记得多写点哦"


class StarService:
    """所有写操作都在单个 SQLite 事务内完成；写事务使用 BEGIN IMMEDIATE。"""

    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _require_user(self, conn, user_id: str) -> dict:
        user = user_repo.get(conn, user_id)
        if user is None:
            raise NotFoundError(f"用户不存在：{user_id}")
        return user

    def _get_star(self, conn, star_id: str) -> dict:
        star = star_repo.get(conn, star_id)
        if star is None:
            raise NotFoundError("星星不存在")
        return star

    def _get_owned_star(self, conn, actor_id: str, star_id: str) -> dict:
        """按 ID 操作私人星时，非作者统一表现为不存在，避免 hidden existence probe。"""
        star = star_repo.get(conn, star_id)
        if star is None or star["author_id"] != actor_id:
            raise NotFoundError("星星不存在")
        return star

    def _transition_star(self, conn, star_id: str, frm: E.StarState,
                         to: E.StarState, **fields) -> None:
        """状态机校验 + 条件更新；rowcount != 1 说明并发下状态已变化（§48）。"""
        require_transition(frm, to)
        changed = star_repo.update_state(
            conn, star_id, to.value, from_state=frm.value,
            updated_at=now_iso(), **fields,
        )
        if changed != 1:
            raise InvalidStateError("星星状态刚刚发生了变化，操作未生效")

    def _audit(self, conn, event: E.EventType, actor_id=None,
               star_id=None, detail=None) -> None:
        audit_repo.insert(conn, event.value, actor_id, star_id, detail, now_iso())

    def _notify(self, conn, recipient_id: str, ntype: E.NotificationType,
                title: str, body=None, star_id=None) -> None:
        notification_repo.insert(
            conn, new_id("ntf"), recipient_id, ntype.value, title, body, star_id, now_iso()
        )

    # ------------------------------------------------------------------
    # 查看足迹统一口径（第二轮修订）：只有"明确打开正文详情"才叫查看；
    # 列表刷新、数量查询、轮询、内部装配都不计。first_view 只写一次；
    # 后续只按人累计次数（star_view_counts），不保存逐次查看时间。
    # ------------------------------------------------------------------
    def _record_view(self, conn, star_id: str, viewer_id: str, viewed_at: str) -> None:
        """每一次明确查看按人累计 +1；第一次的时间与人用 CAS 只初始化一次。"""
        star_repo.set_first_view_if_empty(
            conn, star_id, first_view_at=viewed_at, first_view_by=viewer_id
        )
        star_repo.upsert_view_count(conn, star_id, viewer_id)

    def _users_map(self, conn) -> dict:
        return {u["id"]: u for u in user_repo.list_all(conn)}

    @staticmethod
    def _parse_enum(enum_cls, value, label: str):
        try:
            return enum_cls(value)
        except ValueError:
            raise ValidationError(f"{label} 的取值不合法：{value}")

    @staticmethod
    def _with_flags(star: dict) -> dict:
        s = dict(star)
        s["being_offered"] = s["state"] == E.StarState.OFFERED.value
        s["waiting_random_delivery"] = s["state"] == E.StarState.ALLOCATED_RANDOMLY.value
        s["in_active_session"] = s["state"] == E.StarState.SESSION_LOCKED.value
        return s

    def _decorate_shared(self, star: dict, users: dict) -> dict:
        s = dict(star)
        s["author_name"] = users.get(s["author_id"], {}).get("name")
        opener = s.get("opened_by")
        s["opened_by_name"] = users.get(opener, {}).get("name") if opener else None
        first_viewer = s.get("first_view_by")
        s["first_view_by_name"] = users.get(first_viewer, {}).get("name") if first_viewer else None
        s["source_label"] = SOURCE_LABELS.get(s.get("shared_origin"))
        return s

    @staticmethod
    def _public_request(req: dict) -> dict:
        """流程完成前不向 API 暴露内部随机分配的 hidden star id。"""
        result = dict(req)
        allocated = result.pop("allocated_star_id", None)
        if result.get("opened_at") and allocated:
            result["opened_star_id"] = allocated
        return result

    @staticmethod
    def _public_offer(offer: dict, actor_id: str) -> dict:
        """作者知道自己递了哪颗；接收者在真正打开前不能看到 hidden star id。"""
        result = dict(offer)
        if actor_id != result["sender_id"] and not result.get("opened_at"):
            result.pop("star_id", None)
        return result

    @staticmethod
    def _validate_iso_date(value: str | None, label: str) -> str | None:
        """F17：只接受严格的 YYYY-MM-DD；date.fromisoformat 也认 20260914 这类
        非规范写法，直接拿原串去和库里的日期子串比较会悄悄筛出空结果。"""
        if value is None:
            return None
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValidationError(f"{label} 必须是有效的 YYYY-MM-DD 日期")
        if value != parsed.isoformat():
            raise ValidationError(f"{label} 必须是 YYYY-MM-DD 格式")
        return value

    # ------------------------------------------------------------------
    # F10：幂等操作。客户端为每次“意图”生成操作 ID；网络丢失响应后重试
    # 携带同一 ID，服务端在同一事务内返回已持久化的结果，不再重复写入。
    # ------------------------------------------------------------------
    _IDEMPOTENCY_MISS = object()

    @staticmethod
    def _check_operation_id(operation_id: str | None) -> str | None:
        if operation_id is None:
            return None
        operation_id = operation_id.strip()
        if not operation_id or len(operation_id) > MAX_OPERATION_ID_LENGTH:
            raise ValidationError("Idempotency-Key 不合法")
        return operation_id

    def _load_idempotent(self, conn, actor_id: str, operation_id: str | None,
                         endpoint: str):
        if operation_id is None:
            return self._IDEMPOTENCY_MISS
        cached = idempotency_repo.get(conn, actor_id, operation_id, endpoint)
        if cached is None:
            return self._IDEMPOTENCY_MISS
        return json.loads(cached)

    def _save_idempotent(self, conn, actor_id: str, operation_id: str | None,
                         endpoint: str, result: dict) -> dict:
        if operation_id is None:
            return result
        idempotency_repo.insert(
            conn, actor_id, operation_id, endpoint,
            json.dumps(result, ensure_ascii=False, default=str), now_iso(),
        )
        return result

    # ------------------------------------------------------------------
    # F02：隐藏星数量提示。不给每颗隐藏星留下可回溯的持久事件档案
    # （逐颗 created_at 会暴露写入日期与顺序）。数据库中每个接收人
    # 至多保留一行该类型通知；有新星时只把它重新置为未读，
    # created_at 维持首次创建时间不变。输出层另行合并脱敏（见
    # list_notifications），历史多行旧数据同样收敛为一条。
    # ------------------------------------------------------------------
    def _bump_hidden_count_notice(self, conn, partner_id: str, author_name: str) -> None:
        existing = notification_repo.find_any_by_type(
            conn, partner_id, E.NotificationType.HIDDEN_STAR_ADDED.value
        )
        if existing is not None:
            notification_repo.mark_unread(conn, existing["id"])
            return
        self._notify(conn, partner_id, E.NotificationType.HIDDEN_STAR_ADDED,
                     f"{author_name}的瓶子里又多了一颗星星", None, None)

    # ------------------------------------------------------------------
    # Step3：写星星（§4）/ 编辑（§51）/ 删除（§52）
    # ------------------------------------------------------------------
    def create_star(self, actor_id: str, content: str, visibility: str,
                    mood_type: str | None = None, mood_text: str | None = None,
                    note: str | None = None,
                    operation_id: str | None = None) -> dict:
        content = (content or "").strip()
        if not content:
            raise ValidationError("星星内容不能为空")
        if len(content) > MAX_CONTENT_LENGTH:
            raise ValidationError(f"星星最长 {MAX_CONTENT_LENGTH} 字——它是一颗星星，不是一封信")
        mood_type = (mood_type or "").strip() or None
        mood_text = (mood_text or "").strip() or None
        note = (note or "").strip() or None
        if mood_type and len(mood_type) > MAX_MOOD_LENGTH:
            raise ValidationError(f"心情类型最长 {MAX_MOOD_LENGTH} 字")
        if mood_text and len(mood_text) > MAX_MOOD_LENGTH:
            raise ValidationError(f"心情文本最长 {MAX_MOOD_LENGTH} 字")
        if note and len(note) > MAX_NOTE_LENGTH:
            raise ValidationError(f"注释最长 {MAX_NOTE_LENGTH} 字")
        vis = self._parse_enum(E.Visibility, visibility, "可见性")
        operation_id = self._check_operation_id(operation_id)

        with self.db.transaction(immediate=True) as conn:
            author = self._require_user(conn, actor_id)
            prior = self._load_idempotent(conn, actor_id, operation_id, "stars.create")
            if prior is not self._IDEMPOTENCY_MISS:
                return prior
            now = now_iso()
            star = {
                "id": new_id("star"),
                "author_id": actor_id,
                "recipient_id": author["partner_id"],
                "content": content,
                "mood_type": mood_type,
                "mood_text": mood_text,
                "note": note,
                "visibility": vis.value,
                "state": E.StarState.CREATED.value,
                "written_at": now,
                "shared_at": None,
                "opened_at": None,
                "opened_by": None,
                "open_mode": None,
                "shared_origin": None,
                "cycle_id": None,
                "created_at": now,
                "updated_at": now,
            }
            star_repo.insert(conn, star)

            if vis is E.Visibility.VISIBLE:
                # 可见星：写入即进入我们的瓶子，不经过任何审批（§5.1）
                self._transition_star(
                    conn, star["id"], E.StarState.CREATED, E.StarState.SHARED,
                    shared_at=now,
                    open_mode=E.OpenMode.VISIBLE_FROM_START.value,
                    shared_origin=E.SharedOrigin.VISIBLE_FROM_START.value,
                )
                self._audit(conn, E.EventType.STAR_CREATED_VISIBLE, actor_id, star["id"])
                # §38.1 新可见星通知：不含正文的轻提示；注释可以作为"为什么写"捎给对方
                self._notify(conn, author["partner_id"], E.NotificationType.NEW_VISIBLE_STAR,
                             f"来自{author['name']}的一颗星进了我们的瓶子",
                             note or None, star["id"])
            else:
                # 隐藏星：进入作者自己的私人瓶子（§6）
                self._transition_star(conn, star["id"], E.StarState.CREATED, E.StarState.SEALED)
                self._audit(conn, E.EventType.STAR_CREATED_HIDDEN, actor_id, star["id"])
                # §38.2 只提示数量变化；F02：合并为一条不落时间的数量提示
                self._bump_hidden_count_notice(conn, author["partner_id"], author["name"])

            result = star_repo.get(conn, star["id"])
            return self._save_idempotent(conn, actor_id, operation_id,
                                         "stars.create", result)

    # ------------------------------------------------------------------
    # 编辑（用户规则）：写错就要能改——作者本人随时可改自己的星，
    # 私人封存中和已进公共池的都可以；只有正在流程中（递出 / 被锁定 /
    # 待接住 / 状态切换的一瞬）短暂冻结。对方永远不能改。
    # 刻意不提供删除：星星写下就留下，改可以，抹掉不行。
    # ------------------------------------------------------------------
    EDITABLE_STATES = (E.StarState.SEALED.value, E.StarState.SHARED.value)

    def edit_star(self, actor_id: str, star_id: str,
                  content: str | None = None, mood_type: str | None = None,
                  mood_text: str | None = None, note: str | None = None) -> dict:
        """字段语义（F16）：None = 这次不改；空字符串 = 清空这个字段。"""
        if content is None and mood_type is None and mood_text is None and note is None:
            raise ValidationError("没有要修改的内容")
        if content is not None:
            content = content.strip()
            if not content:
                raise ValidationError("星星内容不能为空")
            if len(content) > MAX_CONTENT_LENGTH:
                raise ValidationError(f"星星最长 {MAX_CONTENT_LENGTH} 字")
        if mood_type is not None:
            mood_type = mood_type.strip()
            if len(mood_type) > MAX_MOOD_LENGTH:
                raise ValidationError(f"心情类型最长 {MAX_MOOD_LENGTH} 字")
        if mood_text is not None:
            mood_text = mood_text.strip()
            if len(mood_text) > MAX_MOOD_LENGTH:
                raise ValidationError(f"心情文本最长 {MAX_MOOD_LENGTH} 字")
        if note is not None:
            note = note.strip()
            if len(note) > MAX_NOTE_LENGTH:
                raise ValidationError(f"注释最长 {MAX_NOTE_LENGTH} 字")

        with self.db.transaction(immediate=True) as conn:
            self._require_user(conn, actor_id)
            star = self._get_owned_star(conn, actor_id, star_id)
            if star["state"] not in self.EDITABLE_STATES:
                raise InvalidStateError("这颗星正在流程中，等它落定之后再改")

            fields: dict = {"updated_at": now_iso()}
            if content is not None:
                fields["content"] = content
            if mood_type is not None:
                fields["mood_type"] = mood_type or None
            if mood_text is not None:
                fields["mood_text"] = mood_text or None
            if note is not None:
                fields["note"] = note or None
            star_repo.update_fields(conn, star_id, **fields)
            self._audit(conn, E.EventType.STAR_EDITED, actor_id, star_id)
            return star_repo.get(conn, star_id)

    # ------------------------------------------------------------------
    # Step3：查看（§7 / §37 / §39 / §40）
    # ------------------------------------------------------------------
    def get_identity(self, actor_id: str) -> dict:
        with self.db.transaction() as conn:
            me = self._require_user(conn, actor_id)
            partner = self._require_user(conn, me["partner_id"])
            return {
                "id": me["id"],
                "name": me["name"],
                "partner_id": partner["id"],
                "partner_name": partner["name"],
            }

    def count_bottles(self, actor_id: str) -> dict:
        with self.db.transaction() as conn:
            me = self._require_user(conn, actor_id)
            partner = user_repo.get(conn, me["partner_id"])
            return {
                "my_private_count": star_repo.count_hidden_unopened(conn, actor_id),
                "partner_private_count": star_repo.count_hidden_unopened(conn, me["partner_id"]),
                "shared_count": star_repo.count_shared(conn),
                "my_name": me["name"],
                "partner_name": partner["name"],
                "my_bottle": f"{me['name']}的瓶子",
                "partner_bottle": f"{partner['name']}的瓶子",
                "shared_bottle": "我们的瓶子",
            }

    def list_my_hidden_stars(self, actor_id: str) -> dict:
        """只有作者本人能列出自己的隐藏星；服务层不存在“列他人的隐藏星”的方法。"""
        with self.db.transaction() as conn:
            self._require_user(conn, actor_id)
            stars = star_repo.list_by_author_states(conn, actor_id, PRIVATE_ACTIVE_STATES)
            return {"items": [self._with_flags(s) for s in stars]}

    def list_shared_stars(self, actor_id: str, *, author_id: str | None = None,
                          written_date: str | None = None,
                          opened_date: str | None = None,
                          shared_origin: str | None = None,
                          session_id: str | None = None) -> dict:
        written_date = self._validate_iso_date(written_date, "written_date")
        opened_date = self._validate_iso_date(opened_date, "opened_date")
        with self.db.transaction() as conn:
            self._require_user(conn, actor_id)
            if author_id is not None:
                self._require_user(conn, author_id)
            if shared_origin is not None:
                shared_origin = self._parse_enum(
                    E.SharedOrigin, shared_origin, "shared_origin"
                ).value
            users = self._users_map(conn)
            items = [
                self._decorate_shared(s, users)
                for s in star_repo.list_shared(
                    conn,
                    author_id=author_id,
                    written_date=written_date,
                    opened_date=opened_date,
                    shared_origin=shared_origin,
                    cycle_id=session_id,
                )
            ]
            return {"items": items}

    def get_shared_star(self, actor_id: str, star_id: str, *,
                        record_view: bool = True) -> dict:
        """公共星详情。record_view=True（REST 用户显式打开详情）计 1 次 view；
        服务内部复用装配时传 False，绝不累计——同一次揭晓不允许记两次。

        用户规则：每一次明确查看都留足迹按人累计；第一次真正查看的时间
        与人只写一次（first_view_at / first_view_by），之后永不覆盖。"""
        with self.db.transaction(immediate=True) as conn:
            self._require_user(conn, actor_id)
            star = star_repo.get(conn, star_id)
            # 非公开星一律“不存在”：不泄露隐藏星的存在性（§58 第十三）
            if star is None or star["state"] != E.StarState.SHARED.value:
                raise NotFoundError("星星不存在")
            if record_view:
                self._record_view(conn, star_id, actor_id, now_iso())
                star = star_repo.get(conn, star_id)
            return self._assemble_shared_detail(conn, star)

    def _assemble_shared_detail(self, conn, star: dict) -> dict:
        """装配公共星详情 DTO（回应 + 查看足迹 + 首次查看），不产生任何写入。"""
        users = self._users_map(conn)
        result = self._decorate_shared(star, users)
        result["responses"] = [
            {**dict(r),
             "responder_name": users.get(r["responder_id"], {}).get("name")}
            for r in response_repo.list_by_star(conn, star["id"])
        ]
        result["views"] = [
            {**v,
             "viewer_name": users.get(v["viewer_id"], {}).get("name")}
            for v in star_repo.view_stats(conn, star["id"])
        ]
        return result

    def get_my_hidden_star(self, actor_id: str, star_id: str) -> dict:
        """私人星详情：只有作者本人能读；打开计 1 次 view 并初始化
        first_view（为空时）。不改变 state，不共享，不通知对方。"""
        with self.db.transaction(immediate=True) as conn:
            self._require_user(conn, actor_id)
            star = star_repo.get(conn, star_id)
            # 非作者或已出瓶的星一律“不存在”，避免存在性探测
            if star is None or star["author_id"] != actor_id or \
                    star["visibility"] != E.Visibility.HIDDEN.value or \
                    star["state"] not in PRIVATE_ACTIVE_STATES:
                raise NotFoundError("星星不存在")
            self._record_view(conn, star_id, actor_id, now_iso())
            star = star_repo.get(conn, star_id)
            users = self._users_map(conn)
            result = self._with_flags(star)
            result["views"] = [
                {**v,
                 "viewer_name": users.get(v["viewer_id"], {}).get("name")}
                for v in star_repo.view_stats(conn, star_id)
            ]
            return result

    # ------------------------------------------------------------------
    # Step4：方式一 —— 请求一颗隐藏星（§8.1 / §10 / §11 / §12 / §13 / §47）
    # ------------------------------------------------------------------
    def request_hidden_star(self, actor_id: str) -> dict:
        """发起权 ≠ 读取权（§10）：只创建请求，绝不返回任何正文。

        F03：一次只能有一个未走完的请求周期；用户规则：上一颗从私人瓶
        打开的星还没留话之前，不能再要下一颗。
        """
        with self.db.transaction(immediate=True) as conn:
            me = self._require_user(conn, actor_id)
            partner_id = me["partner_id"]
            unresponded = star_repo.find_unresponded_revealed(conn, actor_id)
            if unresponded is not None:
                raise InvalidStateError("先给上一颗打开的星星留句话，再来申请下一颗吧")
            unfinished = request_repo.find_unfinished(conn, actor_id)
            if unfinished is not None:
                raise InvalidStateError(
                    "上一颗请求还没有走完（等对方决定，或先去接住它），一次只能有一颗"
                )
            # §49：瓶子为空时诚实提示，不制造虚假星星
            if star_repo.count_sealed(conn, partner_id) == 0:
                raise BottleEmptyError("对方的瓶子现在是空的")

            now = now_iso()
            req = {
                "id": new_id("req"),
                "requester_id": actor_id,
                "owner_id": partner_id,
                "status": E.RequestStatus.PENDING.value,
                "allocated_star_id": None,  # §31：创建请求时绝不带 star_id
                "requested_at": now,
                "responded_at": None,
                "allocated_at": None,
                "opened_at": None,
            }
            request_repo.insert(conn, req)
            self._audit(conn, E.EventType.STAR_REQUEST_CREATED, actor_id,
                        None, f"requester={actor_id} owner={partner_id}")
            self._notify(conn, partner_id, E.NotificationType.STAR_REQUEST_RECEIVED,
                         f"{me['name']}想要你的一颗星星。", "你可以给一颗，或者现在先不给。")
            return self._public_request(req)

    def list_requests(self, actor_id: str) -> dict:
        with self.db.transaction() as conn:
            self._require_user(conn, actor_id)
            return {
                "incoming": [self._public_request(r) for r in request_repo.list_incoming(conn, actor_id)],
                "outgoing": [self._public_request(r) for r in request_repo.list_outgoing(conn, actor_id)],
            }

    def respond_hidden_request(self, actor_id: str, request_id: str, decision: str,
                               star_id: str | None = None) -> dict:
        """§11：无论什么原因都必须审批；§47：give 时服务端随机分配。

        用户规则：也可以指定给哪一颗（star_id）；不指定就随机。
        """
        if decision not in ("give", "not_now"):
            raise ValidationError("decision 只能是 give 或 not_now")

        with self.db.transaction(immediate=True) as conn:
            me = self._require_user(conn, actor_id)
            req = request_repo.get(conn, request_id)
            if req is None:
                raise NotFoundError("请求不存在")
            if req["owner_id"] != actor_id:
                raise PermissionDeniedError("只有星的主人才能回应这个请求")
            if req["status"] != E.RequestStatus.PENDING.value:
                raise InvalidStateError("这个请求已经被处理过了")

            now = now_iso()
            request_repo.update_fields(conn, request_id, responded_at=now)
            users = self._users_map(conn)
            requester_name = users[req["requester_id"]]["name"]

            if decision == "not_now":
                request_repo.update_fields(conn, request_id,
                                           status=E.RequestStatus.REJECTED.value)
                self._audit(conn, E.EventType.STAR_REQUEST_REJECTED, actor_id,
                            None, f"request={request_id}")
                self._notify(conn, req["requester_id"], E.NotificationType.STAR_REQUEST_DECLINED,
                             "这次先没有拿到星星", f"{me['name']}这次先不给，以后再试试吧。")
                return {"request": self._public_request(request_repo.get(conn, request_id)),
                        "note": "现在先不给"}

            # give：候选 = 主人当前 SEALED 的隐藏星（未被递出 / 未被锁定）
            # 用户规则：对方还有一颗打开后没留话的星时，先不再给新的
            unresponded = star_repo.find_unresponded_revealed(conn, req["requester_id"])
            if unresponded is not None:
                raise InvalidStateError(
                    "对方还有一颗打开的星星没有留话，等 TA 回应完再给新的吧"
                )

            if star_id is not None:
                # 指定给这一颗：必须是主人自己的、当前封存的隐藏星
                star = star_repo.get(conn, star_id)
                if star is None or star["author_id"] != actor_id or \
                        star["visibility"] != E.Visibility.HIDDEN.value or \
                        star["state"] != E.StarState.SEALED.value:
                    raise ValidationError("指定的星星不在可给出的范围里")
            else:
                star = star_repo.pick_random_sealed(conn, req["owner_id"])
            if star is None:
                # §50：批准时候选集合为空 → 请求结束，不读被锁住的星
                request_repo.update_fields(conn, request_id,
                                           status=E.RequestStatus.REJECTED.value)
                self._audit(conn, E.EventType.STAR_REQUEST_REJECTED, actor_id,
                            None, f"request={request_id} no_candidates")
                self._notify(conn, req["requester_id"], E.NotificationType.STAR_REQUEST_DECLINED,
                             "现在没有可以递出的隐藏星了", "等 TA 再写一颗吧。")
                return {"request": self._public_request(request_repo.get(conn, request_id)),
                        "note": "现在没有可以递出的隐藏星了"}

            # 随机或指定的结果落库即锁定，不可重抽（§13）
            self._transition_star(conn, star["id"],
                                  E.StarState.SEALED, E.StarState.ALLOCATED_RANDOMLY)
            request_repo.update_fields(
                conn, request_id,
                status=E.RequestStatus.APPROVED.value,
                allocated_star_id=star["id"],
                allocated_at=now,
            )
            self._audit(conn, E.EventType.STAR_REQUEST_APPROVED, actor_id,
                        star["id"], f"request={request_id}")
            self._audit(conn, E.EventType.STAR_RANDOM_ALLOCATED, None,
                        star["id"], f"request={request_id} chosen={'owner' if star_id else 'random'}")
            # §41：批准后请求人只看到“给了你一颗星星”，打开前不泄露正文
            self._notify(conn, req["requester_id"], E.NotificationType.STAR_REQUEST_GIVEN,
                         f"{me['name']}给了你一颗星星", "去接住并打开它吧。")
            return {"request": self._public_request(request_repo.get(conn, request_id)),
                    "note": None}

    def open_allocated_star(self, actor_id: str, request_id: str) -> dict:
        """§43.7：必须验证 requester / approved / 已分配，然后才返回正文。

        用户规则：还有打开后没留话的星（任何路径打开的）就不能打开下一颗；
        历史积压的多条 approved 请求同样被按序拦住（F03）。
        """
        with self.db.transaction(immediate=True) as conn:
            self._require_user(conn, actor_id)
            unresponded = star_repo.find_unresponded_revealed(conn, actor_id)
            if unresponded is not None:
                raise InvalidStateError("先给上一颗打开的星星留句话，再来打开这一颗")
            req = request_repo.get(conn, request_id)
            if req is None:
                raise NotFoundError("请求不存在")
            if req["requester_id"] != actor_id:
                raise PermissionDeniedError("只有请求人自己可以打开这颗星")
            if req["status"] != E.RequestStatus.APPROVED.value:
                raise InvalidStateError("请求尚未批准，或这颗星已经打开过了")
            if not req["allocated_star_id"]:
                raise InvalidStateError("这个请求还没有分配到星星")

            star = self._get_star(conn, req["allocated_star_id"])
            now = now_iso()
            self._transition_star(conn, star["id"],
                                  E.StarState.ALLOCATED_RANDOMLY, E.StarState.OPENED,
                                  opened_at=now, opened_by=actor_id)
            self._transition_star(conn, star["id"], E.StarState.OPENED, E.StarState.SHARED,
                                  open_mode=E.OpenMode.REQUEST_RANDOM.value,
                                  shared_origin=E.SharedOrigin.REVEALED_BY_REQUEST.value,
                                  shared_at=now)
            request_repo.update_fields(conn, request_id, opened_at=now)
            # 揭晓动作本身已把正文返回给请求人：计 1 次 view 并初始化 first_view；
            # 前端直接用本返回值渲染，不再补发会计 view 的详情请求。
            self._record_view(conn, star["id"], actor_id, now)
            self._audit(conn, E.EventType.STAR_OPENED, actor_id, star["id"],
                        f"request={request_id}")
            self._audit(conn, E.EventType.STAR_MOVED_TO_SHARED, None, star["id"])
            return self._assemble_shared_detail(conn, star_repo.get(conn, star["id"]))

    # ------------------------------------------------------------------
    # Step5：方式二 —— 作者主动递星（§8.2 / §14 / §15 / §29.3）
    # ------------------------------------------------------------------
    def offer_hidden_star(self, actor_id: str, star_id: str,
                          message: str | None = None) -> dict:
        """§14：作者可以自由挑选自己私人瓶子里的任何一颗可用隐藏星。

        用户规则：递的时候可以捎一句话（可不填）；要给看好几颗就连着递几次。
        """
        message = (message or "").strip() or None
        if message and len(message) > MAX_OFFER_MESSAGE_LENGTH:
            raise ValidationError(f"捎的话最长 {MAX_OFFER_MESSAGE_LENGTH} 字")
        with self.db.transaction(immediate=True) as conn:
            me = self._require_user(conn, actor_id)
            star = self._get_owned_star(conn, actor_id, star_id)
            if star["visibility"] != E.Visibility.HIDDEN.value:
                raise ValidationError("这颗星已经在我们的瓶子里了，不需要递")
            if star["state"] != E.StarState.SEALED.value:
                raise InvalidStateError("这颗星当前不在可递出的状态")

            now = now_iso()
            self._transition_star(conn, star_id, E.StarState.SEALED, E.StarState.OFFERED)
            offer = {
                "id": new_id("off"),
                "star_id": star_id,
                "sender_id": actor_id,
                "recipient_id": me["partner_id"],
                "status": E.OfferStatus.OFFERED.value,
                "offered_at": now,
                "opened_at": None,
                "message": message,
            }
            offer_repo.insert(conn, offer)
            self._audit(conn, E.EventType.STAR_OFFERED, actor_id, star_id,
                        f"offer={offer['id']}")
            # §42：接收者只看到“递给你一颗星星”；捎的话随通知带给对方
            self._notify(conn, me["partner_id"], E.NotificationType.STAR_OFFERED,
                         f"{me['name']}递给你一颗星星。",
                         message or "接住它，正文才会展开。")
            return self._public_offer(offer, actor_id)

    def list_offers(self, actor_id: str) -> dict:
        with self.db.transaction() as conn:
            self._require_user(conn, actor_id)
            return {
                "incoming": [
                    self._public_offer(o, actor_id)
                    for o in offer_repo.list_incoming(conn, actor_id)
                ],
                "outgoing": [
                    self._public_offer(o, actor_id)
                    for o in offer_repo.list_outgoing(conn, actor_id)
                ],
            }

    def accept_offered_star(self, actor_id: str, offer_id: str) -> dict:
        """§15：接住后正文才展开；§29.3：OFFERED -> DELIVERED -> OPENED -> SHARED。
        全程在同一个事务里，星星不会停留在“已递出但无人接”的悬空状态。

        用户规则：还有打开后没留话的星时，不能接住新的。
        """
        with self.db.transaction(immediate=True) as conn:
            self._require_user(conn, actor_id)
            unresponded = star_repo.find_unresponded_revealed(conn, actor_id)
            if unresponded is not None:
                raise InvalidStateError("先给上一颗打开的星星留句话，再来接住这一颗")
            offer = offer_repo.get(conn, offer_id)
            if offer is None:
                raise NotFoundError("没有这颗递来的星星")
            if offer["recipient_id"] != actor_id:
                raise PermissionDeniedError("这颗星星不是递给你的")
            if offer["status"] != E.OfferStatus.OFFERED.value:
                raise InvalidStateError("这颗星星已经被接住了")

            star = self._get_star(conn, offer["star_id"])
            now = now_iso()
            self._transition_star(conn, star["id"], E.StarState.OFFERED, E.StarState.DELIVERED)
            self._transition_star(conn, star["id"], E.StarState.DELIVERED, E.StarState.OPENED,
                                  opened_at=now, opened_by=actor_id)
            self._transition_star(conn, star["id"], E.StarState.OPENED, E.StarState.SHARED,
                                  open_mode=E.OpenMode.AUTHOR_OFFER.value,
                                  shared_origin=E.SharedOrigin.REVEALED_BY_OFFER.value,
                                  shared_at=now)
            offer_repo.update_fields(conn, offer_id,
                                     status=E.OfferStatus.COMPLETED.value, opened_at=now)
            # 接住即揭晓：计 1 次 view 并初始化 first_view，前端直接渲染返回值
            self._record_view(conn, star["id"], actor_id, now)
            self._audit(conn, E.EventType.STAR_ACCEPTED, actor_id, star["id"],
                        f"offer={offer_id}")
            self._audit(conn, E.EventType.STAR_OPENED, actor_id, star["id"],
                        f"offer={offer_id}")
            self._audit(conn, E.EventType.STAR_MOVED_TO_SHARED, None, star["id"])
            return self._assemble_shared_detail(conn, star_repo.get(conn, star["id"]))

    # ------------------------------------------------------------------
    # Step6：回应（§20）/ 通知（§38）/ 审计（§53）
    # ------------------------------------------------------------------
    def respond_to_star(self, actor_id: str, star_id: str, response_type: str,
                        text: str | None = None, audio_url: str | None = None,
                        audio_duration: float | None = None,
                        operation_id: str | None = None) -> dict:
        """§20：重点是让写星的人知道，这颗星真的被接住了。
        语音字段已预留（§21），V1 使用文字回应。"""
        rtype = self._parse_enum(E.ResponseType, response_type, "回应类型")
        if rtype is E.ResponseType.TEXT:
            text = (text or "").strip()
            if not text:
                raise ValidationError("文字回应不能为空")
            if len(text) > MAX_RESPONSE_TEXT_LENGTH:
                raise ValidationError(f"文字回应最长 {MAX_RESPONSE_TEXT_LENGTH} 字")
        if rtype is E.ResponseType.AUDIO:
            # F03：空白/占位 URL 不再被当作有效回应保存
            url = (audio_url or "").strip()
            if not url or not _AUDIO_URL_RE.match(url):
                raise ValidationError("音频回应需要一个有效的 http(s) audio_url")
            audio_url = url
        operation_id = self._check_operation_id(operation_id)

        with self.db.transaction(immediate=True) as conn:
            me = self._require_user(conn, actor_id)
            prior = self._load_idempotent(conn, actor_id, operation_id, "stars.respond")
            if prior is not self._IDEMPOTENCY_MISS:
                return prior
            star = self._get_star(conn, star_id)
            if star["state"] != E.StarState.SHARED.value:
                if star["author_id"] != actor_id:
                    raise NotFoundError("星星不存在")
                raise ValidationError("只有已经进入我们瓶子的星星才能回应")
            if actor_id not in (star["author_id"], star["recipient_id"]):
                raise PermissionDeniedError("只有这颗星相关的两个人可以回应")

            resp = {
                "id": new_id("res"),
                "star_id": star_id,
                "responder_id": actor_id,
                "type": rtype.value,
                "text": text,
                "audio_url": audio_url,
                "audio_duration": audio_duration,
                "created_at": now_iso(),
            }
            response_repo.insert(conn, resp)
            self._audit(conn, E.EventType.STAR_RESPONSE_ADDED, actor_id, star_id,
                        f"response={resp['id']} type={rtype.value}")

            # 提醒另一方：TA 的星星收到了回应
            other_id = star["recipient_id"] if actor_id == star["author_id"] else star["author_id"]
            preview = text if (rtype is E.ResponseType.TEXT and text) else "（一段语音回应）"
            self._notify(conn, other_id, E.NotificationType.STAR_RESPONSE_ADDED,
                         f"{me['name']}回应了你的星星", preview, star_id)
            return self._save_idempotent(conn, actor_id, operation_id,
                                         "stars.respond", resp)

    def list_notifications(self, actor_id: str, unread_only: bool = False,
                           limit: int = 50) -> dict:
        """F02/F12：隐藏星数量提示在输出层合并为一条、置顶且不带时间；
        unread_count 由服务端按合并后的口径计算，客户端不得自己按页重算。"""
        limit = max(1, min(int(limit or 50), 200))
        with self.db.transaction() as conn:
            me = self._require_user(conn, actor_id)
            partner = self._require_user(conn, me["partner_id"])
            items = notification_repo.list_for(conn, actor_id, unread_only, limit)
            hidden_type = E.NotificationType.HIDDEN_STAR_ADDED.value
            hidden_rows = [n for n in items if n["type"] == hidden_type]
            others = []
            for note in items:
                if note["type"] == hidden_type:
                    continue
                others.append(note)

            unread_hidden = notification_repo.count_unread_by_type(
                conn, actor_id, hidden_type
            )
            hidden_entry = None
            if hidden_rows or unread_hidden:
                if not hidden_rows:
                    hidden_rows = [notification_repo.find_any_by_type(
                        conn, actor_id, hidden_type
                    )]
                base = hidden_rows[0] or {}
                hidden_entry = {
                    "id": base.get("id"),
                    "recipient_id": actor_id,
                    "type": hidden_type,
                    "title": f"{partner['name']}的瓶子里又多了星星",
                    "body": None,
                    "star_id": None,          # 历史 star_id 一并剥离
                    "created_at": None,       # 不暴露任何逐颗时间
                    "is_read": 0 if unread_hidden else 1,
                }

            unread_total = (
                notification_repo.count_unread(conn, actor_id)
                - unread_hidden
                + (1 if unread_hidden else 0)
            )
            return {
                "items": ([hidden_entry] if hidden_entry else []) + others,
                "unread_count": unread_total,
            }

    def mark_notification_read(self, actor_id: str, notification_id: str) -> dict:
        with self.db.transaction(immediate=True) as conn:
            self._require_user(conn, actor_id)
            note = notification_repo.get(conn, notification_id)
            # F15：以接收人 + ID 一起判断，别人的和不存在的一律 404，
            # 不再通过 403/404 的差异泄露通知 ID 的存在性。
            if note is None or note["recipient_id"] != actor_id:
                raise NotFoundError("通知不存在")
            if note["type"] == E.NotificationType.HIDDEN_STAR_ADDED.value:
                # F02：合并提示读掉时，把该类型所有行一起置为已读
                notification_repo.mark_all_type_read(
                    conn, actor_id, E.NotificationType.HIDDEN_STAR_ADDED.value
                )
            else:
                notification_repo.mark_read(conn, notification_id, actor_id)
            return {"read": True, "id": notification_id}

    # ------------------------------------------------------------------
    # Step7：特殊日期（§34）与纪念日拆星 session（§22~§28 / §35 / §36）
    # ------------------------------------------------------------------
    def list_special_dates(self, actor_id: str) -> dict:
        with self.db.transaction() as conn:
            self._require_user(conn, actor_id)
            return {"items": special_date_repo.list_active(conn)}

    def add_special_date(self, actor_id: str, name: str, month: int, day: int,
                         year: int | None = None, date_type: str = "custom") -> dict:
        name = (name or "").strip()
        if not name:
            raise ValidationError("日期名称不能为空")
        if len(name) > MAX_NAME_LENGTH:
            raise ValidationError(f"日期名称最长 {MAX_NAME_LENGTH} 字")
        try:
            # 周年型无年份日期用闰年验证，因此 2/29 合法、2/30 等真实非法日期会被拒绝。
            date(year if year is not None else 2000, month, day)
        except ValueError:
            raise ValidationError("month/day/year 不是一个真实存在的日期")
        if date_type not in ("anniversary", "custom"):
            raise ValidationError(f"日期类型不合法：{date_type}")

        with self.db.transaction(immediate=True) as conn:
            self._require_user(conn, actor_id)
            d = {
                "id": new_id("sd"),
                "name": name,
                "month": month,
                "day": day,
                "year": year,
                "type": date_type,
                "active": 1,
                "created_by": actor_id,
                "created_at": now_iso(),
            }
            special_date_repo.insert(conn, d)
            return d

    def list_sessions(self, actor_id: str) -> dict:
        """F11：共同瓶按批次回看需要历史 session 列表（含绑定的特殊日期名）。"""
        with self.db.transaction() as conn:
            self._require_user(conn, actor_id)
            users = self._users_map(conn)
            dates = {d["id"]: d["name"] for d in special_date_repo.list_active(conn)}
            items = []
            for s in session_repo.list_all(conn):
                item = dict(s)
                item["initiated_by_name"] = users.get(s["initiated_by"], {}).get("name")
                item["special_date_name"] = dates.get(s["special_date_id"])
                items.append(item)
            return {"items": items}

    def start_special_session(self, actor_id: str, session_type: str | None = None,
                              special_date_id: str | None = None) -> dict:
        """§23：任何一方都不能单独拆对方的瓶子——先发起，等对方确认。

        第二轮修订（P0）：
        * 必须绑定具体 special_date_id；session.type 由服务端从 special_dates.type
          派生（anniversary -> anniversary，custom -> special_day），调用方显式传的
          type 若与派生结果不一致直接拒绝——"选了纪念日却按 special_day 发起"
          这类错配不再可能发生。
        * anniversary 只能在该纪念日的业务日（month/day 与 business_today 相同）
          发起；测试跨日期用 set_business_clock 注入时钟，不放宽生产规则。
        * anniversary 的交换额度在发起时按"当天 00:00（业务时区）冻结快照"计算
          并随 session 持久化（actor_a/b_count + quota_cutoff_at）；确认只把
          waiting -> active，绝不按确认时数量重算。custom/special_day 保留原有
          "确认那一刻瓶里数量"的配额口径，quota_cutoff_at 为空。
        """
        with self.db.transaction(immediate=True) as conn:
            me = self._require_user(conn, actor_id)
            if not special_date_id:
                raise ValidationError("必须选择一个具体的纪念日或特殊日")
            sdate = special_date_repo.get(conn, special_date_id)
            if sdate is None:
                raise NotFoundError("特殊日期不存在")
            derived = (E.SessionType.ANNIVERSARY.value
                       if sdate["type"] == "anniversary"
                       else E.SessionType.SPECIAL_DAY.value)
            if session_type is not None:
                requested = self._parse_enum(E.SessionType, session_type,
                                             "session 类型").value
                if requested != derived:
                    raise ValidationError(
                        f"该日期是 {sdate['type']} 类型，不能按 {requested} 发起"
                    )
            cutoff = None
            quota_a = quota_b = None
            if derived == E.SessionType.ANNIVERSARY.value:
                today = business_today()
                if (today.month, today.day) != (sdate["month"], sdate["day"]):
                    raise ValidationError(
                        f"「{sdate['name']}」是 {sdate['month']}.{sdate['day']}，"
                        "只能在纪念日当天发起一起拆星"
                    )
                cutoff = anniversary_cutoff(today)
                # 0 点冻结快照：0 点前已写、且 0 点时仍在各自私人瓶的 hidden 星数
                quota_a = star_repo.count_hidden_asof(conn, actor_id, cutoff)
                quota_b = star_repo.count_hidden_asof(conn, me["partner_id"], cutoff)

            if session_repo.get_unfinished(conn) is not None:
                raise DuplicateError("已经有一场拆星星在进行中了")

            sess = {
                "id": new_id("sess"),
                "type": derived,
                "special_date_id": special_date_id,
                "initiated_by": actor_id,
                "confirmed_by": None,
                "status": E.SessionStatus.WAITING_CONFIRMATION.value,
                "started_at": None,
                "ended_at": None,
                "actor_a_count": quota_a,
                "actor_b_count": quota_b,
                "quota_cutoff_at": cutoff,
            }
            session_repo.insert_session(conn, sess)
            self._audit(conn, E.EventType.SPECIAL_SESSION_STARTED, actor_id,
                        None, f"session={sess['id']} type={derived} "
                              f"date={special_date_id} cutoff={cutoff} "
                              f"quota={quota_a}+{quota_b}")
            invite_body = ("你同意后，就能互相看对方瓶子里的星星——"
                           + ("纪念日额度按当天 0 点时瓶里的星数冻结。"
                              if derived == E.SessionType.ANNIVERSARY.value
                              else "自己有多少颗，就能看对方多少颗。"))
            self._notify(conn, me["partner_id"], E.NotificationType.SESSION_INVITE,
                         f"{me['name']}：一起看星星吗？", invite_body)
            return sess

    def confirm_special_session(self, actor_id: str, session_id: str) -> dict:
        """§23：双方确认才激活。

        第二轮修订：anniversary 的额度在发起时已按 0 点快照持久化，
        确认只把 waiting -> active，绝不按确认时的当前数量覆盖
        （跨午夜/重启都不重算）。custom/special_day 保留"确认那一刻
        瓶里数量"的既有口径。
        """
        with self.db.transaction(immediate=True) as conn:
            me = self._require_user(conn, actor_id)
            sess = session_repo.get(conn, session_id)
            if sess is None:
                raise NotFoundError("拆星 session 不存在")
            if sess["status"] != E.SessionStatus.WAITING_CONFIRMATION.value:
                raise InvalidStateError("这场拆星不在等待确认的状态")
            if me["partner_id"] != sess["initiated_by"]:
                raise PermissionDeniedError("只有被邀请的一方可以确认")

            now = now_iso()
            if sess["type"] == E.SessionType.ANNIVERSARY.value:
                # 额度与 cutoff 已在发起时冻结，确认不改
                session_repo.update_fields(
                    conn, session_id,
                    status=E.SessionStatus.ACTIVE.value,
                    confirmed_by=actor_id,
                    started_at=now,
                )
            else:
                count_a = star_repo.count_hidden_unopened(conn, sess["initiated_by"])
                count_b = star_repo.count_hidden_unopened(conn, actor_id)
                session_repo.update_fields(
                    conn, session_id,
                    status=E.SessionStatus.ACTIVE.value,
                    confirmed_by=actor_id,
                    started_at=now,
                    actor_a_count=count_a,
                    actor_b_count=count_b,
                )
            self._audit(conn, E.EventType.SPECIAL_SESSION_CONFIRMED, actor_id,
                        None, f"session={session_id}")
            self._notify(conn, sess["initiated_by"], E.NotificationType.SESSION_CONFIRMED,
                         f"{me['name']}同意啦，开始看星星吧！",
                         "额度已经确定，看的时候才真正打开。")
            return session_repo.get(conn, session_id)

    def get_current_session(self, actor_id: str) -> dict:
        """本轮概览：我看过了几颗 / 我的额度 / 对方瓶里还有几颗可看。

        anniversary：额度 = 发起时冻结的 0 点快照（quota_cutoff_at 持久化，
        重启、跨午夜都不重算）；对方剩余 = 本轮 0 点口径下当前仍 SEALED 的候选。
        special_day：额度 = 确认那一刻瓶里数量（既有口径），对方剩余 = 当前 SEALED。
        """
        with self.db.transaction() as conn:
            me = self._require_user(conn, actor_id)
            sess = session_repo.get_unfinished(conn)
            if sess is None:
                return {"session": None}
            viewed = star_repo.count_session_viewed_by(conn, sess["id"], actor_id)
            quota = self._session_quota(conn, sess, actor_id)
            if sess["type"] == E.SessionType.ANNIVERSARY.value and sess["quota_cutoff_at"]:
                partner_remaining = star_repo.count_anniversary_candidates(
                    conn, me["partner_id"], sess["quota_cutoff_at"]
                )
            else:
                partner_remaining = star_repo.count_sealed(conn, me["partner_id"])
            return {
                "session": sess,
                "my_viewed": viewed,
                "my_quota": quota,
                "partner_remaining": partner_remaining,
                "can_view_more": sess["status"] == E.SessionStatus.ACTIVE.value
                and viewed < quota and partner_remaining > 0,
            }

    @staticmethod
    def _session_quota(conn, sess: dict, actor_id: str) -> int:
        """本轮固定额度：anniversary = 发起时冻结的 0 点快照；
        special_day = 确认那一刻自己瓶里的星数。确认后永不重算。"""
        if actor_id == sess["initiated_by"]:
            raw = sess["actor_a_count"]
        else:
            raw = sess["actor_b_count"]
        return int(raw or 0)

    def take_next_session_star(self, actor_id: str, session_id: str,
                               operation_id: str | None = None) -> dict:
        """看一颗对方瓶子里的星（配额互看）。

        * anniversary：额度 = 0 点快照；候选 = 0 点时在对方瓶里、现在仍是
          SEALED 的星——0 点后新写的星既不占额度也不进本轮候选；
          我的额度耗尽与对方候选耗尽返回不同的提示文案；
        * special_day：保留既有口径（确认时额度 + 当前 SEALED 候选）；
        * 看的瞬间这颗星直接打开进入公共池（opened_by 是看的人）；
        * 揭晓动作本身计 1 次 view 并初始化 first_view，返回值即完整详情，
          前端直接渲染、不再补发会计 view 的详情请求；
        * 用户规则：还有打开后没留话的星时不能继续看；
        * F10：网络丢失响应后重试同一操作 ID 返回同一颗星，不多看一颗。
        """
        operation_id = self._check_operation_id(operation_id)
        with self.db.transaction(immediate=True) as conn:
            self._require_user(conn, actor_id)
            prior = self._load_idempotent(
                conn, actor_id, operation_id, "sessions.take_next"
            )
            if prior is not self._IDEMPOTENCY_MISS:
                return prior
            sess = session_repo.get(conn, session_id)
            if sess is None:
                raise NotFoundError("拆星 session 不存在")
            if actor_id not in (sess["initiated_by"], sess["confirmed_by"] or ""):
                raise PermissionDeniedError("这场拆星不属于你")
            if sess["status"] != E.SessionStatus.ACTIVE.value:
                raise InvalidStateError("拆星 session 未激活或已经结束")

            me = self._require_user(conn, actor_id)
            unresponded = star_repo.find_unresponded_revealed(conn, actor_id)
            if unresponded is not None:
                raise InvalidStateError("先给上一颗打开的星星留句话，再继续看下一颗")

            quota = self._session_quota(conn, sess, actor_id)
            viewed = star_repo.count_session_viewed_by(conn, session_id, actor_id)

            is_anniversary = sess["type"] == E.SessionType.ANNIVERSARY.value
            if is_anniversary:
                cutoff = sess["quota_cutoff_at"]
                # 我的额度耗尽 与 对方本轮候选耗尽 是两种场景，文案必须区分
                if viewed >= quota:
                    raise BottleEmptyError(ANNIVERSARY_QUOTA_EXHAUSTED)
                star = star_repo.pick_anniversary_candidate(
                    conn, me["partner_id"], cutoff
                )
                if star is None:
                    raise BottleEmptyError(ANNIVERSARY_PARTNER_EMPTY)
            else:
                if viewed >= quota:
                    raise BottleEmptyError(SPECIAL_DAY_EXHAUSTED)
                star = star_repo.pick_random_sealed(conn, me["partner_id"])
                if star is None:
                    raise BottleEmptyError(SPECIAL_DAY_EXHAUSTED)

            now = now_iso()
            if is_anniversary:
                open_mode = E.OpenMode.ANNIVERSARY.value
                origin = E.SharedOrigin.REVEALED_BY_ANNIVERSARY.value
            else:
                open_mode = E.OpenMode.SPECIAL_DAY.value
                origin = E.SharedOrigin.REVEALED_BY_SPECIAL_DAY.value

            self._transition_star(conn, star["id"],
                                  E.StarState.SEALED, E.StarState.OPENED,
                                  opened_at=now, opened_by=actor_id)
            self._transition_star(conn, star["id"], E.StarState.OPENED, E.StarState.SHARED,
                                  open_mode=open_mode, shared_origin=origin,
                                  cycle_id=session_id, shared_at=now)  # §37：所属批次
            # 揭晓即查看：只计这 1 次 view，并初始化 first_view（若仍为空）
            self._record_view(conn, star["id"], actor_id, now)
            self._audit(conn, E.EventType.STAR_OPENED, actor_id, star["id"],
                        f"session={session_id} viewed={viewed + 1}/{quota}")
            self._audit(conn, E.EventType.STAR_MOVED_TO_SHARED, None, star["id"])
            result = self._assemble_shared_detail(conn, star_repo.get(conn, star["id"]))
            return self._save_idempotent(conn, actor_id, operation_id,
                                         "sessions.take_next", result)

    def finish_special_session(self, actor_id: str, session_id: str) -> dict:
        """结束本轮。新逻辑不预先锁定任何星，没看过的自然留在各自瓶子里。"""
        with self.db.transaction(immediate=True) as conn:
            self._require_user(conn, actor_id)
            sess = session_repo.get(conn, session_id)
            if sess is None:
                raise NotFoundError("拆星 session 不存在")
            if actor_id not in (sess["initiated_by"], sess["confirmed_by"] or ""):
                raise PermissionDeniedError("这场拆星不属于你")
            if sess["status"] != E.SessionStatus.ACTIVE.value:
                raise InvalidStateError("只有进行中的拆星才能结束")

            viewed_total = sum(
                star_repo.count_session_viewed_by(conn, session_id, uid)
                for uid in (sess["initiated_by"], sess["confirmed_by"])
            )
            session_repo.update_fields(
                conn, session_id,
                status=E.SessionStatus.COMPLETED.value,
                ended_at=now_iso(),
            )
            self._audit(conn, E.EventType.SPECIAL_SESSION_COMPLETED, actor_id, None,
                        f"session={session_id} viewed_total={viewed_total}")
            return session_repo.get(conn, session_id)

    # ------------------------------------------------------------------
    # F05：等待确认的邀请必须有出路——发起方可以撤回，被邀请方可以拒绝，
    # 不应要求“先同意才能结束”。两者都把 session 事务化地转到 cancelled，
    # 释放全局占位，不影响任何隐藏星（此时尚未快照锁定）。
    # ------------------------------------------------------------------
    def cancel_special_session(self, actor_id: str, session_id: str) -> dict:
        """发起方在对方确认前撤回邀请。"""
        with self.db.transaction(immediate=True) as conn:
            me = self._require_user(conn, actor_id)
            sess = session_repo.get(conn, session_id)
            if sess is None:
                raise NotFoundError("拆星 session 不存在")
            if sess["initiated_by"] != actor_id:
                raise PermissionDeniedError("只有发起方可以撤回这场邀请")
            if sess["status"] != E.SessionStatus.WAITING_CONFIRMATION.value:
                raise InvalidStateError("只有等待确认的拆星才能撤回")

            session_repo.update_fields(
                conn, session_id,
                status=E.SessionStatus.CANCELLED.value,
                ended_at=now_iso(),
            )
            self._audit(conn, E.EventType.SPECIAL_SESSION_CANCELLED, actor_id,
                        None, f"session={session_id}")
            self._notify(conn, me["partner_id"], E.NotificationType.SESSION_CANCELLED,
                         f"{me['name']}撤回了拆星邀请",
                         "这一轮没有开始，谁的瓶子都没有变化。")
            return session_repo.get(conn, session_id)

    def decline_special_session(self, actor_id: str, session_id: str) -> dict:
        """被邀请方在确认前拒绝邀请；拒绝不需要先同意。"""
        with self.db.transaction(immediate=True) as conn:
            me = self._require_user(conn, actor_id)
            sess = session_repo.get(conn, session_id)
            if sess is None:
                raise NotFoundError("拆星 session 不存在")
            if actor_id == sess["initiated_by"]:
                raise PermissionDeniedError("发起方想结束邀请请使用“撤回”")
            if me["partner_id"] != sess["initiated_by"]:
                raise PermissionDeniedError("这场拆星不属于你")
            if sess["status"] != E.SessionStatus.WAITING_CONFIRMATION.value:
                raise InvalidStateError("只有等待确认的拆星才能拒绝")

            session_repo.update_fields(
                conn, session_id,
                status=E.SessionStatus.CANCELLED.value,
                ended_at=now_iso(),
            )
            self._audit(conn, E.EventType.SPECIAL_SESSION_DECLINED, actor_id,
                        None, f"session={session_id}")
            self._notify(conn, sess["initiated_by"], E.NotificationType.SESSION_DECLINED,
                         f"{me['name']}这次先不想一起拆",
                         "星星都还在各自的瓶子里，想拆的时候再发起。")
            return session_repo.get(conn, session_id)

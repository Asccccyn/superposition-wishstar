"""全局配置：路径、业务常量、初始种子数据。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("SUPERPOSITION_DB_PATH", str(DATA_DIR / "superposition.db"))).expanduser()

# 写星长度约束（架构文档 §4.1：宽松上限，避免误写超长内容）
MAX_CONTENT_LENGTH = 2000
MAX_MOOD_LENGTH = 100
MAX_NOTE_LENGTH = 500              # 注释（为什么写）上限
MAX_OFFER_MESSAGE_LENGTH = 200     # 递星附言上限
MAX_RESPONSE_TEXT_LENGTH = 1000   # 文字回应上限（与前端 maxlength 一致）
MAX_NAME_LENGTH = 100             # 特殊日期等名称上限
MAX_OPERATION_ID_LENGTH = 128     # 幂等操作 ID 上限

# 版本可观测性（F20）：health 端点返回；build commit 由部署环境注入，不含任何秘密
APP_VERSION = "1.3.0"
BUILD_COMMIT = os.getenv("SUPERPOSITION_BUILD_COMMIT") or None

# 业务时区（第二轮修订）：“纪念日当天 00:00”以它为准，不随系统时区 / VPN /
# 服务器迁移漂移。库里历史时间串是无 offset 的本地时间，继续按此口径读写。
BUSINESS_TIMEZONE = os.getenv("SUPERPOSITION_BUSINESS_TIMEZONE", "Asia/Shanghai")


def load_or_create_mcp_oauth_secret() -> str:
    """远程 MCP OAuth（第一方授权服务）的签名密钥。

    用于 HS256 JWT 签发/校验与 client_id 确定性派生；持久化在数据库同目录的
    mcp_oauth_secret.txt。删除该文件并重启即撤销全部已发放令牌（连接器需重新授权）。
    """
    import secrets as _secrets

    secret_file = DB_PATH.parent / "mcp_oauth_secret.txt"
    if secret_file.exists():
        value = secret_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = _secrets.token_urlsafe(32)
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(value, encoding="utf-8")
    return value

# 双方完全同权（§2）：系统只有 actor / partner 之分，不区分人类 / AI
DEFAULT_USERS = [
    {"id": "human", "name": "人类", "partner_id": "companion"},
    {"id": "companion", "name": "AI 伴侣", "partner_id": "human"},
]

# 界面分面：网页登录只属于人类；AI 伴侣只能通过
# MCP（本机 stdio / 远程 OAuth 登录页）接入。产品权利仍然完全同权，这只是界面分工。
WEB_LOGIN_ACTOR = "human"

# 特殊日期只作为“数据”存在（§34：不硬编码进业务逻辑）
# 开源版不预置任何特殊日期：纪念日是使用者自己的数据（隐私）。
# 首次启动后通过页面 / API / add_special_date 工具添加即可（§34：是数据，不是代码）。
DEFAULT_SPECIAL_DATES: list[dict] = []

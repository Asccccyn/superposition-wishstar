"""FastAPI 应用工厂：装配 Database 与 StarService，注册异常映射。"""
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import APP_VERSION, BASE_DIR, DB_PATH
from .db.connection import Database
from .db.schema import DDL, MIGRATIONS
from .db.seed import seed_if_empty
from .domain.errors import DomainError
from .services.star_service import StarService


def create_app() -> FastAPI:
    expose_docs = os.getenv("SUPERPOSITION_ENABLE_DOCS", "").lower() in {"1", "true", "yes"}
    app = FastAPI(
        title="Superposition · 许愿星 / 星星瓶 API",
        version=APP_VERSION,
        description="人类 & AI 伴侣的许愿星系统后端（双方完全同权，权限全部在服务层强制）",
        docs_url="/docs" if expose_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if expose_docs else None,
    )

    db = Database(DB_PATH, migrations=MIGRATIONS)
    db.init(DDL, seed_if_empty)
    app.state.db = db
    app.state.service = StarService(db)

    app.include_router(router, prefix="/api")
    web_dir = BASE_DIR / "app" / "web"
    app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def root():
        return FileResponse(web_dir / "index.html")

    # 手机端 claude.ai 远程连接器：OAuth 2.1 授权服务 + Bearer 保护的
    # Streamable HTTP MCP（/mcp）。经既有 Cloudflare 隧道公网可达，CF 侧零改动。
    # 注意必须最后挂载："/" 兜底挂载会拦截其后注册的一切路由。
    # SUPERPOSITION_MCP_HTTP=0 可整体关闭（默认开启）。
    if os.getenv("SUPERPOSITION_MCP_HTTP", "1").lower() not in {"0", "false", "no"}:
        from contextlib import asynccontextmanager

        from .mcp_auth import ROUTER as MCP_OAUTH_ROUTER, BearerGate
        from mcp_server import streamable_http_starlette_app

        mcp_star = streamable_http_starlette_app("/mcp")
        app.include_router(MCP_OAUTH_ROUTER)
        # Starlette 的 Mount 不会执行子应用 lifespan，而 StreamableHTTP 的
        # session manager 必须由 lifespan 启动——这里在宿主 lifespan 里手动进入。
        _inner_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def _lifespan_with_remote_mcp(host_app):
            async with mcp_star.router.lifespan_context(mcp_star), \
                    _inner_lifespan(host_app):
                yield

        app.router.lifespan_context = _lifespan_with_remote_mcp
        app.mount("/", BearerGate(mcp_star))
        print("[wish-star] 远程 MCP 已启用: /mcp (OAuth 2.1，登录页见 /mcp/oauth/authorize)")

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        # F20：请求体校验失败时给出统一提示，不把原始输入（可能包含
        # 被误粘贴的凭证）回显到响应里，也不进入默认的详细错误结构。
        return JSONResponse(
            status_code=422,
            content={"code": "validation_error", "message": "请求参数不合法"},
        )

    return app


app = create_app()

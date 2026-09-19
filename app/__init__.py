"""Superposition · 许愿星 / 星星瓶系统后端。

分层（架构文档 §45）：
    api         —— 传输适配层（Web / 未来的 MCP），不含业务规则
    services    —— StarService：所有业务规则与权限检查的唯一入口
    repositories —— 数据访问层，只做数据搬运
    db          —— SQLite 连接 / 建表 / 种子数据
    domain      —— 纯领域层：枚举、异常、状态机，不依赖任何框架
"""

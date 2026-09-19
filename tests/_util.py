"""测试公共工具：把项目根目录加入 sys.path，并提供独立临时数据库。

每次调用都创建唯一临时目录（F04 相关修复）：并行的两套测试
不会再因为固定文件名互相删除对方的库。
"""
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def fresh_db(name: str):
    """每个步骤用独立临时库，互不影响、不污染正式数据。"""
    from app.db.connection import Database
    from app.db.schema import DDL, MIGRATIONS
    from app.db.seed import seed_if_empty

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix=f"superposition_test_{name}_"))
    db = Database(tmpdir / "test.db", migrations=MIGRATIONS)
    db.init(DDL, seed_if_empty)
    return db

"""Pytest shared configuration.

PostgreSQL-only test infrastructure:

- 所有数据库测试统一连服务器 PG 上的 invoice_test 库（详见 tests_pg.py）；
- 该库 schema 与生产一致（tests/schema_postgresql.sql 快照），零生产数据；
- invoice_test 角色对生产库无 CONNECT 权限，即使连接串配错也碰不到生产；
- 隔离模型保证「每个测试一个全新数据库状态」：
  * 类的 setUpClass 播种完成后拍一次库快照（含种子基线 + 类种子）；
  * 每个测试开始前把库恢复到该快照——类内测试看到同一份类种子，
    且每个测试之间互不污染（组合式 fixture 每测试重新播种也安全）；
  * 没有 setUpClass 的类 / 函数级测试：每个测试前恢复到种子基线。

注意：pytest 9 的 UnitTestCase 在 collect() 里就捕获 setUpClass 引用，
所以包裹必须发生在 pytest_collectstart（早于 UnitTestCase.collect），
不能用 pytest_collection_modifyitems（那时已注册完，包裹不生效）。
"""
import os
import secrets

os.environ.setdefault("ADMIN_EMAIL", "pytest-admin@example.invalid")
os.environ.setdefault("ADMIN_PASSWORD", "pytest-" + secrets.token_urlsafe(24))

import unittest  # noqa: E402

import tests_pg  # noqa: E402  (须在设置 ADMIN_* 后、任何测试模块 import app 之前)

tests_pg.activate()
tests_pg.ensure_schema()

_wrapped = set()


def pytest_collectstart(collector):
    """在 UnitTestCase.collect() 注册 setUpClass fixture 之前包一层快照。"""
    cls = getattr(collector, "obj", None)
    if not (isinstance(cls, type) and issubclass(cls, unittest.TestCase)):
        return
    key = (cls.__module__, cls.__name__)
    if key in _wrapped:
        return
    _wrapped.add(key)
    original_set_up_class = cls.setUpClass

    def wrapped_set_up_class(_cls=None, _orig=original_set_up_class):
        _orig()
        try:
            _cls._pg_snapshot = tests_pg.snapshot_all()
        except Exception:  # 快照失败不应掩盖 setUpClass 本身的异常
            _cls._pg_snapshot = None
            raise

    cls.setUpClass = classmethod(wrapped_set_up_class)


def pytest_runtest_setup(item):
    """在每个测试的 fixture / setUp 之前恢复库状态。

    - 类有快照（setUpClass 已播种）→ 种子基线 + 类种子增量；
    - 否则 → 恢复到 PostgreSQL 种子基线。
    """
    cls = getattr(item, "cls", None)
    snapshot = getattr(cls, "_pg_snapshot", None) if cls is not None else None
    tests_pg.restore_for_test(snapshot)

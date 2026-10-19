"""测试全局夹具。

为什么必须有这个文件：`test_agent_graph.py` 会真的执行 SQL，
依赖维表与事实表里有数据。之前的测试能过，是因为开发机上那个
`sales_insight.db` 早就被手工灌过——换个干净环境（CI、别人 clone 下来）
数据库是空的，5 个用例全挂。

这是个典型的**测试依赖本地脏状态**问题：本地绿、CI 红，而且红得莫名其妙。
修法不是给 CI 加数据，而是让测试自己准备数据。
"""
from __future__ import annotations

import pytest

from app.db.demo import ensure_demo_data
from app.db.seed import ensure_bootstrap
from app.db.session import init_db


@pytest.fixture(scope="session", autouse=True)
def _prepare_database():
    """建表 + 造管理员 + 灌演示数据，整个测试会话只做一次。

    三个函数都是幂等的，本地已有数据时不会重复写入。
    """
    init_db()
    ensure_bootstrap()
    ensure_demo_data()
    yield

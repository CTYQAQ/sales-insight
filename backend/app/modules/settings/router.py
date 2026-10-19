"""设置接口：全部仅管理员可用。

配置里含密钥与内网地址，泄露面等同于 .env 本身，所以连读取都卡 admin。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import CurrentUser, require_roles
from app.db.models.identity import Role
from app.modules.settings import service
from app.modules.settings.service import SettingsPatch

router = APIRouter(prefix="/api/settings", tags=["settings"])

AdminDep = Depends(require_roles(Role.ADMIN))


@router.get("")
def get_settings_snapshot(user: CurrentUser = AdminDep):
    return service.snapshot()


@router.patch("")
def patch_settings(patch: SettingsPatch, user: CurrentUser = AdminDep):
    return service.apply_patch(patch)


@router.post("/test-llm")
def test_llm(user: CurrentUser = AdminDep):
    return service.test_llm()


class NotifyTestReq(BaseModel):
    channel: str  # webhook | email


@router.post("/test-notify")
def test_notify(req: NotifyTestReq, user: CurrentUser = AdminDep):
    return service.test_notify(req.channel)


@router.post("/demo-reset")
def demo_reset(user: CurrentUser = AdminDep):
    return service.demo_reset()

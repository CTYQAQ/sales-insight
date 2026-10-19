from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from app.db.models.identity import Role


class LoginIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserOut"


class UserOut(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    role: Role
    region_scope: list[str]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    email: Optional[EmailStr] = None
    role: Role = Role.VIEWER
    region_scope: list[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    role: Optional[Role] = None
    region_scope: Optional[list[str]] = None
    is_active: Optional[bool] = None


class RegionOption(BaseModel):
    code: str
    name: str

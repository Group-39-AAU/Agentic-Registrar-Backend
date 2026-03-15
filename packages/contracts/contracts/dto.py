from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class UserRef(BaseModel):
    id: str
    display_name: str | None = None
    email: str | None = None


class ProgramRef(BaseModel):
    id: str
    code: str
    name: str


class CourseRef(BaseModel):
    id: str
    code: str
    title: str


class AuditInfo(BaseModel):
    created_at: datetime
    created_by: UserRef | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)
    updated_by: UserRef | None = Field(default=None)


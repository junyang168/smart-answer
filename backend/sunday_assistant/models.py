from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

ZONE = ZoneInfo("America/Chicago")
SERVICE_FIELDS = {"sermonSpeaker": "講員", "sermonTitle": "講道題目", "hymn": "詩歌",
                  "responseHymn": "回應詩歌", "scripture": "讀經經文"}


class Settings(BaseModel):
    enabled: bool = False
    availability_group: str = ""
    service_group: str = ""
    availability_group_name: str = "達拉斯聖道教會"
    service_group_name: str = "主日讲道联络群"
    auto_apply: bool = False
    reminders_enabled: bool = False
    friday_hour: int = Field(default=17, ge=0, le=23)
    friday_minute: int = Field(default=0, ge=0, le=59)
    # WhatsApp author IDs -> exact names in the existing worker list, including spouses.
    sender_workers: dict[str, list[str]] = Field(default_factory=dict)
    trusted_senders: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def groups(self):
        import re
        for group in (self.availability_group, self.service_group):
            if group and not re.fullmatch(r"[0-9]+(?:-[0-9]+)?@g\.us", group):
                raise ValueError("請選擇有效的 WhatsApp 群 ID")
        if self.availability_group and self.availability_group == self.service_group:
            raise ValueError("兩種資料需指定不同的群")
        if self.enabled and not (self.availability_group and self.service_group):
            raise ValueError("請先指定兩個群")
        return self


class ChatMessage(BaseModel):
    message_id: str = Field(min_length=1, max_length=240)
    group_id: str = Field(min_length=1, max_length=160)
    sender_id: str = Field(min_length=1, max_length=160)
    sent_at: datetime
    text: str = Field(default="", max_length=20000)
    quoted_message_id: str | None = Field(default=None, max_length=240)
    source_id: str | None = Field(default=None, max_length=240)
    unsupported_media: bool = False
    initial_sync: bool = False
    revoked: bool = False

    @model_validator(mode="after")
    def timezone_required(self):
        if self.sent_at.tzinfo is None:
            raise ValueError("消息時間必須包含時區")
        return self


class ServiceChange(BaseModel):
    date: date
    field: Literal["sermonSpeaker", "sermonTitle", "hymn", "responseHymn", "scripture"]
    value: str | list[str]
    evidence: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_value(self):
        if self.date.weekday() != 6:
            raise ValueError("崇拜日期必須是主日")
        if self.field == "scripture":
            if not isinstance(self.value, list) or not self.value or any(not x.strip() for x in self.value):
                raise ValueError("讀經需列出完整經文範圍")
        elif not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("欄位不可為空")
        return self


class AbsenceChange(BaseModel):
    worker: str
    start: date
    end: date
    operation: Literal["add", "remove"] = "add"
    evidence: str = Field(min_length=1)
    context_message_id: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("外出結束日不能早於開始日")
        return self


class Extraction(BaseModel):
    service_changes: list[ServiceChange] = Field(default_factory=list, max_length=60)
    absence_changes: list[AbsenceChange] = Field(default_factory=list, max_length=60)
    questions: list[str] = Field(default_factory=list)

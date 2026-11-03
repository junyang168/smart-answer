from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

ZONE = ZoneInfo("America/Chicago")
SERVICE_FIELDS = {"sermonSpeaker": "講員", "sermonTitle": "講道題目", "hymn": "詩歌",
                  "responseHymn": "回應詩歌", "hymnYoutubeUrl": "詩歌 YouTube", "responseHymnYoutubeUrl": "回應詩歌 YouTube", "scripture": "讀經經文", "donationAmount": "本週奉獻", "presider": "司會", "worshipLeader": "領詩", "pianist": "司琴", "hymnNotes": "詩歌演唱說明", "responseHymnNotes": "回應詩歌演唱說明"}
FRIDAY_FIELDS = {field: label for field, label in SERVICE_FIELDS.items() if field not in {"pianist", "donationAmount", "hymnNotes", "responseHymnNotes", "hymnYoutubeUrl", "responseHymnYoutubeUrl"}}


class Settings(BaseModel):
    enabled: bool = False
    availability_group: str = ""
    service_group: str = ""
    songs_group: str = ""
    response_hymn_group: str = ""
    offering_group: str = ""
    availability_group_name: str = "達拉斯聖道教會"
    service_group_name: str = "主日讲道联络群"
    songs_group_name: str = "主日诗歌服侍交流群"
    auto_apply: bool = False
    auto_generate_ppt: bool = True
    maintain_future_skeletons: bool = True
    collect_cross_group: bool = True
    offering_week_rule: Literal["current_preparation_sunday"] = "current_preparation_sunday"
    group_questions_enabled: bool = False
    reminders_enabled: bool = False
    admin_help_enabled: bool = False
    admin_group: str = ""
    admin_group_name: str = "網站管理員"
    friday_hour: int = Field(default=17, ge=0, le=23)
    friday_minute: int = Field(default=0, ge=0, le=59)
    # WhatsApp author IDs -> self. Legacy multiple names are household hints only.
    sender_workers: dict[str, list[str]] = Field(default_factory=dict)
    households: list[list[str]] = Field(default_factory=list, max_length=200)
    worker_aliases: dict[str, str] = Field(default_factory=dict)
    worship_rotation: list[str] = Field(default_factory=list)
    # Retained for old settings files; collection is authorized by selected group.
    trusted_senders: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def groups(self):
        import re
        for group in (self.availability_group, self.service_group, self.songs_group, self.admin_group):
            if group and not re.fullmatch(r"[0-9]+(?:-[0-9]+)?@g\.us", group):
                raise ValueError("請選擇有效的 WhatsApp 群 ID")
        groups = [g for g in (self.availability_group, self.service_group, self.songs_group) if g]
        if self.admin_help_enabled and not self.admin_group:
            raise ValueError("請先指定網站管理員求助群")
        if self.admin_group and self.admin_group in groups:
            raise ValueError("系統求助群需與三個資料來源群分開")
        if len(groups) != len(set(groups)):
            raise ValueError("各資料來源需指定不同的群")
        if any(g and g not in groups for g in (self.response_hymn_group, self.offering_group)):
            raise ValueError("回應詩歌及奉獻需選擇已指定的資料來源群")
        if self.enabled and not (self.availability_group and self.service_group):
            raise ValueError("請先指定兩個群")
        if any(not alias.strip() or alias != alias.strip() or not target.strip() for alias, target in self.worker_aliases.items()):
            raise ValueError("姓名別名及對應同工不可空白，別名首尾不可有空格")
        if self.worship_rotation and (len(self.worship_rotation) != 2 or len(set(self.worship_rotation)) != 2):
            raise ValueError("領詩輪值需列出兩位不同同工")
        members = []
        for pair in self.households:
            if len(pair) != 2 or not all(name.strip() for name in pair) or pair[0] == pair[1]:
                raise ValueError("夫妻對應需列出兩位不同同工")
            members.extend(pair)
        if len(set(members)) != len(members):
            raise ValueError("同一位同工不可同時出現在不同夫妻對應中")
        return self


    @property
    def source_group_ids(self):
        return {g for g in (self.availability_group, self.service_group, self.songs_group) if g}

    def field_destinations(self):
        # Preserve legacy two-group settings; selecting a songs group enables
        # explicit field ownership instead of letting every group edit everything.
        if not self.songs_group:
            result = {field: self.service_group for field in SERVICE_FIELDS}
        else:
            result = {field: self.service_group for field in ("sermonSpeaker", "sermonTitle", "scripture")}
            result["hymn"] = self.songs_group
            result["responseHymn"] = self.songs_group
            result["hymnYoutubeUrl"] = self.songs_group
            result["responseHymnYoutubeUrl"] = self.songs_group
            result["hymnNotes"] = self.songs_group
            result["responseHymnNotes"] = self.songs_group
            result["donationAmount"] = self.availability_group
            result["presider"] = self.availability_group
            result["worshipLeader"] = self.songs_group
            result["pianist"] = self.songs_group
        for field, group in (("responseHymn", self.response_hymn_group), ("donationAmount", self.offering_group)):
            if group:
                result[field] = group
                if field == "responseHymn":
                    result["responseHymnYoutubeUrl"] = group
        return {field: group for field, group in result.items() if group}

    def fields_for_group(self, group_id):
        if self.collect_cross_group and group_id in self.source_group_ids:
            return set(SERVICE_FIELDS)
        return {field for field, group in self.field_destinations().items() if group == group_id}


class ChatMessage(BaseModel):
    message_id: str = Field(min_length=1, max_length=240)
    group_id: str = Field(min_length=1, max_length=160)
    sender_id: str = Field(min_length=1, max_length=160)
    sent_at: datetime
    source_sent_at: datetime | None = None
    unverified_edit: bool = False
    text: str = Field(default="", max_length=20000)
    origin: Literal["whatsapp", "schedule"] = "whatsapp"
    schedule_date: date | None = None
    schedule_field: Literal["sermonSpeaker", "presider", "worshipLeader", "hymnNotes", "responseHymnNotes"] | None = None
    quoted_message_id: str | None = Field(default=None, max_length=240)
    source_id: str | None = Field(default=None, max_length=240)
    unsupported_media: bool = False
    initial_sync: bool = False
    revoked: bool = False

    @model_validator(mode="after")
    def timezone_required(self):
        if self.sent_at.tzinfo is None:
            raise ValueError("消息時間必須包含時區")
        if self.source_sent_at is not None and self.source_sent_at.tzinfo is None:
            raise ValueError("原消息時間必須包含時區")
        return self


class ServiceChange(BaseModel):
    date: date
    field: Literal["sermonSpeaker", "sermonTitle", "hymn", "responseHymn", "scripture", "donationAmount", "presider", "worshipLeader", "pianist", "hymnNotes", "responseHymnNotes", "hymnYoutubeUrl", "responseHymnYoutubeUrl"]
    value: str | list[str] | float | None
    evidence: str = Field(min_length=1)

    @field_validator("value", mode="before")
    @classmethod
    def reject_boolean_amount(cls, value):
        if isinstance(value, bool):
            raise ValueError("欄位值不可為布林值")
        return value

    @model_validator(mode="after")
    def valid_value(self):
        if self.date.weekday() != 6:
            raise ValueError("崇拜日期必須是主日")
        if self.field == "donationAmount":
            import math
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not math.isfinite(self.value) or self.value < 0:
                raise ValueError("奉獻金額需為明確的非負金額")
            if round(self.value, 2) != self.value:
                raise ValueError("奉獻金額最多保留兩位小數")
        elif self.field == "scripture":
            if not isinstance(self.value, list) or not self.value or any(not x.strip() for x in self.value):
                raise ValueError("讀經需列出完整經文範圍")
        elif self.field in {"hymnYoutubeUrl", "responseHymnYoutubeUrl"}:
            from backend.sunday_video import youtube_url
            self.value = youtube_url(self.value)
        elif self.field in {"hymnNotes", "responseHymnNotes"}:
            if self.value is not None and not isinstance(self.value, str):
                raise ValueError("演唱說明需為文字或空值")
            self.value = self.value.strip() or None if isinstance(self.value, str) else None
        elif self.field == "pianist" and self.value is None:
            pass  # Optional assignment may be explicitly cancelled.
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
    include_household: bool = False

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("外出結束日不能早於開始日")
        return self


class Extraction(BaseModel):
    clarification_id: int | None = Field(default=None, ge=1)
    cancel_clarification: bool = False
    service_changes: list[ServiceChange] = Field(default_factory=list, max_length=60)
    absence_changes: list[AbsenceChange] = Field(default_factory=list, max_length=60)
    questions: list[str] = Field(default_factory=list)

from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=512, repr=False)


class UserOut(BaseModel):
    id: uuid.UUID
    sam_account_name: str
    display_name: str
    is_admin: bool


class MeOut(BaseModel):
    user: UserOut
    csrf_token: str


class ActiveMeetingOut(BaseModel):
    id: uuid.UUID
    started_at: datetime
    participants: int


class RoomOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    max_participants: int
    has_password: bool
    transcription_enabled: bool
    record_audio: bool
    camera_allowed: bool
    screen_share_allowed: bool
    active_meeting: ActiveMeetingOut | None = None


class JoinIn(BaseModel):
    password: str | None = Field(default=None, max_length=256, repr=False)


class ClientConfig(BaseModel):
    screen_profile: str = "sharp"
    screen_share_audio: bool = False
    one_sharer_at_a_time: bool = False


class JoinOut(BaseModel):
    meeting_id: uuid.UUID
    room: RoomOut
    livekit_url: str
    livekit_room: str
    token: str = Field(repr=False)
    identity: str
    recording: bool = True
    client: ClientConfig = Field(default_factory=ClientConfig)


class ParticipantOut(BaseModel):
    user_id: uuid.UUID
    display_name: str
    joined_at: datetime
    left_at: datetime | None
    online: bool


class MeetingOut(BaseModel):
    id: uuid.UUID
    room_id: uuid.UUID
    room_name: str
    started_at: datetime
    ended_at: datetime | None
    end_reason: str | None
    transcription_enabled: bool
    participants: list[ParticipantOut]


class SegmentOut(BaseModel):
    id: int
    uid: uuid.UUID
    meeting_id: uuid.UUID
    user_id: uuid.UUID | None
    display_name: str
    identity: str
    started_at: datetime
    ended_at: datetime
    text: str
    language: str | None


class TranscriptOut(BaseModel):
    meeting_id: uuid.UUID
    segments: list[SegmentOut]
    has_more: bool


# ---- админка
class AclEntryIn(BaseModel):
    subject_type: str = Field(pattern="^(group|user)$")
    subject_ref: str = Field(min_length=1, max_length=512)
    display_name: str | None = Field(default=None, max_length=300)


class AclEntryOut(AclEntryIn):
    model_config = ConfigDict(from_attributes=True)


class RoomAdminOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    is_enabled: bool
    max_participants: int
    has_password: bool
    transcription_enabled: bool
    record_audio: bool
    camera_allowed: bool
    screen_share_allowed: bool
    text_retention_days: int | None
    audio_retention_days: int | None
    protocol_instructions: str | None
    acl: list[AclEntryOut]
    active_meeting_id: uuid.UUID | None = None


class RoomCreateIn(BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_enabled: bool = True
    max_participants: int = Field(default=20, ge=1, le=200)
    password: str | None = Field(default=None, max_length=256, repr=False)
    transcription_enabled: bool = True
    record_audio: bool = False
    camera_allowed: bool = True
    screen_share_allowed: bool = True
    text_retention_days: int | None = Field(default=None, ge=0, le=36500)
    audio_retention_days: int | None = Field(default=None, ge=0, le=36500)
    protocol_instructions: str | None = Field(default=None, max_length=20000)
    acl: list[AclEntryIn] = Field(default_factory=list)

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError("slug: a-z, 0-9 и '-', 2–63 символа, начинается с буквы/цифры")
        return v


class RoomPatchIn(BaseModel):
    """Частичное обновление. Пароль: строка — задать, пустая строка — снять, поле не передано — не менять."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    is_enabled: bool | None = None
    max_participants: int | None = Field(default=None, ge=1, le=200)
    password: str | None = Field(default=None, max_length=256, repr=False)
    transcription_enabled: bool | None = None
    record_audio: bool | None = None
    camera_allowed: bool | None = None
    screen_share_allowed: bool | None = None
    text_retention_days: int | None = Field(default=None, ge=0, le=36500)
    audio_retention_days: int | None = Field(default=None, ge=0, le=36500)
    protocol_instructions: str | None = Field(default=None, max_length=20000)
    acl: list[AclEntryIn] | None = None

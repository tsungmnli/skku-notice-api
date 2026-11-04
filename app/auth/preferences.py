"""Per-account settings that follow the user from device to device (the calendar filters).

One JSON document per user (`users.preferences`). Reading is lenient: a section that no longer
fits the schema is dropped instead of turning `/me` into a 500. Writing is strict.
"""

from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

# Category codes are the API's own (`academic`, `scholarship`, ...). The shape is checked, not
# the list: a code that was renamed or removed later must not make a saved document unreadable.
CategoryCode = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,32}$")]
MAX_HIDDEN_CATEGORIES = 20


class CalendarPrefs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # What the user switched OFF, not what is on: a category added later then shows up by default.
    hidden_categories: list[CategoryCode] = Field(
        default_factory=list, max_length=MAX_HIDDEN_CATEGORIES
    )
    favorites_only: bool = False

    @field_validator("hidden_categories")
    @classmethod
    def _unique_and_sorted(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


class Preferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # None = never saved. The web then keeps its own copy (or the defaults).
    calendar: CalendarPrefs | None = None


class PreferencesPatch(BaseModel):
    """Body of `PATCH /me/preferences`: each key that is present replaces that section, and
    `null` resets it. Keys that are absent are left alone."""

    model_config = ConfigDict(extra="ignore")

    calendar: CalendarPrefs | None = None


def load_preferences(raw: object) -> Preferences:
    """The stored document as `Preferences`; sections that cannot be read are dropped."""
    data = raw if isinstance(raw, dict) else {}
    calendar = None
    if data.get("calendar") is not None:
        try:
            calendar = CalendarPrefs.model_validate(data["calendar"])
        except ValidationError:
            calendar = None
    return Preferences(calendar=calendar)


def apply_patch(stored: object, patch: PreferencesPatch) -> dict[str, Any]:
    """The document to store after `patch`. Never edits `stored` in place."""
    data: dict[str, Any] = dict(stored) if isinstance(stored, dict) else {}
    for key in patch.model_fields_set:
        value = getattr(patch, key)
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value.model_dump(mode="json")
    return data

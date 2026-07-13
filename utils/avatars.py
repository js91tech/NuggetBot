from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

ASSETS_ROOT = Path(__file__).resolve().parent.parent / "assets" / "avatars"
CUSTOM_ASSETS_ROOT = Path(__file__).resolve().parent.parent / "assets" / "custom"
DEFAULT_AVATAR_ID = "nugget_raider"
CUSTOM_AVATAR_PREFIX = "custom_"
UNIQUE_DEFAULT_PREFIX = "raider_"
ALLOWED_IMAGE_EXTS = frozenset({".png", ".gif", ".jpg", ".jpeg", ".webp"})


@dataclass(frozen=True)
class AvatarDef:
    id: str
    name: str
    description: str
    price: float
    emoji: str = "🍘"


AVATARS: tuple[AvatarDef, ...] = (
    AvatarDef(
        "nugget_raider",
        "Nugget Raider",
        "Default raid mascot — unlocked for everyone.",
        0.0,
        "⚔️",
    ),
    AvatarDef(
        "duel_champion",
        "Duel Champion",
        "Flex after PvP wins.",
        2_500.0,
        "🥊",
    ),
    AvatarDef(
        "raid_medic",
        "Raid Medic",
        "For healers and field medics.",
        2_500.0,
        "💊",
    ),
    AvatarDef(
        "vault_mogul",
        "Vault Mogul",
        "Economy grinder aesthetic.",
        5_000.0,
        "💰",
    ),
    AvatarDef(
        "boss_slayer",
        "Boss Slayer",
        "Trophy hunter victory pose.",
        10_000.0,
        "🏆",
    ),
    AvatarDef(
        "season_gold",
        "Season Gold",
        "Ranked season exclusive — redeem via /season shop.",
        0.0,
        "🥇",
    ),
)

AVATAR_MAP: dict[str, AvatarDef] = {a.id: a for a in AVATARS}


def custom_avatar_id(user_id: int) -> str:
    return f"{CUSTOM_AVATAR_PREFIX}{user_id}"


def unique_default_avatar_id(user_id: int, guild_id: int) -> str:
    from utils.avatar_generate import unique_default_avatar_id as _uid

    return _uid(user_id, guild_id)


def is_custom_avatar_id(avatar_id: str) -> bool:
    return avatar_id.startswith(CUSTOM_AVATAR_PREFIX)


def is_unique_default_avatar_id(avatar_id: str) -> bool:
    return avatar_id.startswith(UNIQUE_DEFAULT_PREFIX)


def unique_default_avatar_dir(guild_id: int, user_id: int) -> Path:
    from utils.avatar_generate import unique_default_avatar_dir as _dir

    return _dir(guild_id, user_id)


def custom_avatar_dir(guild_id: int, user_id: int) -> Path:
    return CUSTOM_ASSETS_ROOT / str(guild_id) / str(user_id)


def get_avatar(avatar_id: str | None) -> AvatarDef | None:
    if not avatar_id:
        return AVATAR_MAP.get(DEFAULT_AVATAR_ID)
    if is_custom_avatar_id(avatar_id):
        return AvatarDef(avatar_id, "Custom Avatar", "Your uploaded victory art.", 0.0, "🎨")
    if is_unique_default_avatar_id(avatar_id):
        return AvatarDef(
            avatar_id,
            "Raid Mascot",
            "Your unique starter raider — generated just for you.",
            0.0,
            "⚔️",
        )
    return AVATAR_MAP.get(avatar_id.strip().lower())


def attachment_image_ext(attachment: discord.Attachment) -> str | None:
    """Infer file extension from MIME type or filename (Discord may omit content_type)."""
    content_type = (attachment.content_type or "").lower()
    if content_type.startswith("image/"):
        if "gif" in content_type:
            return ".gif"
        if "webp" in content_type:
            return ".webp"
        if "jpeg" in content_type or "jpg" in content_type:
            return ".jpg"
        return ".png"
    suffix = Path(attachment.filename or "").suffix.lower()
    if suffix == ".jpeg":
        return ".jpg"
    if suffix in ALLOWED_IMAGE_EXTS:
        return suffix
    return None


def is_valid_image_attachment(attachment: discord.Attachment) -> bool:
    return attachment_image_ext(attachment) is not None


def portrait_path(avatar_id: str, *, guild_id: int | None = None, user_id: int | None = None) -> Path:
    if is_unique_default_avatar_id(avatar_id) and guild_id is not None and user_id is not None:
        folder = unique_default_avatar_dir(guild_id, user_id)
        for name in ("portrait.png", "portrait.gif", "portrait.jpg", "portrait.webp"):
            p = folder / name
            if p.is_file():
                return p
    if is_custom_avatar_id(avatar_id) and guild_id is not None and user_id is not None:
        folder = custom_avatar_dir(guild_id, user_id)
        for name in ("portrait.png", "portrait.gif", "portrait.jpg", "portrait.webp"):
            path = folder / name
            if path.is_file():
                return path
    return ASSETS_ROOT / avatar_id / "portrait.png"


def victory_path(
    avatar_id: str,
    *,
    guild_id: int | None = None,
    user_id: int | None = None,
) -> Path:
    if is_unique_default_avatar_id(avatar_id) and guild_id is not None and user_id is not None:
        folder = unique_default_avatar_dir(guild_id, user_id)
        for name in ("victory.gif", "victory.png", "victory.jpg", "victory.webp"):
            p = folder / name
            if p.is_file():
                return p
    if is_custom_avatar_id(avatar_id) and guild_id is not None and user_id is not None:
        folder = custom_avatar_dir(guild_id, user_id)
        for name in ("victory.gif", "victory.png", "victory.jpg", "victory.webp"):
            path = folder / name
            if path.is_file():
                return path
    gif = ASSETS_ROOT / avatar_id / "victory.gif"
    if gif.is_file():
        return gif
    return ASSETS_ROOT / avatar_id / "victory.png"


def victory_attachment_name(avatar_id: str) -> str:
    path = victory_path(avatar_id)
    return f"victory_{avatar_id}{path.suffix}"


def normalize_catalog_avatar_id(avatar_id: str | None) -> str:
    """Map equipped id to a catalog folder name (lowercase)."""
    if not avatar_id:
        return DEFAULT_AVATAR_ID
    if is_custom_avatar_id(avatar_id):
        return avatar_id
    lowered = avatar_id.strip().lower()
    if lowered in AVATAR_MAP:
        return lowered
    return avatar_id.strip()


def resolve_equipped_avatar_id(stored: str | None) -> str:
    if not stored:
        return DEFAULT_AVATAR_ID
    if is_custom_avatar_id(stored) or is_unique_default_avatar_id(stored):
        return stored
    lowered = stored.strip().lower()
    if lowered in AVATAR_MAP:
        return lowered
    return DEFAULT_AVATAR_ID


def _custom_user_id_from_avatar(avatar_id: str, user_id: int | None) -> int | None:
    if not is_custom_avatar_id(avatar_id):
        return user_id
    try:
        return int(avatar_id.removeprefix(CUSTOM_AVATAR_PREFIX))
    except ValueError:
        return user_id


def _file_from_bytes(data: bytes, filename: str) -> discord.File:
    import discord

    buffer = io.BytesIO(data)
    buffer.seek(0)
    return discord.File(buffer, filename=filename)


def _read_asset_bytes(path: Path) -> tuple[bytes, str] | None:
    if not path.is_file():
        return None
    return path.read_bytes(), path.suffix


def build_victory_attachment(
    avatar_id: str | None,
    *,
    guild_id: int | None = None,
    user_id: int | None = None,
    custom_victory: tuple[bytes, str] | None = None,
) -> tuple[list[discord.File], str | None]:
    """Return Discord files and attachment:// filename for embed.set_image."""
    aid = resolve_equipped_avatar_id(avatar_id)
    uid = _custom_user_id_from_avatar(aid, user_id)
    if custom_victory is not None:
        data, ext = custom_victory
        filename = f"victory_{aid}{ext}"
        return [_file_from_bytes(data, filename)], filename
    blob = _read_asset_bytes(victory_path(aid, guild_id=guild_id, user_id=uid))
    if blob is None:
        return [], None
    data, ext = blob
    filename = f"victory_{aid}{ext}"
    return [_file_from_bytes(data, filename)], filename


def build_portrait_attachment(
    avatar_id: str | None,
    *,
    guild_id: int | None = None,
    user_id: int | None = None,
    custom_portrait: tuple[bytes, str] | None = None,
) -> tuple[list[discord.File], str | None]:
    aid = resolve_equipped_avatar_id(avatar_id)
    uid = _custom_user_id_from_avatar(aid, user_id)
    if custom_portrait is not None:
        data, ext = custom_portrait
        filename = f"portrait_{aid}{ext}"
        return [_file_from_bytes(data, filename)], filename
    blob = _read_asset_bytes(portrait_path(aid, guild_id=guild_id, user_id=uid))
    if blob is None:
        return [], None
    data, ext = blob
    filename = f"portrait_{aid}{ext}"
    return [_file_from_bytes(data, filename)], filename


async def load_custom_attachment_bytes(
    db: object,
    avatar_id: str | None,
    *,
    guild_id: int | None,
    user_id: int | None,
) -> tuple[tuple[bytes, str] | None, tuple[bytes, str] | None]:
    """Load portrait/victory bytes from DB when a custom avatar is equipped."""
    aid = resolve_equipped_avatar_id(avatar_id)
    if not is_custom_avatar_id(aid) or guild_id is None:
        return None, None
    uid = _custom_user_id_from_avatar(aid, user_id)
    if uid is None:
        return None, None
    assets = await db.get_custom_avatar_assets(guild_id, uid)
    if assets is None:
        return None, None
    portrait_data, victory_data, ext = assets
    return (portrait_data, ext), (victory_data, ext)


async def load_avatar_attachment_bytes(
    db: object,
    avatar_id: str | None,
    *,
    guild_id: int | None,
    user_id: int | None,
) -> tuple[tuple[bytes, str] | None, tuple[bytes, str] | None]:
    """Load portrait/victory bytes for custom (DB) or catalog (disk) avatars."""
    custom_portrait, custom_victory = await load_custom_attachment_bytes(
        db, avatar_id, guild_id=guild_id, user_id=user_id,
    )
    if custom_victory is not None or custom_portrait is not None:
        return custom_portrait, custom_victory
    aid = resolve_equipped_avatar_id(avatar_id)
    uid = _custom_user_id_from_avatar(aid, user_id)
    portrait_blob = _read_asset_bytes(portrait_path(aid, guild_id=guild_id, user_id=uid))
    victory_blob = _read_asset_bytes(victory_path(aid, guild_id=guild_id, user_id=uid))
    return portrait_blob, victory_blob


async def build_avatar_embed_files(
    db: object,
    avatar_id: str | None,
    *,
    guild_id: int,
    user_id: int,
) -> tuple[list[discord.File], str | None, str | None]:
    """Build Discord file attachments for equipped avatar victory + portrait."""
    portrait_blob, victory_blob = await load_avatar_attachment_bytes(
        db, avatar_id, guild_id=guild_id, user_id=user_id,
    )
    aid = resolve_equipped_avatar_id(avatar_id)
    files: list[discord.File] = []
    victory_name: str | None = None
    portrait_name: str | None = None
    if victory_blob is not None:
        data, ext = victory_blob
        victory_name = f"victory_{aid}{ext}"
        files.append(_file_from_bytes(data, victory_name))
    if portrait_blob is not None:
        data, ext = portrait_blob
        portrait_name = f"portrait_{aid}{ext}"
        files.append(_file_from_bytes(data, portrait_name))
    return files, victory_name, portrait_name

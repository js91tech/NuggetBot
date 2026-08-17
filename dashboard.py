from __future__ import annotations

import hashlib
import hmac
import html
import logging
import time
from typing import Any

from aiohttp import web
from discord.ext import commands

import config
from utils.boss_mechanics import dashboard_boss_variants
from utils.helpers import fmt_amount


class DashboardServer:
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self) -> None:
        if not config.DASHBOARD_ENABLED:
            logging.info("Dashboard server disabled")
            return

        app = web.Application()
        app.add_routes(
            [
                web.get("/", self.dashboard),
                web.get("/dashboard", self.dashboard),
                web.post("/login", self.login),
                web.post("/logout", self.logout),
                web.get("/health", self.health),
                web.get("/api/status", self.api_status),
                web.post("/api/guild/{guild_id}/channels", self.api_update_channels),
                web.get("/api/guild/{guild_id}/config", self.api_get_config),
                web.post("/api/guild/{guild_id}/config", self.api_update_config),
                web.post("/api/guild/{guild_id}/boss/summon", self.api_summon_boss),
                web.post(
                    "/api/guild/{guild_id}/attributes/reset-all",
                    self.api_reset_guild_attributes,
                ),
                web.get("/api/guild/{guild_id}/spy/{user_id}", self.api_spy_user),
                web.post(
                    "/api/guild/{guild_id}/spy/{user_id}/grant",
                    self.api_spy_grant,
                ),
                web.post(
                    "/api/guild/{guild_id}/spy/{user_id}/take",
                    self.api_spy_take,
                ),
                web.get("/api/item-catalog", self.api_item_catalog),
            ]
        )
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, config.DASHBOARD_HOST, config.DASHBOARD_PORT)
        await self._site.start()
        logging.info(
            "Dashboard server listening on %s:%s",
            config.DASHBOARD_HOST,
            config.DASHBOARD_PORT,
        )

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    async def health(self, request: web.Request) -> web.Response:
        del request
        return web.json_response({"ok": True, "bot_ready": self.bot.is_ready()})

    async def login(self, request: web.Request) -> web.Response:
        data = await request.post()
        token = str(data.get("token", ""))
        if not self._valid_token(token):
            return self._html_response(self._login_page("Invalid dashboard token."), status=401)

        response = web.HTTPFound("/dashboard")
        response.set_cookie(
            config.DASHBOARD_COOKIE_NAME,
            self._session_value(),
            httponly=True,
            secure=request.secure,
            samesite="Strict",
            max_age=60 * 60 * 12,
        )
        raise response

    async def logout(self, request: web.Request) -> web.Response:
        del request
        response = web.HTTPFound("/")
        response.del_cookie(config.DASHBOARD_COOKIE_NAME)
        raise response

    async def dashboard(self, request: web.Request) -> web.Response:
        if not config.DASHBOARD_TOKEN:
            return self._html_response(self._disabled_page(), status=503)

        query_token = request.query.get("token")
        if query_token is not None and self._valid_token(query_token):
            response = web.HTTPFound("/dashboard")
            response.set_cookie(
                config.DASHBOARD_COOKIE_NAME,
                self._session_value(),
                httponly=True,
                secure=request.secure,
                samesite="Strict",
                max_age=60 * 60 * 12,
            )
            raise response

        if not self._authorized(request):
            return self._html_response(self._login_page())

        snapshots = await self._snapshots()
        return self._html_response(self._dashboard_page(snapshots))

    async def api_status(self, request: web.Request) -> web.Response:
        if not config.DASHBOARD_TOKEN:
            return web.json_response({"error": "dashboard token is not configured"}, status=503)
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"guilds": await self._snapshots()})

    async def api_update_channels(self, request: web.Request) -> web.Response:
        if not config.DASHBOARD_TOKEN:
            return web.json_response({"error": "dashboard token is not configured"}, status=503)
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        try:
            guild_id = int(request.match_info["guild_id"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "invalid guild id"}, status=400)

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json body"}, status=400)

        if not isinstance(payload, dict):
            return web.json_response({"error": "body must be a json object"}, status=400)

        text_channel_ids = {
            channel.id for channel in guild.text_channels if channel.permissions_for(guild.me).send_messages
        }

        if "main_channel_id" in payload:
            raw = payload["main_channel_id"]
            if raw is None or raw == "":
                await self.bot.db.set_main_channel_id(guild_id, None)
            else:
                try:
                    channel_id = int(raw)
                except (TypeError, ValueError):
                    return web.json_response({"error": "main_channel_id must be an integer"}, status=400)
                if channel_id not in text_channel_ids:
                    return web.json_response({"error": "main channel not writable"}, status=400)
                await self.bot.db.set_main_channel_id(guild_id, channel_id)

        if "designated_channel_id" in payload:
            raw = payload["designated_channel_id"]
            if raw is None or raw == "":
                await self.bot.db.set_designated_channel_id(guild_id, None)
            else:
                try:
                    channel_id = int(raw)
                except (TypeError, ValueError):
                    return web.json_response(
                        {"error": "designated_channel_id must be an integer"},
                        status=400,
                    )
                if channel_id not in text_channel_ids:
                    return web.json_response({"error": "designated channel not writable"}, status=400)
                await self.bot.db.set_designated_channel_id(guild_id, channel_id)

        if "split_announcement_channels" in payload:
            await self.bot.db.set_split_announcement_channels(
                guild_id,
                bool(payload["split_announcement_channels"]),
            )

        settings = await self.bot.db.get_guild_channel_settings(guild_id)
        return web.json_response({"ok": True, "channels": self._channel_snapshot(guild, settings)})

    async def api_get_config(self, request: web.Request) -> web.Response:
        if not config.DASHBOARD_TOKEN:
            return web.json_response({"error": "dashboard token is not configured"}, status=503)
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            guild_id = int(request.match_info["guild_id"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "invalid guild id"}, status=400)
        if self.bot.get_guild(guild_id) is None:
            return web.json_response({"error": "guild not found"}, status=404)
        return web.json_response(
            {
                "economy_settings": await self._economy_settings_payload(guild_id),
                "duel_settings": await self._duel_settings_payload(guild_id),
            }
        )

    async def api_summon_boss(self, request: web.Request) -> web.Response:
        if not config.DASHBOARD_TOKEN:
            return web.json_response({"error": "dashboard token is not configured"}, status=503)
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            guild_id = int(request.match_info["guild_id"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "invalid guild id"}, status=400)

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json body"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "body must be a json object"}, status=400)

        variant = str(payload.get("variant", "normal")).lower().strip()
        if variant not in config.BOSS_VARIANTS:
            return web.json_response(
                {"error": f"variant must be one of: {', '.join(config.BOSS_VARIANTS)}"},
                status=400,
            )

        boss_cog = self.bot.get_cog("Boss")
        if boss_cog is None:
            return web.json_response({"error": "boss cog not loaded"}, status=503)

        result = await boss_cog.dashboard_spawn_boss(guild, variant)
        if result is None:
            return web.json_response({"error": "spawn failed"}, status=400)

        hp, spawned_variant = result
        boss = await self.bot.db.get_active_boss(guild_id)
        return web.json_response(
            {
                "ok": True,
                "boss": self._boss_snapshot(boss),
                "variant": spawned_variant,
                "hp": hp,
            }
        )

    async def api_reset_guild_attributes(self, request: web.Request) -> web.Response:
        if not config.DASHBOARD_TOKEN:
            return web.json_response({"error": "dashboard token is not configured"}, status=503)
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            guild_id = int(request.match_info["guild_id"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "invalid guild id"}, status=400)

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return web.json_response({"error": "guild not found"}, status=404)

        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            return web.json_response({"error": "body must be a json object"}, status=400)
        if not payload.get("confirm"):
            return web.json_response(
                {"error": "confirmation required — set confirm: true"},
                status=400,
            )

        updated = await self.bot.db.reset_guild_character_attributes(guild_id)
        return web.json_response({"ok": True, "characters_reset": updated})

    async def api_item_catalog(self, request: web.Request) -> web.Response:
        if not config.DASHBOARD_TOKEN:
            return web.json_response({"error": "dashboard token is not configured"}, status=503)
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        from items import ITEM_ORDER, ITEMS

        catalog = [
            {
                "item_id": item_id,
                "name": ITEMS[item_id].name,
                "category": ITEMS[item_id].category,
            }
            for item_id in ITEM_ORDER
            if item_id in ITEMS
        ]
        return web.json_response({"items": catalog})

    def _parse_guild_user(self, request: web.Request) -> tuple[int, int] | web.Response:
        try:
            guild_id = int(request.match_info["guild_id"])
            user_id = int(request.match_info["user_id"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "invalid guild or user id"}, status=400)
        if self.bot.get_guild(guild_id) is None:
            return web.json_response({"error": "guild not found"}, status=404)
        return guild_id, user_id

    async def api_spy_user(self, request: web.Request) -> web.Response:
        if not config.DASHBOARD_TOKEN:
            return web.json_response({"error": "dashboard token is not configured"}, status=503)
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        parsed = self._parse_guild_user(request)
        if isinstance(parsed, web.Response):
            return parsed
        guild_id, user_id = parsed
        from items import get_item

        await self.bot.db.ensure_user(user_id, guild_id)
        user = await self.bot.db.get_user(user_id, guild_id)
        inventory_rows = await self.bot.db.get_inventory(user_id, guild_id)
        equipment = await self.bot.db.get_equipment(user_id, guild_id)
        drugs = await self.bot.db.get_drug_inventory(user_id, guild_id)
        equipped_items = set(equipment.values())
        inventory = []
        for row in inventory_rows:
            item_id = str(row["item_id"])
            item = get_item(item_id)
            inventory.append(
                {
                    "item_id": item_id,
                    "name": item.name if item is not None else item_id,
                    "quantity": int(row["quantity"]),
                    "equipped": item_id in equipped_items,
                }
            )
        logging.info("dashboard spy view guild=%s user=%s", guild_id, user_id)
        return web.json_response(
            {
                "ok": True,
                "user_id": user_id,
                "guild_id": guild_id,
                "wallet": float(user["wallet"]),
                "bank": float(user["bank"]),
                "equipment": equipment,
                "inventory": inventory,
                "drugs": drugs,
            }
        )

    async def api_spy_grant(self, request: web.Request) -> web.Response:
        if not config.DASHBOARD_TOKEN:
            return web.json_response({"error": "dashboard token is not configured"}, status=503)
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        parsed = self._parse_guild_user(request)
        if isinstance(parsed, web.Response):
            return parsed
        guild_id, user_id = parsed
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json body"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "body must be a json object"}, status=400)
        from items import get_item

        item_id = str(payload.get("item_id", "")).strip()
        if get_item(item_id) is None:
            return web.json_response({"error": "unknown item_id"}, status=400)
        try:
            quantity = int(payload.get("quantity", 1))
        except (TypeError, ValueError):
            return web.json_response({"error": "quantity must be an integer"}, status=400)
        if quantity < 1:
            return web.json_response({"error": "quantity must be at least 1"}, status=400)
        granted = await self.bot.db.grant_inventory_quantity(
            user_id, guild_id, item_id, quantity,
        )
        logging.info(
            "dashboard spy grant guild=%s user=%s item=%s qty=%s",
            guild_id, user_id, item_id, granted,
        )
        return web.json_response(
            {"ok": True, "item_id": item_id, "granted": granted},
        )

    async def api_spy_take(self, request: web.Request) -> web.Response:
        if not config.DASHBOARD_TOKEN:
            return web.json_response({"error": "dashboard token is not configured"}, status=503)
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        parsed = self._parse_guild_user(request)
        if isinstance(parsed, web.Response):
            return parsed
        guild_id, user_id = parsed
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json body"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "body must be a json object"}, status=400)
        from items import get_item

        item_id = str(payload.get("item_id", "")).strip()
        if get_item(item_id) is None:
            return web.json_response({"error": "unknown item_id"}, status=400)
        try:
            quantity = int(payload.get("quantity", 1))
        except (TypeError, ValueError):
            return web.json_response({"error": "quantity must be an integer"}, status=400)
        if quantity < 1:
            return web.json_response({"error": "quantity must be at least 1"}, status=400)
        removed = await self.bot.db.remove_inventory_quantity(
            user_id, guild_id, item_id, quantity,
        )
        logging.info(
            "dashboard spy take guild=%s user=%s item=%s qty=%s",
            guild_id, user_id, item_id, removed,
        )
        return web.json_response(
            {"ok": True, "item_id": item_id, "removed": removed},
        )

    async def api_update_config(self, request: web.Request) -> web.Response:
        if not config.DASHBOARD_TOKEN:
            return web.json_response({"error": "dashboard token is not configured"}, status=503)
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            guild_id = int(request.match_info["guild_id"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "invalid guild id"}, status=400)
        if self.bot.get_guild(guild_id) is None:
            return web.json_response({"error": "guild not found"}, status=404)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json body"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"error": "body must be a json object"}, status=400)

        settings_payload = payload.get("settings")
        if not isinstance(settings_payload, dict):
            return web.json_response({"error": "settings object required"}, status=400)

        updated: dict[str, float] = {}
        for key, raw in settings_payload.items():
            if key not in config.DASHBOARD_SLIDER_SETTINGS:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return web.json_response({"error": f"invalid value for {key}"}, status=400)
            try:
                updated[key] = await self.bot.db.set_config_value(guild_id, key, value)
            except ValueError as exc:
                return web.json_response({"error": f"{key}: {exc}"}, status=400)

        return web.json_response(
            {
                "ok": True,
                "updated": updated,
                "economy_settings": await self._economy_settings_payload(guild_id),
                "duel_settings": await self._duel_settings_payload(guild_id),
            }
        )

    async def _settings_payload(
        self, guild_id: int, keys: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        values = await self.bot.db.get_config_values(guild_id)
        custom = await self.bot.db.custom_config_names(guild_id)
        rows: list[dict[str, Any]] = []
        for key in keys:
            spec = config.LIVE_SETTINGS[key]
            maximum = spec.maximum
            if maximum is None:
                maximum = 1_000_000.0 if key == "prestige_min_wallet" else 100.0
            rows.append(
                {
                    "key": key,
                    "label": spec.description,
                    "value": float(values[key]),
                    "default": float(spec.default),
                    "minimum": float(spec.minimum),
                    "maximum": float(maximum),
                    "is_custom": key in custom,
                }
            )
        return rows

    async def _economy_settings_payload(self, guild_id: int) -> list[dict[str, Any]]:
        return await self._settings_payload(guild_id, config.ECONOMY_TUNING_SETTINGS)

    async def _duel_settings_payload(self, guild_id: int) -> list[dict[str, Any]]:
        return await self._settings_payload(guild_id, config.DUEL_TUNING_SETTINGS)

    def _authorized(self, request: web.Request) -> bool:
        header = request.headers.get("X-Dashboard-Token", "")
        if self._valid_token(header):
            return True
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer ") and self._valid_token(auth_header.removeprefix("Bearer ")):
            return True
        cookie = request.cookies.get(config.DASHBOARD_COOKIE_NAME, "")
        return hmac.compare_digest(cookie, self._session_value())

    @staticmethod
    def _valid_token(token: str) -> bool:
        return bool(config.DASHBOARD_TOKEN) and hmac.compare_digest(token, config.DASHBOARD_TOKEN)

    @staticmethod
    def _session_value() -> str:
        secret = config.DASHBOARD_TOKEN.strip()
        return hashlib.sha256(f"goonbot-dashboard:{secret}".encode()).hexdigest()

    async def _snapshots(self) -> list[dict[str, Any]]:
        snapshots = []
        for guild in self.bot.guilds:
            stats = await self.bot.db.economy_stats(guild.id)
            bounties = await self.bot.db.count_bounties(guild.id)
            boss = await self.bot.db.get_active_boss(guild.id)
            virus = await self.bot.db.get_hacker_pot(guild.id)
            custom_settings = await self.bot.db.custom_config_names(guild.id)
            leaderboard = await self.bot.db.leaderboard(guild.id, limit=5)
            raid_rows = await self.bot.db.list_boss_damage(guild.id) if boss is not None else []
            gear_rows = await self.bot.db.gear_distribution(guild.id, limit=6)
            event = await self.bot.db.get_active_guild_event(guild.id)
            channel_settings = await self.bot.db.get_guild_channel_settings(guild.id)
            hall = await self.bot.db.hall_of_fame_snapshot(guild.id, limit=5)
            economy_settings = await self._economy_settings_payload(guild.id)
            duel_settings = await self._duel_settings_payload(guild.id)
            snapshots.append(
                {
                    "id": guild.id,
                    "name": guild.name,
                    "members": guild.member_count or len(guild.members),
                    "tracked_users": int(stats["users"]),
                    "total_wallet": float(stats["total_wallet"]),
                    "total_earned": float(stats["total_earned"]),
                    "messages_sent": int(stats["messages_sent"]),
                    "bounties": bounties,
                    "boss": self._boss_snapshot(boss),
                    "virus": self._virus_snapshot(virus),
                    "custom_settings": sorted(custom_settings),
                    "channels": self._channel_snapshot(guild, channel_settings),
                    "text_channels": self._text_channel_options(guild),
                    "leaderboard": [
                        {
                            "name": self._member_name(guild, int(row["user_id"])),
                            "wallet": float(row["net"]),
                        }
                        for row in leaderboard
                    ],
                    "raid_leaderboard": [
                        {
                            "name": self._member_name(guild, int(row["user_id"])),
                            "damage": float(row["damage"]),
                        }
                        for row in raid_rows[:5]
                    ],
                    "gear_distribution": [
                        {
                            "item_id": item_id,
                            "count": count,
                        }
                        for item_id, count in gear_rows
                    ],
                    "seasonal_event": (
                        {
                            "type": str(event["event_type"]),
                            "multiplier": float(event["multiplier"]),
                            "seconds_left": int(max(0, float(event["ends_at"]) - time.time())),
                        }
                        if event is not None
                        else None
                    ),
                    "economy_settings": economy_settings,
                    "duel_settings": duel_settings,
                    "hall_of_fame": self._hall_of_fame_snapshot(guild, hall),
                }
            )
        return snapshots

    def _hall_of_fame_snapshot(self, guild: Any, hall: dict[str, list]) -> dict[str, list[dict[str, Any]]]:
        def rows_for(key: str, *, value_key: str) -> list[dict[str, Any]]:
            return [
                {
                    "name": self._member_name(guild, int(row["user_id"])),
                    "value": float(row[value_key]),
                }
                for row in hall.get(key, [])
            ]

        return {
            "richest": rows_for("richest", value_key="net"),
            "boss_kills": rows_for("boss_kills", value_key="score"),
            "heals": rows_for("heals", value_key="score"),
            "achievements": rows_for("achievements", value_key="score"),
        }

    @staticmethod
    def _text_channel_options(guild: Any) -> list[dict[str, str]]:
        options = []
        for channel in sorted(guild.text_channels, key=lambda ch: ch.position):
            if not channel.permissions_for(guild.me).send_messages:
                continue
            label = f"#{channel.name}"
            if channel.category is not None:
                label = f"{channel.category.name} / {label}"
            options.append({"id": str(channel.id), "label": label})
        return options

    @staticmethod
    def _channel_snapshot(guild: Any, settings: dict[str, int | bool | None]) -> dict[str, Any]:
        main_id = settings.get("main_channel_id")
        designated_id = settings.get("designated_channel_id")
        return {
            "main_channel_id": main_id,
            "designated_channel_id": designated_id,
            "split_announcement_channels": bool(settings.get("split_announcement_channels")),
            "main_channel_label": DashboardServer._channel_label(guild, main_id),
            "designated_channel_label": DashboardServer._channel_label(guild, designated_id),
        }

    @staticmethod
    def _channel_label(guild: Any, channel_id: int | None) -> str:
        if channel_id is None:
            return "Not set (fallback)"
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            return f"#{channel_id}"
        return f"#{channel.name}"

    @staticmethod
    def _boss_snapshot(boss: Any) -> dict[str, Any] | None:
        if boss is None:
            return None
        return {
            "name": str(boss["name"]),
            "variant": str(boss["variant"]),
            "hp": float(boss["hp"]),
            "max_hp": float(boss["max_hp"]),
        }

    @staticmethod
    def _virus_snapshot(virus: Any) -> dict[str, Any] | None:
        if virus is None:
            return None
        return {
            "holder_id": int(virus["holder_id"]),
            "pass_count": int(virus["pass_count"]),
            "seconds_left": int(max(0, float(virus["expires_at"]) - time.time())),
        }

    @staticmethod
    def _member_name(guild: Any, user_id: int) -> str:
        member = guild.get_member(user_id)
        return member.display_name if member is not None else f"User {user_id}"

    @staticmethod
    def _html_response(markup: str, *, status: int = 200) -> web.Response:
        return web.Response(text=markup, status=status, content_type="text/html")

    def _login_page(self, error: str = "") -> str:
        error_markup = f"<p class='error'>{html.escape(error)}</p>" if error else ""
        return self._page_shell(
            "GoonBot Dashboard Login",
            f"""
            <main class="login-card">
              <div class="badge">GoonBot</div>
              <h1>Dashboard Login</h1>
              <p>Enter your <code>DASHBOARD_TOKEN</code> to view server stats.</p>
              {error_markup}
              <form method="post" action="/login">
                <input name="token" type="password" autocomplete="current-password" placeholder="Dashboard token" required>
                <button type="submit">Open Dashboard</button>
              </form>
            </main>
            """,
        )

    def _disabled_page(self) -> str:
        return self._page_shell(
            "GoonBot Dashboard Disabled",
            """
            <main class="login-card">
              <div class="badge">Setup needed</div>
              <h1>Dashboard is protected</h1>
              <p>Set <code>DASHBOARD_TOKEN</code> in Railway, redeploy, then open this page again.</p>
              <p>The health endpoint at <code>/health</code> stays available without exposing bot data.</p>
            </main>
            """,
        )

    def _dashboard_page(self, snapshots: list[dict[str, Any]]) -> str:
        total_wallet = sum(item["total_wallet"] for item in snapshots)
        total_users = sum(item["tracked_users"] for item in snapshots)
        active_bosses = sum(1 for item in snapshots if item["boss"] is not None)
        active_viruses = sum(1 for item in snapshots if item["virus"] is not None)
        cards = "\n".join(self._guild_card(item) for item in snapshots) or self._empty_state()
        return self._page_shell(
            "GoonBot Dashboard",
            f"""
            <header class="hero">
              <div>
                <div class="badge">Live Dashboard</div>
                <h1>GoonBot Control Room</h1>
                <p>Railway-friendly status dashboard served by the bot itself.</p>
              </div>
              <form method="post" action="/logout"><button class="ghost" type="submit">Logout</button></form>
            </header>
            <section class="summary-grid">
              {self._metric("Servers", len(snapshots))}
              {self._metric("Tracked users", total_users)}
              {self._metric("Total wallets", fmt_amount(total_wallet))}
              {self._metric("Active bosses", active_bosses)}
              {self._metric("Active viruses", active_viruses)}
            </section>
            <section class="guild-grid">{cards}</section>
            <script>
            document.querySelectorAll(".channel-form").forEach((form) => {{
              form.addEventListener("submit", async (event) => {{
                event.preventDefault();
                const status = form.querySelector(".channel-status");
                const guildId = form.dataset.guildId;
                const main = form.elements.main_channel_id.value;
                const designated = form.elements.designated_channel_id.value;
                const split = form.elements.split_announcement_channels.checked;
                status.textContent = "Saving...";
                status.className = "channel-status";
                try {{
                  const response = await fetch(`/api/guild/${{guildId}}/channels`, {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{
                      main_channel_id: main || null,
                      designated_channel_id: designated || null,
                      split_announcement_channels: split,
                    }}),
                  }});
                  const data = await response.json();
                  if (!response.ok) {{
                    status.textContent = data.error || "Save failed";
                    status.className = "channel-status error";
                    return;
                  }}
                  status.textContent = "Saved.";
                  status.className = "channel-status ok";
                }} catch (err) {{
                  status.textContent = "Network error";
                  status.className = "channel-status error";
                }}
              }});
            }});
            document.querySelectorAll(".boss-summon-form").forEach((form) => {{
              form.addEventListener("submit", async (event) => {{
                event.preventDefault();
                const status = form.querySelector(".boss-summon-status");
                const guildId = form.dataset.guildId;
                const variant = form.elements.variant.value;
                status.textContent = "Summoning...";
                status.className = "boss-summon-status";
                try {{
                  const response = await fetch(`/api/guild/${{guildId}}/boss/summon`, {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{ variant }}),
                  }});
                  const data = await response.json();
                  if (!response.ok) {{
                    status.textContent = data.error || "Summon failed";
                    status.className = "boss-summon-status error";
                    return;
                  }}
                  const b = data.boss;
                  status.textContent = b
                    ? `Spawned ${{b.variant}} ${{b.name}} (${{b.hp}} / ${{b.max_hp}} HP).`
                    : "Boss spawned.";
                  status.className = "boss-summon-status ok";
                }} catch (err) {{
                  status.textContent = "Network error";
                  status.className = "boss-summon-status error";
                }}
              }});
            }});
            document.querySelectorAll(".attributes-reset-form").forEach((form) => {{
              form.addEventListener("submit", async (event) => {{
                event.preventDefault();
                const status = form.querySelector(".attributes-reset-status");
                const guildId = form.dataset.guildId;
                const confirmed = form.elements.confirm.checked;
                if (!confirmed) {{
                  status.textContent = "Check the confirmation box first.";
                  status.className = "attributes-reset-status error";
                  return;
                }}
                status.textContent = "Resetting...";
                status.className = "attributes-reset-status";
                try {{
                  const response = await fetch(`/api/guild/${{guildId}}/attributes/reset-all`, {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{ confirm: true }}),
                  }});
                  const data = await response.json();
                  if (!response.ok) {{
                    status.textContent = data.error || "Reset failed";
                    status.className = "attributes-reset-status error";
                    return;
                  }}
                  status.textContent = `Reset ${{data.characters_reset}} character(s) to 0 in all stats.`;
                  status.className = "attributes-reset-status ok";
                }} catch (err) {{
                  status.textContent = "Network error";
                  status.className = "attributes-reset-status error";
                }}
              }});
            }});
            document.querySelectorAll(".tuning-form").forEach((form) => {{
              form.addEventListener("submit", async (event) => {{
                event.preventDefault();
                const status = form.querySelector(".tuning-status");
                const guildId = form.dataset.guildId;
                const settings = {{}};
                form.querySelectorAll("[data-setting-key]").forEach((input) => {{
                  settings[input.dataset.settingKey] = parseFloat(input.value);
                }});
                status.textContent = "Saving...";
                status.className = "tuning-status";
                try {{
                  const response = await fetch(`/api/guild/${{guildId}}/config`, {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{ settings }}),
                  }});
                  const data = await response.json();
                  if (!response.ok) {{
                    status.textContent = data.error || "Save failed";
                    status.className = "tuning-status error";
                    return;
                  }}
                  status.textContent = "Saved.";
                  status.className = "tuning-status ok";
                }} catch (err) {{
                  status.textContent = "Network error";
                  status.className = "tuning-status error";
                }}
              }});
            }});
            (async () => {{
              try {{
                const response = await fetch("/api/item-catalog");
                if (!response.ok) return;
                const data = await response.json();
                let list = document.getElementById("item-catalog");
                if (!list) {{
                  list = document.createElement("datalist");
                  list.id = "item-catalog";
                  document.body.appendChild(list);
                }}
                list.innerHTML = (data.items || []).map((item) =>
                  `<option value="${{item.item_id}}">${{item.name}}</option>`
                ).join("");
              }} catch (err) {{}}
            }})();
            document.querySelectorAll(".spy-panel").forEach((panel) => {{
              const guildId = panel.dataset.guildId;
              const status = panel.querySelector(".spy-status");
              const summary = panel.querySelector(".spy-summary");
              const inventory = panel.querySelector(".spy-inventory");
              const userInput = panel.querySelector(".spy-user-id");
              const itemInput = panel.querySelector(".spy-item-id");
              const qtyInput = panel.querySelector(".spy-qty");
              const setStatus = (text, cls) => {{
                status.textContent = text;
                status.className = "spy-status " + (cls || "");
              }};
              const renderSpy = (data) => {{
                summary.innerHTML =
                  `<p><strong>Wallet:</strong> ${{data.wallet}} · <strong>Bank:</strong> ${{data.bank}}</p>` +
                  `<p><strong>Equipment:</strong> ${{
                    Object.entries(data.equipment || {{}}).map(([slot, id]) => `${{slot}}=${{id}}`).join(", ") || "none"
                  }}</p>`;
                const rows = (data.inventory || []).map((row) =>
                  `<li><span>${{row.name}} (${{row.item_id}})${{row.equipped ? " · equipped" : ""}}</span><strong>×${{row.quantity}}</strong></li>`
                ).join("") || "<li><span>Empty inventory</span></li>";
                inventory.innerHTML = rows;
              }};
              panel.querySelector(".spy-load-btn").addEventListener("click", async () => {{
                const userId = (userInput.value || "").trim();
                if (!userId) {{
                  setStatus("Enter a Discord user ID.", "error");
                  return;
                }}
                setStatus("Loading...", "");
                try {{
                  const response = await fetch(`/api/guild/${{guildId}}/spy/${{userId}}`);
                  const data = await response.json();
                  if (!response.ok) {{
                    setStatus(data.error || "Load failed", "error");
                    return;
                  }}
                  renderSpy(data);
                  setStatus("Loaded.", "ok");
                }} catch (err) {{
                  setStatus("Network error", "error");
                }}
              }});
              const mutate = async (action) => {{
                const userId = (userInput.value || "").trim();
                const itemId = (itemInput.value || "").trim();
                const quantity = parseInt(qtyInput.value || "1", 10);
                if (!userId || !itemId) {{
                  setStatus("User ID and item ID are required.", "error");
                  return;
                }}
                setStatus(action === "grant" ? "Granting..." : "Taking...", "");
                try {{
                  const response = await fetch(`/api/guild/${{guildId}}/spy/${{userId}}/${{action}}`, {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{ item_id: itemId, quantity }}),
                  }});
                  const data = await response.json();
                  if (!response.ok) {{
                    setStatus(data.error || "Action failed", "error");
                    return;
                  }}
                  const count = action === "grant" ? data.granted : data.removed;
                  setStatus(
                    action === "grant"
                      ? `Granted ${{count}}× ${{itemId}} (silent).`
                      : `Took ${{count}}× ${{itemId}} (silent).`,
                    "ok",
                  );
                  const reload = await fetch(`/api/guild/${{guildId}}/spy/${{userId}}`);
                  if (reload.ok) renderSpy(await reload.json());
                }} catch (err) {{
                  setStatus("Network error", "error");
                }}
              }};
              panel.querySelector(".spy-grant-btn").addEventListener("click", () => mutate("grant"));
              panel.querySelector(".spy-take-btn").addEventListener("click", () => mutate("take"));
            }});
            </script>
            """,
        )

    def _guild_card(self, item: dict[str, Any]) -> str:
        boss = item["boss"]
        if boss is None:
            boss_text = "None"
        else:
            boss_text = (
                f"{html.escape(boss['variant'].title())} {html.escape(boss['name'])} "
                f"({fmt_amount(boss['hp'])} / {fmt_amount(boss['max_hp'])})"
            )

        virus = item["virus"]
        virus_text = "None" if virus is None else (
            f"Holder {virus['holder_id']} - {virus['seconds_left']}s left "
            f"(passes: {virus['pass_count']})"
        )
        custom_settings = item["custom_settings"]
        settings_text = ", ".join(html.escape(name) for name in custom_settings) if custom_settings else "None"
        channels = item["channels"]
        channel_options = item["text_channels"]
        main_options = self._channel_select_options(
            channel_options,
            channels["main_channel_id"],
            empty_label="Not set (fallback)",
        )
        designated_options = self._channel_select_options(
            channel_options,
            channels["designated_channel_id"],
            empty_label="Not set (use main)",
        )
        split_checked = "checked" if channels["split_announcement_channels"] else ""
        channel_form = f"""
          <form class="channel-form" data-guild-id="{item['id']}">
            <label>
              <span>Main channel (coin drops)</span>
              <select name="main_channel_id">{main_options}</select>
            </label>
            <label>
              <span>Designated channel (boss / bot posts)</span>
              <select name="designated_channel_id">{designated_options}</select>
            </label>
            <label class="checkbox-row">
              <input type="checkbox" name="split_announcement_channels" {split_checked}>
              <span>Split channels — boss in designated, random gifts in main</span>
            </label>
            <button type="submit">Save channel settings</button>
            <p class="channel-status" aria-live="polite"></p>
          </form>
        """
        leaderboard = "\n".join(
            f"<li><span>{html.escape(row['name'])}</span><strong>{fmt_amount(row['wallet'])}</strong></li>"
            for row in item["leaderboard"]
        ) or "<li><span>No wallet data yet</span></li>"
        raid_rows = item.get("raid_leaderboard", [])
        raid_board = "\n".join(
            f"<li><span>{html.escape(row['name'])}</span><strong>{fmt_amount(row['damage'])}</strong></li>"
            for row in raid_rows
        ) or "<li><span>No raid damage yet</span></li>"
        gear_rows = item.get("gear_distribution", [])
        gear_board = "\n".join(
            f"<li><span>{html.escape(row['item_id'])}</span><strong>{row['count']}</strong></li>"
            for row in gear_rows
        ) or "<li><span>No equipped gear tracked</span></li>"
        hof = item.get("hall_of_fame", {})
        hof_richest = self._hof_list(hof.get("richest", []), money=True)
        hof_kills = self._hof_list(hof.get("boss_kills", []))
        hof_heals = self._hof_list(hof.get("heals", []))
        hof_ach = self._hof_list(hof.get("achievements", []))
        economy_sliders = self._tuning_form_sliders(item.get("economy_settings", []))
        economy_form = f"""
          <form class="economy-form tuning-form" data-guild-id="{item['id']}">
            {economy_sliders}
            <button type="submit">Save economy tuning</button>
            <p class="economy-status tuning-status" aria-live="polite"></p>
          </form>
        """
        duel_sliders = self._tuning_form_sliders(item.get("duel_settings", []))
        duel_form = f"""
          <form class="duel-form tuning-form" data-guild-id="{item['id']}">
            {duel_sliders}
            <button type="submit">Save duel tuning</button>
            <p class="duel-status tuning-status" aria-live="polite"></p>
          </form>
        """
        boss_variants = "".join(
            f'<option value="{html.escape(variant)}">{html.escape(label)}</option>'
            for variant, label in dashboard_boss_variants()
        )
        boss_summon_form = f"""
          <form class="boss-summon-form" data-guild-id="{item['id']}">
            <label>
              <span>Spawn boss (free, no summoner penalty)</span>
              <select name="variant">{boss_variants}</select>
            </label>
            <button type="submit">Summon on server</button>
            <p class="boss-summon-status" aria-live="polite"></p>
          </form>
        """
        attributes_reset_form = f"""
          <form class="attributes-reset-form" data-guild-id="{item['id']}">
            <p class="admin-warning">Sets <strong>every player&apos;s</strong> STR/DEX/AGI/DEF/VIT to <strong>0</strong> on this server.</p>
            <label class="confirm-row">
              <input type="checkbox" name="confirm" />
              <span>I understand — reset all attribute stats</span>
            </label>
            <button type="submit" class="danger">Reset all attribute stats</button>
            <p class="attributes-reset-status" aria-live="polite"></p>
          </form>
        """
        spy_panel = f"""
          <div class="spy-panel" data-guild-id="{item['id']}">
            <p class="admin-warning">Silent inventory tools — the player is never notified.</p>
            <label>
              <span>Discord user ID</span>
              <input type="text" class="spy-user-id" name="user_id" placeholder="e.g. Discord user ID" required>
            </label>
            <button type="button" class="spy-load-btn">Load inventory</button>
            <p class="spy-status" aria-live="polite"></p>
            <div class="spy-summary"></div>
            <div class="spy-inventory-wrap"><ol class="leaderboard spy-inventory"></ol></div>
            <div class="spy-actions">
              <label>
                <span>Item ID</span>
                <input list="item-catalog" class="spy-item-id" name="item_id" placeholder="jail_key" required>
              </label>
              <label>
                <span>Quantity</span>
                <input type="number" class="spy-qty" name="quantity" min="1" max="{config.DASHBOARD_SPY_MAX_QUANTITY}" value="1">
              </label>
              <button type="button" class="spy-grant-btn">Grant (silent)</button>
              <button type="button" class="spy-take-btn danger">Take (silent)</button>
            </div>
          </div>
        """
        seasonal = item.get("seasonal_event")
        if seasonal is None:
            event_text = "None"
        else:
            hours = seasonal["seconds_left"] // 3600
            event_text = (
                f"{html.escape(seasonal['type'])} ({seasonal['multiplier']:g}×) — {hours}h left"
            )
        return f"""
        <article class="guild-card">
          <div class="card-title">
            <h2>{html.escape(item["name"])}</h2>
            <span>{item["members"]} members</span>
          </div>
          <div class="mini-grid">
            {self._mini("Tracked", item["tracked_users"])}
            {self._mini("Wallets", fmt_amount(item["total_wallet"]))}
            {self._mini("Earned", fmt_amount(item["total_earned"]))}
            {self._mini("Messages", item["messages_sent"])}
          </div>
          <div class="status-list">
            <p><strong>Bounties:</strong> {item["bounties"]}</p>
            <p><strong>Boss:</strong> {boss_text}</p>
            <p><strong>Virus:</strong> {virus_text}</p>
            <p><strong>Custom config:</strong> {settings_text}</p>
            <p><strong>Seasonal event:</strong> {event_text}</p>
          </div>
          <h3>Channels</h3>
          {channel_form}
          <h3>Economy tuning</h3>
          {economy_form}
          <h3>Duel tuning</h3>
          {duel_form}
          <h3>Boss spawn</h3>
          {boss_summon_form}
          <h3>Attributes admin</h3>
          {attributes_reset_form}
          <h3>Inventory Spy</h3>
          {spy_panel}
          <h3>Hall of fame</h3>
          <div class="hof-grid">
              <div><p class="hof-title">Richest</p><ol class="leaderboard">{hof_richest}</ol></div>
            <div><p class="hof-title">Boss kills</p><ol class="leaderboard">{hof_kills}</ol></div>
            <div><p class="hof-title">Heals</p><ol class="leaderboard">{hof_heals}</ol></div>
            <div><p class="hof-title">Achievements</p><ol class="leaderboard">{hof_ach}</ol></div>
          </div>
          <h3>Top wallets</h3>
          <ol class="leaderboard">{leaderboard}</ol>
          <h3>Active raid damage</h3>
          <ol class="leaderboard">{raid_board}</ol>
          <h3>Equipped gear</h3>
          <ol class="leaderboard">{gear_board}</ol>
        </article>
        """

    @staticmethod
    def _hof_list(rows: list[dict[str, Any]], *, money: bool = False) -> str:
        if not rows:
            return "<li><span>No data yet</span></li>"
        parts = []
        for row in rows:
            value = float(row["value"])
            text = fmt_amount(value) if money else f"{int(value):,}"
            parts.append(
                f"<li><span>{html.escape(row['name'])}</span><strong>{html.escape(text)}</strong></li>"
            )
        return "".join(parts)

    @staticmethod
    def _tuning_form_sliders(settings: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for row in settings:
            key = str(row["key"])
            label = html.escape(str(row["label"]))
            value = float(row["value"])
            minimum = float(row["minimum"])
            maximum = float(row["maximum"])
            default = float(row["default"])
            custom = "custom" if row.get("is_custom") else "default"
            if key == "duel_loss_fraction":
                step = 0.01
                display = f"{int(round(value * 100))}%"
            elif key == "duel_same_target_cooldown_seconds":
                step = 60
                display = f"{int(value // 60)} min"
            elif key.endswith("_chance") or key in ("gambling_house_tax",):
                step = 0.001
                display = f"{value:.3g}"
            elif key == "prestige_min_wallet":
                step = 1000
                display = f"{int(value):,}"
            elif key == "duel_max_attacks_per_hour":
                step = 1
                display = f"{int(value)}"
            else:
                step = 0.01 if maximum <= 10 else 1
                display = f"{value:g}"
            parts.append(
                f"""
                <label class="slider-row">
                  <span>{label} <em class="{custom}">({display})</em></span>
                  <input type="range" data-setting-key="{html.escape(key)}"
                    min="{minimum}" max="{maximum}" step="{step}" value="{value}">
                  <span class="slider-hint">Default {default:g}</span>
                </label>
                """
            )
        return "".join(parts)

    @staticmethod
    def _channel_select_options(
        options: list[dict[str, str]],
        selected_id: int | None,
        *,
        empty_label: str,
    ) -> str:
        selected = str(selected_id) if selected_id is not None else ""
        parts = [f'<option value="">{html.escape(empty_label)}</option>']
        for option in options:
            sel = ' selected' if option["id"] == selected else ""
            parts.append(
                f'<option value="{html.escape(option["id"])}"{sel}>'
                f'{html.escape(option["label"])}</option>'
            )
        return "".join(parts)

    @staticmethod
    def _metric(label: str, value: object) -> str:
        return f"<article class='metric'><span>{html.escape(label)}</span><strong>{html.escape(str(value))}</strong></article>"

    @staticmethod
    def _mini(label: str, value: object) -> str:
        return f"<div><span>{html.escape(label)}</span><strong>{html.escape(str(value))}</strong></div>"

    @staticmethod
    def _empty_state() -> str:
        return "<article class='guild-card'><h2>No servers yet</h2><p>The bot has not joined any servers.</p></article>"

    @staticmethod
    def _page_shell(title: str, body: str) -> str:
        return f"""
        <!doctype html>
        <html lang="en">
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <title>{html.escape(title)}</title>
          <style>
            :root {{
              color-scheme: dark;
              --bg: #090b12;
              --panel: rgba(18, 24, 38, 0.82);
              --panel-strong: rgba(30, 39, 60, 0.95);
              --text: #f6f7fb;
              --muted: #aab3c8;
              --gold: #f3bd4d;
              --pink: #ff6fae;
              --cyan: #69e3ff;
              --line: rgba(255, 255, 255, 0.12);
            }}
            * {{ box-sizing: border-box; }}
            body {{
              margin: 0;
              min-height: 100vh;
              font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
              color: var(--text);
              background:
                radial-gradient(circle at top left, rgba(255,111,174,0.28), transparent 35rem),
                radial-gradient(circle at top right, rgba(105,227,255,0.22), transparent 32rem),
                linear-gradient(135deg, #090b12 0%, #11182a 55%, #090b12 100%);
              padding: 32px;
            }}
            code {{
              color: var(--gold);
              background: rgba(255, 255, 255, 0.08);
              padding: 2px 6px;
              border-radius: 8px;
            }}
            .hero {{
              display: flex;
              align-items: center;
              justify-content: space-between;
              gap: 24px;
              margin: 0 auto 26px;
              max-width: 1180px;
            }}
            h1, h2, h3, p {{ margin-top: 0; }}
            h1 {{ font-size: clamp(2rem, 6vw, 4rem); margin-bottom: 10px; letter-spacing: -0.05em; }}
            h2 {{ margin-bottom: 4px; }}
            h3 {{ color: var(--muted); font-size: 0.88rem; text-transform: uppercase; letter-spacing: 0.13em; }}
            p {{ color: var(--muted); }}
            .badge {{
              display: inline-flex;
              color: #16100a;
              background: linear-gradient(135deg, var(--gold), #fff0a8);
              border-radius: 999px;
              padding: 7px 12px;
              font-size: 0.78rem;
              font-weight: 800;
              letter-spacing: 0.08em;
              text-transform: uppercase;
              margin-bottom: 16px;
            }}
            .summary-grid, .guild-grid {{
              display: grid;
              gap: 18px;
              max-width: 1180px;
              margin: 0 auto 22px;
            }}
            .summary-grid {{ grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); }}
            .guild-grid {{ grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); }}
            .metric, .guild-card, .login-card {{
              border: 1px solid var(--line);
              background: var(--panel);
              box-shadow: 0 24px 80px rgba(0,0,0,0.32);
              backdrop-filter: blur(18px);
              border-radius: 28px;
            }}
            .metric {{ padding: 20px; }}
            .metric span, .mini-grid span {{ color: var(--muted); font-size: 0.82rem; }}
            .metric strong {{ display: block; font-size: 1.55rem; margin-top: 6px; }}
            .guild-card {{ padding: 24px; }}
            .card-title {{
              display: flex;
              justify-content: space-between;
              gap: 16px;
              border-bottom: 1px solid var(--line);
              padding-bottom: 18px;
              margin-bottom: 18px;
            }}
            .card-title span {{ color: var(--muted); white-space: nowrap; }}
            .mini-grid {{
              display: grid;
              grid-template-columns: repeat(2, 1fr);
              gap: 12px;
              margin-bottom: 18px;
            }}
            .mini-grid div {{
              background: rgba(255,255,255,0.06);
              border-radius: 18px;
              padding: 14px;
            }}
            .mini-grid strong {{ display: block; margin-top: 5px; }}
            .status-list {{
              background: var(--panel-strong);
              border-radius: 20px;
              padding: 16px;
              margin-bottom: 18px;
            }}
            .status-list p {{ margin-bottom: 8px; }}
            .status-list p:last-child {{ margin-bottom: 0; }}
            .leaderboard {{
              list-style-position: inside;
              padding: 0;
              margin: 0;
            }}
            .leaderboard li {{
              display: flex;
              justify-content: space-between;
              gap: 12px;
              padding: 10px 0;
              border-bottom: 1px solid var(--line);
            }}
            .leaderboard li:last-child {{ border-bottom: 0; }}
            .login-card {{
              max-width: 460px;
              margin: 10vh auto;
              padding: 34px;
            }}
            input, button {{
              width: 100%;
              border: 0;
              border-radius: 16px;
              padding: 14px 16px;
              font: inherit;
            }}
            input {{
              color: var(--text);
              background: rgba(255,255,255,0.08);
              border: 1px solid var(--line);
              margin-bottom: 12px;
            }}
            button {{
              cursor: pointer;
              color: #16100a;
              background: linear-gradient(135deg, var(--gold), var(--pink));
              font-weight: 800;
            }}
            .ghost {{
              width: auto;
              color: var(--text);
              background: rgba(255,255,255,0.08);
              border: 1px solid var(--line);
            }}
            .error {{ color: #ff9a9a; }}
            .channel-form {{
              display: grid;
              gap: 12px;
              margin-bottom: 18px;
              background: rgba(255,255,255,0.04);
              border-radius: 20px;
              padding: 16px;
              border: 1px solid var(--line);
            }}
            .channel-form label {{
              display: grid;
              gap: 6px;
            }}
            .channel-form label span {{
              color: var(--muted);
              font-size: 0.82rem;
            }}
            .channel-form select {{
              width: 100%;
              border: 1px solid var(--line);
              border-radius: 14px;
              padding: 12px 14px;
              color: var(--text);
              background: rgba(255,255,255,0.08);
              font: inherit;
            }}
            .channel-form .checkbox-row {{
              display: flex;
              align-items: flex-start;
              gap: 10px;
            }}
            .channel-form .checkbox-row input {{
              width: auto;
              margin: 4px 0 0;
            }}
            .channel-form .checkbox-row span {{
              color: var(--text);
              font-size: 0.9rem;
            }}
            .channel-form button {{
              margin-top: 4px;
            }}
            .channel-status {{
              margin: 0;
              font-size: 0.85rem;
              color: var(--muted);
            }}
            .channel-status.ok {{ color: #9dffb8; }}
            .channel-status.error {{ color: #ff9a9a; }}
            .economy-form, .duel-form, .tuning-form {{
              display: grid;
              gap: 12px;
              margin-bottom: 18px;
              background: rgba(255,255,255,0.04);
              border-radius: 20px;
              padding: 16px;
              border: 1px solid var(--line);
            }}
            .slider-row {{
              display: grid;
              gap: 6px;
            }}
            .slider-row span em.custom {{ color: var(--cyan); font-style: normal; }}
            .slider-row span em.default {{ color: var(--muted); font-style: normal; }}
            .slider-hint {{ color: var(--muted); font-size: 0.78rem; }}
            .economy-form input[type="range"] {{
              width: 100%;
              accent-color: var(--gold);
            }}
            .economy-status, .duel-status, .boss-summon-status, .tuning-status, .attributes-reset-status {{
              margin: 0;
              font-size: 0.85rem;
              color: var(--muted);
            }}
            .economy-status.ok, .duel-status.ok, .boss-summon-status.ok, .tuning-status.ok, .attributes-reset-status.ok {{ color: #9dffb8; }}
            .economy-status.error, .duel-status.error, .boss-summon-status.error, .tuning-status.error, .attributes-reset-status.error {{ color: #ff9a9a; }}
            .admin-warning {{ color: #ffd89a; font-size: 0.9rem; margin: 0 0 0.5rem; }}
            .confirm-row {{ display: flex; align-items: center; gap: 0.5rem; margin: 0.5rem 0; font-size: 0.9rem; }}
            button.danger {{ background: #8b2e2e; border-color: #a33; }}
            button.danger:hover {{ background: #a33; }}
            .hof-grid {{
              display: grid;
              grid-template-columns: repeat(2, 1fr);
              gap: 12px;
              margin-bottom: 18px;
            }}
            .hof-title {{
              color: var(--muted);
              font-size: 0.78rem;
              text-transform: uppercase;
              letter-spacing: 0.1em;
              margin: 0 0 6px;
            }}
            @media (max-width: 720px) {{
              body {{ padding: 18px; }}
              .hero {{ align-items: flex-start; flex-direction: column; }}
              .ghost {{ width: 100%; }}
            }}
          </style>
        </head>
        <body>{body}</body>
        </html>
        """

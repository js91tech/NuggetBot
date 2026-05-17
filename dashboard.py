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
        return hashlib.sha256(f"nuggetbot-dashboard:{config.DASHBOARD_TOKEN}".encode()).hexdigest()

    async def _snapshots(self) -> list[dict[str, Any]]:
        snapshots = []
        for guild in self.bot.guilds:
            stats = await self.bot.db.economy_stats(guild.id)
            bounties = await self.bot.db.count_bounties(guild.id)
            boss = await self.bot.db.get_active_boss(guild.id)
            virus = await self.bot.db.get_hacker_pot(guild.id)
            custom_settings = await self.bot.db.custom_config_names(guild.id)
            leaderboard = await self.bot.db.leaderboard(guild.id, limit=5)
            channel_settings = await self.bot.db.get_guild_channel_settings(guild.id)
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
                            "wallet": float(row["wallet"]),
                        }
                        for row in leaderboard
                    ],
                }
            )
        return snapshots

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
            "NuggetBot Dashboard Login",
            f"""
            <main class="login-card">
              <div class="badge">NuggetBot</div>
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
            "NuggetBot Dashboard Disabled",
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
            "NuggetBot Dashboard",
            f"""
            <header class="hero">
              <div>
                <div class="badge">Live Dashboard</div>
                <h1>NuggetBot Control Room</h1>
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
          </div>
          <h3>Channels</h3>
          {channel_form}
          <h3>Top wallets</h3>
          <ol class="leaderboard">{leaderboard}</ol>
        </article>
        """

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

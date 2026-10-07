import logging
from typing import Optional, Dict, Any
import httpx
from radio_listener.config import settings
from radio_listener.models import ContestAlert, EventType, ActionType

logger = logging.getLogger("radio_listener.notifier.discord")

class DiscordNotifier:
    """
    Delivers rich embed notifications to a Discord channel via Webhook.
    """
    def __init__(
        self,
        webhook_url: Optional[str] = None,
        enabled: Optional[bool] = None
    ):
        self.webhook_url = webhook_url or settings.DISCORD_WEBHOOK_URL
        self.enabled = enabled if enabled is not None else settings.DISCORD_ENABLED
        self.client = httpx.AsyncClient(timeout=4.0)

    async def send_alert(self, alert: ContestAlert) -> bool:
        if not self.enabled or not self.webhook_url:
            return False

        logger.info(f"Dispatching Discord webhook alert: {alert.title}")

        # Color codes: Red for emergency call-in (0xEF4444), Gold for clue (0xF59E0B), Green for others (0x10B981)
        color = 0x10B981
        if alert.is_actionable_now and alert.action_type == ActionType.PHONE_CALL:
            color = 0xEF4444
        elif alert.event_type == EventType.NEW_CLUE:
            color = 0xF59E0B

        fields = []
        if alert.clue_number:
            fields.append({"name": "Clue Number", "value": f"#{alert.clue_number}", "inline": True})
        if alert.caller_guess:
            fields.append({"name": "Caller Guess", "value": alert.caller_guess, "inline": True})
        if alert.verdict:
            fields.append({"name": "Verdict", "value": alert.verdict.value, "inline": True})
        
        # Phone dial action
        if alert.phone_number:
            fields.append({"name": "📞 Studio Line", "value": f"[{alert.phone_number}](tel:{settings.STATION_STUDIO_PHONE_CLEAN})", "inline": True})

        description = alert.summary
        if alert.clue_text:
            description = f"**\"{alert.clue_text}\"**\n\n{alert.summary}"

        embed = {
            "title": alert.title,
            "description": description,
            "color": color,
            "fields": fields,
            "footer": {
                "text": "The Zone @ 91-3 (CJZN-FM) • Victoria, BC"
            },
            "timestamp": alert.timestamp.isoformat()
        }

        content = "@everyone 🚨 **URGENT CONTEST ALERT!**" if (alert.is_actionable_now and alert.action_type == ActionType.PHONE_CALL) else ""

        payload = {
            "username": "The Zone 91-3 Contest Radar",
            "avatar_url": "https://media.socastsrm.com/wordpress/wp-content/blogs.dir/460/files/2015/10/Zat-site-icon-2015.png",
            "content": content,
            "embeds": [embed]
        }

        try:
            resp = await self.client.post(self.webhook_url, json=payload)
            if resp.status_code in [200, 204]:
                logger.info("Discord webhook notification delivered successfully.")
                return True
            else:
                logger.error(f"Discord returned error {resp.status_code}: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Discord request failed: {e}")
            return False

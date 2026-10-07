import logging
from typing import Optional, Dict, Any
import httpx
from radio_listener.config import settings
from radio_listener.models import ContestAlert, EventType, ActionType

logger = logging.getLogger("radio_listener.notifier")

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"

class PushoverNotifier:
    """
    Delivers sub-second high-priority alerts to user's mobile device via Pushover.
    Configures priority=2 emergency bypass and click-to-dial tel: URLs.
    """
    def __init__(
        self,
        app_token: Optional[str] = None,
        user_key: Optional[str] = None,
        enabled: Optional[bool] = None
    ):
        self.app_token = app_token or settings.PUSHOVER_APP_TOKEN
        self.user_key = user_key or settings.PUSHOVER_USER_KEY
        self.enabled = enabled if enabled is not None else settings.PUSHOVER_ENABLED
        self.client = httpx.AsyncClient(timeout=3.0)

    async def send_alert(self, alert: ContestAlert) -> bool:
        """
        Sends an alert formatted with action buttons and appropriate priority.
        """
        logger.info(f"Dispatching notification: {alert.title}")

        if not self.enabled or not self.app_token or not self.user_key:
            logger.info(f"[SIMULATED NOTIFICATION] (Pushover disabled or credentials unset)\nTitle: {alert.title}\nBody: {alert.summary}\nAction: {alert.dial_url or alert.sms_url}")
            return True

        # Priority matrix:
        # Priority 2: Emergency / Alarm (Requires retry & expire parameters)
        # Priority 1: High Priority (Bypasses quiet hours, loud sound)
        # Priority 0: Normal
        priority = 0
        sound = "pushover"
        
        if alert.is_actionable_now and alert.action_type == ActionType.PHONE_CALL:
            priority = 2
            sound = "siren"
        elif alert.is_actionable_now and alert.event_type == EventType.NEW_CLUE:
            priority = 1
            sound = "cosmic"
        elif alert.is_actionable_now:
            priority = 1
            sound = "cashregister"

        payload: Dict[str, Any] = {
            "token": self.app_token,
            "user": self.user_key,
            "title": alert.title[:250],
            "message": alert.summary[:1024],
            "priority": priority,
            "sound": sound
        }

        # Emergency priority 2 requires retry and expire
        if priority == 2:
            payload["retry"] = 30
            payload["expire"] = 180

        # Action URL (Click to dial or Click to text)
        if alert.dial_url:
            payload["url"] = alert.dial_url
            payload["url_title"] = f"📞 Call {settings.STATION_STUDIO_PHONE} NOW"
        elif alert.sms_url:
            payload["url"] = alert.sms_url
            payload["url_title"] = f"💬 Text {settings.STATION_STUDIO_PHONE}"
        elif alert.event_type == EventType.NEW_CLUE:
            payload["url"] = settings.CONTEST_URL
            payload["url_title"] = "🌐 Open /shhhhhh/ Board"

        try:
            resp = await self.client.post(PUSHOVER_API_URL, data=payload)
            if resp.status_code == 200:
                logger.info("Pushover notification successfully delivered.")
                return True
            else:
                logger.error(f"Pushover returned error {resp.status_code}: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Pushover request failed: {e}")
            return False

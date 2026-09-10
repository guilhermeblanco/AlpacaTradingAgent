"""
Constants and configuration for TradingAgents WebUI
"""

from webui.config.tokens import PALETTE

# Define colors for consistency
# A view onto webui.config.tokens.PALETTE under the names this codebase
# already uses. The tokens module is the authority; changing a colour here
# would only change it for Python and leave the stylesheet behind.
COLORS = {
    "primary": PALETTE["accent"],
    "secondary": PALETTE["positive"],
    "background": PALETTE["background"],
    "card": PALETTE["surface"],
    "text": PALETTE["text"],
    "pending": PALETTE["neutral"],
    "in_progress": PALETTE["caution"],
    "completed": PALETTE["positive"],
    "error": PALETTE["negative"],
    "nav_active": PALETTE["text"],
    "nav_inactive": PALETTE["text-faint"],
    "border": PALETTE["border"],
    "hover": PALETTE["accent-hover"],
}

# Fallback poll intervals, in milliseconds. The server-sent pulse is the
# primary signal (see webui/utils/pulse.py); these only cover the case where
# a proxy eats the stream, so they are deliberately slow.
REFRESH_INTERVALS = {
    "fast": 15000,     # nudged by the pulse; this is the no-stream fallback
    "medium": 30000,
    "slow": 60000,     # account data
}

# App configuration
APP_CONFIG = {
    "title": "TradingAgents - Multi-Agent Financial Analysis",
    "external_stylesheets": [
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css"
    ],
    "suppress_callback_exceptions": True,
    "update_title": None,
} 
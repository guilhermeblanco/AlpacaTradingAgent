"""
Constants and configuration for TradingAgents WebUI
"""

# Define colors for consistency
COLORS = {
    "primary": "#3B82F6",         # Bright blue
    "secondary": "#10B981",       # Green
    "background": "#0F172A",      # Dark blue background
    "card": "#1E293B",            # Slightly lighter card background
    "text": "#F1F5F9",            # Light text
    "pending": "#94A3B8",         # Slate gray
    "in_progress": "#F59E0B",     # Amber
    "completed": "#10B981",       # Green
    "error": "#EF4444",           # Red
    "nav_active": "#F1F5F9",      # White for active nav
    "nav_inactive": "#64748B",    # Slate for inactive nav
    "border": "#334155",          # Border color
    "hover": "#2563EB",           # Hover color
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
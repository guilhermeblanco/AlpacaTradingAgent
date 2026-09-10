#!/usr/bin/env python
"""
run_webui_dash.py - Run the Dash-based web UI for TradingAgents
"""

import argparse
import sys
import os
import socket
from webui.app_dash import run_app


#: Set truthy to bind the requested port or fail. See strict_port_requested().
STRICT_PORT_ENV = "TRADINGAGENTS_STRICT_PORT"

_TRUTHY = {"1", "true", "yes", "on"}


def strict_port_requested(env=None):
    """Whether to refuse to fall back to a different port.

    Port hunting is a convenience on a laptop, where the browser follows
    whatever the console prints. Under a container runtime it is a trap: the
    port is published by the pod, so moving from 7860 to 7861 does not
    relocate the mapping — it makes the service unreachable while the process
    reports that it started fine. The image sets this so a clash is a loud
    failure instead of a quiet one.
    """
    env = os.environ if env is None else env
    return str(env.get(STRICT_PORT_ENV, "")).strip().lower() in _TRUTHY


def port_is_free(port, host="0.0.0.0"):
    """Whether `port` can be bound on `host` right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def find_available_port(start_port, end_port=None):
    """Find an available port in the given range"""
    if end_port is None:
        end_port = start_port + 100  # Try up to 100 ports after the start port
    
    for port in range(start_port, end_port + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('localhost', port))
                return port
            except OSError:
                continue
    
    return None


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(description="TradingAgents Dash Web UI")
    
    parser.add_argument(
        "--port",
        type=int,
        default=7860,
        help="Port to run the server on",
    )
    
    parser.add_argument(
        "--share",
        action="store_true",
        help="Share the app publicly",
    )
    
    parser.add_argument(
        "--server-name",
        type=str,
        default="127.0.0.1",
        help="Server name to run the app on",
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run in debug mode",
    )
    
    parser.add_argument(
        "--max-threads",
        type=int,
        default=40,
        help="Maximum number of threads",
    )
    
    return parser.parse_args()


def main():
    """Run the Dash web UI"""
    args = parse_args()
    
    # Find an available port if the specified one is not available — unless
    # the port was published by something outside this process, in which case
    # a different port is worse than no server at all.
    if strict_port_requested():
        if not port_is_free(args.port, args.server_name):
            print(
                f"Error: port {args.port} is already in use and "
                f"{STRICT_PORT_ENV} forbids falling back to another one."
            )
            return 1
        port = args.port
    else:
        port = find_available_port(args.port)
        if port is None:
            print(f"Error: Could not find an available port between {args.port} and {args.port + 100}")
            return 1

        if port != args.port:
            print(f"Port {args.port} is already in use. Using port {port} instead.")
    
    print(f"Starting TradingAgents Dash Web UI on port {port}...")
    
    # The manager is disabled by default and independently gates live accounts.
    try:
        from tradingagents.dataflows.virtual_stops_manager import VirtualStopsManager
        daemon_status = VirtualStopsManager.start_realtime_daemon()
        if not daemon_status.get("started") and daemon_status.get("reason") != "disabled":
            print(f"VirtualStops daemon did not start: {daemon_status.get('reason')}")
    except Exception as e:
        print(f"Warning: Could not start VirtualStopsManager daemon: {e}")

    # Run the app
    sys.exit(run_app(
        port=port,
        share=args.share,
        server_name=args.server_name,
        debug=args.debug,
        max_threads=args.max_threads
    ))


if __name__ == "__main__":
    sys.exit(main() or 0)

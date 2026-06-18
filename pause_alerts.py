#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Standalone Alert Pause Toggle Tool

This tool connects to the Mycelian Web Engine and toggles the alert pause status.
Can be compiled to a standalone executable.

Usage: python pause_alerts.py [--host HOST] [--port PORT]
"""

import argparse
import sys
import time

try:
    import socketio
except ImportError:
    print("Error: python-socketio is required. Install with: pip install python-socketio")
    sys.exit(1)

def toggle_pause(host: str = '127.0.0.1', port: int = 5000, timeout: int = 5) -> bool:
    """
    Connect to web engine and toggle alert pause status
    
    Args:
        host (str): Web engine host address
        port (int): Web engine port number
        timeout (int): Connection timeout in seconds
        
    Returns:
        bool: True if operation was successful, False otherwise
    """
    url = f"http://{host}:{port}"
    
    # Create SocketIO client
    sio = socketio.Client(
        reconnection=False,
        reconnection_attempts=0,
        logger=False,
        engineio_logger=False
    )
    
    try:
        # Connect to the server
        sio.connect(url, wait_timeout=timeout)
        
        # Send the pause_alerts event to toggle status
        sio.emit('pause_alerts')
        
        # Give it a moment to process
        time.sleep(0.5)
        
        return True
        
    except Exception:
        return False
    finally:
        # Clean up connection
        try:
            if sio.connected:
                sio.disconnect()
        except Exception:
            pass

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Toggle alert pause status in Mycelian Web Engine"
    )
    
    parser.add_argument(
        '--host', 
        default='127.0.0.1',
        help='Web Engine host address (default: 127.0.0.1)'
    )
    
    parser.add_argument(
        '--port', 
        type=int, 
        default=5000,
        help='Web Engine port number (default: 5000)'
    )
    
    parser.add_argument(
        '--timeout',
        type=int,
        default=5,
        help='Connection timeout in seconds (default: 5)'
    )
    
    args = parser.parse_args()
    
    # Toggle pause status
    success = toggle_pause(host=args.host, port=args.port, timeout=args.timeout)
    
    if success:
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == '__main__':
    main() 
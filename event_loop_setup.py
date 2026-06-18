"""Windows asyncio event loop configuration for proper MQTT socket handling."""

import asyncio
import sys


def setup_windows_event_loop() -> None:
    """
    Configure the event loop for Windows.
    
    On Windows, the default selector-based event loop doesn't support 
    socket operations that aiomqtt needs. This function switches to the 
    ProactorEventLoop, which is the only loop that properly handles I/O 
    operations on Windows.
    
    Should be called early in the application initialization, before 
    creating any asyncio tasks or connections.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

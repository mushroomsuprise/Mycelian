# Mycelian - Streaming Toolkit

Mycelian is a comprehensive streaming toolkit that combines custom alert systems, interactive browser sources, and multi-platform integrations for Twitch streamers. Built with Python and Flask, it provides both a desktop application and a powerful web-based template system.

📖 **[Complete Documentation](https://mycelian.readthedocs.io/en/latest/)**

## What is Mycelian?

Mycelian transforms your streaming experience by providing:

- **🎯 Smart Alert System** - Real-time alerts for follows, subs, bits, donations, channel points, and PSN trophies
- **🌐 Custom Browser Sources** - Interactive HTML templates served at `http://localhost:5000` for OBS
- **🎮 Multi-Platform Integration** - Twitch, Spotify, PlayStation Network, and more
- **⚡ Real-Time WebSocket API** - Live data communication between your stream and templates
- **🎨 Template Customization** - JSON configuration files for easy template customization
- **📱 Modern Desktop Interface** - Tabbed interface for managing alerts, settings, and templates

## Key Features

**Alert System:**
- Support for all major Twitch events (follows, subs, bits, raids, channel points)
- PlayStation Network trophy notifications with game artwork
- Customizable GIF animations and audio for each alert type
- Alert history with replay functionality

**Interactive Templates:**
- **BitBoss** - Interactive boss battle using viewer bits and subscriptions
- **Activity Feed** - Real-time scrolling display of stream events
- **Chat Integration** - Live chat with emote support
- **Now Playing** - Spotify track display with album artwork
- **Counters & Timers** - Interactive counting displays and roulette wheels

**Desktop Application:**
- **Activity Feed Tab** - Monitor and replay alerts in real-time
- **Settings Tab** - Configure service integrations and OAuth connections
- **Custom Sources Tab** - Visual template configuration interface
- **Source Controls Tab** - Interactive controls for real-time template manipulation

## 🚀 Quick Start

**Requirements:**
- Python 3.10+ (3.12.3 recommended)
- Windows, macOS, or Linux

**Installation:**
```bash
# Install dependencies
pip install -r requirements.txt

# Run application
python main.py
```

**Setup:**
1. **Configure Twitch Integration** - Add your Twitch API credentials in the Settings tab
2. **Set Up Browser Sources** - Add browser sources in OBS pointing to `http://localhost:5000/{template_name}`
3. **Customize Templates** - Use the Custom Sources tab to configure template appearance and behavior
4. **Monitor Activity** - Use the Activity Feed tab to view and replay alerts

**Popular Browser Source URLs:**
- `http://localhost:5000/activity_feed` - Real-time alert feed
- `http://localhost:5000/bitboss` - Interactive boss battle game
- `http://localhost:5000/chat` - Chat integration
- `http://localhost:5000/counter` - Interactive counters

## 🔧 Template Development

Mycelian provides a powerful WebSocket API for creating custom templates:

- **Real-time Communication** - Connect to `ws://localhost:5000` for live data
- **Alert Events** - Receive `next_alert` events with full alert data
- **Service Integration** - Access Twitch, Spotify, and PSN data in real-time
- **Template Controls** - Interactive elements with dynamic control events

**Simple Template Example:**
```javascript
const socket = io();
socket.on('next_alert', function(data) {
    // Display alert with data.username, data.alert_type, etc.
    console.log(`${data.username} sent ${data.amt_cheered} bits!`);
});
```

## ❓ Need Help?

- 📖 **[Complete Documentation](https://mycelian.readthedocs.io/en/latest/)** - Comprehensive setup and usage guide
- 🎨 **[Template Guide](https://mycelian.readthedocs.io/en/latest/templates.html)** - Create custom browser sources
- ⚡ **[WebSocket API](https://mycelian.readthedocs.io/en/latest/usage.html#websocket-api-reference)** - Real-time data integration
- 🔧 **[Troubleshooting](https://mycelian.readthedocs.io/en/latest/usage.html#troubleshooting)** - Common issues and solutions

---

*Built with ❤️ for the streaming community*
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Help Content Database

Contains all help topics, categories, and content for the Mycelian help system.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class HelpCategory(Enum):
    """Categories for help topics"""

    GETTING_STARTED = "getting_started"
    ALERTS = "alerts"
    TEMPLATES = "templates"
    CHATBOT = "chatbot"
    CONNECTORS = "connectors"
    INTEGRATIONS = "integrations"
    SETTINGS = "settings"
    TROUBLESHOOTING = "troubleshooting"


@dataclass
class HelpSection:
    """Section within a help topic"""

    title: str
    content: str
    code_examples: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    tips: List[str] = field(default_factory=list)


@dataclass
class HelpTopic:
    """Single help topic/article"""

    id: str
    title: str
    category: HelpCategory
    summary: str
    content: str  # Markdown content
    keywords: List[str] = field(default_factory=list)
    related_topics: List[str] = field(default_factory=list)
    video_url: Optional[str] = None
    ui_context: Optional[str] = None  # Which UI element this relates to


# Help Topics Database
HELP_TOPICS: Dict[str, HelpTopic] = {
    # =========================================
    # Getting Started
    # =========================================
    "getting_started_intro": HelpTopic(
        id="getting_started_intro",
        title="Welcome to Mycelian",
        category=HelpCategory.GETTING_STARTED,
        summary="Learn the basics of Mycelian streaming toolkit",
        content="""
# Welcome to Mycelian

Mycelian is a comprehensive streaming toolkit designed for Twitch streamers.
It provides custom alerts, interactive browser sources, and integrations with
multiple platforms.

## Key Features

- **Custom Alert System**: Fully customizable [alerts](help:alerts_overview) for follows, subs, bits,
  raids, donations, channel points, and PSN trophies
- **Browser Sources**: Beautiful, animated [overlays](help:templates_intro) for your stream
- **Chatbot**: Custom [commands](help:chatbot_commands), events, quotes, and greetings
- **Integrations**: Connect [Twitch](help:integrations_twitch), [Spotify](help:integrations_spotify), [PlayStation Network](help:integrations_psn), and more
- **Template System**: Customize every [visual aspect](help:template_configuration); build custom overlays in [Spore Studio](help:spore_studio_overview)

## Quick Start

1. **Connect Twitch**: [Connect your Twitch account](help:twitch_setup) in Settings
2. **Set Up Alerts**: [Configure alerts](help:alerts_overview) in the Alerts tab
3. **Add Browser Sources**: [Copy URLs from Templates](help:templates_intro) to [OBS](help:obs_setup)
4. **Customize**: Adjust [settings](help:settings_overview) to match your stream's style

> **Tip:** Follow the Quick Start steps above in order for the smoothest setup experience.

## Need Help?

- Click the **?** button next to any setting for context-specific help
- Use the **Help** menu for searchable documentation
- Check the [Troubleshooting](help:troubleshooting_alerts) section for common issues
        """,
        keywords=["start", "begin", "intro", "overview", "basics"],
        related_topics=[
            "twitch_setup",
            "first_alert_setup",
            "alerts_overview",
            "templates_intro",
        ],
    ),
    "twitch_setup": HelpTopic(
        id="twitch_setup",
        title="Connecting Your Twitch Account",
        category=HelpCategory.GETTING_STARTED,
        summary="How to connect and configure Twitch integration",
        content="""
# Connecting Your Twitch Account

Mycelian requires a Twitch connection to receive events like follows, subs, and bits.
See the full [Twitch Integration](help:integrations_twitch) guide for advanced details.

## Prerequisites

You'll need:
- A Twitch account
- Twitch Developer Application credentials (optional for advanced users)

> **Note:** You can start with the Quick Connection below and skip the manual setup entirely.

## Quick Connection (Recommended)

1. Open **Settings** → **Twitch** tab
2. Click **"Connect with Twitch"**
3. Authorize Mycelian in the browser window
4. Return to the app - you should see "Connected" status

## Manual Setup (Advanced)

If you prefer to use your own Twitch application:

1. Go to [Twitch Developer Console](https://dev.twitch.tv/console)
2. Create a new application
3. Set OAuth Redirect URL to: `http://localhost:17563`
4. Copy your Client ID and Client Secret
5. Enter them in Settings → Twitch

## Connection Status

| Status | Meaning |
|--------|---------|
| 🟢 Connected | Twitch API is active and receiving events |
| 🟡 Connecting | Authentication in progress |
| 🔴 Disconnected | Not connected - click Connect to authenticate |
| ⚠️ Error | Check logs for details |

## Troubleshooting

> **Tip:** For more detailed connection troubleshooting, see [Connection Issues](help:troubleshooting_connections).

**"Authentication Failed"**
- Clear browser cookies and try again
- Ensure pop-ups are not blocked

**"Token Expired"**
- Click "Reconnect" to refresh your token
- Tokens are automatically refreshed when possible
        """,
        keywords=["twitch", "connect", "oauth", "authentication", "api"],
        related_topics=[
            "getting_started_intro",
            "alerts_overview",
            "first_alert_setup",
        ],
        ui_context="settings.twitch",
    ),
    "first_alert_setup": HelpTopic(
        id="first_alert_setup",
        title="Setting Up Your First Alert",
        category=HelpCategory.GETTING_STARTED,
        summary="Step-by-step guide to creating your first stream alert",
        content="""
# Setting Up Your First Alert

This guide walks you through creating your first alert in Mycelian.

## Prerequisites

Before creating alerts, ensure you have:
- [Connected your Twitch account](help:twitch_setup) (Settings → Twitch)
- OBS or streaming software ready
- Basic understanding of [browser sources](help:obs_setup)

## Quick Start: Follow Alert

Let's create a simple follow alert:

### Step 1: Open Alert Settings
1. Click the **Alerts** tab in Mycelian
2. Select **Follows** from the sub-tabs

### Step 2: Enable the Alert
1. Toggle **"Enable Follow Alerts"** to ON
2. You should see the alert settings panel expand

### Step 3: Configure Basic Settings
1. **Alert Duration**: Set how long the alert displays (e.g., 5 seconds)
2. **Alert Message**: Customize the text shown
   - Use `{user}` to insert the follower's name
   - Example: "Welcome {user} to the stream!"

### Step 4: Add Media (Optional)
1. **Alert Image/GIF**: Click to select an image or GIF
2. **Alert Sound**: Click to select an audio file
3. **Volume**: Adjust the sound volume

### Step 5: Add to OBS
1. Go to the [Templates](help:templates_intro) tab in Mycelian
2. Find the **Alerts** browser source URL
3. In OBS: Add → Browser Source (see [OBS setup guide](help:obs_setup))
4. Paste the URL
5. Set size (recommended: 800x600)

### Step 6: Test Your Alert
1. Return to the **Alerts** tab
2. Click **"Test Alert"** button (see [Testing Alerts](help:alert_testing))
3. Watch for the alert in OBS
4. Adjust settings as needed

## Common Alert Types

| Alert Type | Trigger | Common Use |
|------------|---------|------------|
| Follow | New follower | Welcome message |
| Subscribe | New/resub | Thank you celebration |
| Bits | Cheers | Appreciation |
| Raid | Incoming raid | Welcome raiders |
| Donation | Tips | Thank donors |

## Tips for Great Alerts

### Keep It Simple
- Start with basic alerts
- Add complexity gradually
- Test each change

### Match Your Brand
- Use consistent colors
- Match your stream's style
- Keep text readable

### Mind the Duration
- 3-5 seconds for simple alerts
- 5-10 seconds for special events
- Don't block important gameplay

## Next Steps

Once comfortable with basic alerts:
- Explore [alert variations](help:alert_configuration) for different amounts
- Set up [alert sounds](help:alert_media) for each type
- Try [GIF alerts](help:alert_media) for more visual impact
- Configure **channel points** alerts

> **Tip:** Use the Test button to preview each change before going live.
        """,
        keywords=["first", "setup", "beginner", "start", "create", "alert"],
        related_topics=["alerts_overview", "twitch_setup", "obs_setup"],
    ),
    "obs_setup": HelpTopic(
        id="obs_setup",
        title="Setting Up Browser Sources in OBS",
        category=HelpCategory.GETTING_STARTED,
        summary="How to add Mycelian browser sources to your OBS stream",
        content="""
# Setting Up Browser Sources in OBS

Browser sources allow you to display Mycelian overlays, [alerts](help:alerts_overview), and interactive elements on your stream.

> **Note:** Make sure Mycelian is running before adding browser sources to OBS.

## Prerequisites

- [OBS Studio](https://obsproject.com/download) installed (free, open-source)
- Mycelian running on the same computer
- For detailed OBS help, see the [OBS Knowledge Base](https://obsproject.com/kb)

## Adding a Browser Source

1. In OBS, right-click in the **Sources** panel
2. Select **"Browser"** or **"Browser Source"**
3. Configure the source:
   - **Name**: Choose a descriptive name (e.g., "Mycelian Alerts")
   - **URL**: Copy from Mycelian's Templates tab
   - **Width/Height**: Match your canvas resolution
   - **Shutdown source when not visible**: Enable for performance

## Template URLs

Mycelian serves templates at `http://localhost:5000/`:

| Template | URL |
|----------|-----|
| Alerts | `http://localhost:5000/alerts` |
| Chat | `http://localhost:5000/chat` |
| Activity Feed | `http://localhost:5000/activity_feed` |
| Sub Bar | `http://localhost:5000/subbar` |
| Bit Bar | `http://localhost:5000/bitbar` |

## Browser Source Settings

### Recommended Settings
- **Width**: 1920 (or your canvas width)
- **Height**: 1080 (or your canvas height)
- **FPS**: 60
- **Custom CSS**: Leave blank
- **Shutdown when not visible**: Enabled

### Advanced Settings
- **Refresh browser when scene becomes active**: Optional
- **Refresh cache of current page**: Optional
- **Reroute audio**: Disable (Mycelian handles audio separately)

## Positioning and Scaling

1. **Scale to fit**: Use OBS transform controls to fit the source
2. **Position**: Place alerts in corners or dedicated areas
3. **Layering**: Put alerts above other sources but below webcam

## Performance Tips

- **Hardware acceleration**: Ensure enabled in OBS settings
- **Browser cache**: Clear occasionally if sources seem slow
- **Multiple sources**: Use sparingly to avoid performance issues

## Troubleshooting

**Source shows blank/white**
- Check if Mycelian is running
- Verify the URL is correct
- Try refreshing the browser source

> **Tip:** See [Troubleshooting Alerts](help:troubleshooting_alerts) for more detailed solutions.

**Poor performance**
- Reduce browser source resolution
- Enable "Shutdown when not visible"
- Close unused browser tabs in OBS
- See [Performance Optimization](help:troubleshooting_performance) for more tips

**Audio not working**
- Browser sources handle video only
- Audio plays through OBS media sources or separately
- See [Audio Troubleshooting](help:troubleshooting_audio) for solutions
        """,
        keywords=["obs", "browser source", "overlay", "stream", "setup"],
        related_topics=["getting_started_intro", "templates_intro"],
        ui_context="templates",
    ),
    # =========================================
    # Alerts
    # =========================================
    "alerts_overview": HelpTopic(
        id="alerts_overview",
        title="Alert System Overview",
        category=HelpCategory.ALERTS,
        summary="Understanding how alerts work in Mycelian",
        content="""
# Alert System Overview

The alert system displays notifications on your stream when viewers interact.
Make sure you've [connected Twitch](help:twitch_setup) and [set up browser sources](help:obs_setup) first.

## Alert Types

| Type | Trigger |
|------|---------|
| **Follow** | New follower |
| **Sub** | New subscription |
| **Resub** | Returning subscriber |
| **Gift Sub** | Gifted subscription(s) |
| **Bits** | Bit cheers |
| **Raid** | Incoming raid |
| **Points** | Channel point redemption |
| **Donation** | Tips / donations (when connected via supported integrations) |

## Alert Configuration

Each alert type can have:
- **Multiple variations**: Different alerts for different amounts
- **Range alerts**: e.g., 100-500 bits shows alert A, 500+ shows alert B
- **Custom media**: GIFs, images, audio
- **Duration settings**: How long the alert displays
- **Skip option**: Disable without deleting

## Adding an Alert

1. Go to **Alerts** tab
2. Select the alert type (e.g., "Bits")
3. Click **"Add Alert"**
4. Configure:
   - **Amount/Range**: When this alert triggers
   - **GIF**: Visual element
   - **Audio**: Sound effect
   - **Duration**: Display time
5. Click **Save**

## Alert Priority

Alerts are matched in this order:
1. Exact amount match
2. Range match (most specific)
3. Default alert

## Tips

- Use shorter durations (3-5s) for frequent alerts
- Reserve longer, dramatic alerts for milestones
- [Test alerts](help:alert_testing) using the "Test" button before going live
- Configure [alert media](help:alert_media) (GIFs, sounds) for visual impact

> **Tip:** Start with simple alerts and gradually add [advanced configurations](help:alert_configuration) as you get comfortable.
        """,
        keywords=["alerts", "notifications", "follow", "sub", "bits", "raid"],
        related_topics=[
            "first_alert_setup",
            "alert_configuration",
            "alert_media",
            "alert_testing",
        ],
    ),
    "alert_configuration": HelpTopic(
        id="alert_configuration",
        title="Configuring Alert Settings",
        category=HelpCategory.ALERTS,
        summary="How to set up and customize individual alerts",
        content="""
# Configuring Alert Settings

Learn how to create, edit, and manage your alert configurations.
For a beginner walkthrough, see [Setting Up Your First Alert](help:first_alert_setup).

## Creating an Alert

### Basic Alert Setup
1. Navigate to the **Alerts** tab
2. Select an alert type from the dropdown
3. Click **"Add Alert"**
4. Fill in the configuration fields

### Configuration Fields

| Field | Description | Example |
|-------|-------------|---------|
| **Name** | Display name for the alert | "Big Bits Alert" |
| **Amount** | Trigger amount or range | "500" or "100-999" |
| **GIF Path** | Path to animation file | `/assets/alerts/bits/big_cheer.gif` |
| **Audio Path** | Path to sound file | `/assets/alerts/bits/cheer.mp3` |
| **Duration** | Display time in seconds | "5.0" |
| **Volume** | Audio volume (0-100) | "80" |

## Range Alerts

Use ranges for tiered alerts:

```
Amount: 1-99     → Small cheer
Amount: 100-499  → Medium cheer
Amount: 500+     → Big celebration
```

## Advanced Settings

### Audio Randomization
Add variety to repetitive alerts:
- **Randomized Audio**: Enable random sound selection
- **Randomized Directory**: Folder containing audio files
- **Random Chance**: Percentage chance to play random audio

### Extra Rare Audio
For special milestone sounds:
- **Extra Rare Directory**: Rare sound effects
- **Extra Rare Chance**: Low percentage for surprise sounds

## Managing Alerts

### Editing Alerts
- Click the edit icon next to any alert
- Modify settings as needed
- Click **Save** to apply changes

### Deleting Alerts
- Click the delete icon next to the alert
- Confirm deletion in the dialog

### Reordering Alerts
- Drag and drop alerts to change priority
- Higher alerts override lower ones for the same trigger

### Testing Alerts
- Use the **"Test"** button to preview alerts
- Test with different amounts to see range matching
- Verify audio plays and GIF animates correctly

## Best Practices

### Alert Duration
- **Frequent events** (follows): 3-5 seconds
- **Milestone events** (1000 bits): 8-12 seconds
- **Rare events** (raids): 10-15 seconds

### File Organization
```
assets/
├── alerts/
│   ├── bits/
│   │   ├── small_cheer.gif
│   │   ├── medium_cheer.gif
│   │   ├── big_cheer.gif
│   │   ├── random/
│   │   │   ├── cheer1.mp3
│   │   │   ├── cheer2.mp3
│   │   │   └── cheer3.mp3
│   │   └── rare/
│   │       └── epic_cheer.mp3
```

### Performance
- Optimize GIFs for web (reduce colors/frame rate)
- Use MP3 format for audio (smaller than WAV)
- [Test alerts](help:alert_testing) during stream setup, not during live stream
- See [Alert Media Configuration](help:alert_media) for file format details

> **Warning:** Overlapping amount ranges can cause unexpected alert behavior. Always test range boundaries.
        """,
        keywords=["alert", "configuration", "setup", "settings", "range", "tier"],
        related_topics=["alerts_overview", "alert_media"],
    ),
    "alert_media": HelpTopic(
        id="alert_media",
        title="Alert Media Configuration",
        category=HelpCategory.ALERTS,
        summary="Setting up GIFs, audio, and randomization for alerts",
        content="""
# Alert Media Configuration

## GIF/Image Settings

Alerts can display animated GIFs or static images. See [Alert Configuration](help:alert_configuration)
for general setup and [Testing Alerts](help:alert_testing) to verify your media works.

### File Requirements
- **Formats**: GIF, PNG, JPEG, WebP
- **Recommended size**: 300x300 to 500x500 pixels
- **Location**: Place in `assets/alerts/{type}/` folder

### Configuration
```
GIF Directory: /assets/alerts/bits/
GIF Filename: my_animation.gif
```

## Audio Settings

### File Requirements
- **Formats**: MP3, WAV, OGG
- **Recommended length**: Match alert duration
- **Location**: Place in `assets/alerts/{type}/` folder

### Configuration
```
Audio Directory: /assets/alerts/bits/
Audio Filename: cheer_sound.mp3
Volume: 80 (0-100)
```

## Audio Randomization

Add variety to your alerts with random audio selection.

### Basic Randomization
1. Enable **"Randomized Audio"**
2. Set **Randomized Directory** to folder with multiple audio files
3. Set **Randomized Chance** (1-100%)

### Extra Rare Audio
For special surprise sounds:
1. Enable **"Extra Rare Randomization"**
2. Set **Extra Rare Directory**
3. Set **Extra Rare Chance** (lower = rarer)

### Example Setup
```
Static Audio: /assets/alerts/bits/default.mp3
Randomized: ON
Randomized Dir: /assets/alerts/bits/random/
Randomized Chance: 30%
Extra Rare: ON
Extra Rare Dir: /assets/alerts/bits/rare/
Extra Rare Chance: 5%
```

This means:
- 65% chance: Play `default.mp3`
- 30% chance: Play random file from `random/`
- 5% chance: Play random file from `rare/`

> **Tip:** If audio isn't playing, check the [Audio Troubleshooting](help:troubleshooting_audio) guide.
        """,
        keywords=["gif", "audio", "sound", "media", "randomization", "animation"],
        related_topics=["alerts_overview", "alert_configuration"],
    ),
    "alert_testing": HelpTopic(
        id="alert_testing",
        title="Testing Your Alerts",
        category=HelpCategory.ALERTS,
        summary="How to test alerts before going live",
        content="""
# Testing Your Alerts

Ensure your [alerts](help:alerts_overview) work correctly before your stream goes live.

> **Important:** Always test alerts before going live to avoid surprises during your stream.

## Test Button

Each [alert configuration](help:alert_configuration) has a **"Test"** button:

1. Go to **Alerts** tab
2. Select an alert type
3. Find the alert you want to test
4. Click the **Test** button

## What Happens During Test

- Alert appears in browser sources
- Audio plays (if configured)
- Alert follows normal timing and behavior
- Test results appear in Activity Feed

## Testing Checklist

### Basic Functionality
- [ ] Alert appears visually
- [ ] Audio plays at correct volume
- [ ] Alert duration is appropriate
- [ ] Alert disappears after duration

### Range Testing
- [ ] Test minimum range values
- [ ] Test maximum range values
- [ ] Test boundary values (e.g., exactly 100 bits)
- [ ] Verify correct alert triggers for each range

### Randomization Testing
- [ ] Test multiple times to verify randomization
- [ ] Check that rare sounds occasionally play
- [ ] Verify volume levels are consistent

## Common Issues

### Alert Doesn't Show
- Check [browser source URL](help:obs_setup) in OBS
- Verify Mycelian web server is running
- See [Troubleshooting Alerts](help:troubleshooting_alerts) for more solutions

### Audio Doesn't Play
- Check browser audio permissions
- Verify file paths are correct
- See [Audio Troubleshooting](help:troubleshooting_audio) for detailed solutions

### Wrong Alert Plays
- Check alert ordering (drag to reorder)
- Verify amount ranges don't overlap
- Test with exact amounts vs ranges

## Performance Testing

### Memory Usage
- Monitor OBS performance during tests
- Check for memory leaks with repeated tests
- Verify smooth playback on your hardware

### Network Impact
- Test during low-network conditions
- Monitor for lag or delays
- Verify large GIFs don't cause issues

## Advanced Testing

### Integration Testing
- Test alerts with real Twitch events (use test account)
- Verify connector triggers work with alerts
- Test alert queuing during high activity

### Stress Testing
- Trigger multiple alerts rapidly
- Test alert limits and queuing
- Verify system stability under load
        """,
        keywords=["test", "testing", "preview", "verify", "check", "debug"],
        related_topics=["alerts_overview", "alert_configuration"],
    ),
    # =========================================
    # Chatbot
    # =========================================
    "chatbot_commands": HelpTopic(
        id="chatbot_commands",
        title="Creating Chat Commands",
        category=HelpCategory.CHATBOT,
        summary="How to create and manage custom chat commands",
        content="""
# Creating Chat Commands

Custom commands let viewers trigger responses by typing `!command` in chat.
Use [variables](help:chatbot_variables) to make responses dynamic.

## Creating a Command

1. Go to **Chatbot** → **Commands** tab
2. Click **"Add Command"**
3. Configure:
   - **Name**: The trigger word (e.g., `socials`)
   - **Response**: What the bot says
   - **Cooldown**: Time between uses
   - **User Level**: Who can use it

## Response Variables

Use these placeholders in your response:

| Variable | Description | Example |
|----------|-------------|---------|
| `{username}` | User who triggered | "Hey StreamerName!" |
| `{channel}` | Channel name | "Welcome to StreamerChannel" |
| `{count}` | Times command used | "Used 42 times" |
| `{random:a,b,c}` | Random choice | "You got: b" |
| `{uptime}` | Stream uptime | "Live for 2h 30m" |
| `{game}` | Current game | "Playing Minecraft" |
| `{title}` | Stream title | "Chill stream today" |

## Example Commands

### Simple Response
```
Name: discord
Response: Join our Discord: https://discord.gg/example
```

### Dynamic Response
```
Name: hug
Response: {username} gives {touser} a big hug! 🤗
```

### Counter Command
```
Name: deaths
Response: Deaths this stream: {count}
Type: Counter
```

### Random Response
```
Name: magic8ball
Response: 🎱 {random:Yes!,No way,Ask again,Maybe,Definitely}
```

## User Levels

- **Everyone**: All viewers
- **Subscriber**: Subs only
- **VIP**: VIPs and above
- **Moderator**: Mods only
- **Broadcaster**: You only

## Cooldowns

- **Global Cooldown**: Time before anyone can use again
- **User Cooldown**: Time before same user can use again

> **Tip:** Pair commands with [Events](help:chatbot_events) and [Connectors](help:connectors_intro) for powerful automation workflows.
        """,
        keywords=["commands", "chat", "bot", "custom", "response", "variables"],
        related_topics=[
            "chatbot_events",
            "chatbot_quotes",
            "chatbot_greetings",
            "chatbot_giveaways",
            "chatbot_variables",
        ],
    ),
    "chatbot_events": HelpTopic(
        id="chatbot_events",
        title="Chatbot Events and Automation",
        category=HelpCategory.CHATBOT,
        summary="Automated chatbot responses for stream events",
        content="""
# Chatbot Events and Automation

Set up automatic chatbot responses for stream events. For chat commands,
see [Creating Chat Commands](help:chatbot_commands).

## Event Types

### Stream Events
- **Stream Start**: When you go live
- **Stream End**: When stream ends
- **Game Change**: When you change games
- **Title Change**: When you update title

### Viewer Events
- **New Follower**: Automatic welcome message
- **New Subscriber**: Welcome new subs
- **Resubscriber**: Welcome returning subs
- **Gift Sub**: Thank gift givers
- **Raid**: Welcome raiders

### Chat Events
- **First Message**: Welcome new chatters
- **Return Message**: Welcome returning viewers

## Creating Event Responses

1. Go to **Chatbot** → **Events** tab
2. Select an event type
3. Click **"Add Response"**
4. Configure the message and conditions

## Event Variables

Use these in your event responses:

| Variable | Description | Example |
|----------|-------------|---------|
| `{username}` | Event user | "Welcome {username}!" |
| `{channel}` | Your channel | "Thanks for joining {channel}" |
| `{months}` | Sub months | "Thanks for {months} months!" |
| `{giftcount}` | Gift subs | "Gifted {giftcount} subs!" |
| `{raidercount}` | Raid size | "Raided with {raidercount} viewers!" |
| `{game}` | New game | "Now playing {game}" |

## Example Event Messages

### Welcome Follower
```
"Thank you {username} for the follow! Welcome to the community! 🎉"
```

### Subscriber Welcome
```
"Thank you {username} for subscribing! You've been subscribed for {months} months! ⭐"
```

### Raid Welcome
```
"Thank you {username} for the raid with {raidercount} viewers! Everyone say hi! 👋"
```

### Stream Start
```
"Stream is now live! Thanks for joining everyone! 🚀"
```

## Advanced Features

### Conditional Responses
Set up different responses based on conditions:

- **Time-based**: Different messages during different hours
- **Count-based**: Special messages for milestones
- **User-based**: Different messages for regulars vs new viewers

### Response Priority
- Multiple responses can be set for the same event
- Set priority levels to control which response plays
- Use random selection for variety

### Cooldowns and Limits
- Prevent spam with cooldown settings
- Limit responses per user per stream
- Set maximum responses per hour

## Best Practices

### Welcome Messages
- Keep them friendly and welcoming
- Personalize with username variables
- Don't overwhelm new viewers

### Milestone Celebrations
- Make them exciting and special
- Use appropriate emotes
- Coordinate with alert animations

### Community Building
- Use events to build community
- Encourage interaction
- Share community guidelines

> **Note:** Use [chatbot variables](help:chatbot_variables) like `{username}` and `{months}` to personalize event messages. You can also set up [greetings](help:chatbot_greetings) for first-time chatters.
        """,
        keywords=["events", "automation", "welcome", "messages", "responses"],
        related_topics=["chatbot_commands", "chatbot_quotes"],
    ),
    "chatbot_quotes": HelpTopic(
        id="chatbot_quotes",
        title="Managing Quotes and Highlights",
        category=HelpCategory.CHATBOT,
        summary="How to collect and manage memorable chat moments",
        content="""
# Managing Quotes and Highlights

Capture and share memorable moments from your chat. Quotes work alongside
[commands](help:chatbot_commands) and [events](help:chatbot_events).

## What are Quotes?

Quotes are saved chat messages that capture funny, interesting, or memorable moments.

## Adding Quotes

### Manual Addition
1. Go to **Chatbot** → **Quotes** tab
2. Click **"Add Quote"**
3. Enter the quote text
4. Add context (game, date, etc.)
5. Save the quote

### Automatic Collection
Enable automatic quote collection:
1. Go to **Chatbot** settings
2. Enable **"Auto-collect quotes"**
3. Set trigger keywords (e.g., "quote", "save")
4. Viewers can type `!quote [message]` to save

## Quote Commands

### Random Quote
```
!quote
```
Returns a random saved quote.

### Specific Quote
```
!quote 5
```
Returns quote number 5.

### Search Quotes
```
!quote search funny
```
Finds quotes containing "funny".

## Managing Quotes

### Editing Quotes
- Click edit icon next to any quote
- Modify text, context, or tags
- Update quote information

### Deleting Quotes
- Click delete icon next to quote
- Confirm deletion

### Organizing Quotes
- Add **tags** for categorization
- Add **context** (game, date, event)
- **Favorite** important quotes

## Best Practices

### Collection Guidelines
- Ask permission before saving personal quotes
- Focus on positive/funny moments
- Include context for better understanding

### Moderation
- Review quotes before making public
- Remove inappropriate content
- Respect viewer privacy

### Community Engagement
- Use quotes in streams for laughs
- Create "quote of the week" segments
- Let viewers vote on favorite quotes

## Quote Variables

Use in custom commands:

| Variable | Description |
|----------|-------------|
| `{quote}` | Random quote |
| `{quote:5}` | Specific quote number |
| `{quote:tag}` | Random quote with tag |

## Example Uses

### Quote Command
```
Name: quote
Response: 💬 "{quote}"
```

### Tagged Quote
```
Name: dadjoke
Response: 😂 Dad joke of the day: "{quote:dadjoke}"
```

### Quote of the Stream
```
Name: qots
Response: Quote of the stream: "{quote:qots}"
```
        """,
        keywords=["quotes", "highlights", "memorable", "moments", "save"],
        related_topics=["chatbot_commands", "chatbot_events"],
    ),
    "chatbot_greetings": HelpTopic(
        id="chatbot_greetings",
        title="Chatbot Greetings",
        category=HelpCategory.CHATBOT,
        summary="Automatically greet viewers when they join chat",
        content="""
# Chatbot Greetings

Set up automatic greetings to welcome viewers when they first chat.
Use [variables](help:chatbot_variables) to personalize your greeting messages.

## What are Greetings?

Greetings are automatic messages sent when a viewer sends their first
message in a stream session.

## Setting Up Greetings

### Enable Greetings
1. Go to **Chatbot** → **Greetings** tab
2. Toggle **"Enable Greetings"** to ON
3. Configure the greeting message

### Greeting Message
Customize what the bot says:
```
Welcome to the stream, {user}! Enjoy your stay!
```

### Available Variables
- `{user}` - The viewer's username
- `{channel}` - Your channel name
- `{game}` - Current game being played
- `{uptime}` - How long you've been live

## Greeting Options

### First-Time Viewers
Special greetings for new viewers:
```
Hey {user}! Welcome to your first time here! 🎉
```

### Returning Viewers
Different message for regulars:
```
Welcome back, {user}! Great to see you again!
```

### VIP/Subscriber Greetings
Special treatment for supporters:
```
VIP alert! {user} is in the house! 👑
```

## Cooldown Settings

Prevent spam with cooldowns:
- **Global Cooldown**: Minimum time between any greetings
- **Per-User Cooldown**: Time before same user is greeted again
- **Session-based**: Only greet once per stream session

## Best Practices

### Keep It Short
- Brief, friendly messages work best
- Don't spam chat with long greetings

### Be Authentic
- Match your stream's personality
- Don't be overly formal if you're casual

### Consider Timing
- Set appropriate cooldowns
- Don't greet during intense moments

## Troubleshooting

### Greetings Not Sending
- Verify greetings are enabled
- Check bot has chat permissions
- Verify [Twitch connection](help:twitch_setup) is active

### Too Many Greetings
- Increase cooldown timers
- Enable session-based greeting
- Limit to first message only
        """,
        keywords=["greetings", "welcome", "hello", "chat", "viewers", "automatic"],
        related_topics=["chatbot_commands", "chatbot_events", "chatbot_variables"],
    ),
    "chatbot_giveaways": HelpTopic(
        id="chatbot_giveaways",
        title="Chatbot Giveaways",
        category=HelpCategory.CHATBOT,
        summary="Keyword-based giveaways, draws, and winner announcements",
        content="""
# Chatbot Giveaways

Run giveaways from **Chatbot → Giveaways**. Viewers enter by typing a phrase you
configure; you draw winners and the bot posts a **Twitch chat announcement**
(as the chatbot account). See [Twitch Integration](help:integrations_twitch) for chatbot permissions.

## Active giveaway

Chat entries are collected only when **both** are true:

1. You set a **non-empty entry keyword** in the UI.
2. You click **Start giveaway** (accepting entries is on).

If the keyword is empty, **no** chat messages are counted as entries—even if
Start was pressed. Clearing the keyword turns accepting off automatically.

**Stop accepting** ends the active phase but keeps your keyword and pool until
you change them.

## Typical workflow

1. Configure options (below), set **Entry keyword** to the exact line viewers
   should type (match is the **full message**, case-insensitive).
2. Click **Start giveaway**.
3. Viewers type the keyword to enter; the **pool** lists every ticket.
4. Click **Draw winners**. The app picks up to **Number of winners per draw**,
   sends **one** announcement, and updates statistics.
5. The pool is **not** cleared by a draw—you can draw again from the same
   entries, or use **Clear giveaway** to reset.

## Buttons

| Button | What it does |
|--------|----------------|
| **Start giveaway** | Turns on accepting entries (requires keyword). Does not clear the pool. |
| **Stop accepting** | Turns off accepting; keyword and pool stay. |
| **Draw winners** | Picks winners, sends announcement, records stats. **Does not** clear pool or stop accepting. |
| **Clear giveaway** | Empties the pool, clears the keyword, stops accepting. |
| **Refresh** | Reloads this panel from saved settings. |

## Settings (detailed)

### Entry keyword

- The viewer’s message must **equal** this text after trimming, **case-insensitive**.
- The whole line is the keyword (not a substring).
- Messages that start with `!` are handled as **commands first**; if a command
  matches, the message is **not** treated as a giveaway entry.

### No duplicate entries

When enabled, each Twitch user ID can only have **one** ticket in the pool at
a time. When off, the same user can stack multiple entries (better odds per
ticket).

### Unique winners per draw

When **Number of winners per draw** is greater than 1:

- **On:** each user can win at most **one slot** in that single draw (weighted
  by tickets for the first slot only).
- **Off:** `random.sample` over tickets can pick the same user multiple times
  if they hold multiple tickets.

### Number of winners per draw

How many names are picked each time you click **Draw winners** (1–100).

### Exclude moderators / Exclude VIPs

Uses Twitch chat badges on the message (`moderator/…`, `vip/…`). Matching users
cannot enter.

### Blocked usernames

List of logins (one per line or comma-separated), stored lowercase. Those users
cannot enter.

### Winning announcement message

Sent as a **chat announcement** (highlight), not a normal chat line, using the
**chatbot** OAuth token when a dedicated bot is connected.

Placeholders (both expand to the **same** text):

- **`{winners}`** — all winner display names for this draw, comma-separated.
- **`{winner}`** — same as `{winners}` (for older templates).

Example:

`🎉 Winners: {winners} — thanks for entering!`

## Announcements and permissions

- The bot needs **`moderator:manage:announcements`** on the broadcaster’s chat
  (see Twitch chatbot setup docs).
- **Dedicated chatbot account:** announcements appear as that moderator.
- **Fallback mode** (no separate bot): the app may send announcements via the
  main account path—use a dedicated bot for strict “always the bot” behavior.

## Statistics

Tracked over time:

- **Giveaways completed** — each successful **Draw winners** click (after the
  announcement sends).
- **Total giveaway entries** — each successful pool add.
- **Average entries per giveaway** — total entries ÷ giveaways completed.
- **Per-user wins** — how many times each display name has been drawn.

Summaries appear on the Giveaways tab; the **Statistics** tab can show totals
as well.

## Troubleshooting

| Problem | Things to check |
|---------|----------------|
| No one enters | Active giveaway (keyword + Start), keyword spelling, exclusions (mod/VIP/blocklist), duplicate-entries rule. |
| Draw does nothing | Empty pool; check notification message. |
| No announcement | Chatbot connected; Helix announcement scopes; see logs. |
| Same people win again | Expected until **Clear giveaway**—draws do not remove tickets. |

> **Tip:** Use [chatbot variables](help:chatbot_variables) like `{winners}` in your announcement message for dynamic winner names.
        """,
        keywords=[
            "giveaway",
            "giveaways",
            "contest",
            "winner",
            "raffle",
            "chatbot",
            "announcement",
        ],
        related_topics=[
            "chatbot_commands",
            "chatbot_variables",
            "integrations_twitch",
        ],
    ),
    "chatbot_variables": HelpTopic(
        id="chatbot_variables",
        title="Chatbot Variables",
        category=HelpCategory.CHATBOT,
        summary="Dynamic variables for commands, alerts, and messages",
        content="""
# Chatbot Variables

Variables let you insert dynamic content into [commands](help:chatbot_commands),
[alerts](help:alerts_overview), and messages.

## What are Variables?

Variables are placeholders that get replaced with actual values when displayed.
For example, `{user}` becomes the actual username.

## Common Variables

### User Variables
| Variable | Description | Example Output |
|----------|-------------|----------------|
| `{user}` | Username | "StreamerFan42" |
| `{displayname}` | Display name | "Streamer Fan 42" |
| `{userid}` | User ID | "123456789" |

### Channel Variables
| Variable | Description | Example Output |
|----------|-------------|----------------|
| `{channel}` | Channel name | "YourChannel" |
| `{game}` | Current game | "Minecraft" |
| `{title}` | Stream title | "Chill vibes!" |
| `{uptime}` | Stream duration | "2h 30m" |

### Event Variables
| Variable | Description | Example Output |
|----------|-------------|----------------|
| `{amount}` | Bits/donation amount | "500" |
| `{months}` | Sub months | "12" |
| `{tier}` | Sub tier | "Tier 3" |
| `{message}` | User's message | "Hello!" |
| `{viewers}` | Raid viewer count | "50" |

### Counter Variables
| Variable | Description | Example Output |
|----------|-------------|----------------|
| `{count}` | Command use count | "42" |
| `{deaths}` | Death counter | "7" |
| `{wins}` | Win counter | "15" |

### Time Variables
| Variable | Description | Example Output |
|----------|-------------|----------------|
| `{time}` | Current local time (24-hour with seconds) | "19:30:45" |
| `{time.hh:mm.12.ampm}` | Filtered time (filters in any order) | "07:30 PM" |
| `{time.UTC.tz}` | Time in UTC with zone label | "00:30:45 UTC" |
| `{date}` | Current date | "Jan 19, 2026" |
| `{countdown:TARGET}` | Time until target | "2h 15m" |

Time filters (dot-separated, any order): `12`/`24`, `ampm`/`noampm`, `sec`/`nosec`, `tz`, layout tokens (`hh:mm`, `hh:mm:ss`, …), and zone codes (`UTC`, `EST`, `PST`, …).

## Using Variables

### In Commands
```
!followage
Response: "{user} has been following for {followage}!"
```

### In Alerts
```
Alert Text: "Thanks for the {amount} bits, {user}!"
```

### In Greetings
```
"Welcome {user}! We're playing {game} today!"
```

## Custom Variables

### Creating Custom Variables
1. Go to **Chatbot** → **Variables**
2. Click **"Add Variable"**
3. Set name and value
4. Use in commands with `{variablename}`

### Dynamic Values
Some variables can be set via commands:
```
!setvar mood happy
```
Then use `{mood}` in other commands.

## Variable Formatting

### Text Transformation
- `{user:upper}` - UPPERCASE
- `{user:lower}` - lowercase
- `{user:title}` - Title Case

### Number Formatting
- `{amount:comma}` - 1,000
- `{amount:currency}` - $10.00

## Troubleshooting

### Variable Not Replacing
- Check spelling matches exactly
- Verify variable is supported for that context
- Use curly braces `{}` not parentheses

### Wrong Value Displayed
- Some variables are event-specific
- Check if variable applies to current context
- Verify data source is connected

> **Note:** Variables available in [greetings](help:chatbot_greetings) and [events](help:chatbot_events) differ from those in [commands](help:chatbot_commands). Check the variable table for each context.
        """,
        keywords=[
            "variables",
            "placeholders",
            "dynamic",
            "text",
            "formatting",
            "custom",
        ],
        related_topics=["chatbot_commands", "chatbot_greetings", "chatbot_events"],
    ),
    # =========================================
    # Connectors
    # =========================================
    "connectors_intro": HelpTopic(
        id="connectors_intro",
        title="Connector System Introduction",
        category=HelpCategory.CONNECTORS,
        summary="Automate actions with the powerful connector system",
        content="""
# Connector System

Connectors let you create automated workflows: "When X happens, do Y."
See [Connector Examples](help:connector_examples) for ready-to-use templates.

## Components

### Triggers
Events that start a connector:
- **Twitch Events**: Follows, subs, bits, raids, points
- **Chat Messages**: Specific messages or patterns
- **Timers**: Scheduled intervals
- **Hotkeys**: Keyboard shortcuts

### Conditions (Optional)
Requirements that must be met:
- Minimum amounts
- User levels
- Time restrictions
- Random chance

### Actions
What happens when triggered:
- Send chat message
- Play alert
- Control templates
- Make API calls
- Run programs

## Creating a Connector

1. Go to **Connectors** tab
2. Click **"New Connector"**
3. Add a **Trigger** (required)
4. Add **Conditions** (optional)
5. Add **Actions** (required)
6. Enable and save

## Example: Welcome Raiders

```
Trigger: Twitch Raid
Condition: Raider count >= 10
Actions:
  1. Send Chat: "Welcome raiders from {raider}! 🎉"
  2. Play Alert: raid_special
  3. Template Control: counter_increment (raid count)
```

## Example: Bit Milestone

```
Trigger: Twitch Bits
Condition: Total bits >= 1000
Actions:
  1. Send Chat: "{username} just hit 1000 bits! Unlocking special emote!"
  2. Template Control: unlock_emote
```

## Tips

- Start simple, add complexity later
- Test thoroughly before going live
- Use [conditions](help:connector_conditions) to prevent spam
- Chain multiple [actions](help:connector_actions) for impact

> **Tip:** Browse [Connector Examples](help:connector_examples) for inspiration and ready-to-use workflow templates.
        """,
        keywords=["connectors", "automation", "triggers", "actions", "workflow"],
        related_topics=[
            "connector_triggers",
            "connector_conditions",
            "connector_actions",
            "connector_examples",
        ],
    ),
    "connector_triggers": HelpTopic(
        id="connector_triggers",
        title="Connector Triggers",
        category=HelpCategory.CONNECTORS,
        summary="Available trigger types and how to configure them",
        content="""
# Connector Triggers

Triggers are the "when" part of connectors - they start the automation.
See also: [Actions](help:connector_actions) and [Conditions](help:connector_conditions).

## Twitch Event Triggers

### Follow Trigger
- **Event**: New follower
- **Variables**: `{username}`, `{channel}`
- **Use case**: Welcome new followers

### Subscription Triggers
- **Sub**: New subscription
- **Resub**: Returning subscriber
- **Gift Sub**: Gifted subscription
- **Variables**: `{username}`, `{months}`, `{tier}`, `{giftcount}`

### Bits Trigger
- **Event**: Bit cheer
- **Variables**: `{username}`, `{amount}`, `{message}`
- **Conditions**: Minimum amount, specific amounts

### Raid Trigger
- **Event**: Incoming raid
- **Variables**: `{username}`, `{raidercount}`, `{channel}`
- **Use case**: Welcome raiders, trigger celebrations

### Channel Points Trigger
- **Event**: Point redemption
- **Variables**: `{username}`, `{rewardname}`, `{cost}`
- **Conditions**: Specific rewards

## Chat Triggers

### Message Pattern
- **Trigger**: Messages matching pattern
- **Patterns**: Exact text, contains, regex
- **Example**: "hello" triggers welcome response

### Command Trigger
- **Trigger**: Custom command usage
- **Variables**: `{username}`, `{args}`, `{count}`

## Timer Triggers

### Interval Timer
- **Trigger**: Every X minutes/hours
- **Use case**: Periodic announcements, reminders

### Scheduled Timer
- **Trigger**: At specific times
- **Format**: HH:MM, daily/weekly

## Hotkey Triggers

### Keyboard Shortcut
- **Trigger**: Key combination pressed
- **Use case**: Manual triggers, emergency buttons

## Advanced Triggers

### Combined Triggers
Multiple conditions must be met:
- AND: All conditions required
- OR: Any condition sufficient

### Threshold Triggers
- **Accumulated**: Trigger after X events
- **Time Window**: Events within time period
- **Example**: 10 follows in 5 minutes

## Trigger Configuration

### Basic Setup
1. Select trigger type
2. Configure specific settings
3. Set conditions (optional)
4. Add actions

### Variables and Context
Each trigger provides context variables for use in actions.

### Testing Triggers
- Use test mode to verify triggers work
- Monitor logs for trigger activation
- Test edge cases and error conditions
        """,
        keywords=["triggers", "events", "twitch", "chat", "timer", "hotkey"],
        related_topics=["connectors_intro", "connector_actions"],
    ),
    "connector_actions": HelpTopic(
        id="connector_actions",
        title="Connector Actions",
        category=HelpCategory.CONNECTORS,
        summary="Available actions and how to configure them",
        content="""
# Connector Actions

Actions are what happens when a connector [trigger](help:connector_triggers) activates.
Add [conditions](help:connector_conditions) to control when actions execute.

## Chat Actions

### Send Message
- **Action**: Send chat message
- **Variables**: Use trigger variables in message
- **Example**: "Welcome {username} to the stream!"

### Whisper User
- **Action**: Send private message
- **Use case**: Private welcomes, instructions

## Alert Actions

### Play Alert
- **Action**: Trigger specific alert
- **Configuration**: Select alert by name
- **Use case**: Custom alert responses

### Custom Alert
- **Action**: Create dynamic alert
- **Configuration**: Text, image, sound
- **Variables**: Include trigger data

## Template Actions

### Control Templates
- **Action**: Update template variables
- **Examples**:
  - Increment counters
  - Show/hide elements
  - Update text displays

### Reset Template
- **Action**: Reset template to default state
- **Use case**: End of stream cleanup

## Audio Actions

### Play Sound
- **Action**: Play audio file
- **Configuration**: File path, volume
- **Use case**: Sound effects, music

### Stop Audio
- **Action**: Stop currently playing audio
- **Use case**: Interrupt music for alerts

## OBS Actions

### Scene Switch
- **Action**: Change OBS scene
- **Configuration**: Scene name
- **Use case**: Automatic scene transitions

### Source Control
- **Action**: Show/hide/toggle sources
- **Use case**: Dynamic overlays

### Recording Control
- **Action**: Start/stop recording
- **Use case**: Automatic recording triggers

## External Actions

### Run Program
- **Action**: Execute external program
- **Configuration**: Command, arguments
- **Use case**: Launch applications, scripts

### HTTP Request
- **Action**: Make web request
- **Configuration**: URL, method, data
- **Use case**: Webhooks, API calls

### File Operations
- **Action**: Create, modify, delete files
- **Use case**: Log events, update configs

### Game Hook (memory write)
- **Action**: Game Hook (memory write) — see [Game Hooks](help:game_hooks) for supported games, live data, and each write operation in plain language
- **Use case**: Crowd control on your own single-player session (e.g. add gil when channel points are redeemed)

## Advanced Actions

### Conditional Actions
Execute different actions based on conditions:
- **If/Else**: Different paths based on variables
- **Switch**: Multiple outcomes

### Delayed Actions
- **Delay**: Wait before executing
- **Sequence**: Chain actions with timing

### Looping Actions
- **Repeat**: Execute multiple times
- **Until**: Continue until condition met

## Action Configuration

### Variable Substitution
Use trigger variables in action parameters:
```
Message: "Thanks {username} for {amount} bits!"
```

### Error Handling
- **Retry**: Retry failed actions
- **Fallback**: Alternative action on failure
- **Timeout**: Maximum execution time

### Testing Actions
- Test individual actions
- Verify variable substitution
- Check error handling

## Best Practices

### Keep it Simple
- Start with single actions
- Add complexity gradually
- Test each addition

### Error Prevention
- Add conditions to prevent spam
- Use timeouts for external actions
- Implement fallbacks

### Performance
- Avoid resource-intensive actions
- Use delays for smooth execution
- Monitor system impact
- See [Performance Optimization](help:troubleshooting_performance) if actions cause lag
        """,
        keywords=["actions", "responses", "automation", "effects", "controls"],
        related_topics=["connectors_intro", "connector_triggers", "game_hooks"],
    ),
    "connector_conditions": HelpTopic(
        id="connector_conditions",
        title="Connector Conditions",
        category=HelpCategory.CONNECTORS,
        summary="Add conditional logic to your connector workflows",
        content="""
# Connector Conditions

Conditions let you add logic to [connectors](help:connectors_intro) so [actions](help:connector_actions)
only run when specific criteria are met.

## What are Conditions?

Conditions are filters that evaluate to true or false. When a trigger fires,
conditions are checked before actions run.

## Adding Conditions

### To an Existing Connector
1. Open the connector for editing
2. Click **"Add Condition"**
3. Select condition type
4. Configure the parameters
5. Save the connector

## Condition Types

### Value Comparisons
Compare numbers or text:

| Operator | Description | Example |
|----------|-------------|---------|
| Equals | Exact match | bits = 100 |
| Not Equals | Different value | tier ≠ 1 |
| Greater Than | Larger number | amount > 50 |
| Less Than | Smaller number | viewers < 10 |
| Contains | Text includes | message contains "hello" |

### User Conditions
Filter by user attributes:
- **Is Subscriber**: Only for subs
- **Is VIP**: Only for VIPs
- **Is Moderator**: Only for mods
- **Is Broadcaster**: Only for you
- **Username Equals**: Specific user

### Time Conditions
Filter by time:
- **Stream Uptime**: After X minutes of streaming
- **Time of Day**: During specific hours
- **Day of Week**: On specific days
- **Cooldown**: Time since last trigger

### Event Conditions
Filter by event properties:
- **First-time Event**: First sub, first donation, etc.
- **Gift Sub**: Is this a gifted subscription
- **Anonymous**: Is the user anonymous

## Combining Conditions

### AND Logic
All conditions must be true:
```
Trigger: Bits received
Condition 1: Amount >= 100
Condition 2: User is subscriber
→ Only runs for subs cheering 100+ bits
```

### OR Logic
Any condition can be true:
```
Trigger: Subscription
Condition: Tier = 2 OR Tier = 3
→ Runs for Tier 2 or Tier 3 subs
```

## Example Conditions

### VIP-Only Shoutout
```
Trigger: Chat command "!so"
Condition: User is Moderator OR User is Broadcaster
Action: Send shoutout message
```

### Big Donation Alert
```
Trigger: Donation received
Condition: Amount >= 50
Action: Play special celebration
```

### Prime Time Greeting
```
Trigger: First chat message
Condition: Time between 7PM and 11PM
Action: Send evening greeting
```

## Troubleshooting

### Condition Never Passes
- Check operator (= vs >=)
- Verify value format (number vs text)
- Test with simpler conditions first

### Wrong Condition Evaluated
- Check condition order
- Verify AND/OR logic
- Review condition parameters

### Condition Always Passes
- Check for typos in values
- Verify comparison is correct
- Ensure condition is enabled
        """,
        keywords=["conditions", "logic", "filter", "if", "when", "compare"],
        related_topics=["connectors_intro", "connector_triggers", "connector_actions"],
    ),
    "connector_examples": HelpTopic(
        id="connector_examples",
        title="Connector Examples",
        category=HelpCategory.CONNECTORS,
        summary="Ready-to-use connector templates and inspiration",
        content="""
# Connector Examples

Learn from these real-world connector examples you can recreate. For setup
basics, see the [Connector Introduction](help:connectors_intro).

## Alert Enhancement

### Raid Welcome Package
Create a special experience for raiders:
```
Trigger: Raid received
Conditions: Viewers >= 10
Actions:
  1. Play raid sound effect
  2. Send chat message: "Welcome raiders from {raider}!"
  3. Wait 2 seconds
  4. Change scene to "Raid Welcome"
  5. Wait 10 seconds
  6. Return to main scene
```

### Milestone Celebration
Celebrate subscription milestones:
```
Trigger: Resub received
Conditions: Months = 12 OR Months = 24 OR Months = 36
Actions:
  1. Play anniversary sound
  2. Show special anniversary overlay
  3. Send chat: "🎉 {user} celebrates {months} months!"
```

## Chat Automation

### Auto-Shoutout on Raid
```
Trigger: Raid received
Actions:
  1. Wait 5 seconds
  2. Send chat: "/shoutout {raider}"
  3. Send chat: "Check out {raider} at twitch.tv/{raider}!"
```

### Hydration Reminder
```
Trigger: Timer (every 30 minutes)
Conditions: Stream uptime > 30 minutes
Actions:
  1. Send chat: "💧 Hydration check! Drink some water!"
```

### Lurk Response
```
Trigger: Chat command "!lurk"
Actions:
  1. Send chat: "Enjoy the lurk, {user}! See you when you're back!"
```

## Donation Responses

### Tip Jar Tiers
Small donation:
```
Trigger: Donation received
Conditions: Amount >= 1 AND Amount < 5
Actions:
  1. Play "coin" sound
  2. Show small thank you message
```

Medium donation:
```
Trigger: Donation received
Conditions: Amount >= 5 AND Amount < 20
Actions:
  1. Play "register" sound
  2. Show animated thank you
  3. Send chat thank you
```

Large donation:
```
Trigger: Donation received
Conditions: Amount >= 20
Actions:
  1. Play celebration sound
  2. Show full-screen celebration
  3. Send chat: "HUGE thanks to {user}!"
  4. Add to donation leaderboard
```

## Subscriber Perks

### Sub Sound Request
```
Trigger: Channel point redeem "Sound Request"
Conditions: User is Subscriber
Actions:
  1. Play requested sound from library
  2. Send confirmation message
```

### Tier 3 VIP Treatment
```
Trigger: Subscription received
Conditions: Tier = 3
Actions:
  1. Play Tier 3 fanfare
  2. Show special Tier 3 alert
  3. Send chat: "👑 {user} joins the Tier 3 royalty!"
```

## Interactive Elements

### Death Counter
```
Trigger: Chat command "!death"
Conditions: User is Moderator OR User is Broadcaster
Actions:
  1. Increment death counter
  2. Update death overlay
  3. Send chat: "Death #{count} 💀"
```

### Hype Meter
```
Trigger: Bits received
Actions:
  1. Add {amount} to hype meter
  2. Check if threshold reached
  3. If threshold: Trigger celebration
```

## Tips for Creating Connectors

### Start Simple
- Begin with one trigger, one action
- Add complexity gradually
- Test each step

### Use Delays Wisely
- Add pauses between actions
- Give time for overlays to display
- Don't spam chat

### Consider Edge Cases
- What if triggered rapidly?
- What about anonymous users?
- Handle missing data gracefully

> **Tip:** Start with the simpler examples above and combine [triggers](help:connector_triggers), [conditions](help:connector_conditions), and [actions](help:connector_actions) as you gain confidence.
        """,
        keywords=[
            "examples",
            "templates",
            "recipes",
            "ideas",
            "use cases",
            "workflows",
        ],
        related_topics=[
            "connectors_intro",
            "connector_triggers",
            "connector_actions",
            "connector_conditions",
        ],
    ),
    # =========================================
    # Templates
    # =========================================
    "templates_intro": HelpTopic(
        id="templates_intro",
        title="Template System Overview",
        category=HelpCategory.TEMPLATES,
        summary="Understanding browser source templates and customization",
        content="""
# Template System Overview

Templates are browser-based overlays that display on your stream.

## What are Templates?

Templates are HTML/CSS/JavaScript files served by Mycelian that you add to
[OBS as browser sources](help:obs_setup).

## Available Templates

| Template | Purpose |
|----------|---------|
| **Alerts** | Shows alert notifications |
| **Chat** | Displays chat messages |
| **Activity Feed** | Recent activity log |
| **Sub Bar** | Subscriber milestone tracker |
| **Bit Bar** | Bit cheer progress bar |
| **Title** | Stream title display |
| **Counter** | Custom counter display |
| **Custom (Spore Studio)** | User-built overlays edited in [Spore Studio](help:spore_studio_overview) |

## Visual Template Editor (Spore Studio)

Use the **Spore Studio** main tab to design custom browser sources visually: drag blocks,
wire [event bindings](help:spore_studio_bindings), set up [counters](help:spore_studio_counters) and
[data displays](help:spore_studio_data_sources), add [Stream Deck actions](help:spore_studio_streamdeck)
and [dynamic controls](help:spore_studio_dynamic_controls), then **Save** to generate HTML and JSON configs.
See [Spore Studio Overview](help:spore_studio_overview) for the full workflow.

## Adding Templates to OBS

1. Copy template URL from Mycelian
2. Create new **Browser Source** in OBS
3. Paste URL and configure settings
4. Position and scale as needed

## Template URLs

All templates are served at `http://localhost:5000/`:

```
Alerts:      http://localhost:5000/alerts
Chat:        http://localhost:5000/chat
Activity:    http://localhost:5000/activity_feed
Sub Bar:     http://localhost:5000/subbar
Bit Bar:     http://localhost:5000/bitbar
Title:       http://localhost:5000/title
Counter:     http://localhost:5000/counter
```

## Customization

### Template Configs
Each template has a JSON configuration file in `templates/template_configs/`.

### Live Controls
Some templates support real-time control through the Source Controls tab.

### CSS Customization
Modify template appearance with custom CSS in the template configuration.

## Template Variables

Templates use variables for dynamic content:

| Variable | Description |
|----------|-------------|
| `{username}` | User who triggered event |
| `{amount}` | Bits, subs, etc. |
| `{message}` | Chat message or cheer |
| `{count}` | Running totals |

## WebSocket Integration

Templates connect via WebSocket for real-time updates:

```javascript
const socket = io('http://localhost:5000');

socket.on('alert', (data) => {
    // Handle alert data
});
```

## Best Practices

### Performance
- Use appropriate resolutions
- Optimize images and animations
- Enable browser source shutdown when not visible

### Positioning
- Place alerts in corners or dedicated areas
- Ensure readability at stream resolution
- Test on actual stream layout

### Testing
- Preview templates before going live
- Test with real data
- Verify on different screen sizes

> **Tip:** Learn about [WebSocket events](help:template_websocket) to understand how templates receive live data, use [Source Controls](help:source_controls) during your stream, and build new overlays in [Spore Studio](help:spore_studio_overview).
        """,
        keywords=[
            "templates",
            "browser sources",
            "overlays",
            "obs",
            "customization",
            "spore studio",
        ],
        related_topics=[
            "template_configuration",
            "template_custom_css",
            "template_websocket",
            "source_controls",
            "spore_studio_overview",
            "spore_studio_counters",
            "spore_studio_dynamic_controls",
        ],
    ),
    "template_configuration": HelpTopic(
        id="template_configuration",
        title="Configuring Template Settings",
        category=HelpCategory.TEMPLATES,
        summary="How to customize template appearance and behavior",
        content="""
# Configuring Template Settings

Customize the look and behavior of your [browser source templates](help:templates_intro).

## Template Configuration Files

Each template has a JSON config file in `templates/template_configs/`:

```
templates/template_configs/
├── alerts.json
├── chat.json
├── activity_feed.json
└── ...
```

## Editing Configurations

### Via UI
1. Go to **Custom Sources** tab
2. Select a template
3. Modify settings in the editor
4. Click **Save** to apply

### Direct File Edit
1. Open config file in text editor
2. Modify JSON settings
3. Restart Mycelian or refresh browser sources

## Common Configuration Options

### Appearance Settings
```json
{
    "theme": "dark",
    "font_family": "Arial",
    "font_size": "24px",
    "colors": {
        "primary": "#ff6b6b",
        "secondary": "#4ecdc4",
        "background": "#2c3e50"
    }
}
```

### Layout Settings
```json
{
    "position": "bottom_right",
    "width": 400,
    "height": 200,
    "margin": 20,
    "z_index": 100
}
```

### Animation Settings
```json
{
    "animation_duration": 0.5,
    "animation_type": "slide_in",
    "easing": "ease_out"
}
```

## Template-Specific Settings

### Alerts Template
- Alert duration and timing
- Animation styles
- Text formatting
- Sound integration

### Chat Template
- Message display format
- Emote handling
- User badges
- Message filtering

### Activity Feed
- Number of items to show
- Update frequency
- Item formatting
- Scroll behavior

## Custom CSS

Add custom CSS for advanced styling:

```json
{
    "custom_css": "
        .alert-text {
            font-weight: bold;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
        }
        
        .alert-container {
            border-radius: 10px;
            box-shadow: 0 4px 8px rgba(0,0,0,0.3);
        }
    "
}
```

## Variables and Dynamic Content

### Template Variables
Use variables that get replaced with real data:

```
{{ username }} cheered {{ amount }} bits!
```

### Conditional Content
Show/hide elements based on conditions:

```
{% if user_is_subscriber %}
    ⭐ {{ username }}
{% else %}
    {{ username }}
{% endif %}
```

## Testing Changes

### Preview Mode
- Use OBS preview to test changes
- Refresh browser sources after config updates
- Check different scenarios

### Validation
- Verify JSON syntax is correct
- Test with real data
- Check performance impact

## Backup and Restore

### Backup Configurations
- Copy config files before major changes
- Use version control for templates
- Document customizations

### Restore Defaults
- Delete custom config to use defaults
- Copy from backup files
- Reset through UI

## Advanced Customization

### Custom Templates

**Recommended:** Use [Spore Studio](help:spore_studio_overview) to build overlays visually. Fields you mark
**Expose in Source Settings (JSON)** appear here automatically — see
[Designing Templates in Spore Studio](help:spore_studio_design). Author live stream buttons in
[Dynamic Controls (Source Controls Tab)](help:spore_studio_dynamic_controls).

**Advanced / legacy:** Create templates manually:
1. Copy existing template structure
2. Modify HTML/CSS/JS
3. Add to template_configs
4. Test thoroughly

### Theme System
Create reusable themes:
- Define color schemes
- Set font combinations
- Create consistent styling
- Apply across multiple templates

> **Tip:** For advanced CSS customization, see [Custom CSS for Templates](help:template_custom_css). For live adjustments during your stream, use [Source Controls](help:source_controls).
        """,
        keywords=[
            "configuration",
            "settings",
            "customization",
            "json",
            "css",
            "spore studio",
        ],
        related_topics=[
            "templates_intro",
            "source_controls",
            "template_custom_css",
            "spore_studio_design",
            "spore_studio_dynamic_controls",
        ],
    ),
    "template_custom_css": HelpTopic(
        id="template_custom_css",
        title="Custom CSS for Templates",
        category=HelpCategory.TEMPLATES,
        summary="Advanced styling with custom CSS",
        content="""
# Custom CSS for Templates

Use custom CSS to fully customize the appearance of your [browser sources](help:templates_intro).

## What is Custom CSS?

CSS (Cascading Style Sheets) controls how elements look - colors, fonts,
sizes, animations, and positioning.

## Adding Custom CSS

### In Template Settings
1. Open the template in **Source Settings**
2. Find the **Custom CSS** section
3. Enter your CSS code
4. Click **Save**
5. Refresh the browser source

### Common Customizations

#### Changing Fonts
```css
body {
    font-family: 'Comic Sans MS', cursive;
}

.alert-text {
    font-size: 32px;
    font-weight: bold;
}
```

#### Custom Colors
```css
.alert-container {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    border: 3px solid #gold;
}

.username {
    color: #ff6b6b;
    text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
}
```

#### Animations
```css
@keyframes bounce {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-20px); }
}

.alert-image {
    animation: bounce 0.5s ease infinite;
}
```

## CSS Selectors

### Common Classes
| Class | Element |
|-------|---------|
| `.alert-container` | Main alert wrapper |
| `.alert-image` | GIF/image element |
| `.alert-text` | Text message |
| `.username` | User's name |
| `.amount` | Bits/donation amount |

### Template-Specific Selectors
Each template may have unique classes. Check the template HTML
or use browser DevTools to find class names.

## Advanced Techniques

### Hiding Elements
```css
.unwanted-element {
    display: none !important;
}
```

### Custom Positioning
```css
.alert-container {
    position: fixed;
    bottom: 20px;
    right: 20px;
}
```

### Responsive Scaling
```css
.alert-text {
    font-size: clamp(16px, 4vw, 32px);
}
```

### Transparency
```css
.alert-container {
    background: rgba(0, 0, 0, 0.7);
    backdrop-filter: blur(10px);
}
```

## OBS Browser Source CSS

You can also add CSS directly in OBS:
1. Edit the Browser Source
2. Find "Custom CSS" field
3. Add your CSS code

Note: This applies to ALL content in that source.

## Troubleshooting

### CSS Not Applying
- Check for typos in selectors
- Use `!important` to override defaults
- Verify you saved changes
- Hard refresh the browser source

### Selector Not Found
- Use browser DevTools (F12) to inspect
- Right-click element → Inspect
- Check the actual class names

### Conflicting Styles
- Be more specific with selectors
- Use `!important` carefully
- Check for duplicate rules

## Resources

### Useful CSS Properties
- `color` - Text color
- `background` - Background color/image
- `font-family` - Font style
- `font-size` - Text size
- `padding` - Inner spacing
- `margin` - Outer spacing
- `border` - Element borders
- `border-radius` - Rounded corners
- `box-shadow` - Drop shadows
- `animation` - Animated effects
- `transform` - Scale, rotate, move
- `opacity` - Transparency

### Learning CSS
- [MDN Web Docs](https://developer.mozilla.org/en-US/docs/Web/CSS)
- [CSS-Tricks](https://css-tricks.com)

> **Note:** CSS changes in the [template configuration](help:template_configuration) override OBS Browser Source CSS. Use `!important` if your styles aren't being applied.
        """,
        keywords=["css", "styling", "custom", "design", "colors", "fonts", "animation"],
        related_topics=["templates_intro", "template_configuration"],
    ),
    "template_websocket": HelpTopic(
        id="template_websocket",
        title="WebSocket Events",
        category=HelpCategory.TEMPLATES,
        summary="Real-time data communication for templates",
        content="""
# WebSocket Events

WebSocket provides real-time communication between Mycelian and your
[browser sources](help:templates_intro). [Spore Studio](help:spore_studio_bindings) provides a visual
binding picker for the same events without hand-writing `socket.on` handlers.

## What is WebSocket?

WebSocket is a protocol for real-time, two-way communication. [Templates](help:templates_intro)
receive live updates without refreshing the page.

## How It Works

1. Browser source connects to Mycelian's WebSocket server
2. Mycelian sends events when things happen
3. Template JavaScript listens for events
4. Template updates in real-time

## Connection

### Automatic Connection
Most templates auto-connect when loaded. The connection URL is:
```
ws://localhost:5000/socket.io/
```

### Manual Connection (JavaScript)
```javascript
const socket = io('http://localhost:5000');

socket.on('connect', () => {
    console.log('Connected to Mycelian');
});
```

## Event Types

### Alert Events
| Event | Description | Data |
|-------|-------------|------|
| `new_alert` | New alert triggered | Alert details |
| `alert_complete` | Alert finished | Alert ID |
| `clear_alerts` | Clear all alerts | None |

### State Events
| Event | Description | Data |
|-------|-------------|------|
| `pause_status` | Alerts paused/resumed | Boolean |
| `source_update` | Source settings changed | Settings |
| `template_refresh` | Force refresh | None |

### Data Events
| Event | Description | Data |
|-------|-------------|------|
| `spotify_update` | Now playing changed | Track info |
| `trophy_earned` | PSN trophy earned | Trophy data |
| `stats_update` | Statistics changed | Stats data |

## Listening for Events

### Basic Event Handler
```javascript
socket.on('new_alert', (data) => {
    console.log('New alert:', data);
    displayAlert(data);
});
```

### Alert Data Structure
```javascript
{
    type: 'subscription',
    user: 'ViewerName',
    message: 'Great stream!',
    amount: null,
    tier: 1,
    months: 6,
    timestamp: '2026-01-19T12:00:00Z'
}
```

## Sending Events

### From Template to Mycelian
```javascript
socket.emit('alert_displayed', {
    alertId: '12345',
    duration: 5000
});
```

### Common Outgoing Events
| Event | Purpose |
|-------|---------|
| `alert_displayed` | Confirm alert shown |
| `template_ready` | Template loaded |
| `request_data` | Request current state |

## Debugging WebSocket

### Browser Console
1. Open browser DevTools (F12)
2. Go to Console tab
3. Look for connection messages
4. Check for errors

### Network Tab
1. Open DevTools → Network
2. Filter by "WS" (WebSocket)
3. Click the connection
4. View Messages tab

## Troubleshooting

### "Connection Failed"
- Verify Mycelian is running
- Check port 5000 is available
- Look for firewall blocks
- Try http instead of https

### "Events Not Received"
- Check socket is connected
- Verify event name spelling
- Ensure handler is registered
- Check for JavaScript errors

### "Delayed Updates"
- Network latency is normal
- Check CPU usage (see [Performance Optimization](help:troubleshooting_performance))
- Reduce event frequency
- Optimize event handlers

## Advanced Usage

### Reconnection Handling
```javascript
socket.on('disconnect', () => {
    console.log('Disconnected, will auto-reconnect');
});

socket.on('reconnect', () => {
    console.log('Reconnected!');
    requestCurrentState();
});
```

### Event Filtering
```javascript
socket.on('new_alert', (data) => {
    if (data.type === 'subscription') {
        handleSubscription(data);
    }
});
```
        """,
        keywords=[
            "websocket",
            "events",
            "real-time",
            "socket",
            "javascript",
            "api",
            "spore studio",
        ],
        related_topics=[
            "templates_intro",
            "template_configuration",
            "source_controls",
            "spore_studio_bindings",
        ],
    ),
    "source_controls": HelpTopic(
        id="source_controls",
        title="Real-time Source Controls",
        category=HelpCategory.TEMPLATES,
        summary="Control template elements in real-time during streams",
        content="""
# Real-time Source Controls

Control [template](help:templates_intro) elements live during your stream.

## What are Source Controls?

Source controls let you modify template variables and states in real-time
without editing [configuration files](help:template_configuration).

## Available Controls

### Counter Controls
- **Increment**: Add to counter value
- **Decrement**: Subtract from counter value
- **Set Value**: Set specific number
- **Reset**: Return to zero

### Text Controls
- **Update Text**: Change displayed text
- **Append Text**: Add to existing text
- **Clear Text**: Remove all text

### Visibility Controls
- **Show Element**: Make element visible
- **Hide Element**: Make element invisible
- **Toggle Element**: Switch visibility

### Style Controls
- **Change Color**: Update colors
- **Change Font**: Modify typography
- **Change Size**: Adjust dimensions

## Using Source Controls

### During Stream
1. Go to **Source Controls** tab
2. Select active template
3. Click control buttons
4. See changes instantly in OBS

### Hotkey Integration
- Assign keyboard shortcuts to controls
- Use Stream Deck buttons
- Integrate with connectors

## Control Groups

Organize controls into logical groups:

```
Game Stats
├── Kills: +1
├── Deaths: +1
└── Reset All

Chat Highlights
├── Show Poll
├── Hide Poll
└── Update Results
```

## Advanced Features

### Conditional Controls
Controls that only work under certain conditions:
- Time-based availability
- State-dependent actions
- Permission-based access

### Automated Controls
- Timer-based updates
- Event-triggered changes
- [Connector](help:connectors_intro) integration

## Best Practices

### Stream Preparation
- Set up controls before going live
- Test all controls in preview
- Have backup methods ready

### During Stream
- Use descriptive control names
- Group related controls together
- Monitor OBS for smooth updates

### Performance
- Avoid excessive real-time updates
- Use batch operations when possible
- Monitor browser source performance

## Authoring Controls in Spore Studio

This topic covers **using** controls during a stream. To **create** controls (buttons, toggles,
counter shortcuts, pause alerts), use Spore Studio's **Source Controls** inspector tab:

[Dynamic Controls (Source Controls Tab)](help:spore_studio_dynamic_controls)

| Tab | When |
|-----|------|
| **Spore Studio → Source Controls** | Design-time: define what controls exist |
| **Mycelian → Source Controls** | Stream-time: click controls live in OBS |
        """,
        keywords=[
            "controls",
            "real-time",
            "live",
            "stream",
            "interactive",
            "dynamic controls",
            "spore studio",
        ],
        related_topics=[
            "templates_intro",
            "template_configuration",
            "spore_studio_dynamic_controls",
            "spore_studio_overview",
        ],
    ),
    "spore_studio_overview": HelpTopic(
        id="spore_studio_overview",
        title="Spore Studio Overview",
        category=HelpCategory.TEMPLATES,
        summary="Visual editor for custom overlay templates and their JSON configurations",
        content="""
# Spore Studio Overview

[Spore Studio](help:spore_studio_overview) is Mycelian's visual editor for building custom
[browser source overlays](help:templates_intro). You arrange blocks on a canvas, wire
[event bindings](help:spore_studio_bindings), and **Save** to generate the HTML, JSON config,
and editor sidecar files your stream uses.

## What Spore Studio Is — and Is Not

**Spore Studio is:**

- A **block-based canvas designer** (Text, Image, Video, Audio, Container)
- A **binding editor** for websocket events, Stream Deck actions, and Twitch API calls
- A **Source Settings author** via per-field **Expose in Source Settings (JSON)** checkboxes

**Spore Studio is not:**

- A split-pane HTML/CSS/JavaScript code editor (except the **Advanced JS** tab for custom script)
- An OBS scene manager (no WebSocket control of OBS from the editor)
- An export/import tool (no zip or standalone HTML export)

When you click **Save** on a Spore template, Mycelian writes:

| File | Purpose |
|------|---------|
| `templates/{name}.html` | Generated overlay served to OBS |
| `templates/template_configs/{name}.json` | [Source Settings](help:template_configuration) / Stream Deck public config |
| `templates/_spore/{name}.spore.json` | Editor model (authoritative for Spore Studio) |
| `assets/{name}/` | Images, video, audio, fonts for this template |

> **Tip:** Start with [Designing Templates in Spore Studio](help:spore_studio_design), then read
> [Event Bindings & Actions](help:spore_studio_bindings) when you wire live behavior.

## Prerequisites

1. **Overlay web engine running** — Spore Studio loads inside an iframe at
   `http://127.0.0.1:{port}/_spore_studio_editor`. The server starts with the alert system.
   If you open the tab too early, you see **Waiting for the overlay server to start…** and a
   **Retry** button.
2. **[OBS](help:obs_setup) or another browser-source host** — to display finished templates on stream.
3. Familiarity with [templates](help:templates_intro) and optional [alert setup](help:alerts_overview).

## Opening Spore Studio

### From the main app

1. Click the **Spore Studio** tab in the top bar (alongside Activity Feed, Alerts, etc.).
2. The tab subtitle reads: *Visual editor for Mycelian HTML templates and their JSON configurations.*
3. The editor loads in a full-bleed iframe once the web engine is up.

### Host controls (outside the iframe)

| Button | What it does |
|--------|----------------|
| **Reload editor** | Cache-busts and reloads the iframe (use after server restarts) |
| **Open externally** | Opens `/_spore_studio_editor` in your default browser |

### Inside the editor toolbar

| Control | Purpose |
|---------|---------|
| Template dropdown | Switch active template (`title="Active template"`) |
| **+ New** | Create a template |
| **Delete** | Remove current Spore template (disabled for legacy/protected) |
| Undo / Redo | History (see shortcuts below) |
| **Preview** | Open [live preview](help:spore_studio_advanced) |
| **Save** | Write HTML + JSON + sidecar to disk |

Switching templates with unsaved edits prompts: *Discard unsaved changes?*

## Editor Layout

```
┌─────────────┬──────────────────────┬─────────────────┐
│ Blocks      │                      │ Properties      │
│ Outline     │      Canvas          │ Bindings        │
│ Assets      │   (transparent)      │ Stream Deck     │
│             │                      │ Source Controls │
│             │                      │ Advanced JS     │
│             │                      │ Canvas          │
├─────────────┴──────────────────────┴─────────────────┤
│ Status: Ready · Unsaved changes                    │
└────────────────────────────────────────────────────┘
```

- **Left column — Blocks:** draggable palette (Text, Image, Video, Audio, Container)
- **Left column — Outline:** element tree grouped by category; click a row to select
- **Left column — Assets:** files in `assets/{template_name}/`; drag or double-click to assign
- **Center — Canvas:** design surface with checkerboard (transparent overlays for OBS)
- **Right — Inspector tabs:** per-element and template-level settings
- **Bottom — Status bar:** **Ready**, save messages, **Unsaved changes** indicator

## Template Lifecycle

| Action | UI | Result |
|--------|-----|--------|
| **New** | **+ New** → **New template** dialog | Creates `.spore.json` sidecar + boilerplate HTML/JSON from queue or instant preset |
| **Open** | Template dropdown | Loads model from `templates/_spore/{name}.spore.json` |
| **Save** | **Save** or Ctrl/Cmd+S | Regenerates HTML; merges JSON config; updates sidecar |
| **Delete** | **Delete** + confirm | Removes HTML, JSON config, sidecar, and `assets/{name}/` folder |
| **Copy** | **Copy from existing** in New dialog | Clones an existing Spore template's sidecar |

### New template dialog

| Field | Details |
|-------|---------|
| **Name** | Template stem (e.g. `my_follow_alert`). Avoid reserved names like `static`, `api`. |
| **Alert system** | **Queue** (`next_alert`) or **Instant** (`instant_alert`) — see [Canvas tab](help:spore_studio_design) |
| **Copy from existing** | `(none)` or pick a Spore template to clone |
| **Width (px)** | Default 800 (min 320, max 7680) |
| **Height (px)** | Default 200 (min 240, max 4320) |

Buttons: **Cancel**, **Create**.

### Protected templates

These built-in overlays cannot be opened for visual edit or deleted from Spore Studio:

- `activity_feed`
- `source_controls`

### Legacy templates (optgroup)

Templates listed under **Legacy (advanced mode)** are hand-authored HTML **without** a
`.spore.json` sidecar. Spore Studio opens them in a limited mode — see
[Advanced JS, Preview & Legacy Templates](help:spore_studio_advanced).

## Using Your Template in OBS

1. **Save** your template in Spore Studio.
2. In Mycelian's **Source Settings** tab (or template URL list), copy the browser source URL:
   `http://127.0.0.1:{port}/{template_name}`
3. In OBS: **Add** → **Browser Source** → paste URL.
4. Set width/height to match your canvas dimensions (or scale in OBS).
5. Enable **Shutdown source when not visible** for performance when appropriate.

The canvas uses a **transparent** background by design so overlays composite cleanly over your stream.

> **Note:** Spore Studio does not connect to OBS directly. You add the generated URL as a normal browser source, same as built-in templates.

## Creating Your First Template

Quick walkthrough from blank canvas to OBS:

1. Open the **Spore Studio** tab and wait for the editor iframe (or click **Retry**).
2. Click **+ New** → enter a name (e.g. `my_first_overlay`), pick **Queue** or **Instant**, set width/height → **Create**.
3. Drag a **Text** block onto the canvas → pick a category → style in **Properties**.
4. Optional: drop images into `assets/my_first_overlay/` and assign via the **Assets** panel.
5. Wire behavior: **Bindings** for show/hide, **Counter** mode for HUD totals — see topic links below.
6. Click **Preview** to test with mock events.
7. Click **Save** (Ctrl/Cmd+S).
8. Add OBS Browser Source: `http://127.0.0.1:{port}/my_first_overlay`.

> **Tip:** Clone faster by choosing **Copy from existing** in the New dialog (e.g. `bitcounter`).

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+Z / Cmd+Z | Undo |
| Ctrl+Shift+Z / Cmd+Shift+Z | Redo |
| Ctrl+Y / Cmd+Y | Redo |
| Ctrl+S / Cmd+S | Save |
| Delete / Backspace | Delete selected element (ignored while typing in inputs) |
| **Alt** (while dragging) | Force drop onto canvas root instead of nesting in a container |

## Next Steps

| Goal | Read |
|------|------|
| Blocks, properties, assets | [Designing Templates in Spore Studio](help:spore_studio_design) |
| Counters and counter rules | [Counters in Spore Studio](help:spore_studio_counters) |
| Data displays and live values | [Data Sources & Data Displays](help:spore_studio_data_sources) |
| Websocket triggers and actions | [Event Bindings & Actions](help:spore_studio_bindings) |
| Stream Deck button mapping | [Stream Deck Actions in Spore Studio](help:spore_studio_streamdeck) |
| Live stream control buttons | [Dynamic Controls (Source Controls Tab)](help:spore_studio_dynamic_controls) |
| Preview, Advanced JS, legacy | [Advanced JS, Preview & Legacy Templates](help:spore_studio_advanced) |
| Step-by-step builds | [Spore Studio Examples & Recipes](help:spore_studio_examples) |
        """,
        keywords=[
            "spore studio",
            "visual editor",
            "template editor",
            "overlay designer",
            "canvas",
            "blocks",
            "browser source",
            "counter",
            "data source",
            "dynamic controls",
            "stream deck",
        ],
        related_topics=[
            "templates_intro",
            "spore_studio_design",
            "spore_studio_counters",
            "spore_studio_data_sources",
            "spore_studio_streamdeck",
            "spore_studio_dynamic_controls",
            "template_configuration",
            "obs_setup",
        ],
        ui_context="spore_studio",
    ),
    "spore_studio_design": HelpTopic(
        id="spore_studio_design",
        title="Designing Templates in Spore Studio",
        category=HelpCategory.TEMPLATES,
        summary="Blocks, canvas, assets, element properties, and Source Settings exposure",
        content="""
# Designing Templates in Spore Studio

This guide covers everything you draw and configure on the canvas before wiring
[bindings](help:spore_studio_bindings). For editor chrome and file layout, see
[Spore Studio Overview](help:spore_studio_overview).

## Block Types

Drag blocks from the **Blocks** panel onto the canvas:

| Block | Label | Typical use |
|-------|-------|-------------|
| T | **Text** | Labels, usernames, messages |
| camera icon | **Image** | Alert art, avatars, static graphics |
| film icon | **Video** | MP4/WebM loops; can bundle separate audio |
| speaker icon | **Audio** | Sound effects, voice lines |
| square icon | **Container** | Group and clip child elements |

Dropping a block opens the **Element category** dialog.

## Element Category Dialog

| Control | Purpose |
|---------|---------|
| **Existing category** | Dropdown of categories already used in this template |
| **New category name** | Type a new group name |
| **+ New category…** | Switches focus to the new-name field |
| **Cancel** | Abort — no element is created |
| **Use category** | Confirm and place the element |

Categories group elements in the **Outline** panel and in the saved
[template JSON config](help:template_configuration) (Source Settings sections).

> **Tip:** Use one category per logical overlay (e.g. `alert_box`, `username_label`) so
> [Source Controls](help:source_controls) stay organized.

## Canvas Interactions

| Interaction | Behavior |
|-------------|----------|
| **Drag** element | Move; position stored as X/Y in pixels |
| **Resize handle** (bottom-right) | Change W/H |
| **Drop into container** | Container highlights; element becomes a child (`parent_id`) |
| **Drag out of container** | Un-nest to previous parent or canvas root |
| **Alt + drop** | Force placement on canvas root (skip auto-nest) |
| **Click empty canvas** | Deselect all |
| **Delete element** (Properties) | Removes element and all descendants |

Nested children move with their parent container.

## Outline Panel

- Lists every element grouped under its **category** heading.
- Each row shows the element **id** and type.
- Click a row to select that element on the canvas and in the inspector.

Empty state: *No elements yet — drag a block onto the canvas, or open an existing template to populate this list.*

## Assets Panel

Media lives on disk at `assets/{template_name}/` (images, video, audio, fonts).

| Action | Result |
|--------|--------|
| **Drag** asset onto canvas | Creates an image, video, or audio element at the drop position |
| **Double-click** asset | Sets **Source URL** on the **selected** image/video/audio element |
| Drop files into folder on disk | Appears after refresh; hot-reload via `spore_studio_assets_changed` socket event |

Empty hint: *Drop files into assets/{name} to populate.*

If nothing is selected, double-click shows a toast: *Select an image / video / audio element first.*

**URL examples** (after Save, paths are relative to the overlay server):

```
/assets/my_alert/alert.gif
/assets/my_alert/sfx.mp3
```

## Canvas Tab (Template-Level)

Open the **Canvas** inspector tab when no element-specific setting applies, or scroll there for globals:

| Field | Range / options | Purpose |
|-------|-----------------|--------|
| **Width (px)** | 320–7680 | Design width; OBS browser source should match |
| **Height (px)** | 240–4320 | Design height |
| **Alert system** | Queue / Instant | Which alert websocket event this template expects |
| **Title** | Text | Human-readable name in Source Settings |
| **Duration (seconds)** | ≥ minimum from layout | Written to template config as `Duration` for alert queue holds (point rewards matching this template name) |
| **Queued** | Checkbox | When on, matching point redemptions without main-overlay media can hold the alert queue using **Duration** |
| **Reset to minimum** | Button | Sets duration to the computed floor from animations, bindings, and fades |

The **Minimum from layout** label updates live as you edit elements. Duration auto-bumps up when the layout needs more time; you can set it higher manually.

### Queue vs Instant alert system

| Value | Label in UI | Use when |
|-------|-------------|----------|
| `queue` | Queue (`next_alert`; emit `alert_complete` in Advanced JS if you use the alert queue) | Full-screen alerts that participate in the alert queue handshake |
| `instant` | Instant (`instant_alert`) | Sub bars, counters, HUD updates that must not block the queue |

> **Warning:** A queue template that never emits `alert_complete` with the matching `queue_seq`
> will stall the alert processor. See [Advanced JS](help:spore_studio_advanced).

## Properties Tab — Shared Fields

Select any element to edit:

| Field | Description |
|-------|-------------|
| **ID** | Unique element id (used in bindings and generated HTML) |
| **Category** | Outline / JSON grouping |
| **Type** | Read-only (`text`, `image`, `video`, `audio`, `container`) |
| **Parent** | `(canvas root)` or container id; **Move to canvas** unnests |
| **X**, **Y**, **W**, **H** | Position and size in pixels |
| **Placement in container** | When nested: 3×3 anchor presets plus **Offset X/Y** for fine tuning (dragging updates offset) |
| **Start hidden until shown** | Element begins hidden; Show bindings reveal it |
| **Delete element** | Remove element and children |

When **Show** bindings exist, the UI may auto-suggest enabling **Start hidden until shown**.

### Animations (all element types)

| Field | Notes |
|-------|-------|
| **Entrance / Exit** | `none`, `fade`, `slideIn`, `slideOut`, `scaleIn`, `scaleOut` — applied on every `sporeShow` / `sporeHide` (bindings and Advanced JS) |
| **Entrance / Exit duration (ms)** | Per-leg timing (default 300 ms when type ≠ `none`) |
| **Delay before entrance (ms)** | Wait before entrance runs after show |
| **Easing** | CSS timing function for entrance animation |

Elements with non-`none` animations show a small **anim** badge on the canvas.

## Properties by Element Type

### Text

| Property | Notes |
|----------|-------|
| **Text** | Default label content |
| **Font size (px)** | Numeric |
| **Color** | Color picker |
| **Font** | Dropdown of files in `assets/default_assets/fonts/`, or custom name |
| **Font weight** | `normal`, `bold`, or `100`–`900` |
| **Text align** | `left`, `center`, `right` |
| **Vertical align** | `top`, `center`, `bottom` — positions text within the element height |
| **Background** | Color picker (supports `#hex`, `rgba(...)`, or transparent) |

### Image

| Property | Notes |
|----------|-------|
| **Image source mode** | **Static URL** or **From counter (ranges)** |
| **Source URL** | Dropdown of images in `assets/{template_name}/`, or **(custom URL…)** for manual paths |
| **Counter / ranges / default src** | When using counter mode: pick a counter, assign an image per min–max range from the same asset dropdown |
| **Border radius (px)** | Corner rounding |
| **Opacity** | 0–1 |

### Video

| Property | Notes |
|----------|-------|
| **Visual source URL** | Video or image URL for the visual layer |
| **Visual kind** | `video` or `image` (GIF-as-image swaps) |
| **Optional audio URL** | Separate audio track |
| **Audio volume (0–1)** | Default playback level |
| **Audio fade-in (ms)** | 0–120000 |
| **Audio fade-out (ms)** | 0–120000 |
| **Autoplay** | Start when shown |
| **Loop** | Repeat visual |
| **Muted** | Mute bundled audio |
| **Border radius (px)** | Corner rounding |

### Audio

| Property | Notes |
|----------|-------|
| **Source URL** | Audio file path |
| **Volume (0–1)** | Default level |
| **Fade-in (ms)** | 0–120000 |
| **Fade-out (ms)** | 0–120000 |

### Container

| Property | Notes |
|----------|-------|
| **Background** | Fill behind children |
| **Border radius (px)** | Outer corners |
| **Border width (px)** | Outline thickness |
| **Border color** | CSS color |

Containers clip visually; child positions are relative to the container's top-left.

## Expose in Source Settings (JSON)

Most property rows include a checkbox: **Expose in Source Settings (JSON)**.

| Checked | Unchecked |
|---------|-----------|
| Value stored in `template_configs/{name}.json` | Value inlined only in generated HTML |
| Editable in Mycelian **Source Settings** and [Source Controls](help:source_controls) | Fixed until you re-open Spore Studio and Save |

Tooltip when off: *When off, value is inlined in HTML only (omitted from template JSON).*

New elements default exposed keys to **on** for their type's schema fields.

> **Tip:** Expose colors, text, and URLs you tweak often on stream; leave structural sizes
> un-exposed if you do not need live control.

## Parent and Nesting

- Only **container** elements accept children.
- **Parent** dropdown lists valid containers; **Move to canvas** sets `parent_id` to root.
- Dragging with **Alt** avoids accidental nesting when you want a root-level sibling.

## Text Modes (Counters & Data Displays)

For **text** elements, **Text mode** in Properties selects one of:

| Mode | Purpose |
|------|---------|
| **Static text** | Classic fixed or Jinja-backed label |
| **Counter** | Numeric value with rules — see [Counters in Spore Studio](help:spore_studio_counters) |
| **Data display** | Read-only live values — see [Data Sources & Data Displays](help:spore_studio_data_sources) |

**Counter** and **Data display** modes add a **value change animation** section (`tick_up`, fade-in,
etc.) separate from per-element **Entrance / Exit** animations on Show/Hide bindings.

**Image** elements can use **From counter (ranges)** to swap art by threshold — covered in
[Counters in Spore Studio](help:spore_studio_counters).

## Source Controls Tab

The **Source Controls** inspector tab authors `dynamic_controls` for the Mycelian
[Source Controls](help:source_controls) runtime tab — pause buttons, counter controls, toggles,
and custom socket actions.

Full authoring guide: [Dynamic Controls (Source Controls Tab)](help:spore_studio_dynamic_controls).

## What to Do Next

After layout and styling, open the **Bindings** tab — covered in
[Event Bindings & Actions](help:spore_studio_bindings) — or try a full walkthrough in
[Spore Studio Examples & Recipes](help:spore_studio_examples).
        """,
        keywords=[
            "spore studio",
            "blocks",
            "canvas",
            "assets",
            "properties",
            "container",
            "text",
            "image",
            "video",
            "expose",
            "source settings",
            "counter",
            "data display",
        ],
        related_topics=[
            "spore_studio_overview",
            "spore_studio_counters",
            "spore_studio_data_sources",
            "spore_studio_dynamic_controls",
            "spore_studio_bindings",
            "template_configuration",
            "source_controls",
        ],
    ),
    "spore_studio_bindings": HelpTopic(
        id="spore_studio_bindings",
        title="Event Bindings & Actions",
        category=HelpCategory.TEMPLATES,
        summary="Websocket events, payload filters, actions, chained steps, and Twitch API bindings",
        content="""
# Event Bindings & Actions

Bindings connect live [websocket events](help:template_websocket) (or Stream Deck actions)
to visual changes on your overlay. Select an element, open the **Bindings** tab, and click
**+ Add binding**.

For canvas and property basics, see [Designing Templates in Spore Studio](help:spore_studio_design).

## Binding Workflow

1. Select an element on the canvas or in **Outline**.
2. Open **Bindings** → **+ Add binding**.
3. Choose **Trigger** type: **Registry event** or **Stream Deck action**.
4. Pick the **Event** (or Stream Deck action id).
5. Optionally add **payload filters** so the binding runs only for matching data.
6. Choose the primary **Action** and fill in its arguments.
7. Optionally add **Chained actions** (up to 15 steps with delays).

Empty state: *Select an element to attach event bindings.*

Legacy templates: *Legacy templates manage their own websocket bindings inside the hand-authored HTML…*

## Trigger Types

### Registry event

Curated Mycelian socket events safe for overlay authors (not internal plumbing).

### Stream Deck action

Runs when a button mapped to this template's Stream Deck action fires. Configure actions on the
**Stream Deck** inspector tab first — see [Stream Deck Actions in Spore Studio](help:spore_studio_streamdeck).

## Payload Filters

Each binding can require payload fields to match before the action runs.

| Filter key | Filter value | Example |
|------------|--------------|---------|
| `alert_type` | `follow` | Only follow alerts |
| `tier` | `3000` | Tier 3 subs |
| `paused` | `true` | JSON boolean (parsed automatically) |
| `username` | `CoolViewer` | Exact username match |

Add rows in the binding card's filter section. Leave a row blank to ignore it.

> **Tip:** Test filters with **Preview** mock buttons — see [live preview](help:spore_studio_advanced).

## Chained Actions

After the primary **Action** runs, up to **15** chained steps execute in order.

| Field per step | Purpose |
|----------------|---------|
| **delay_ms** | Milliseconds to wait after the previous step's synchronous JS (not after animations finish) |
| **Action** | Same action list as the primary action |
| **Args** | Action-specific arguments |

> **Note:** A chained **Show for N seconds** timer is independent of the delay before the next
> chain step — delays do not wait for the inner hide animation to complete.

Example chain: **Show** → 200 ms → **Play CSS class animation** (`sporeShake`) → 3000 ms → **Hide**.

## Event Reference

| Event | Label | When it fires | Key payload fields |
|-------|-------|---------------|-------------------|
| `next_alert` | Alert (queue) | Queued alerts (follow, sub, raid, bits, etc.) | `queue_seq`, `alert_type`, `username`, `message`, `amount`, `tier`, `gif_name`, `duration` |
| `instant_alert` | Alert (instant) | Non-blocking alerts (HUD, counters) | `alert_type`, `username`, `message`, `amount`, `tier`, `quantity` |
| `refresh-alerts` | Refresh alerts | Alert settings changed | (empty) |
| `alerts_paused` | Alerts paused | User paused alerts | `paused` |
| `alerts_resumed` | Alerts resumed | User resumed alerts | `paused` |
| `pause_status_update` | Pause status update | Every pause toggle + startup | `paused` |
| `new-message` | Chat message (Twitch) | Each Twitch chat line | `username`, `message`, `color`, `badges`, `message_type` |
| `chat_add_message` | Chat message (connector) | Connector "Add message" action | `username`, `message_text`, `is_moderator` |
| `message_moderation` | Chat moderation event | Delete / timeout / ban | `action`, `user_id`, `message_id` |
| `twitch-api-response` | Twitch API call (response) | Response to a Helix request you configure | Filter on response fields (e.g. `success`) |

### Example filters by event

**`next_alert`**

```
alert_type = follow
alert_type = sub
tier = 3000
```

**`new-message`**

```
message_type = text
username = StreamerName
```

**`message_moderation`**

```
action = delete
action = timeout
```

**`pause_status_update`**

```
paused = true
```

> **Warning:** Queue templates (`next_alert`) require your overlay (or the main alerts template)
> to emit `alert_complete` with the same `queue_seq` when done, or the queue stalls.
> See [Advanced JS](help:spore_studio_advanced).

## Action Reference

| Action | Label | Args |
|--------|-------|------|
| `show` | Show element | — |
| `hide` | Hide element | — |
| `toggle` | Toggle visibility | — |
| `show_for` | Show for N seconds | `seconds` (0.1–600, default 5), `anim_in`, `anim_out`: `none`, `fade`, `slideIn`, `scaleIn`, `slideOut`, `scaleOut` |
| `set_text` | Set text content | `from_payload` (e.g. `username`) **or** `literal` |
| `set_image` | Set image source | `from_payload` **or** `literal` URL |
| `play_audio` | Play audio element | Optional `volume` (0–1), `fade_in_ms` (blank = element default) |
| `set_visual_src` | Set visual source (GIF/video) | `from_payload` **or** `literal` (video bundle) |
| `randomize_position` | Randomize position within bounds | `x_min`, `x_max`, `y_min`, `y_max` (-1 = canvas edge minus element size) |
| `set_transform` | Set CSS transform | `translate_x`, `translate_y`, `rotate_deg`, `scale` (blank skipped) |
| `transform_jitter` | Random transform jitter | `rotate_range`, `translate_range`, `scale_min`, `scale_max` |
| `flash_class` | Play CSS class animation | `class_name` (e.g. `sporeShake`, `sporePop`), `duration_ms` (default 420) |
| `counter_adjust` | Adjust counter | `counter_id`, `operation` (`increment`/`decrement`/`set`/`reset`), `delta_kind` (`fixed`/`random_int`/`random_float`/`data_source`), `delta_value`, `delta_source`, `delta_min`, `delta_max` |

### Action examples

**Show follow alert for 5 seconds with fade:**

- Action: **Show for N seconds**
- `seconds`: `5`
- `anim_in`: `fade`
- `anim_out`: `fade`

**Set username from alert payload:**

- Action: **Set text content**
- `from_payload`: `username`

**Welcome message literal:**

- Action: **Set text content**
- `from_payload`: *(empty)*
- `literal`: `Welcome to the stream!`

**Random position in lower third:**

- Action: **Randomize position within bounds**
- `x_min`: `0`, `x_max`: `-1`, `y_min`: `400`, `y_max`: `-1`

## Counter Adjustments from Bindings

Use **Adjust counter** when a binding should change a numeric counter without adding a
[counter rule](help:spore_studio_counters).

**Example:** On `instant_alert` with filter `alert_type` = `bit`:

- Action: **Adjust counter**
- `counter_id`: `bit_count`
- `operation`: `increment`
- `delta_kind`: `data_source`
- `delta_source`: `alert.amt_cheered`

Prefer **counter rules** on the text element for event-driven math; use **counter_adjust** when
the adjustment is part of a binding chain or tied to a different trigger (e.g. Stream Deck).

## Twitch API Bindings

For event **`twitch-api-response`**:

| Field | Purpose |
|-------|---------|
| **Endpoint** | Full Helix URL, e.g. `https://api.twitch.tv/helix/users` |
| **Method** | GET, POST, etc. |
| **JSON parameters (query)** | Query object as JSON |
| **JSON body** | Body for POST/PATCH/PUT |
| **Response filters** | Rows of `key` / `value` — only run actions when response matches |

The compiler injects a `requestId` into filters and emits `twitch-api-request` on socket connect.

Example response filter:

```
success = true
```

> **Tip:** For one-off Helix calls outside bindings, use Advanced JS — see
> [Advanced JS, Preview & Legacy Templates](help:spore_studio_advanced).

## Start Hidden Until Shown

Enable **Start hidden until shown** on the element (Properties tab) when using **Show** or
**Show for N seconds** bindings so the overlay does not flash content before the first event.

## Related Topics

- [Spore Studio Examples & Recipes](help:spore_studio_examples) — complete binding walkthroughs
- [Connector Examples](help:connector_examples) — automate `chat_add_message` and more
        """,
        keywords=[
            "bindings",
            "websocket",
            "events",
            "actions",
            "filters",
            "chain",
            "next_alert",
            "instant_alert",
            "stream deck",
            "twitch api",
            "counter_adjust",
        ],
        related_topics=[
            "spore_studio_design",
            "spore_studio_counters",
            "spore_studio_advanced",
            "spore_studio_streamdeck",
            "template_websocket",
            "connector_examples",
        ],
    ),
    "spore_studio_advanced": HelpTopic(
        id="spore_studio_advanced",
        title="Advanced JS, Preview & Legacy Templates",
        category=HelpCategory.TEMPLATES,
        summary="Live preview mocks, user JavaScript, queue handshake, Stream Deck config, and legacy modes",
        content="""
# Advanced JS, Preview & Legacy Templates

Topics beyond the visual canvas: testing without going live, custom JavaScript, Stream Deck
metadata, and editing older hand-written templates.

## Live Preview

1. Click **Preview** in the toolbar (tooltip: *Open live preview window*).
2. The **Live preview** dialog opens with a draggable header and resizable corner.
3. The iframe loads your template with **unsaved** draft changes (no Save required for testing).

| Control | Purpose |
|---------|---------|
| **Reload** | Refresh the preview iframe |
| **Close** | Dismiss the dialog |

### Mock toolbar

Below the title bar:

| Section | Purpose |
|---------|---------|
| **Alerts:** | Random queue/instant alert plus one button per alert type (follow, sub, bits, …) with realistic random fields |
| **Other:** | Non-alert events referenced by bindings or counter rules on this template |
| **SD:** | One button per Stream Deck action defined on this template |

Click a mock button to fire a single socket event into the preview overlay. Payloads use the
same shape as production events (demo usernames, alert presets, etc.). On **Save**, the
generated ``template_configs/{name}.json`` includes a ``preview_mocks`` list used by the
Custom Sources preview toolbar as well.

> **Tip:** Build bindings in [Event Bindings & Actions](help:spore_studio_bindings), then verify
> with mocks before adding the browser source to OBS.

**Not supported:** device size presets (iPhone/tablet), responsive breakpoints — resize the
preview window manually to approximate your OBS browser source size.

## Advanced JS Tab

Hand-written JavaScript is stored between markers in the saved HTML:

```javascript
// SPORE_STUDIO:user-begin
// your code here
// SPORE_STUDIO:user-end
```

**Do not edit** the generated block between `// SPORE_STUDIO:auto-begin` and `auto-end` — it is
rebuilt from your bindings on every **Save**.

### Runtime helpers

| Function | Purpose |
|----------|---------|
| `sporeShow(id)` | Show element (clears hidden state) |
| `sporeHide(id)` | Hide element |
| `sporeSetText(id, value)` | Set text element content |
| `sporePlayMediaAudio(id, { volume?, fade_in_ms? })` | Play audio on audio/video bundle |

You may also use `socket.on(...)` for events not covered by the binding picker.

### Queue alert handshake

For templates with **Alert system: Queue**, when your overlay finishes displaying a queued alert
and you are the completion source, emit:

```javascript
socket.on('next_alert', function (data) {
    // ... your show/hide logic ...
    socket.emit('alert_complete', { queue_seq: data.queue_seq });
});
```

If the main `alerts.html` overlay handles timing, subsidiary overlays may only need to echo
`queue_seq` when they are the sole completion source. Otherwise the [alert queue](help:alerts_overview) stalls.

> **Warning:** Missing or mismatched `queue_seq` is the most common cause of "stuck" alerts on
> custom queue templates.

### Twitch API from user script

Same pattern as built-in templates:

```javascript
socket.emit('twitch-api-request', {
    endpoint: 'https://api.twitch.tv/helix/users',
    method: 'GET',
    requestId: 'my-follower-check',
    params: { login: 'someuser' }
});

socket.on('twitch-api-response', function (data) {
    if (data.requestId !== 'my-follower-check') return;
    // handle data
});
```

Prefer **Twitch API bindings** in the inspector when you only need request/response tied to an element.

## Stream Deck Tab

Define per-template Stream Deck actions on the **Stream Deck** inspector tab. Each action gets
an id, display name, socket event, and optional `default_data` JSON.

Full workflow (plugin mapping, payload merge, binding patterns):
[Stream Deck Actions in Spore Studio](help:spore_studio_streamdeck).

Legacy templates: Stream Deck actions are not edited in Spore Studio.

## Legacy Templates

Listed under **Legacy (advanced mode)** in the template dropdown — HTML exists without
`templates/_spore/{name}.spore.json`.

### JSON-only legacy

- Message: *Read-only legacy template. Edits to this value persist to the JSON config; HTML is not regenerated.*
- Edit **Value** fields tied to Source Settings only.
- **Save** updates `template_configs/{name}.json` only.

### HTML-parsed legacy

- Position, size, and some styles editable.
- Bindings are **not** editable in the UI — they live in hand-authored HTML.
- **Save** updates JSON values mapped from HTML; does not rewrite websocket logic.

> **Tip:** To use the full block editor and binding picker, create a new template with **+ New**
> and rebuild, or copy from a similar Spore template.

Legacy templates **cannot** receive new blocks from the palette — toast: *Legacy templates can't accept new blocks — create a new template via '+ New' to edit visually.*

## What Save Does

| Template kind | HTML | JSON config | Sidecar |
|---------------|------|-------------|---------|
| Spore (`.spore.json`) | Regenerated | Merged (preserves user-tuned values by id) | Updated |
| Legacy | Unchanged | Updated only | Not written |

## Limitations

| Feature | Status |
|---------|--------|
| Export to zip / standalone HTML | Not available |
| Import external HTML project | Not available |
| Full HTML/CSS source panes | Not available (use Advanced JS + bindings) |
| OBS scene/source control | Not available from Spore Studio |
| Device preview presets | Not available |

## See Also

- [Spore Studio Overview](help:spore_studio_overview)
- [Spore Studio Examples & Recipes](help:spore_studio_examples)
        """,
        keywords=[
            "advanced js",
            "preview",
            "legacy",
            "stream deck",
            "alert_complete",
            "queue",
            "sporeShow",
            "mock",
        ],
        related_topics=[
            "spore_studio_bindings",
            "spore_studio_streamdeck",
            "spore_studio_examples",
            "template_websocket",
            "alerts_overview",
        ],
    ),
    "spore_studio_examples": HelpTopic(
        id="spore_studio_examples",
        title="Spore Studio Examples & Recipes",
        category=HelpCategory.TEMPLATES,
        summary="Step-by-step overlay builds with bindings, filters, and testing",
        content="""
# Spore Studio Examples & Recipes

Hands-on walkthroughs. For field definitions see [Designing Templates](help:spore_studio_design);
for action args see [Event Bindings & Actions](help:spore_studio_bindings).

## Recipe 1: Simple Follow Alert (Queue)

**Goal:** 800×200 overlay that shows username + image for follows only.

1. **+ New** → Name: `follow_alert`, Alert system: **Queue**, 800×200.
2. Drag **Text** → category `label` → set default **Text** to `New follower!`
3. Drag **Text** → category `username` → leave empty (filled by binding).
4. Drag **Image** → category `avatar` → set **Source URL** to a placeholder under `assets/follow_alert/`.
5. Select `username` text → **Bindings** → **+ Add binding**:
   - Trigger: **Registry event** → `next_alert`
   - Filter: `alert_type` = `follow`
   - Action: **Set text content** → `from_payload`: `username`
6. Select `avatar` image → binding on same event/filter:
   - Action: **Set image source** → `from_payload`: *(use a URL field from payload or literal path)*
7. Select container or root group → binding:
   - Action: **Show for N seconds** → `seconds`: `5`, `anim_in`: `fade`, `anim_out`: `fade`
8. Enable **Start hidden until shown** on text and image elements.
9. **Expose in Source Settings** on colors/fonts you want to tweak live.
10. **Save** → **Preview** → click **Mock:** `next_alert` (follow) → verify animation.
11. Add OBS browser source: `http://127.0.0.1:5000/follow_alert` (use your actual port).

If this template owns queue completion, add to **Advanced JS**:

```javascript
socket.on('next_alert', function (data) {
    if (data.alert_type !== 'follow') return;
    // generated bindings handle show/hide
    setTimeout(function () {
        socket.emit('alert_complete', { queue_seq: data.queue_seq });
    }, 5000);
});
```

## Recipe 2: Instant Sub Counter Bar

**Goal:** HUD that increments a sub total on every sub without blocking the alert queue.

1. **+ New** → `sub_counter`, Alert system: **Instant**, 600×80.
2. Add **Text** → category `Counter` → **Text mode**: **Counter**.
3. Set **Counter id**: `sub_count`, **Format**: `Subs: {value}`, **Initial value**: `0`.
4. **+ Add counter rule**:
   - Event: `instant_alert`
   - Filter: `alert_type` = `sub`
   - Operation: **increment**
   - Delta kind: **fixed** → `1`
5. Optional: enable **tick_up** value change animation.
6. **Save** — no `alert_complete` required. Test with **Preview** → sub mock.

See [Counters in Spore Studio](help:spore_studio_counters) for persistence and Stream Deck triggers.

## Recipe 3: Chat Message Pop

**Goal:** Show last chat line briefly.

1. New template, **Instant** or **Queue** as appropriate (chat is independent of alert queue).
2. **Text** element, **Start hidden until shown** enabled.
3. Binding:
   - Event: `new-message`
   - Action: **Set text content** → `from_payload`: `message`
   - Chained: **Show for N seconds** → `seconds`: `4`
4. Optional filter: `message_type` = `text` to ignore actions.

## Recipe 4: GIF Swap on Cheer

**Goal:** Swap video visual to a cheer GIF and play a sound.

1. Add **Video** block; **Visual kind**: `image` or `video` as needed.
2. Drop cheer GIF into `assets/{template}/`.
3. Binding on `next_alert`, filter `alert_type` = `bit` (or `donation`):
   - **Set visual source** → `from_payload`: `gif_name` *(or literal `/assets/.../cheer.gif`)*
4. Chained step (delay 0 ms): **Play audio element** on a separate **Audio** block.

## Recipe 5: Random Alert Position

**Goal:** Different corner each follow.

1. Group alert art in a **Container**.
2. Binding on `next_alert` / `follow` filter:
   - **Randomize position within bounds**
   - `x_min`: `0`, `x_max`: `-1`, `y_min`: `0`, `y_max`: `-1`
3. **Show for N seconds** as primary or chained action.

## Recipe 6: Stream Deck Toggle Overlay

**Goal:** Button shows/hides a logo overlay.

1. **Stream Deck** tab → **+ Add Stream Deck action**:
   - Action id: `toggle_logo`
   - Display name: `Toggle logo`
   - Socket event: `toggle_logo` *(custom)*
   - default_data: `{}`
2. Select logo **Image** → Binding:
   - Trigger: **Stream Deck action** → `toggle_logo`
   - Action: **Toggle visibility**
3. **Preview** → **SD:** `toggle_logo` to test.

See [Stream Deck Actions in Spore Studio](help:spore_studio_streamdeck) for plugin mapping.

## Recipe 7: Twitch API Follower Check

**Goal:** Show element only when Helix reports success.

1. Binding trigger: **Registry event** → `twitch-api-response`
2. Configure endpoint/method in binding card.
3. **Response filters:** `success` = `true`
4. Action: **Show element** on a status indicator.

## Recipe 8: Chained Action Drama

**Goal:** Pop-in, shake, then hide (max 15 chain steps).

On `next_alert` (any type), primary **Show**, then chain:

| Step | delay_ms | Action | Args |
|------|----------|--------|------|
| 1 | 100 | flash_class | `class_name`: `sporePop`, `duration_ms`: 400 |
| 2 | 450 | flash_class | `class_name`: `sporeShake`, `duration_ms`: 420 |
| 3 | 3000 | hide | — |

> **Note:** You cannot add more than 15 chained steps per binding.

## Recipe 9: Bit Counter HUD (bitcounter pattern)

**Goal:** Running bit total with tiered cheer GIF and tick-up animation.

1. **+ New** → `my_bits`, Alert system: **Instant**, 300×55 (or copy from `bitcounter`).
2. **Text** → **Counter** mode → `counter_id`: `bit_count`, format `{value}`, initial `0`.
3. Counter rule: `instant_alert`, filter `alert_type` = `bit`, **increment**, delta **data_source** → `alert.amt_cheered`.
4. Enable **tick_up** value change animation (~1000 ms).
5. **Image** → **From counter (ranges)** → counter `bit_count`:
   - 0–99 → `/assets/my_bits/cheer1.gif`
   - 100–999 → `/assets/my_bits/cheer100.gif`
   - 1000+ → `/assets/my_bits/cheer1000.gif`
6. Optional **counter image transition**: roll, 600 ms.
7. **Save** → **Preview** → bits mock.

Full counter details: [Counters in Spore Studio](help:spore_studio_counters).

## Recipe 10: Dynamic Controls on Stream

**Goal:** Pause alerts and manually bump a game score from Mycelian's Source Controls tab.

1. Create or open a game HUD template with a **Counter** text element (`game_score`).
2. **Source Controls** inspector tab → **+ Add control**:
   - Type **button**, label `Pause alerts`, action `toggle_alerts`.
3. **+ Add control**:
   - Type **counter_control**, label `Score`, counter `game_score`, default step `1`.
4. **Save**.
5. During stream: Mycelian **Source Controls** tab → select template → use the new buttons.

See [Dynamic Controls (Source Controls Tab)](help:spore_studio_dynamic_controls).

## Testing Checklist

- [ ] **Preview** mocks for each event you bind
- [ ] **Save** then reload template in OBS
- [ ] Queue templates emit `alert_complete` when appropriate
- [ ] Counters persist (or reset) as expected — [Counters](help:spore_studio_counters)
- [ ] Data displays refresh on chosen events — [Data Sources](help:spore_studio_data_sources)
- [ ] Stream Deck **SD:** mocks and physical buttons match — [Stream Deck](help:spore_studio_streamdeck)
- [ ] [Source Controls](help:source_controls) and [dynamic controls](help:spore_studio_dynamic_controls) work live

## See Also

- [Spore Studio Overview](help:spore_studio_overview)
- [First Alert Setup](help:first_alert_setup)
        """,
        keywords=[
            "examples",
            "recipes",
            "tutorial",
            "follow alert",
            "bit counter",
            "sub counter",
            "walkthrough",
            "spore studio",
        ],
        related_topics=[
            "spore_studio_overview",
            "spore_studio_bindings",
            "spore_studio_counters",
            "spore_studio_dynamic_controls",
            "first_alert_setup",
            "source_controls",
        ],
    ),
    "spore_studio_counters": HelpTopic(
        id="spore_studio_counters",
        title="Counters in Spore Studio",
        category=HelpCategory.TEMPLATES,
        summary="Set up numeric counters, rules, persistence, image ranges, and binding adjustments",
        content="""
# Counters in Spore Studio

Counters are numeric values on **text** elements that update automatically from live events,
Stream Deck buttons, or [bindings](help:spore_studio_bindings). Use them for bit totals, sub
counts, game scores, and any HUD that should persist or animate as numbers change.

For canvas basics see [Designing Templates in Spore Studio](help:spore_studio_design).
For [data sources](help:spore_studio_data_sources) used in counter deltas, see that topic.

## When to Use Counter Mode

| Approach | Best for |
|----------|----------|
| **Counter mode** | Running totals, increment/decrement on events, persistence, tick-up animation |
| **Static text** + **set_text** binding | One-off labels that mirror a single payload field (e.g. username) |
| **Data display** mode | Read-only values (session stats, last cheer amount) without arithmetic |

Use **Instant** alert system on the [Canvas tab](help:spore_studio_design) for counter HUDs so
`instant_alert` events do not block the [alert queue](help:alerts_overview).

## Step-by-Step Setup

1. Drag a **Text** block onto the canvas and assign a category (e.g. `Counter`).
2. In **Properties**, set **Text mode** to **Counter**.
3. Configure the counter fields:

| Field | Purpose |
|-------|---------|
| **Counter id** | Unique id within this template (e.g. `bit_count`, `sub_count`) — used in bindings and image ranges |
| **Format** | Display string; use `{value}` for the number (e.g. `Bits: {value}`) |
| **Initial value** | Starting number when the overlay loads (often `0`) |

4. Click **+ Add counter rule** for each event that should change the value.
5. **Save** and test with **Preview** → mock **instant_alert** (bits/sub).

## Counter Rules

Each rule defines *when* and *how* the counter changes.

| Field | Options / notes |
|-------|-----------------|
| **Trigger** | **Registry event** or **Stream Deck action** |
| **Event** | e.g. `instant_alert`, `next_alert`, `new-message` |
| **Payload filters** | Optional rows (e.g. `alert_type` = `bit`) — same syntax as [bindings](help:spore_studio_bindings) |
| **Operation** | `increment`, `decrement`, `set`, `reset` |
| **Delta kind** | `fixed`, `random_int`, `random_float`, or `data_source` |
| **Delta value / min / max** | For fixed or random deltas |
| **Data source** | When delta kind is `data_source` — e.g. `alert.amt_cheered` for bits cheered |

### Example: bits counter on cheer

- Event: `instant_alert`
- Filter: `alert_type` = `bit`
- Operation: **increment**
- Delta kind: **data_source** → `alert.amt_cheered`

This matches the built-in `bitcounter` template pattern.

### Example: +1 per sub

- Event: `instant_alert`
- Filter: `alert_type` = `sub`
- Operation: **increment**
- Delta kind: **fixed** → `1`

## Persist (Database)

Enable **Persist (database)** to save the counter across overlay reloads and stream sessions.

| Field | Default | Purpose |
|-------|---------|---------|
| **Database path** | `{template_name}/counters` | `get_data` / `set_data` path segment |
| **Database key** | Same as counter id | Key within that path |

When persistence is **off**, the counter resets to **Initial value** every time the browser source reloads.

> **Tip:** Use persistence for marathon totals; leave it off for per-stream counters you reset manually.

## Value Change Animation

Separate from element **Entrance / Exit** animations (Show/Hide bindings):

| Field | Purpose |
|-------|---------|
| **Enable on value update** | Animate when the displayed number changes |
| **Animation type** | `tick_up` (count from previous value), `fade-in`, `slide-in`, `bounce` |
| **Duration (ms)** / **Easing** | Timing for the one-shot effect |
| **Continuous pulse** | Looping pulse on every update — can be distracting on fast cheers |

`tick_up` is ideal for bit/sub counters that jump by variable amounts.

## Image: From Counter (Ranges)

Tie an **Image** element's source to a counter value so art changes at thresholds.

1. Select an **Image** element → **Image source mode**: **From counter (ranges)**.
2. Pick the **Counter** (counter id from a text element in Counter mode).
3. Add **ranges**: min–max value → image URL from `assets/{template_name}/`.
4. Set **Default src** for values outside all ranges.

Optional **counter image transition** (roll, fade) animates swaps when the counter crosses a range boundary.

**Example (`bitcounter`):**

| Range | Image |
|-------|-------|
| 0–99 | `/assets/bitcounter/cheer1.gif` |
| 100–999 | `/assets/bitcounter/cheer100.gif` |
| 1000+ | `/assets/bitcounter/cheer1000.gif` |

## counter_adjust Binding Action

Use the **Adjust counter** binding action when you want to change a counter from the
[Bindings tab](help:spore_studio_bindings) without adding a counter rule — e.g. show/hide an
element and bump a counter in one binding chain.

| Arg | Purpose |
|-----|---------|
| `counter_id` | Target counter id from the text element |
| `operation` | `increment`, `decrement`, `set`, `reset` |
| `delta_kind` | `fixed`, `random_int`, `random_float`, `data_source` |
| `delta_value` | Fixed delta or fallback |
| `delta_source` | Data source id when delta kind is `data_source` |
| `delta_min`, `delta_max` | Random range bounds |

**Rules vs bindings:** Prefer **counter rules** for event-driven math (every cheer adds bits).
Use **counter_adjust** when the trigger is an element binding (e.g. chained after **Show**)
or a one-off adjustment from a different event than your main rules.

## Preview and Testing

1. **Save** (or use **Preview** with unsaved draft).
2. Open **Preview** → **Alerts:** → pick a bits or sub mock.
3. Confirm the number updates and any tick-up animation plays.
4. Reload the preview iframe to verify persistence settings.

## See Also

- [Data Sources & Data Displays](help:spore_studio_data_sources) — delta sources like `alert.amt_cheered`
- [Event Bindings & Actions](help:spore_studio_bindings) — `counter_adjust` and filters
- [Spore Studio Examples & Recipes](help:spore_studio_examples) — Recipe 2, Recipe 9
        """,
        keywords=[
            "counter",
            "counters",
            "bit counter",
            "sub counter",
            "increment",
            "persist",
            "counter rules",
            "counter_adjust",
            "tick_up",
            "spore studio",
        ],
        related_topics=[
            "spore_studio_design",
            "spore_studio_data_sources",
            "spore_studio_bindings",
            "spore_studio_examples",
        ],
    ),
    "spore_studio_data_sources": HelpTopic(
        id="spore_studio_data_sources",
        title="Data Sources & Data Displays",
        category=HelpCategory.TEMPLATES,
        summary="Read-only live values from alerts, chat, stats, config, and runtime database",
        content="""
# Data Sources & Data Displays

Data sources are curated live values from alerts, chat, session stats, and more. Use them in
**Data display** text elements (read-only) or as **counter deltas** (numeric adjustments).

See [Counters in Spore Studio](help:spore_studio_counters) for counter rules that consume data sources.

## Data Display Workflow

1. Add a **Text** block → **Text mode**: **Data display**.
2. Pick a **Data source** from the categorized dropdown.
3. Set **Format** — use `{value}` for the resolved value (e.g. `Cheered: {value} bits`).
4. Under **Refresh on events**, select socket events that should re-read the source
   (e.g. `instant_alert`, `new-message`, `pause_status_update`).
5. **Save** and test in **Preview**.

Data displays are **read-only** — they do not increment counters or fire side effects.

## Format Strings

| Pattern | Example output |
|---------|----------------|
| `{value}` | Raw resolved value |
| `User: {value}` | Prefix/suffix around value |
| `Tier {value}` | Works with string sources like `alert.tier` |

Value types follow the source: numbers, strings, or booleans (`alerts.paused`).

## Delta-Only vs Display Sources

| Flag | Meaning |
|------|---------|
| **Delta-only** (`fixed`, `random_int`, `random_float`) | For counter rule deltas only — not shown in data display picker |
| **Display sources** | All other registry entries — usable in data displays and as `data_source` counter deltas |

## Source Catalog

### Delta (counter deltas only)

| Id | Label | Notes |
|----|-------|-------|
| `fixed` | Fixed value | Use delta value field |
| `random_int` | Random integer | Inclusive min/max |
| `random_float` | Random float | Inclusive min/max, optional decimals |

### Alert payload

| Id | Label | Type |
|----|-------|------|
| `alert.amount` | Alert amount | number |
| `alert.quantity` | Alert quantity | number |
| `alert.tier` | Sub tier | string |
| `alert.amt_cheered` | Bits cheered | number |
| `alert.cumulative_months` | Cumulative months | number |
| `alert.raider_count` | Raider count | number |
| `alert.username` | Username | string |
| `alert.message` | Message | string |
| `alert.alert_type` | Alert type | string |
| `alert.currency` | Currency | string |
| `alert.queue_seq` | Queue sequence | number |

### Chat

| Id | Label | Type |
|----|-------|------|
| `chat.username` | Chat username | string |
| `chat.message` | Chat message | string |
| `chat.message_length` | Chat message length | number |
| `chat.userid` | Chat user ID | string |
| `chat.badges` | Chat badges | string |
| `chat.color` | Chat color | string |
| `chat.message_text` | Connector message text | string |

### Alert system

| Id | Label | Type |
|----|-------|------|
| `alerts.paused` | Alerts paused | boolean |

### Session stats

| Id | Label |
|----|-------|
| `stats.total_gift_subs` | Total gift subs (session) |
| `stats.total_bits` | Total bits (session) |
| `stats.follows` | Follows (session) |
| `stats.subs` | Subs (session) |
| `stats.raids` | Raids (session) |
| `stats.cheers` | Cheers (session) |

### Chatbot

| Id | Label |
|----|-------|
| `chatbot.gift_sub_quantity` | Gift sub quantity |
| `chatbot.gift_sub_tier` | Gift sub tier |
| `chatbot.raid_viewer_count` | Raid viewer count |

### Template

| Id | Label | Usage |
|----|-------|-------|
| `counter.{id}` | Another counter in this template | Use `counter.bit_count` etc. |
| `config.{id}` | Source Settings field | Reads exposed config by field id |

### Runtime database

| Id | Label | Usage |
|----|-------|-------|
| `runtime.{path}.{key}` | Runtime database field | User-defined path and key |

### Twitch API

| Id | Label | Usage |
|----|-------|-------|
| `twitch.{binding_id}.{path}` | Twitch API response field | Dot path into last `twitch-api-response` for a binding id |

Configure the binding id and Helix call in [Event Bindings & Actions](help:spore_studio_bindings)
under **Twitch API Bindings**, then reference response fields here.

## Counter Deltas vs Data Displays

| Use case | Mechanism |
|----------|-----------|
| Show last cheer amount (read-only) | Data display → `alert.amt_cheered`, refresh on `instant_alert` |
| Add cheer amount to running total | Counter rule → delta kind `data_source` → `alert.amt_cheered` |
| Show session sub count | Data display → `stats.subs`, refresh on `instant_alert` |
| Paused indicator | Data display → `alerts.paused`, refresh on `pause_status_update` |

## Common Patterns

**Last cheer username**

- Data display → `alert.username`
- Refresh on: `instant_alert`
- Format: `{value}`

**Session bits total (read-only)**

- Data display → `stats.total_bits`
- Refresh on: `instant_alert`, `refresh-alerts`

**Live config value**

- Data display → `config.my_color_field`
- Refresh on events that change Source Settings

## See Also

- [Counters in Spore Studio](help:spore_studio_counters)
- [Event Bindings & Actions](help:spore_studio_bindings) — Twitch API bindings
- [Configuring Template Settings](help:template_configuration) — exposed `config.{id}` fields
        """,
        keywords=[
            "data source",
            "data display",
            "alert payload",
            "session stats",
            "stats.total_bits",
            "alert.amt_cheered",
            "refresh on events",
            "spore studio",
        ],
        related_topics=[
            "spore_studio_counters",
            "spore_studio_design",
            "spore_studio_bindings",
            "template_configuration",
        ],
    ),
    "spore_studio_streamdeck": HelpTopic(
        id="spore_studio_streamdeck",
        title="Stream Deck Actions in Spore Studio",
        category=HelpCategory.TEMPLATES,
        summary="Define template actions, wire bindings, and map physical Stream Deck buttons",
        content="""
# Stream Deck Actions in Spore Studio

Stream Deck actions let physical Elgato buttons trigger your overlay — toggle logos, bump
counters, or emit custom socket events. Define actions in Spore Studio, wire them in
[Bindings](help:spore_studio_bindings), then map buttons in Mycelian's Stream Deck plugin.

## End-to-End Workflow

1. **Spore Studio** → open your template → **Stream Deck** inspector tab.
2. Click **+ Add Stream Deck action** and fill in each field (see table below).
3. **Save** the template so `streamdeck_options` is written to `template_configs/{name}.json`.
4. Select target elements → **Bindings** tab → trigger **Stream Deck action** → pick the action id.
5. In Mycelian's **Stream Deck plugin**: add a **Template Action** button → choose this template
   and the action (by id or display name).
6. Test with **Preview** → **SD:** mock buttons (one per defined action).

> **Important:** The plugin sends `actionName` that must match your **Action id** (or resolve
> via display name). Mismatched ids mean bindings never fire.

## Field Reference

| Field | Purpose |
|-------|---------|
| **Action id** | Stable key — referenced by bindings and the plugin (`actionName`) |
| **Display name** | Human label on the Stream Deck button picker |
| **Description** | Subtitle / tooltip in the plugin UI |
| **Socket event** | Event name emitted to the overlay when pressed |
| **default_data (JSON)** | Default payload object (e.g. `{}` or `{"visible": true}`) |

### Payload merge

When a button fires, Mycelian merges payloads:

1. Start with **default_data** from the action definition.
2. Overlay any fields sent from the plugin's **actionData** (if the user configured extra JSON).

Template config is authoritative for the **socket event** name when the action exists in
`streamdeck_options.actions`.

## Registry Events vs Custom Socket Events

| Choice | When to use |
|--------|-------------|
| **Registry event** as socket event | Reuse curated events like `instant_alert` with a custom filter |
| **Custom event name** (e.g. `toggle_logo`) | Simple toggle/hide logic bound only to Stream Deck |

Custom events do not need to appear in the event registry — bindings listen for the exact
**Socket event** string you define.

## Binding Patterns

### Toggle overlay visibility

1. Stream Deck action: id `toggle_logo`, socket event `toggle_logo`.
2. Image element binding: trigger **Stream Deck action** → `toggle_logo` → action **Toggle visibility**.

### Adjust counter on button press

1. Define Stream Deck action (any socket event, or reuse a registry event).
2. Binding on a container or dummy element: trigger **Stream Deck action** → action **Adjust counter**
   → `counter_id`, `operation`, delta fields. See [Counters](help:spore_studio_counters).

### Chained show + hide

Primary **Show**, chained **Hide** after `delay_ms` — triggered by Stream Deck action instead of
a registry event.

## Plugin Mapping (Mycelian Stream Deck Plugin)

1. Install the Mycelian Stream Deck plugin (`com.mushroomsuprise.mycelian`).
2. Add a **Template Action** key.
3. Select **Template** = your overlay name (e.g. `my_hud`).
4. Select **Action** = the action id or display name from the **Stream Deck** tab.
5. Optional: set extra JSON in the property inspector — merged over `default_data`.

After **Save** in Spore Studio, reload the plugin property inspector if the action list looks stale.

## Dynamic Controls Integration

A [dynamic control](help:spore_studio_dynamic_controls) can **Forward Stream Deck action**
(`streamdeck_forward`) so a Source Controls overlay button triggers the same socket event as a
physical Stream Deck key.

## Preview SD Section

The **Preview** mock toolbar shows an **SD:** row with one button per defined action. Click to
fire the same socket event and merged payload as production — no physical Stream Deck required.

## See Also

- [Event Bindings & Actions](help:spore_studio_bindings) — Stream Deck trigger type
- [Advanced JS, Preview & Legacy Templates](help:spore_studio_advanced) — Preview mocks
- [Dynamic Controls (Source Controls Tab)](help:spore_studio_dynamic_controls) — `streamdeck_forward`
- [Spore Studio Examples & Recipes](help:spore_studio_examples) — Recipe 6, Recipe 10
        """,
        keywords=[
            "stream deck",
            "streamdeck",
            "action id",
            "default_data",
            "actionName",
            "template action",
            "spore studio",
        ],
        related_topics=[
            "spore_studio_bindings",
            "spore_studio_advanced",
            "spore_studio_dynamic_controls",
            "spore_studio_examples",
        ],
    ),
    "spore_studio_dynamic_controls": HelpTopic(
        id="spore_studio_dynamic_controls",
        title="Dynamic Controls (Source Controls Tab)",
        category=HelpCategory.TEMPLATES,
        summary="Author live stream controls that appear in Mycelian's Source Controls overlay",
        content="""
# Dynamic Controls (Source Controls Tab)

The **Source Controls** inspector tab in Spore Studio authors `dynamic_controls` — buttons,
toggles, sliders, and more that appear in Mycelian's **[Source Controls](help:source_controls)**
main tab during your stream.

| Layer | Where | Purpose |
|-------|-------|---------|
| **Authoring** | Spore Studio → **Source Controls** tab | Define controls and wire actions |
| **Runtime** | Mycelian → **Source Controls** tab | Click controls live on stream |

Controls are saved into `templates/template_configs/{name}.json` on **Save**.

## Authoring Workflow

1. Open a Spore template (not legacy-only).
2. Go to the **Source Controls** inspector tab.
3. Click **+ Add control**.
4. Choose **Control type**, **Label**, and **Action**.
5. Fill action-specific parameters (counter id, element id, field id, etc.).
6. **Save** the template.
7. During stream: Mycelian **Source Controls** tab → select your template → use the new controls.

Empty state: *Source Controls are available for Spore Studio templates.*

Hint in editor: *Controls appear in the Source Controls overlay and emit template socket events.*

## Control Types

| Type | User interaction |
|------|------------------|
| **button** | Click fires the action once |
| **toggle** | On/off state sent with the action |
| **text_input** | User types text; value sent on confirm |
| **number_input** | Numeric entry |
| **slider** | Drag to set a numeric value |
| **select** | Pick from predefined options |
| **counter_control** | One Source Controls box per counter: editable step amount, − / + / Reset |

## Actions Reference

### Global alert system

| Action | Effect |
|--------|--------|
| `pause_alerts` | Pause the alert processor |
| `resume_alerts` | Resume alerts |
| `toggle_alerts` | Toggle pause state |
| `skip_alert` | Skip current queued alert |
| `clear_alert_queue` | Empty the alert queue |
| `refresh_alerts` | Reload alert settings in overlays |

### Template elements and counters

| Action | Params | Effect |
|--------|--------|--------|
| `counter_adjust` | `target_counter_id`, `operation` | Increment/decrement/set/reset a counter |
| `element_show` | `element_id` | Show element by id |
| `element_hide` | `element_id` | Hide element |
| `element_toggle` | `element_id` | Toggle visibility |

### Source Settings

| Action | Params | Effect |
|--------|--------|--------|
| `set_config_value` | `field_id`, `value_key` | Update an exposed Source Settings field |

Requires the field to be **Expose in Source Settings (JSON)** in Spore Studio — see
[Designing Templates](help:spore_studio_design).

### Integrations

| Action | Params | Effect |
|--------|--------|--------|
| `twitch_api_request` | `endpoint`, `method` | Fire a Helix request from the overlay |
| `websocket_emit` | `event_name`, `payload_json` | Emit a custom socket event |
| `streamdeck_forward` | `action_name` | Trigger a [Stream Deck action](help:spore_studio_streamdeck) by id |
| `custom_template_action` | `action`, `payload_json` | Emits `{template_name}_{action}` |

## counter_control vs counter_adjust

| Approach | When |
|----------|------|
| **counter_control** type | One dock widget per counter: pick the counter from a dropdown, set a default step, then use − / + / Reset in Source Controls |
| **button** + `counter_adjust` action | A single labeled button for one fixed operation (e.g. “Add 100 bits”) |

Both target a **counter id** from a text element in **Counter** mode. The **counter_control**
type always uses the `counter_adjust` action under the hood; operation and delta are chosen
when you click −, +, or Reset in Source Controls.

## Example: Game Overlay Controls

1. **button** — Label `Pause alerts` → action `toggle_alerts`.
2. **counter_control** — Label `Score` → pick counter `game_score`, default step `1` (− / + / Reset in Source Controls).
3. **button** — Label `Show bonus` → action `element_show` → `element_id`: `bonus_banner`.
4. **slider** — Label `Title size` → action `set_config_value` → `field_id`: `title_font_size`.

**Save**, then open **Source Controls** during stream to operate these without reopening Spore Studio.

## Runtime Usage

For how to use controls on stream (hotkeys, grouping, performance), see
[Real-time Source Controls](help:source_controls).

## See Also

- [Configuring Template Settings](help:template_configuration) — JSON config structure
- [Counters in Spore Studio](help:spore_studio_counters) — counter ids for `counter_adjust`
- [Stream Deck Actions in Spore Studio](help:spore_studio_streamdeck) — `streamdeck_forward`
- [Spore Studio Examples & Recipes](help:spore_studio_examples) — Recipe 10
        """,
        keywords=[
            "dynamic controls",
            "source controls tab",
            "dynamic_controls",
            "pause alerts",
            "counter control",
            "button",
            "toggle",
            "spore studio",
        ],
        related_topics=[
            "source_controls",
            "spore_studio_design",
            "spore_studio_counters",
            "spore_studio_streamdeck",
            "template_configuration",
        ],
    ),
    # =========================================
    # Integrations
    # =========================================
    "integrations_twitch": HelpTopic(
        id="integrations_twitch",
        title="Twitch Integration Setup",
        category=HelpCategory.INTEGRATIONS,
        summary="Complete guide to connecting Twitch services",
        content="""
# Twitch Integration Setup

Connect Mycelian to your Twitch channel for real-time events.
For the quick-start version, see [Connecting Your Twitch Account](help:twitch_setup).

## Prerequisites

- Active Twitch account
- Channel ownership or moderator permissions
- Stable internet connection

## Quick Setup

### Automatic Connection
1. Open **Settings** → **Twitch** tab
2. Click **"Connect with Twitch"**
3. Authorize Mycelian in browser
4. Return to app - status shows "Connected"

### Manual Setup (Advanced)
For custom applications:
1. Visit [Twitch Developer Console](https://dev.twitch.tv/console)
2. Create application
3. Set redirect URL: `http://localhost:17563`
4. Enter Client ID and Secret in settings

## Required Permissions

Mycelian requests these scopes:
- **channel:read:subscriptions** - Subscription events
- **bits:read** - Bit cheer events
- **channel:read:redemptions** - Channel point rewards
- **chat:read** - Chat messages (for chatbot)
- **chat:edit** - Send chat messages

## Event Types

### Viewer Events
- **Follows**: New followers
- **Subscriptions**: New and renewed subs
- **Gift Subs**: Gifted subscriptions
- **Bits**: Cheer events

### Channel Events
- **Raids**: Incoming raids
- **Channel Points**: Reward redemptions
- **Hype Train**: Progress events
- **Polls/Predictions**: Interactive events

## Connection Status

| Status | Description | Action |
|--------|-------------|--------|
| 🟢 Connected | Active connection | None |
| 🟡 Connecting | Establishing link | Wait |
| 🔴 Disconnected | No connection | Click Connect |
| ⚠️ Error | Connection failed | Check logs |

## Troubleshooting

### Connection Issues
**"Authentication Failed"**
- Clear browser cache/cookies
- Try different browser
- Check pop-up blockers

**"Invalid Token"**
- Reconnect account
- Check account permissions
- Verify application settings

**"Rate Limited"**
- Wait before retrying
- Reduce API calls
- Check usage limits

### Event Issues
**Missing Events**
- Verify webhook subscriptions
- Check event log
- Test with manual triggers

**Delayed Events**
- Check network connection
- Monitor Twitch status
- Verify server stability

## Advanced Configuration

### Event Filtering
- Enable/disable specific events
- Set minimum thresholds
- Configure spam protection

### Custom Handling
- Create custom event responses
- Set up [connector triggers](help:connector_triggers)
- Configure [alert](help:alerts_overview) mappings

> **Important:** If events stop arriving, check [Connection Troubleshooting](help:troubleshooting_connections) for solutions.

## Security

### Token Management
- Tokens auto-refresh when possible
- Manual reconnection for expired tokens
- Secure credential storage

### Permission Levels
- Review requested scopes
- Grant minimal required access
- Revoke access when needed
        """,
        keywords=["twitch", "integration", "oauth", "events", "webhooks"],
        related_topics=["getting_started_intro", "alerts_overview"],
    ),
    # =========================================
    # Settings
    # =========================================
    "settings_overview": HelpTopic(
        id="settings_overview",
        title="Application Settings Overview",
        category=HelpCategory.SETTINGS,
        summary="Understanding and configuring Mycelian settings",
        content="""
# Application Settings Overview

Configure Mycelian to match your streaming setup and preferences.

## Settings Categories

### Integration Settings
- **Twitch**: [Connect your channel](help:twitch_setup)
- **Donation alerts**: Configure in **Alerts** → donation alert types and [alert overview](help:alerts_overview)
- **Spotify**: [Music integration](help:integrations_spotify)
- **OBS**: WebSocket connection

### Alert Settings
- **Global Settings**: Default behaviors
- **Media Paths**: File locations
- **Performance**: Timing and limits

### Interface Settings
- **Theme**: Light/dark mode
- **Layout**: Window preferences
- **Notifications**: System alerts

## Accessing Settings

### Main Settings Window
- Click **Settings** button in main window
- Or use **File** → **Settings** menu
- Organized in tabbed interface

### Service-Specific Settings
- Integration settings in respective tabs
- Alert settings in Alerts tab
- Template settings in Custom Sources tab

## Configuration Files

Settings are stored in:
- **User settings**: `config/user_config.json`
- **Template configs**: `templates/template_configs/*.json`
- **Credentials**: Encrypted storage

## Backup and Restore

### Automatic Backups
- Settings backed up on changes
- Restore from backup folder
- Version history maintained

### Manual Backup
1. Go to Settings → Backup tab
2. Click **"Create Backup"**
3. Save file to safe location

## Best Practices

### Organization
- Group related settings
- Use descriptive names
- Document custom configurations

### Testing
- Test changes before going live
- Use preview modes when available
- Verify integration connections

### Security
- Keep credentials secure
- Use strong passwords
- Regular backup schedule

> **Tip:** If you're just getting started, follow the [Welcome to Mycelian](help:getting_started_intro) guide which walks through essential settings step by step.
        """,
        keywords=["settings", "configuration", "preferences", "setup"],
        related_topics=["integrations_twitch", "settings_backup", "game_hooks"],
    ),
    "game_hooks": HelpTopic(
        id="game_hooks",
        title="Game Hooks (live game data)",
        category=HelpCategory.SETTINGS,
        summary="Read party and battle stats from supported games, optional crowd-control writes, and browser overlays.",
        content="""
# Game Hooks

Game Hooks let Mycelian attach to a supported PC game (currently **Final Fantasy VII** English PC / Steam: `ff7_en.exe` or `ff7.exe`) and read live memory. Data is pushed over Socket.IO to browser templates such as the **FF7** overlay (`/ff7`).

## Enabling FF7

1. Open **Settings** → **Game Hooks**
2. Turn on **Final Fantasy VII (2013)**
3. Click **Save**
4. Start the game on the same machine as Mycelian (Windows only for memory access)

If the hook is disabled, templates still load but receive a stub payload with `disabled: true`.

## What is read (FF7)

When attached, the hook sends roughly four times per second:

- **Party**: names, levels, HP, max HP, MP, max MP, limit gauge, ATB (in battle the values are live battle memory; on the field they come from the in-RAM savemap).
- **Enemies** (battle only): name, level, HP, MP, ATB.
- **Gil** and **play time** from the savemap.
- **Boss log**: defeated boss names (newest first), persisted until you reset from the template.
- **Menu colors**: window gradient RGB from the savemap is mapped to overlay panel colors so the browser source can match your configured window style.

## Crowd control (writing memory)

Connectors can run a **Game Hook (memory write)** action. Each action targets one game (FF7 today) and one **operation** with arguments as shown in the connector UI.

Typical FF7 operations include **Add gil** / **Remove gil**, **Add or remove HP**, **KO party member**, **Kill enemy** / **Kill all enemies**, **Damage enemy**, **Rename character**, **Set battle status**, **Change character gear** (now swaps equipped items with the inventory list), **Menu row access** / **Set menu row visibility** / **Set menu row lock** (optional **duration_sec** reverts the full menu visibility+lock words), **Game speed** (0.25×–8× in 0.25 steps; vanilla tick f64 / FFNx literal f64 — see **Community credit (m4v3k)** below; timed restores pause if the game process is not attached), **Battle speed** (savemap config byte 0x10D8; optional timed restore), **Battle ATB mode** (**active** / **wait** / **recommended** on savemap 0x10DA; optional timed restore), **Battle infinite items** (experimental: tops up battle item quantities in RAM while enabled; optional duration), **Set party / enemy level**, **Set party / enemy stat** (str / vit / mag / spr / dex / luk plus hp / mp / max_hp / max_mp; enemies also expose speed / evade / def / mdef), **Change menu colors** (named colors, `#RRGGBB`, or `rgb(r,g,b)` on any corner or all four), **Equip / unequip materia** (validates and swaps through the inventory list), **Start battle** (queued if currently in menu / victory), **Add item** / **Add materia** / **Add gear** (calls the game's own `party_add_item_fn` / `party_add_materia_fn`).

Selected overrides (game speed, battle speed, ATB mode, menu rows, field menu access, world speed multiplier, menu colors, infinite items) are **persisted** under `GameHooks/ff7_connector_overrides` and **re-applied** when the hook next attaches (timed entries use remaining wall-clock time). One-shot inventory or stat writes are not persisted.

In action arguments, use single-brace placeholders with **no spaces** inside the braces, for example `{username}`, `{message}`, `{message.word.1}`, `{message_after_conditions}` (chat text after stripping `message` trigger-condition literals), `{message_after_word.1}` or `{message_after_conditions.word.2}` (single word *N* of that stripped text), `{message_after_from_word.3}` or `{message_after_conditions.from_word.3}` (from word *N* through the end—use for multi-word values such as item names), `{hooks.ff7.party.0.name}`, `{random_character}`, `{random_enemy}`, `{random_damage.1.9999}`. **Chained action outputs** in the same connector use `{actionN.field}` where **N** matches the action's number in the list (e.g. Action #1 → `{action1.quantity}`), such as `item_name`, `quantity`, `resolved_name`, `kind`, and `error` from **Query inventory quantity**. Legacy `{{key}}` forms are still accepted and normalized.

**Battle-only** actions return failure with a short message if you are not in combat. Running as Administrator may be required if Windows denies process memory access.

## Security and fair play

Memory reads and writes are powerful. Use writes responsibly on your own game session; anti-cheat or online services are not a target for this feature.

## Community credit (m4v3k)

Huge shoutouts to **m4v3k** for all of their work and contributions to the FF7 community. I was able to use his tools and research to create this game hook, so please check out his other projects:

- [FF7 Ultima](https://github.com/maciej-trebacz/ff7-ultima) — real-time game editor
- [Landscaper](https://github.com/maciej-trebacz/ff7-landscaper) — world map editor
- [LGP Explorer](https://github.com/maciej-trebacz/ff7-lgp-explorer) — LGP archive browser and asset tools
- [FF7lib](https://github.com/maciej-trebacz/ff7-lib.rs) — Rust library for FF7 memory and data structures

## Related

- [Connector Actions](help:connector_actions) — trigger wiring
- [Settings overview](help:settings_overview)
        """,
        keywords=[
            "ff7",
            "memory",
            "overlay",
            "battle",
            "gil",
            "crowd control",
            "connectors",
        ],
        related_topics=["settings_overview", "connector_actions", "templates_intro"],
    ),
    # =========================================
    # Troubleshooting
    # =========================================
    "troubleshooting_alerts": HelpTopic(
        id="troubleshooting_alerts",
        title="Troubleshooting Alerts",
        category=HelpCategory.TROUBLESHOOTING,
        summary="Common alert issues and solutions",
        content="""
# Troubleshooting Alerts

## Alerts Not Showing

### Check Browser Source
1. Verify URL is correct: `http://localhost:5000/alerts` (see [OBS Setup](help:obs_setup))
2. Refresh the browser source in OBS
3. Check OBS source is visible (eye icon)

### Check Twitch Connection
1. Go to [Settings → Twitch](help:twitch_setup)
2. Status should show "Connected"
3. If not, click "Reconnect" (see [Connection Issues](help:troubleshooting_connections))

### Check Alert Settings
1. Go to Alerts tab
2. Verify alert type is enabled (toggle switch)
3. Test with the "Test" button

## Audio Not Playing

> **Note:** For more in-depth audio help, see the dedicated [Audio Troubleshooting](help:troubleshooting_audio) guide.

### Browser Audio Policy
Modern browsers block autoplay. Solutions:
1. In OBS, right-click browser source → Properties
2. Enable "Control audio via OBS"
3. Or click the browser source once to interact

### Audio File Issues
- Verify audio file exists in the specified path
- Check file format is supported (MP3, WAV, OGG)
- Ensure volume is not 0

## GIF Not Animating

### Common Causes
- File might be static image renamed to .gif
- GIF too large, browser struggling
- Path incorrect

### Solutions
- Verify file is actual animated GIF
- Optimize large GIFs (reduce frames/colors)
- Check console for 404 errors

## Alerts Delayed

### Possible Causes
- Network latency to Twitch
- Queue backing up
- Long alert durations

### Solutions
- Check internet connection
- Reduce alert duration
- Increase delay between alerts
- Check if alerts are paused

## Alert Queue Issues

### Symptoms
- Alerts playing out of order
- Missing alerts
- Stuck on one alert

### Solutions
1. Check Activity Feed for queued alerts
2. Use "Skip" button if alert is stuck
3. "Clear Queue" to reset
4. Restart the app if persistent
        """,
        keywords=["troubleshoot", "fix", "problem", "not working", "issue", "error"],
        related_topics=["alerts_overview", "obs_setup"],
    ),
    "troubleshooting_connections": HelpTopic(
        id="troubleshooting_connections",
        title="Connection and Integration Issues",
        category=HelpCategory.TROUBLESHOOTING,
        summary="Fixing connection problems with Twitch and other services",
        content="""
# Connection and Integration Issues

## Twitch Connection Problems

> **Tip:** Make sure you've followed the [Twitch Setup](help:twitch_setup) steps first.

### Connection Status: Disconnected

**Symptoms:**
- Status shows 🔴 Disconnected
- No events received
- [Alerts](help:alerts_overview) not triggering

**Solutions:**
1. Click **"Reconnect"** button
2. Check internet connection
3. Verify Twitch account permissions
4. Clear browser cache and retry

### Authentication Failed

**Symptoms:**
- "Authentication failed" error
- Browser popup closes immediately

**Solutions:**
1. Clear browser cookies for twitch.tv
2. Disable pop-up blockers
3. Try different browser
4. Check if Twitch is down

### Token Expired

**Symptoms:**
- Events stop working after some time
- Status shows ⚠️ Error

**Solutions:**
1. Click **"Reconnect"** to refresh token
2. Check token expiration settings
3. Verify app permissions haven't changed

## Donation alerts

Configure how donation [alerts](help:alerts_overview) look and sound in the **Alerts** tab (donation alert types and thresholds).

## WebSocket Connection Issues

### OBS Connection Failed

**Symptoms:**
- Cannot connect to OBS WebSocket
- Template controls not working

**Solutions:**
1. Verify OBS WebSocket plugin installed
2. Check OBS WebSocket settings
3. Ensure correct port/password
4. Restart OBS and Mycelian

### Template Not Updating

**Symptoms:**
- Browser sources show old data
- Real-time controls not working

**Solutions:**
1. Refresh browser sources in OBS
2. Check WebSocket connection status
3. Verify template URLs are correct
4. Restart Mycelian web server

## Network Issues

### Firewall Blocking

**Symptoms:**
- Cannot connect to services
- Timeouts and connection errors

**Solutions:**
1. Add Mycelian to firewall exceptions
2. Allow ports 5000 (web) and 17563 (OAuth)
3. Check antivirus software
4. Try different network

### DNS Resolution

**Symptoms:**
- Cannot reach twitch.tv or other services
- "Host not found" errors

**Solutions:**
1. Check DNS settings
2. Try different DNS servers (8.8.8.8)
3. Restart network equipment
4. Contact ISP if persistent

## Service Status

### Checking Service Status
- **Twitch Status**: [status.twitch.tv](https://status.twitch.tv/)
- **OBS WebSocket**: Check OBS logs

### When Services are Down
- Monitor status pages for updates
- Use backup alert methods
- Inform viewers of technical issues
- Resume when services restore

## Advanced Diagnostics

### Log Files
Check Mycelian logs for detailed error information:
- Connection attempts
- API responses
- WebSocket messages
- Error stack traces

### Network Tools
Use diagnostic tools:
- `ping twitch.tv` - Check connectivity
- `tracert twitch.tv` - Trace network path
- Browser developer tools for API calls

### Configuration Verification
- Verify all credentials are correct
- Check API keys and secrets
- Confirm webhook URLs
- Validate JSON configurations
        """,
        keywords=[
            "connection",
            "network",
            "integration",
            "webhook",
            "websocket",
            "api",
        ],
        related_topics=["integrations_twitch", "alerts_overview"],
    ),
    "troubleshooting_audio": HelpTopic(
        id="troubleshooting_audio",
        title="Audio Troubleshooting",
        category=HelpCategory.TROUBLESHOOTING,
        summary="Fix common audio issues with alerts and browser sources",
        content="""
# Audio Troubleshooting

Solve common issues with [alert](help:alerts_overview) sounds, music widgets, and audio playback.
See [Alert Media Configuration](help:alert_media) for file format requirements.

## No Sound Playing

### Check Volume Settings
1. **Alert Volume**: In Alerts tab, check volume slider isn't at 0
2. **Master Volume**: Check Mycelian isn't muted
3. **System Volume**: Verify Windows/Mac volume mixer
4. **OBS Volume**: Check browser source audio in OBS mixer

### Browser Source Audio in OBS
OBS browser sources have their own audio:

1. In OBS, find the browser source in Audio Mixer
2. Make sure it's not muted (speaker icon)
3. Check the volume slider
4. Try clicking the source and pressing "Interact" to allow audio

### Autoplay Restrictions
Modern browsers block autoplay audio:

**In OBS:**
1. Edit the Browser Source
2. Enable "Control audio via OBS"
3. Check "Refresh browser when scene becomes active"

**Alternative:**
Some overlays require user interaction to enable audio.
Click "Interact" on the browser source once.

## Audio Too Quiet/Loud

### Adjusting Alert Volume
1. Go to **Alerts** tab
2. Find the alert type (bits, subs, etc.)
3. Adjust the **Volume** slider
4. Click **Test** to preview

### Normalizing Audio Files
If some sounds are louder than others:
1. Use audio editing software (Audacity)
2. Normalize all files to same level (-12 to -6 dB)
3. Re-import the normalized files

### Volume Levels by Alert Type
Set different volumes for priority:
- **Follow alerts**: 50-70%
- **Sub alerts**: 70-90%
- **Big donations**: 90-100%

## Audio Crackling/Distortion

### Causes
- Corrupted audio files
- High CPU usage
- Incorrect sample rate
- Browser source overload

### Solutions
1. **Replace audio file**: Re-download or convert the file
2. **Reduce CPU load**: Close unnecessary programs
3. **Check sample rate**: Use 44.1kHz or 48kHz files
4. **Lower browser FPS**: Set browser source to 30 FPS

## Audio Delay

### Browser Source Delay
Browser sources may have slight delay (100-500ms):
1. This is normal for browser audio
2. Adjust OBS audio sync offset if needed
3. Go to Advanced Audio Properties
4. Set Sync Offset (negative value to speed up)

### Alert Queue Delay
If alerts play with delay:
1. Check alert queue settings
2. Reduce minimum time between alerts
3. Verify network connection

## Audio File Issues

### Supported Formats
| Format | Support | Notes |
|--------|---------|-------|
| MP3 | ✅ Best | Most compatible |
| WAV | ✅ Good | Larger files |
| OGG | ✅ Good | Open format |
| AAC | ⚠️ Varies | Browser dependent |
| FLAC | ❌ No | Not web compatible |

### File Too Long
- Alert sounds should be 3-10 seconds
- Longer files cause delays
- Trim audio before importing

### File Too Large
- Keep files under 2MB
- Compress to 128-192 kbps MP3
- Avoid uncompressed formats

## OBS Browser Source Audio

### "Browser source audio not showing in mixer"
1. Edit the Browser Source
2. Check "Control audio via OBS"
3. Restart OBS
4. Source should appear in Audio Mixer

### Audio From Multiple Sources
If you have multiple browser sources:
1. Each source has separate audio
2. Mix volumes in OBS Audio Mixer
3. Consider using a single alerts source

### Monitor and Output
To hear browser audio in headphones:
1. Right-click audio source in mixer
2. Advanced Audio Properties
3. Set Audio Monitoring to "Monitor and Output"

## Spotify Widget Audio

### No Music Playing
The [Spotify widget](help:integrations_spotify) displays info only - actual audio
comes from Spotify app:
- Widget shows "Now Playing" info
- Audio plays from Spotify client
- No audio routing through Mycelian

## Quick Fixes

### No audio at all?
1. Check system volume
2. Check OBS mixer
3. Click "Interact" on browser source
4. Restart browser source

### Intermittent audio?
1. Check CPU usage
2. Update audio drivers
3. Reduce browser source count
4. Simplify templates

### Wrong audio playing?
1. Clear alert queue
2. Verify correct audio file assigned
3. Check for duplicate alerts
4. Test with different alert type
        """,
        keywords=[
            "audio",
            "sound",
            "volume",
            "music",
            "mute",
            "quiet",
            "loud",
            "crackling",
        ],
        related_topics=["alert_media", "troubleshooting_alerts", "obs_setup"],
    ),
    "troubleshooting_performance": HelpTopic(
        id="troubleshooting_performance",
        title="Performance Issues and Optimization",
        category=HelpCategory.TROUBLESHOOTING,
        summary="Fixing lag, high CPU usage, and performance problems",
        content="""
# Performance Issues and Optimization

## High CPU Usage

> **Tip:** If you're experiencing lag in OBS, also check your [browser source settings](help:obs_setup).

### Symptoms
- Mycelian using excessive CPU
- System slowdowns
- OBS performance impacted

### Causes and Solutions

**Large GIF Files**
- Optimize GIFs (reduce colors/frames)
- Use WebP or APNG formats
- Resize large animations

**Too Many Browser Sources**
- Limit to essential templates only
- Enable "Shutdown when not visible"
- Use lower resolution sources

**Frequent Updates**
- Reduce WebSocket message frequency
- Batch template updates
- Use appropriate update intervals

## Memory Issues

### High Memory Usage
**Symptoms:**
- Increasing RAM usage over time
- System running out of memory
- Application slowdowns

**Solutions:**
1. Monitor memory usage in Task Manager
2. Restart Mycelian periodically
3. Reduce concurrent browser sources
4. Close unused tabs/windows

### Memory Leaks
**Symptoms:**
- Memory usage grows without stopping
- Performance degrades over time

**Solutions:**
1. Update to latest Mycelian version
2. Clear browser caches regularly
3. Reduce template complexity
4. Report issue to developers

## Lag and Delays

### Alert Delays
**Symptoms:**
- Alerts appear seconds late
- Events not processed immediately

**Causes:**
- Network latency
- Queue processing delays
- Large media files

**Solutions:**
1. Check internet connection speed
2. Reduce alert queue size
3. Optimize media file sizes
4. Use faster storage drives

### OBS Lag
**Symptoms:**
- OBS becomes unresponsive
- Browser sources lag
- Stream quality issues

**Solutions:**
1. Enable OBS hardware acceleration
2. Reduce browser source resolution
3. Close unnecessary OBS plugins
4. Update graphics drivers

## Web Server Issues

### Slow Template Loading
**Symptoms:**
- Browser sources load slowly
- Template updates delayed

**Solutions:**
1. Enable browser caching
2. Optimize template assets
3. Use CDN for static files
4. Check server resource usage

### WebSocket Disconnects
**Symptoms:**
- Real-time updates stop working
- [Template controls](help:source_controls) fail

**Solutions:**
1. Check [WebSocket](help:template_websocket) connection logs
2. Reduce message frequency
3. Implement reconnection logic
4. Verify firewall settings

## Storage Performance

### Slow File Access
**Symptoms:**
- Media files load slowly
- Alert delays during playback

**Solutions:**
1. Use SSD storage for assets
2. Defragment hard drives
3. Organize files logically
4. Cache frequently used files

### Large Asset Libraries
**Symptoms:**
- Long startup times
- High memory usage

**Solutions:**
1. Archive unused assets
2. Use external storage for backups
3. Implement asset lazy loading
4. Clean up duplicate files

## Network Optimization

### Bandwidth Issues
**Symptoms:**
- Slow API responses
- Delayed event processing

**Solutions:**
1. Monitor network usage
2. Reduce concurrent connections
3. Use compression for API calls
4. Optimize webhook payloads

### Connection Limits
**Symptoms:**
- Rate limiting errors
- Failed API requests

**Solutions:**
1. Implement request queuing
2. Use appropriate retry logic
3. Respect API rate limits
4. Cache API responses

## Monitoring and Diagnostics

### Performance Monitoring
- Use Task Manager/Resource Monitor
- Check Mycelian logs for performance warnings
- Monitor OBS performance counters

### Profiling Tools
- Browser developer tools for templates
- Network monitoring tools
- System performance profilers

### Optimization Checklist
- [ ] Update to latest version
- [ ] Optimize all media files
- [ ] Reduce browser source count
- [ ] Enable hardware acceleration
- [ ] Monitor resource usage
- [ ] Clean up unused assets
- [ ] Check network performance
        """,
        keywords=["performance", "lag", "cpu", "memory", "optimization", "speed"],
        related_topics=["obs_setup", "template_configuration"],
    ),
    # =========================================
    # Additional Integrations
    # =========================================
    "integrations_spotify": HelpTopic(
        id="integrations_spotify",
        title="Spotify Integration",
        category=HelpCategory.INTEGRATIONS,
        summary="Display currently playing music on your stream",
        content="""
# Spotify Integration

Connect Spotify to display your currently playing music on stream with beautiful
"Now Playing" overlays. Add overlays to your stream via [browser sources](help:obs_setup).

## Features

- **Now Playing Widget**: Show current song, artist, and album art
- **Real-time Updates**: Track changes update automatically
- **Customizable Display**: Match your stream's aesthetic
- **Pause Detection**: Hide widget when music is paused

## Setup Process

### Spotify Requirements
- A [Spotify Premium](https://www.spotify.com/premium/) subscription
- Active Spotify session (desktop app, web player, or mobile)
- Music must be playing for the widget to display

### Quick Connect
1. Go to **Settings** → **Spotify** tab
2. If you use your own Spotify app (see **Create a Spotify app** below), paste **Client ID** and **Client Secret** into the fields, then click **Save**. Credentials are not stored until you save.
3. Click **Connect**
4. Authorize Mycelian in the browser
5. Return to the app — status should show **Connected**

### Create a Spotify app (Developer Dashboard)
Use these steps when Mycelian should use **your** Spotify application credentials (from the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)):

1. Open the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and sign in with the Spotify account you want to connect.
2. Click **Create app**. Enter an app name and description when prompted.
3. Under **Redirect URIs**, add **exactly** `http://127.0.0.1:9973` — this is Mycelian's OAuth callback. It must match character-for-character (`http`, `127.0.0.1`, port `9973`, no trailing slash).
4. In the app's API settings, enable **Web API** and **Web Playback SDK** (use the same labels shown in the developer console).
5. Save the app settings. On the app page, copy the **Client ID**. Reveal and copy the **Client secret** (Spotify may label it **Client Secret**).
6. In Mycelian, open **Settings** → **Spotify**. Paste **Client ID** and **Client Secret** into the matching fields, click **Save**, then click **Connect** to finish signing in through your browser.

## Browser Source Setup

### Adding to OBS
1. Go to **Templates** → **Now Playing**
2. Copy the browser source URL
3. In OBS, add a **Browser Source**
4. Paste the URL
5. Set dimensions (recommended: 400x100 or custom)

### Recommended Settings
- **Width**: 400px (adjustable)
- **Height**: 100px (adjustable)
- **FPS**: 30
- **CSS**: Leave empty (styling is built-in)

## Widget Customization

### Display Options
- **Show Album Art**: Toggle album artwork visibility
- **Show Artist**: Display artist name
- **Show Progress Bar**: Show song progress
- **Animation Style**: Slide, fade, or bounce

### Styling
- **Theme**: Light, dark, or transparent
- **Font**: Match your stream font
- **Colors**: Custom accent colors
- **Size**: Compact or expanded

## Troubleshooting

### Connection Issues
**"Authentication Failed"**
- Clear browser cookies and retry
- Check Spotify account status
- Try a different browser

**"No Playback Detected"**
- Ensure Spotify is actively playing
- Check if the correct device is selected
- Verify Spotify app is not in offline mode

### Display Issues
**"Widget Not Showing"**
- Verify browser source URL is correct
- Check if music is currently playing
- Refresh the browser source in OBS

**"Album Art Missing"**
- Some local files may not have artwork
- Check Spotify's metadata for the track
- Use fallback image in settings

### Update Delays
**"Song Changes Are Slow"**
- Spotify API has a ~3 second refresh rate
- This is a Spotify limitation
- Updates are as fast as the API allows

## Privacy Notes

- Mycelian only reads playback information
- No listening history is stored
- Credentials are securely encrypted
- You can disconnect at any time

> **Tip:** Customize the widget appearance in [Template Configuration](help:template_configuration) to match your stream's style.
        """,
        keywords=["spotify", "music", "now playing", "song", "audio", "integration"],
        related_topics=["templates_intro", "obs_setup"],
        ui_context="settings.spotify",
    ),
    "integrations_psn": HelpTopic(
        id="integrations_psn",
        title="PlayStation Network Integration",
        category=HelpCategory.INTEGRATIONS,
        summary="Display PSN trophies and gaming activity on stream",
        content="""
# PlayStation Network Integration

Connect your PlayStation Network account to display trophy achievements
and gaming activity on your stream via [browser source overlays](help:obs_setup).

## Features

- **Trophy Alerts**: Show trophy pop-ups when you earn achievements (uses the [alert system](help:alerts_overview))
- **Trophy Progress**: Display completion percentage for games
- **Now Playing**: Show which PS game you're currently playing
- **Profile Display**: Show your PSN profile and avatar

## Setup Process

### Authentication
1. Go to **Settings** → **PSN** tab
2. Click **"Connect with PSN"**
3. Enter your PSN credentials in the secure browser
4. Complete two-factor authentication if enabled
5. Return to app - status shows "Connected"

### NPSSO Token (Alternative Method)
If automatic login fails:
1. Log into [PlayStation Store](https://store.playstation.com) in your browser
2. Visit: `https://ca.account.sony.com/api/v1/ssocookie`
3. Copy the `npsso` token value
4. Paste into Settings → PSN → NPSSO Token field
5. Click "Authenticate"

## Trophy Alerts

### Configuration
- **Trophy Types**: Enable/disable by type (Bronze, Silver, Gold, Platinum)
- **Minimum Rarity**: Only show rare trophies (optional)
- **Alert Duration**: How long the alert displays
- **Sound**: Custom trophy sound effects

### Trophy Data
Each trophy alert includes:
- **Name**: Trophy title
- **Description**: Achievement description
- **Rarity**: Percentage of players who earned it
- **Game**: Which game it's from
- **Image**: Trophy icon

## Browser Sources

### Trophy Alert Overlay
1. Copy the PSN Alert URL from Templates
2. Add as Browser Source in OBS
3. Position where you want trophy pop-ups
4. Recommended size: 400x150

### Trophy Progress Widget
- Shows current game's trophy completion
- Updates as you earn trophies
- Customizable appearance

## Troubleshooting

### Connection Issues
**"Authentication Failed"**
- Double-check PSN credentials
- Complete 2FA if prompted
- Try the NPSSO token method

**"Token Expired"**
- Tokens expire after ~60 days
- Re-authenticate when prompted
- Use "Refresh Token" button

**"Account Not Found"**
- Verify PSN ID spelling
- Check privacy settings on PlayStation
- Ensure account is not suspended

### Trophy Issues
**"Trophies Not Showing"**
- Verify trophy sync is enabled on PlayStation
- Check game-specific trophy settings
- Allow time for PSN to sync (can take minutes)

**"Delayed Trophy Alerts"**
- PSN sync is not instant
- Alerts appear when PSN updates (usually 1-5 minutes)
- This is a PlayStation limitation

### Privacy Settings
On your PlayStation console:
1. Go to **Settings** → **Account Management**
2. Select **Privacy Settings**
3. Set **Gaming | Media** to at least "Friends" or "Anyone"
4. Enable **Trophy** visibility

## Security Notes

- Credentials are encrypted locally
- No passwords are stored in plain text
- Session tokens auto-refresh
- You can revoke access anytime from PlayStation settings

> **Warning:** NPSSO tokens expire after approximately 60 days. You'll need to re-authenticate when this happens.
        """,
        keywords=[
            "psn",
            "playstation",
            "trophy",
            "achievements",
            "gaming",
            "ps4",
            "ps5",
        ],
        related_topics=["alerts_overview", "templates_intro"],
        ui_context="settings.psn",
    ),
    "integrations_youtube": HelpTopic(
        id="integrations_youtube",
        title="YouTube Integration",
        category=HelpCategory.INTEGRATIONS,
        summary="Monitor uploads, connect live chat via Google OAuth, and map memberships/Super Chats to alerts",
        content="""
# YouTube Integration

Monitor one or more YouTube channels for latest uploads, and optionally connect
Google OAuth to ingest **live chat**, **memberships**, and **Super Chats** into
Mycelian's chat overlay and alert system.

## Features

- **Multi-Channel Monitoring**: Track multiple YouTube channels simultaneously
- **Latest Video Tracking**: Automatically detects the newest upload across all
  configured channels
- **Chatbot Variables**: Use variables like `{youtube.latest_video_url}` and
  `{youtube.latest_video_title}` in commands and automated messages
- **Channel-Specific Variables**: Access per-channel data using the channel name
  as a prefix (e.g. `{ChannelName_latest_video_url}`)
- **Playlist Filter**: Exclude videos that belong to specific playlists so they
  are not surfaced as the "latest video"
- **Auto-Refresh**: Video data refreshes automatically every 30 minutes
- **Live Chat**: When authorized, poll your active broadcast's live chat
- **Alerts**: Memberships map to sub/resub/giftsub alerts; Super Chats/Stickers
  map to donation alerts (same alert configs as Twitch)

## Setup Process — API Key (uploads)

### Prerequisites
You need a **YouTube Data API v3** key from the Google Cloud Console:
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or select an existing one)
3. Enable the [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com)
4. Create an API key under **Credentials**

### Connection Steps
1. Go to the **YouTube** tab (or **Settings** → **YouTube**)
2. Paste your **API Key** into the API Key field
3. Enter one or more **Channel URLs** separated by the `|` character
   - Supported formats: `https://youtube.com/@Handle`,
     `https://youtube.com/channel/UCxxxxxx`, `https://youtube.com/c/Name`
4. Click **Test** to verify the connection
5. Click **Save** to persist your settings

## Setup Process — Live Chat & Alerts (OAuth)

Live chat uses Google OAuth the same way Spotify uses its Client ID/Secret flow.

### Quick Connect
1. Go to **Settings** → **YouTube** → **Live chat & alerts**
2. If Mycelian already has built-in YouTube OAuth credentials in
   `api_credentials.json`, you can skip to step 3. Otherwise paste your
   **OAuth Client ID** and **Client Secret**, then click **Save**.
3. Click **Connect**
4. Authorize Mycelian in the browser (Google account that owns the channel)
5. Return to the app — live status should show **Offline** (authorized) or
   **Live** when you are streaming

### Create a Google OAuth app (optional / your own credentials)
Use these steps when Mycelian should use **your** Google Cloud OAuth client:

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create/select a project
2. Enable **YouTube Data API v3**
3. Configure the **OAuth consent screen** (External is fine for personal use)
4. Create credentials → **OAuth client ID** → Application type **Web application**
5. Under **Authorized redirect URIs**, add exactly:
   `http://127.0.0.1:9974`
6. Copy the **Client ID** and **Client Secret** into Mycelian, click **Save**,
   then **Connect**

### Show chat in the overlay
YouTube chat messages are **off by default**. In the chat template Source Settings,
enable **Enable YouTube Chat** (`EnableYouTubeChat` in `chat.json`). Alerts for
memberships and Super Chats still fire even when this toggle is off.

### OAuth scope (chat send)
Live chat uses the `youtube.force-ssl` scope so Mycelian can **read** live chat and
**send** chatbot replies to YouTube. If you previously connected with the older
readonly scope, click **Disconnect** then **Connect** (Reconnect) to grant the new
permission.

### Process YouTube alerts
The **Process YouTube alerts** switch gates only the alert pipeline (queue, instant
alerts, activity feed). Chat overlay, Connectors, and Chatbot events still run when
alerts are disabled.

### Event mapping
| YouTube event | Mycelian alert / chat |
|---|---|
| Text message | Chat overlay (if Enable YouTube Chat) |
| New membership | Sub alert (feed: Membership) |
| Member milestone | Resub alert (feed: Member Milestone) |
| Gift memberships | Giftsub alert (feed: Gift Membership) |
| Super Chat / Super Sticker | Donation alert (feed: Super Chat / Super Sticker) |

## Playlist Filter

The playlist filter lets you exclude videos from specific YouTube playlists.
When a video belongs to a filtered playlist, it will be skipped and the next
most recent non-filtered video will be used instead.

### How It Works
- Type a playlist name into the input field and press **Enter** to add it
- Each playlist name appears as a removable chip/bubble
- Click the **X** on a chip to remove it
- Matching is **case-insensitive** and requires an **exact match** on the
  playlist name (e.g. adding "Shorts" will exclude videos in a playlist
  literally named "Shorts" but not "Short Clips")
- Videos are checked against all playlists on the channel; if a video appears
  in any filtered playlist, it is excluded

### Use Cases
- Exclude "Shorts" or "Clips" playlists so only full-length videos are announced
- Filter out specific series or categories you don't want promoted on stream

## Chatbot Variables

### Global Variables (latest across all channels)
- `{youtube.latest_video_url}` - URL of the latest video
- `{youtube.latest_video_title}` - Title of the latest video
- `{youtube.latest_video_id}` - Video ID of the latest video
- `{youtube.latest_video_channel}` - Channel name of the latest video
- `{youtube.connection_status}` - Current connection status
- `{youtube.channel_count}` - Number of configured channels

### Channel-Specific Variables
Replace `ChannelName` with the channel's display name (spaces and special
characters removed):
- `{ChannelName_latest_video_url}` - Latest video URL from that channel
- `{ChannelName_latest_video_title}` - Latest video title from that channel
- `{ChannelName_latest_video_id}` - Latest video ID from that channel
- `{ChannelName_channel_title}` - Channel display name
- `{ChannelName_channel_url}` - Channel URL
- `{ChannelName_last_updated}` - Last update timestamp

## Troubleshooting

### Connection Issues
**"API Key Required"**
- Ensure you have entered a valid YouTube Data API v3 key
- Verify the API key has the YouTube Data API v3 enabled in Google Cloud Console

**"Channel URLs Required"**
- Enter at least one channel URL separated by `|` if adding multiple

**"All Channels Failed"**
- Double-check that the channel URLs are correct and the channels are public
- Verify your API key has not been revoked or restricted

**Live chat stays "Not authorized"**
- Confirm Client ID/Secret are filled (from `api_credentials.json` or the YouTube tab)
- Confirm they belong to a **Web** OAuth client with redirect URI
  `http://127.0.0.1:9974`
- Complete the browser consent with the channel owner account

**Live status stuck on Offline while streaming**
- Live Streaming must be enabled on the YouTube channel
- Wait up to ~60 seconds for rediscovery after going live
- Confirm the OAuth account is the broadcast owner

### Quota Issues
**"Quota Exceeded"**
- The YouTube Data API has a daily quota of 10,000 units
- Mycelian will automatically retry after 1 hour when quota is exceeded
- Reduce the number of monitored channels if quota is a recurring problem
- The playlist filter uses additional quota when active (one extra API call
  per filtered playlist per update cycle)
- **Live chat polling** costs about **5 units per poll** and respects Google's
  `pollingIntervalMillis` (often ~5 seconds). Long streams may need a
  [quota extension](https://support.google.com/youtube/contact/yt_api_form)
  in Google Cloud

### Data Issues
**"Latest video not updating"**
- Video data refreshes every 30 minutes; use the **Refresh** button to
  trigger an immediate check
- If the playlist filter is active, the latest video may be excluded; check
  your filter list

## API Notes

- Upload monitoring uses a YouTube Data API v3 key (free tier available)
- Live chat uses OAuth scope `youtube.readonly` and redirect
  `http://127.0.0.1:9974`
- Daily quota limit of 10,000 units by default; request an extension for
  reliable all-day live chat polling
- Only public videos are detected for upload monitoring
- Upload data refreshes every 30 minutes automatically

> **Tip:** Use YouTube [chatbot variables](help:chatbot_variables) like `{youtube.latest_video_url}` in custom [commands](help:chatbot_commands) to automatically share your latest uploads with viewers.
        """,
        keywords=[
            "youtube",
            "videos",
            "channel",
            "google",
            "uploads",
            "content",
            "playlist",
            "filter",
            "oauth",
            "live chat",
            "super chat",
            "membership",
            "donation",
            "exclude",
            "api key",
        ],
        related_topics=["templates_intro", "obs_setup"],
        ui_context="settings.youtube",
    ),
}

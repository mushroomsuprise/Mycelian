# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Help Content Database

Topic metadata for the Mycelian help system. Each topic's page body lives in
``content/<topic_id>.html`` (see ``help_render`` for the authoring format).
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class HelpCategory(Enum):
    """Categories for help topics"""

    GETTING_STARTED = "getting_started"
    CONNECTION_GUIDES = "connection_guides"
    ALERTS = "alerts"
    TEMPLATES = "templates"
    SPORE_STUDIO = "spore_studio"
    CHATBOT = "chatbot"
    CONNECTORS = "connectors"
    INTEGRATIONS = "integrations"
    SETTINGS = "settings"
    TROUBLESHOOTING = "troubleshooting"


@dataclass
class HelpTopic:
    """Single help topic/article. The page body is loaded from content/<id>.html."""

    id: str
    title: str
    category: HelpCategory
    summary: str
    keywords: List[str] = field(default_factory=list)
    related_topics: List[str] = field(default_factory=list)
    video_url: Optional[str] = None
    ui_context: Optional[str] = None  # Which UI element this relates to

    @property
    def content(self) -> str:
        """Raw HTML body of the topic."""
        from .help_render import load_raw_html

        return load_raw_html(self.id)

    @property
    def plain_text(self) -> str:
        """Body text with markup removed (search and reading time)."""
        from .help_render import topic_plain_text

        return topic_plain_text(self.id)


# Help Topics Database
HELP_TOPICS: Dict[str, HelpTopic] = {
    # =========================================
    # Getting Started
    # =========================================
    'getting_started_intro': HelpTopic(
        id='getting_started_intro',
        title='Welcome to Mycelian',
        category=HelpCategory.GETTING_STARTED,
        summary='Learn the basics of Mycelian streaming toolkit',
        keywords=['start', 'begin', 'intro', 'overview', 'basics'],
        related_topics=['twitch_setup', 'first_alert_setup', 'alerts_overview', 'templates_intro', 'notifications'],
    ),
    'first_alert_setup': HelpTopic(
        id='first_alert_setup',
        title='Setting Up Your First Alert',
        category=HelpCategory.GETTING_STARTED,
        summary='Step-by-step guide to creating your first stream alert',
        keywords=['first', 'setup', 'beginner', 'start', 'create', 'alert', 'follow', 'test alert'],
        related_topics=['alerts_overview', 'twitch_setup', 'obs_setup'],
    ),
    'obs_setup': HelpTopic(
        id='obs_setup',
        title='Setting Up Browser Sources in OBS',
        category=HelpCategory.GETTING_STARTED,
        summary='How to add Mycelian browser sources to your OBS stream',
        keywords=['obs', 'browser source', 'overlay', 'stream', 'setup', 'dock', 'activity feed'],
        related_topics=['getting_started_intro', 'templates_intro', 'obs_websocket'],
        ui_context='templates',
    ),
    # =========================================
    # Connection Guides
    # =========================================
    'connection_guides_overview': HelpTopic(
        id='connection_guides_overview',
        title='Connection Guides',
        category=HelpCategory.CONNECTION_GUIDES,
        summary='Step-by-step setup for every service Mycelian connects to',
        keywords=['connect', 'setup', 'services', 'integrations', 'accounts', 'guide', 'link'],
        related_topics=['twitch_setup', 'obs_websocket', 'guide_spotify', 'guide_psn', 'status_footer'],
    ),
    'twitch_setup': HelpTopic(
        id='twitch_setup',
        title='Connect Twitch',
        category=HelpCategory.CONNECTION_GUIDES,
        summary='Create a Twitch app, then connect with Client ID and Client Secret',
        keywords=['twitch', 'connect', 'oauth', 'authentication', 'api', 'client id', 'client secret', 'redirect'],
        related_topics=['connection_guides_overview', 'integrations_twitch', 'alerts_overview', 'first_alert_setup', 'status_footer', 'troubleshooting_connections'],
        ui_context='settings.twitch',
    ),
    'obs_websocket': HelpTopic(
        id='obs_websocket',
        title='Connect OBS (WebSocket)',
        category=HelpCategory.CONNECTION_GUIDES,
        summary='Connect Mycelian to OBS so it can refresh its own browser sources',
        keywords=['obs', 'websocket', 'connection', 'port', 'password', '4455'],
        related_topics=['connection_guides_overview', 'obs_setup', 'templates_intro', 'status_footer'],
        ui_context='settings',
    ),
    'guide_spotify': HelpTopic(
        id='guide_spotify',
        title='Connect Spotify',
        category=HelpCategory.CONNECTION_GUIDES,
        summary='Create a Spotify app and sign in so overlays and chat can show what is playing',
        keywords=['spotify', 'music', 'now playing', 'client id', 'client secret', 'redirect', 'developer dashboard'],
        related_topics=['connection_guides_overview', 'integrations_spotify', 'status_footer'],
    ),
    'guide_psn': HelpTopic(
        id='guide_psn',
        title='Connect PlayStation Network',
        category=HelpCategory.CONNECTION_GUIDES,
        summary='Sign in to PSN with an NPSSO token for trophy overlays and game info',
        keywords=['psn', 'playstation', 'npsso', 'trophies', 'sony', 'sign in'],
        related_topics=['connection_guides_overview', 'integrations_psn', 'status_footer'],
    ),
    'guide_youtube': HelpTopic(
        id='guide_youtube',
        title='Connect YouTube',
        category=HelpCategory.CONNECTION_GUIDES,
        summary='Set up YouTube access for channel data and live chat',
        keywords=['youtube', 'google', 'api key', 'oauth', 'channel', 'live chat'],
        related_topics=['connection_guides_overview', 'integrations_youtube', 'status_footer'],
    ),
    'guide_discord': HelpTopic(
        id='guide_discord',
        title='Connect Discord',
        category=HelpCategory.CONNECTION_GUIDES,
        summary='Create a Discord bot, paste its token, and invite it to your server',
        keywords=['discord', 'bot', 'token', 'server', 'go live', 'announcements'],
        related_topics=['connection_guides_overview', 'integrations_discord', 'status_footer'],
    ),
    'guide_streamlabs': HelpTopic(
        id='guide_streamlabs',
        title='Connect Streamlabs',
        category=HelpCategory.CONNECTION_GUIDES,
        summary='Link Streamlabs so tips and donations trigger alerts',
        keywords=['streamlabs', 'donations', 'tips', 'socket token', 'api'],
        related_topics=['connection_guides_overview', 'integrations_streamlabs', 'status_footer'],
    ),
    'guide_streamelements': HelpTopic(
        id='guide_streamelements',
        title='Connect StreamElements',
        category=HelpCategory.CONNECTION_GUIDES,
        summary='Link StreamElements so tips trigger alerts',
        keywords=['streamelements', 'donations', 'tips', 'jwt', 'token'],
        related_topics=['connection_guides_overview', 'integrations_streamelements', 'status_footer'],
    ),
    'guide_streamdeck': HelpTopic(
        id='guide_streamdeck',
        title='Connect Stream Deck',
        category=HelpCategory.CONNECTION_GUIDES,
        summary='Install the Mycelian Stream Deck plugin and control Mycelian from your deck',
        keywords=['stream deck', 'elgato', 'plugin', 'buttons', 'actions'],
        related_topics=['connection_guides_overview', 'spore_studio_streamdeck', 'source_controls'],
    ),
    # =========================================
    # Alerts
    # =========================================
    'alerts_overview': HelpTopic(
        id='alerts_overview',
        title='Alert System Overview',
        category=HelpCategory.ALERTS,
        summary='Understanding how alerts work in Mycelian',
        keywords=['alerts', 'notifications', 'follow', 'sub', 'bits', 'raid'],
        related_topics=['first_alert_setup', 'alert_configuration', 'alert_media', 'alert_testing'],
    ),
    'alert_configuration': HelpTopic(
        id='alert_configuration',
        title='Configuring Alert Settings',
        category=HelpCategory.ALERTS,
        summary='How to set up and customize individual alerts',
        keywords=['alert', 'configuration', 'setup', 'settings', 'range', 'tier'],
        related_topics=['alerts_overview', 'alert_media'],
    ),
    'alert_media': HelpTopic(
        id='alert_media',
        title='Alert Media Configuration',
        category=HelpCategory.ALERTS,
        summary='Setting up GIFs, audio, and randomization for alerts',
        keywords=['gif', 'audio', 'sound', 'media', 'randomization', 'animation'],
        related_topics=['alerts_overview', 'alert_configuration'],
    ),
    'activity_feed': HelpTopic(
        id='activity_feed',
        title='Activity Feed',
        category=HelpCategory.ALERTS,
        summary='Watch events live, pause, skip or replay alerts, and use the feed as an OBS dock',
        keywords=['activity feed', 'feed', 'replay', 'skip', 'pause', 'mute', 'up next', 'now playing',
                  'queue', 'history', 'by person', 'dock', 'layout', 'ticker', 'cards',
                  'multi-month', 'gift months', 'prepaid sub'],
        related_topics=['alert_testing', 'alerts_overview', 'obs_setup', 'settings_overview', 'troubleshooting_alerts'],
        ui_context='activity_feed',
    ),
    'alert_testing': HelpTopic(
        id='alert_testing',
        title='Testing Your Alerts',
        category=HelpCategory.ALERTS,
        summary='How to test alerts before going live',
        keywords=['test', 'testing', 'preview', 'verify', 'check', 'debug', 'replay'],
        related_topics=['alerts_overview', 'alert_configuration', 'activity_feed'],
    ),
    # =========================================
    # Chatbot
    # =========================================
    'chatbot_commands': HelpTopic(
        id='chatbot_commands',
        title='Creating Chat Commands',
        category=HelpCategory.CHATBOT,
        summary='How to create and manage custom chat commands',
        keywords=['commands', 'chat', 'bot', 'custom', 'response', 'reply', 'variables', 'alias', 'aliases', 'counter', 'cooldown', 'permission', 'inputs', 'lookup', 'followage', 'uptime', 'shoutout', 'twitch api'],
        related_topics=['chatbot_events', 'chatbot_quotes', 'chatbot_greetings', 'chatbot_giveaways', 'chatbot_polls', 'chatbot_variables'],
    ),
    'chatbot_events': HelpTopic(
        id='chatbot_events',
        title='Chatbot Events and Automation',
        category=HelpCategory.CHATBOT,
        summary='Automated chatbot responses for stream events',
        keywords=['events', 'automation', 'welcome', 'messages', 'responses', 'follow', 'raid', 'bits', 'subs', 'timer', 'interval', 'time of day', 'chat message'],
        related_topics=['chatbot_commands', 'chatbot_quotes', 'chatbot_variables', 'chatbot_greetings'],
    ),
    'chatbot_quotes': HelpTopic(
        id='chatbot_quotes',
        title='Managing Quotes and Highlights',
        category=HelpCategory.CHATBOT,
        summary='How to collect and manage memorable chat moments',
        keywords=['quotes', 'highlights', 'memorable', 'moments', 'save', '!quote', 'add_quote', 'author', 'cooldown', 'renumber', 'reply format'],
        related_topics=['chatbot_commands', 'chatbot_events'],
    ),
    'chatbot_greetings': HelpTopic(
        id='chatbot_greetings',
        title='Chatbot Greetings',
        category=HelpCategory.CHATBOT,
        summary='Automatically greet viewers on their first chat message',
        keywords=['greetings', 'welcome', 'hello', 'chat', 'viewers', 'automatic', 'personal greeting', 'first message', 'greet again', 'regulars'],
        related_topics=['chatbot_commands', 'chatbot_events', 'chatbot_variables'],
    ),
    'chatbot_giveaways': HelpTopic(
        id='chatbot_giveaways',
        title='Chatbot Giveaways',
        category=HelpCategory.CHATBOT,
        summary='Keyword-based giveaways, draws, and winner announcements',
        keywords=['giveaway', 'giveaways', 'contest', 'winner', 'raffle', 'chatbot', 'announcement', 'entrants', 'draw', 'keyword', 'blocked', 'overlay', 'confetti', 'spore studio'],
        related_topics=['chatbot_commands', 'chatbot_variables', 'integrations_twitch'],
    ),
    'chatbot_polls': HelpTopic(
        id='chatbot_polls',
        title='Chatbot Polls',
        category=HelpCategory.CHATBOT,
        summary='Overlay polls driven by #keywords in Twitch and YouTube chat',
        keywords=['poll', 'polls', 'vote', 'votes', 'keyword', 'chatbot', 'banner', 'options', 'timer', 'overlay', 'startpoll'],
        related_topics=['chatbot_giveaways', 'chatbot_commands', 'spore_studio_widgets'],
    ),
    'chatbot_variables': HelpTopic(
        id='chatbot_variables',
        title='Chatbot Variables',
        category=HelpCategory.CHATBOT,
        summary='Placeholders for chatbot commands, events and messages',
        keywords=['variables', 'placeholders', 'dynamic', 'text', 'formatting', 'custom', 'custom variables', 'date format', 'time format', 'math', 'statistics', 'quotes'],
        related_topics=['chatbot_commands', 'chatbot_greetings', 'chatbot_events'],
    ),
    # =========================================
    # Connectors
    # =========================================
    'connectors_intro': HelpTopic(
        id='connectors_intro',
        title='Connector System Introduction',
        category=HelpCategory.CONNECTORS,
        summary='Automate stream reactions: when an event happens, check conditions, run actions',
        keywords=['connectors', 'automation', 'triggers', 'actions', 'workflow'],
        related_topics=['connector_triggers', 'connector_conditions', 'connector_actions', 'connector_examples'],
    ),
    'connector_triggers': HelpTopic(
        id='connector_triggers',
        title='Connector Triggers',
        category=HelpCategory.CONNECTORS,
        summary='Available trigger types and how to configure them',
        keywords=['triggers', 'events', 'twitch', 'chat', 'hotkey', 'obs', 'youtube', 'stream deck', 'webhook', 'timer', 'schedule', 'cooldown'],
        related_topics=['connectors_intro', 'connector_actions'],
    ),
    'connector_actions': HelpTopic(
        id='connector_actions',
        title='Connector Actions',
        category=HelpCategory.CONNECTORS,
        summary='Available actions and how to configure them',
        keywords=['actions', 'responses', 'automation', 'effects', 'controls', 'variables', 'placeholders', 'date format'],
        related_topics=['connectors_intro', 'connector_triggers', 'game_hooks', 'obs_websocket'],
    ),
    'connector_conditions': HelpTopic(
        id='connector_conditions',
        title='Connector Conditions',
        category=HelpCategory.CONNECTORS,
        summary='Add conditional logic to your connector workflows',
        keywords=['conditions', 'logic', 'filter', 'if', 'when', 'compare', 'and', 'or', 'regex', 'case'],
        related_topics=['connectors_intro', 'connector_triggers', 'connector_actions'],
    ),
    'connector_examples': HelpTopic(
        id='connector_examples',
        title='Connector Examples',
        category=HelpCategory.CONNECTORS,
        summary='Complete example connectors you can rebuild',
        keywords=['examples', 'templates', 'recipes', 'ideas', 'use cases', 'workflows'],
        related_topics=['connectors_intro', 'connector_triggers', 'connector_actions', 'connector_conditions'],
    ),
    # =========================================
    # Templates
    # =========================================
    'templates_intro': HelpTopic(
        id='templates_intro',
        title='Template System Overview',
        category=HelpCategory.TEMPLATES,
        summary='Understanding browser source templates and customization',
        keywords=['templates', 'browser sources', 'overlays', 'obs', 'customization', 'spore studio'],
        related_topics=['template_configuration', 'template_title', 'template_custom_css', 'template_websocket', 'source_controls', 'spore_studio_overview', 'spore_studio_counters', 'spore_studio_dynamic_controls'],
    ),
    'template_title': HelpTopic(
        id='template_title',
        title='Title Overlay (Stream Category)',
        category=HelpCategory.TEMPLATES,
        summary='Sizing the Title browser source and keeping the category up to date',
        keywords=['title', 'category', 'game', 'stream title', 'font scaling', 'auto fit', 'browser source size', 'eventsub', 'obs sizing'],
        related_topics=['templates_intro', 'template_configuration', 'source_controls', 'obs_setup'],
    ),
    'template_configuration': HelpTopic(
        id='template_configuration',
        title='Configuring Template Settings',
        category=HelpCategory.TEMPLATES,
        summary='Edit template settings in the Source Settings tab',
        keywords=['configuration', 'settings', 'customization', 'json', 'css', 'spore studio', 'source settings', 'preview', 'search settings', 'changed only', 'sample events', 'revert', 'discard'],
        related_topics=['templates_intro', 'source_controls', 'color_picker', 'template_custom_css', 'spore_studio_design', 'spore_studio_dynamic_controls'],
    ),
    'color_picker': HelpTopic(
        id='color_picker',
        title='Color Picker',
        category=HelpCategory.TEMPLATES,
        summary='Pick solid colors, transparency and gradients in Source Settings, the Theme tab and Spore Studio',
        keywords=['color', 'colour', 'color picker', 'colour picker', 'swatch', 'transparent', 'transparency', 'opacity', 'alpha', 'gradient', 'eyedropper', 'hex', 'rgba', 'theme', 'spore studio', 'source settings'],
        related_topics=['template_configuration', 'settings_overview', 'spore_studio_design', 'spore_studio_widgets'],
    ),
    'template_custom_css': HelpTopic(
        id='template_custom_css',
        title='Custom CSS for Templates',
        category=HelpCategory.TEMPLATES,
        summary='Restyle overlays with CSS in the OBS browser source',
        keywords=['css', 'styling', 'custom', 'design', 'colors', 'fonts', 'animation', 'obs', 'selectors'],
        related_topics=['templates_intro', 'template_configuration', 'obs_setup'],
    ),
    'template_websocket': HelpTopic(
        id='template_websocket',
        title='WebSocket Events',
        category=HelpCategory.TEMPLATES,
        summary='Socket.IO events overlays send and receive',
        keywords=['websocket', 'events', 'real-time', 'socket', 'javascript', 'api', 'spore studio', 'socket.io'],
        related_topics=['templates_intro', 'template_configuration', 'source_controls', 'spore_studio_bindings'],
    ),
    'source_controls': HelpTopic(
        id='source_controls',
        title='Real-time Source Controls',
        category=HelpCategory.TEMPLATES,
        summary='Control template elements in real-time during streams',
        keywords=['controls', 'real-time', 'live', 'stream', 'interactive', 'dynamic controls', 'spore studio', 'obs dock', 'dock', 'custom browser dock', 'dock link'],
        related_topics=['templates_intro', 'template_configuration', 'spore_studio_dynamic_controls', 'spore_studio_overview', 'obs_setup'],
    ),
    # =========================================
    # Spore Studio
    # =========================================
    'spore_studio_overview': HelpTopic(
        id='spore_studio_overview',
        title='Spore Studio Overview',
        category=HelpCategory.SPORE_STUDIO,
        summary='Visual editor for custom overlay templates and their JSON configurations',
        keywords=['spore studio', 'visual editor', 'template editor', 'overlay designer', 'canvas', 'blocks', 'browser source', 'counter', 'data source', 'dynamic controls', 'stream deck'],
        related_topics=['templates_intro', 'spore_studio_design', 'spore_studio_counters', 'spore_studio_data_feed', 'spore_studio_data_sources', 'spore_studio_streamdeck', 'spore_studio_dynamic_controls', 'spore_studio_containers', 'template_configuration', 'obs_setup'],
        ui_context='spore_studio',
    ),
    'spore_studio_containers': HelpTopic(
        id='spore_studio_containers',
        title='Overlay Containers',
        category=HelpCategory.SPORE_STUDIO,
        summary='Combine several templates on one browser-source URL',
        keywords=['container', 'overlay container', 'combine templates', 'browser source', 'layout', 'mute', 'spore studio'],
        related_topics=['spore_studio_overview', 'templates_intro', 'obs_setup'],
        ui_context='spore_studio',
    ),
    'spore_studio_design': HelpTopic(
        id='spore_studio_design',
        title='Designing Templates in Spore Studio',
        category=HelpCategory.SPORE_STUDIO,
        summary='Blocks, canvas, assets, element properties, and Source Settings exposure',
        keywords=['spore studio', 'blocks', 'canvas', 'assets', 'properties', 'container', 'text', 'image', 'video', 'expose', 'source settings', 'counter', 'data display'],
        related_topics=['spore_studio_overview', 'spore_studio_counters', 'spore_studio_data_feed', 'spore_studio_data_sources', 'spore_studio_dynamic_controls', 'spore_studio_bindings', 'spore_studio_widgets', 'color_picker', 'template_configuration', 'source_controls'],
    ),
    'spore_studio_widgets': HelpTopic(
        id='spore_studio_widgets',
        title='Widget Reference',
        category=HelpCategory.SPORE_STUDIO,
        summary='Every Spore Studio block, grouped by category, with each setting explained',
        keywords=['widget', 'blocks', 'list', 'wheel', 'qr', 'embed', 'now playing', 'stat card', 'poll', 'progress bar', 'confetti', 'highlight', 'spore studio'],
        related_topics=['spore_studio_design', 'spore_studio_counters', 'spore_studio_data_feed', 'spore_studio_data_sources', 'spore_studio_bindings', 'spore_studio_advanced_js'],
    ),
    'spore_studio_bindings': HelpTopic(
        id='spore_studio_bindings',
        title='Event Bindings & Actions',
        category=HelpCategory.SPORE_STUDIO,
        summary='Websocket events, payload filters, actions, chained steps, and Twitch API bindings',
        keywords=['bindings', 'websocket', 'events', 'actions', 'filters', 'chain', 'next_alert', 'instant_alert', 'stream deck', 'twitch api', 'counter_adjust'],
        related_topics=['spore_studio_design', 'spore_studio_counters', 'spore_studio_advanced', 'spore_studio_streamdeck', 'template_websocket', 'connector_examples'],
    ),
    'spore_studio_advanced': HelpTopic(
        id='spore_studio_advanced',
        title='Advanced JS, Preview & Legacy Templates',
        category=HelpCategory.SPORE_STUDIO,
        summary='Live preview mocks, user JavaScript, queue handshake, Stream Deck config, and legacy modes',
        keywords=['advanced js', 'preview', 'legacy', 'stream deck', 'alert_complete', 'queue', 'sporeShow', 'mock'],
        related_topics=['spore_studio_advanced_js', 'spore_studio_bindings', 'spore_studio_streamdeck', 'spore_studio_examples', 'template_websocket', 'alerts_overview'],
    ),
    'spore_studio_advanced_js': HelpTopic(
        id='spore_studio_advanced_js',
        title='Advanced JS Reference',
        category=HelpCategory.SPORE_STUDIO,
        summary='How custom JavaScript is saved, which helpers you can call, and how to hook into live events',
        keywords=['advanced js', 'sporeShow', 'sporeCounterAdjust', 'alert_complete', 'user-begin', 'custom javascript', 'spore studio'],
        related_topics=['spore_studio_advanced', 'spore_studio_bindings', 'spore_studio_widgets', 'spore_studio_counters', 'template_websocket', 'alerts_overview'],
    ),
    'spore_studio_examples': HelpTopic(
        id='spore_studio_examples',
        title='Spore Studio Examples & Recipes',
        category=HelpCategory.SPORE_STUDIO,
        summary='Step-by-step overlay builds with bindings, filters, and testing',
        keywords=['examples', 'recipes', 'tutorial', 'follow alert', 'bit counter', 'sub counter', 'walkthrough', 'spore studio'],
        related_topics=['spore_studio_overview', 'spore_studio_bindings', 'spore_studio_counters', 'spore_studio_dynamic_controls', 'first_alert_setup', 'source_controls'],
    ),
    'spore_studio_counters': HelpTopic(
        id='spore_studio_counters',
        title='Counters in Spore Studio',
        category=HelpCategory.SPORE_STUDIO,
        summary='Set up numeric counters, progressive goals, rules, persistence, image ranges, and binding adjustments',
        keywords=['counter', 'counters', 'bit counter', 'sub counter', 'increment', 'persist', 'counter rules', 'counter_adjust', 'tick_up', 'progressive goals', 'goal reached', 'spore studio'],
        related_topics=['spore_studio_design', 'spore_studio_data_sources', 'spore_studio_bindings', 'spore_studio_examples'],
    ),
    'spore_studio_data_feed': HelpTopic(
        id='spore_studio_data_feed',
        title='Data Feeds',
        category=HelpCategory.SPORE_STUDIO,
        summary='The shared source, filter, format, and target setup every Spore Studio block uses',
        keywords=['data feed', 'filter', 'template output', 'roulette result', 'wheel result', 'formatting', 'plural', 'giveaway_sync', 'replace all rows', 'spore studio'],
        related_topics=['spore_studio_widgets', 'spore_studio_data_sources', 'spore_studio_counters', 'spore_studio_bindings'],
        ui_context='spore_studio',
    ),
    'spore_studio_data_sources': HelpTopic(
        id='spore_studio_data_sources',
        title='Data Sources & Data Displays',
        category=HelpCategory.SPORE_STUDIO,
        summary='Read-only live values from alerts, chat, stats, config, and runtime database',
        keywords=['data source', 'data display', 'alert payload', 'session stats', 'stats.total_bits', 'alert.amt_cheered', 'refresh on events', 'spore studio'],
        related_topics=['spore_studio_data_feed', 'spore_studio_counters', 'spore_studio_design', 'spore_studio_bindings', 'template_configuration'],
    ),
    'spore_studio_streamdeck': HelpTopic(
        id='spore_studio_streamdeck',
        title='Stream Deck Actions in Spore Studio',
        category=HelpCategory.SPORE_STUDIO,
        summary='Define template actions, wire bindings, and map physical Stream Deck buttons',
        keywords=['stream deck', 'streamdeck', 'action id', 'default_data', 'actionName', 'template action', 'spore studio'],
        related_topics=['spore_studio_bindings', 'spore_studio_advanced', 'spore_studio_dynamic_controls', 'spore_studio_examples', 'guide_streamdeck'],
    ),
    'spore_studio_dynamic_controls': HelpTopic(
        id='spore_studio_dynamic_controls',
        title='Dynamic Controls (Source Controls Tab)',
        category=HelpCategory.SPORE_STUDIO,
        summary="Author live stream controls that appear in Mycelian's Source Controls overlay",
        keywords=['dynamic controls', 'source controls tab', 'dynamic_controls', 'pause alerts', 'counter control', 'button', 'toggle', 'spore studio'],
        related_topics=['source_controls', 'spore_studio_design', 'spore_studio_counters', 'spore_studio_streamdeck', 'template_configuration'],
    ),
    # =========================================
    # Integrations
    # =========================================
    'integrations_twitch': HelpTopic(
        id='integrations_twitch',
        title='Twitch Integration',
        category=HelpCategory.INTEGRATIONS,
        summary='Events and options after your Twitch account is connected',
        keywords=['twitch', 'integration', 'oauth', 'events', 'alerts', 'shoutout'],
        related_topics=['twitch_setup', 'getting_started_intro', 'alerts_overview', 'connector_triggers', 'status_footer'],
    ),
    # =========================================
    # Settings
    # =========================================
    'settings_overview': HelpTopic(
        id='settings_overview',
        title='Application Settings Overview',
        category=HelpCategory.SETTINGS,
        summary='Understanding and configuring Mycelian settings',
        keywords=['settings', 'configuration', 'preferences', 'setup', 'about', 'theme', 'database', 'backup'],
        related_topics=['integrations_twitch', 'getting_started_intro', 'game_hooks', 'database', 'status_footer', 'notifications', 'color_picker'],
    ),
    'settings_statistics': HelpTopic(
        id='settings_statistics',
        title='Statistics',
        category=HelpCategory.SETTINGS,
        summary='Charts, leaderboards and a shareable highlights image for your stream activity',
        keywords=['statistics', 'stats', 'analytics', 'leaderboard', 'highlights', 'export', 'chart', 'viewer', 'top chatters'],
        related_topics=['settings_overview', 'status_footer', 'chatbot_giveaways', 'chatbot_polls'],
        ui_context='settings.statistics',
    ),
    'status_footer': HelpTopic(
        id='status_footer',
        title='Status Footer',
        category=HelpCategory.SETTINGS,
        summary='What the colored badges and icon rings on the bottom status bar mean',
        keywords=['status', 'footer', 'badge', 'color', 'minimal', 'ring', 'connected', 'disconnected'],
        related_topics=['settings_overview', 'integrations_twitch', 'obs_websocket', 'notifications'],
        ui_context='settings.status_footer',
    ),
    'notifications': HelpTopic(
        id='notifications',
        title='Notifications',
        category=HelpCategory.SETTINGS,
        summary='Pop-ups, the notification centre, and choosing what you see and how it looks',
        keywords=['notification', 'notifications', 'pop-up', 'popup', 'toast', 'bell', 'notification centre', 'notification center',
                  'mute', 'silence', 'desktop', 'dock', 'repeat', 'error', 'warning'],
        related_topics=['settings_overview', 'status_footer', 'troubleshooting_connections', 'source_controls', 'obs_setup'],
        ui_context='settings.notifications',
    ),
    'database': HelpTopic(
        id='database',
        title='Database and Database Explorer',
        category=HelpCategory.SETTINGS,
        summary='Where Mycelian keeps your data, switching to Firebase or MongoDB, moving your data, and editing items in the Database Explorer',
        keywords=['database', 'sqlite', 'local file', 'mycelian.db', 'firebase', 'mongodb', 'mongo', 'migrate', 'move my data',
                  'explorer', 'json', 'backup', 'service account', 'connection string', 'timeout', 'retry', "can't connect"],
        related_topics=['settings_overview', 'notifications', 'troubleshooting_connections', 'game_hooks'],
        ui_context='settings.database',
    ),
    'game_hooks': HelpTopic(
        id='game_hooks',
        title='Game Hooks (live game data)',
        category=HelpCategory.SETTINGS,
        summary='Read party and battle stats from supported games, optional crowd-control writes, and browser overlays.',
        keywords=['ff7', 'factorio', 'memory', 'overlay', 'battle', 'gil', 'crowd control', 'connectors', 'game hooks', 'install mod', 'mod'],
        related_topics=['settings_overview', 'connector_actions', 'templates_intro', 'database'],
    ),
    # =========================================
    # Troubleshooting
    # =========================================
    'troubleshooting_alerts': HelpTopic(
        id='troubleshooting_alerts',
        title='Troubleshooting Alerts',
        category=HelpCategory.TROUBLESHOOTING,
        summary='Common alert issues and solutions',
        keywords=['troubleshoot', 'fix', 'problem', 'not working', 'issue', 'error'],
        related_topics=['alerts_overview', 'alert_testing', 'troubleshooting_audio', 'obs_setup'],
    ),
    'troubleshooting_connections': HelpTopic(
        id='troubleshooting_connections',
        title='Connection and Integration Issues',
        category=HelpCategory.TROUBLESHOOTING,
        summary='Fixing connection problems with Twitch and other services',
        keywords=['connection', 'network', 'integration', 'websocket', 'api'],
        related_topics=['integrations_twitch', 'twitch_setup', 'obs_websocket', 'connection_guides_overview', 'status_footer', 'notifications'],
    ),
    'troubleshooting_audio': HelpTopic(
        id='troubleshooting_audio',
        title='Audio Troubleshooting',
        category=HelpCategory.TROUBLESHOOTING,
        summary='Fix common audio issues with alerts and browser sources',
        keywords=['audio', 'sound', 'volume', 'music', 'mute', 'quiet', 'loud', 'crackling'],
        related_topics=['alert_media', 'troubleshooting_alerts', 'obs_setup'],
    ),
    'troubleshooting_performance': HelpTopic(
        id='troubleshooting_performance',
        title='Performance Issues and Optimization',
        category=HelpCategory.TROUBLESHOOTING,
        summary='Fixing lag, high CPU usage, and performance problems',
        keywords=['performance', 'lag', 'cpu', 'memory', 'optimization', 'speed'],
        related_topics=['obs_setup', 'template_configuration'],
    ),
    # =========================================
    # Integrations
    # =========================================
    'integrations_spotify': HelpTopic(
        id='integrations_spotify',
        title='Spotify Integration',
        category=HelpCategory.INTEGRATIONS,
        summary='Display currently playing music on your stream',
        keywords=['spotify', 'music', 'now playing', 'song', 'audio', 'integration'],
        related_topics=['templates_intro', 'obs_setup', 'status_footer', 'guide_spotify'],
        ui_context='settings.spotify',
    ),
    'integrations_psn': HelpTopic(
        id='integrations_psn',
        title='PlayStation Network Integration',
        category=HelpCategory.INTEGRATIONS,
        summary='Display PSN trophies and gaming activity on stream',
        keywords=['psn', 'playstation', 'trophy', 'gaming', 'ps4', 'ps5'],
        related_topics=['alerts_overview', 'templates_intro', 'status_footer', 'guide_psn'],
        ui_context='settings.psn',
    ),
    'integrations_youtube': HelpTopic(
        id='integrations_youtube',
        title='YouTube Integration',
        category=HelpCategory.INTEGRATIONS,
        summary='Monitor uploads, connect live chat via Google OAuth, and map memberships/Super Chats to alerts',
        keywords=['youtube', 'videos', 'channel', 'google', 'uploads', 'content', 'playlist', 'filter', 'oauth', 'live chat', 'super chat', 'membership', 'donation', 'exclude', 'api key'],
        related_topics=['templates_intro', 'obs_setup', 'status_footer', 'guide_youtube'],
        ui_context='settings.youtube',
    ),
    'integrations_discord': HelpTopic(
        id='integrations_discord',
        title='Discord Integration',
        category=HelpCategory.INTEGRATIONS,
        summary='Connect your own Discord bot to send chatbot replies, connector actions, and go-live announcements',
        keywords=['discord', 'bot', 'token', 'go live', 'announcement', 'channel', 'server', 'guild'],
        related_topics=['integrations_twitch', 'integrations_youtube', 'connector_actions', 'status_footer', 'guide_discord'],
        ui_context='settings.discord',
    ),
    'integrations_streamlabs': HelpTopic(
        id='integrations_streamlabs',
        title='Streamlabs Donations',
        category=HelpCategory.INTEGRATIONS,
        summary='Show Streamlabs cash tips as Mycelian donation alerts',
        keywords=['streamlabs', 'donation', 'tip', 'oauth', 'client id'],
        related_topics=['alerts_overview', 'integrations_streamelements', 'status_footer', 'guide_streamlabs'],
        ui_context='settings.streamlabs',
    ),
    'integrations_streamelements': HelpTopic(
        id='integrations_streamelements',
        title='StreamElements Donations',
        category=HelpCategory.INTEGRATIONS,
        summary='Show StreamElements tips as Mycelian donation alerts',
        keywords=['streamelements', 'donation', 'tip', 'jwt'],
        related_topics=['alerts_overview', 'integrations_streamlabs', 'status_footer', 'guide_streamelements'],
        ui_context='settings.streamelements',
    ),
}

"""
Help Browser UI

In-app help browser component with search, navigation, and markdown rendering.
Styled to match the Mycelian application design.
"""

from nicegui import ui, core
from ..notification_engine import notify
import json
import logging
import re
import webbrowser
from typing import Optional, List, Dict

from ..theme_manager import HELP_SYSTEM_CSS
from .help_manager import get_help_manager, HelpTopic, HelpCategory

logger = logging.getLogger(__name__)

_help_system_styles_injected = False


def ensure_help_system_styles() -> None:
    """Inject help UI stylesheet once (shared); uses :root theme CSS variables."""
    global _help_system_styles_injected
    if _help_system_styles_injected:
        return
    ui.add_head_html(
        f'<style id="mycelian-help-system">{HELP_SYSTEM_CSS}</style>',
        shared=True,
    )
    if core.loop is not None:
        css_escaped = json.dumps(HELP_SYSTEM_CSS)
        ui.run_javascript(f"""
            if (!document.getElementById('mycelian-help-system')) {{
                const s = document.createElement('style');
                s.id = 'mycelian-help-system';
                s.textContent = {css_escaped};
                document.head.appendChild(s);
            }}
        """)
    _help_system_styles_injected = True


class HelpBrowser:
    """In-app help browser component with modern styling"""

    def __init__(self):
        self.help_manager = get_help_manager()
        self.current_topic: Optional[HelpTopic] = None
        self.search_query = ""
        self.history: List[str] = []
        self.history_index = -1
        self.dialog = None
        self.sidebar_container = None
        self.search_results_container = None
        self.content_container = None
        self.toc_container = None
        self.breadcrumb_container = None
        self.category_containers: Dict[HelpCategory, dict] = {}
        self.topic_buttons: Dict[str, object] = {}
        self.back_btn = None
        self.forward_btn = None
        self._nav_bridge = None

    def show(self, initial_topic: str = None, category: str = None):
        """Show help browser in a dialog

        Args:
            initial_topic: Specific topic ID to open
            category: Help category to navigate to (will show first topic in category)
        """
        try:
            ensure_help_system_styles()

            with ui.dialog() as self.dialog:
                self.dialog.classes("help-browser-dialog")
                self.dialog.props("maximized")

                with ui.card().classes("w-full h-full p-0 m-0 help-card-root").style(
                    "border-radius: 0; max-width: 100%; max-height: 100%;"
                ):
                    with ui.row().classes("w-full h-full").style("display: flex; height: 100%;"):
                        self._build_sidebar()
                        self._build_content_area()

                    self._nav_bridge = ui.element('div').style('display:none')
                    self._nav_bridge.on('helpnav',
                        lambda e: self._handle_help_link(e),
                        js_handler='(e) => emit(e.detail)')
                    self._nav_bridge.on('externallink',
                        lambda e: self._handle_external_link(e),
                        js_handler='(e) => emit(e.detail)')

            self._setup_help_link_handler()

            if initial_topic:
                self.navigate_to(initial_topic)
            elif category:
                self._navigate_to_category(category)
            else:
                self._show_welcome()

            self.dialog.open()
            logger.debug(f"Help browser opened with topic={initial_topic}, category={category}")

        except Exception as e:
            logger.error(f"Error opening help browser: {e}")
            notify(f"Error opening help: {e}", type="negative")

    def _handle_help_link(self, e):
        """Handle a help: protocol link click from JavaScript."""
        try:
            topic_id = None
            if isinstance(e.args, dict):
                topic_id = e.args.get('detail', '')
            elif isinstance(e.args, str):
                topic_id = e.args
            if topic_id:
                self.navigate_to(str(topic_id))
        except Exception as ex:
            logger.error(f"Error handling help link: {ex}")

    def _handle_external_link(self, e):
        """Open an external URL in the user's default browser."""
        try:
            url = None
            if isinstance(e.args, dict):
                url = e.args.get('detail', '')
            elif isinstance(e.args, str):
                url = e.args
            if url and url.startswith(('http://', 'https://')):
                webbrowser.open(url)
                logger.debug(f"Opened external link in browser: {url}")
        except Exception as ex:
            logger.error(f"Error opening external link: {ex}")

    def _setup_help_link_handler(self):
        """Install document-level click handler for help: and external links."""
        if not self._nav_bridge:
            return
        bridge_id = self._nav_bridge.id
        ui.run_javascript(f"""
        document.addEventListener('click', function(e) {{
            const link = e.target.closest('a[href]');
            if (!link) return;
            const href = link.getAttribute('href');
            if (!href) return;

            if (href.startsWith('help:')) {{
                e.preventDefault();
                e.stopPropagation();
                const topicId = href.substring(5);
                try {{
                    const el = getHtmlElement({bridge_id});
                    if (el) {{
                        el.dispatchEvent(new CustomEvent('helpnav', {{
                            detail: topicId,
                            bubbles: false
                        }}));
                    }}
                }} catch(err) {{
                    console.error('Help link navigation error:', err);
                }}
            }} else if (href.startsWith('http://') || href.startsWith('https://')) {{
                e.preventDefault();
                e.stopPropagation();
                try {{
                    const el = getHtmlElement({bridge_id});
                    if (el) {{
                        el.dispatchEvent(new CustomEvent('externallink', {{
                            detail: href,
                            bubbles: false
                        }}));
                    }}
                }} catch(err) {{
                    console.error('External link open error:', err);
                }}
            }}
        }});
        """)

    def _inject_content_enhancements(self):
        """Post-process rendered markdown: callout styling and external link icons."""
        ui.run_javascript("""
        (function() {
            const container = document.querySelector('.help-markdown-content');
            if (!container) return;

            // Callout styling for blockquotes
            const blockquotes = container.querySelectorAll('blockquote');
            const types = {
                'tip:': 'tip', 'warning:': 'warning',
                'note:': 'note', 'important:': 'important'
            };
            blockquotes.forEach(bq => {
                const firstP = bq.querySelector('p');
                if (!firstP) return;
                const strong = firstP.querySelector('strong');
                if (!strong) return;
                const text = strong.textContent.trim().toLowerCase();
                for (const [key, cls] of Object.entries(types)) {
                    if (text === key || text === key.slice(0, -1)) {
                        bq.classList.add('help-callout', 'help-callout-' + cls);
                        break;
                    }
                }
            });

            // Mark external links with an icon so users know they open in the browser
            container.querySelectorAll('a[href]').forEach(a => {
                const href = a.getAttribute('href');
                if (href && (href.startsWith('http://') || href.startsWith('https://'))) {
                    a.style.cursor = 'pointer';
                    if (!a.querySelector('.help-external-icon')) {
                        const icon = document.createElement('span');
                        icon.className = 'help-external-icon';
                        icon.textContent = ' \\u2197';
                        icon.style.fontSize = '0.75em';
                        a.appendChild(icon);
                    }
                }
            });
        })();
        """)

    def _extract_toc(self, content: str) -> list:
        """Extract table of contents entries from markdown content."""
        toc = []
        for line in content.strip().split('\n'):
            stripped = line.strip()
            if stripped.startswith('## ') and not stripped.startswith('###'):
                toc.append(('h2', stripped[3:].strip()))
            elif stripped.startswith('### '):
                toc.append(('h3', stripped[4:].strip()))
        return toc

    @staticmethod
    def _estimate_reading_time(content: str) -> int:
        """Estimate reading time in minutes based on word count."""
        words = len(re.findall(r'\w+', content))
        return max(1, round(words / 200))

    def _build_sidebar(self):
        """Build sidebar with search and categories"""
        try:
            with ui.column().classes("help-sidebar help-scroll").style(
                "width: 280px; min-width: 280px; height: 100%; display: flex; flex-direction: column;"
            ):
                with ui.column().classes("help-sidebar-header w-full p-4"):
                    with ui.row().classes("w-full items-center justify-between mb-3"):
                        ui.label("Help Center").classes("text-lg font-semibold help-text-primary")
                        ui.icon("help_outline", size="sm").classes("help-icon-accent")

                    with ui.input(
                        placeholder="Search help topics...",
                    ).props("outlined dense clearable").classes(
                        "help-search-input w-full"
                    ) as search_input:
                        search_input.on("update:model-value", self._on_search)

                self.search_results_container = ui.column().classes(
                    "w-full px-3 py-2"
                ).style("display: none;")

                with ui.scroll_area().classes("help-scroll grow").style("flex: 1;"):
                    with ui.column().classes("w-full p-3 gap-1"):
                        ui.label("Categories").classes(
                            "text-xs font-medium uppercase tracking-wider mb-2 help-text-muted"
                        )

                        categories = self.help_manager.get_all_categories()
                        for category in categories:
                            topics = self.help_manager.get_topics_by_category(category)
                            if topics:
                                self._build_category_section(category, topics)

        except Exception as e:
            logger.error(f"Error building sidebar: {e}")

    def _build_category_section(self, category: HelpCategory, topics: List[HelpTopic]):
        """Build a collapsible category section"""
        category_name = category.value.replace("_", " ").title()
        category_icon = self._get_category_icon(category)

        with ui.column().classes("w-full"):
            with ui.button().props("flat dense align=left no-caps").classes(
                "help-category-btn w-full"
            ).style("padding: 10px 12px;") as header_btn:
                with ui.row().classes("w-full items-center"):
                    ui.icon(category_icon, size="xs").classes("help-icon-accent").style("margin-right: 8px;")
                    ui.label(category_name).classes(
                        "grow text-left text-sm help-text-primary"
                    )
                    expand_icon = ui.icon("expand_more", size="xs").classes("help-expand-icon")
                    ui.badge(str(len(topics))).props("outline").classes(
                        "ml-2 help-category-count-badge"
                    )

                header_btn.on("click", lambda c=category: self._toggle_category(c))

            topics_container = ui.column().classes("w-full pl-2").style("display: none;")

            with topics_container:
                for topic in topics[:15]:
                    topic_btn = ui.button().props("flat dense align=left no-caps").classes(
                        "help-topic-btn w-full"
                    ).on("click", lambda t=topic: self.navigate_to(t.id))
                    with topic_btn:
                        ui.label(topic.title).classes("text-sm truncate help-sidebar-topic-title")
                    self.topic_buttons[topic.id] = topic_btn

                if len(topics) > 15:
                    ui.label(f"+ {len(topics) - 15} more...").classes(
                        "text-xs pl-4 py-1 help-text-muted"
                    )

            self.category_containers[category] = {
                'header': header_btn,
                'content': topics_container,
                'icon': expand_icon,
                'expanded': False
            }

    def _get_category_icon(self, category: HelpCategory) -> str:
        """Get icon for category"""
        icons = {
            HelpCategory.GETTING_STARTED: "rocket_launch",
            HelpCategory.ALERTS: "notifications",
            HelpCategory.TEMPLATES: "dashboard_customize",
            HelpCategory.CONNECTORS: "cable",
            HelpCategory.CHATBOT: "smart_toy",
            HelpCategory.TROUBLESHOOTING: "build",
            HelpCategory.INTEGRATIONS: "hub",
            HelpCategory.SETTINGS: "settings",
        }
        return icons.get(category, "article")

    def _build_content_area(self):
        """Build main content area with optional TOC sidebar"""
        try:
            with ui.column().classes("help-content-area grow h-full").style(
                "flex: 1; min-width: 0; display: flex; flex-direction: column;"
            ):
                with ui.row().classes("w-full items-center p-3 gap-2 help-toolbar"):
                    self.back_btn = ui.button(icon="arrow_back").props("flat dense").classes(
                        "help-nav-btn"
                    ).style("min-width: 36px;")
                    self.back_btn.on("click", self._go_back)
                    self.back_btn.disable()

                    self.forward_btn = ui.button(icon="arrow_forward").props("flat dense").classes(
                        "help-nav-btn"
                    ).style("min-width: 36px;")
                    self.forward_btn.on("click", self._go_forward)
                    self.forward_btn.disable()

                    ui.button(icon="home").props("flat dense").classes(
                        "help-nav-btn"
                    ).style("min-width: 36px;").on("click", self._show_welcome).tooltip("Home")

                    self.breadcrumb_container = ui.row().classes(
                        "help-breadcrumb grow items-center mx-2"
                    )
                    with self.breadcrumb_container:
                        ui.label("Home").classes("help-breadcrumb-item text-sm")

                    ui.button(icon="close").props("flat dense").classes(
                        "help-close-btn"
                    ).style("min-width: 36px;").on("click", lambda: self.dialog.close()).tooltip("Close")

                with ui.scroll_area().classes("help-scroll grow").style("flex: 1;"):
                    with ui.row().classes("w-full").style(
                        "display: flex; align-items: flex-start; gap: 0;"
                    ):
                        self.content_container = ui.column().classes("p-6").style(
                            "flex: 1; min-width: 0;"
                        )
                        self.toc_container = ui.column().classes("help-toc").style(
                            "display: none;"
                        )

        except Exception as e:
            logger.error(f"Error building content area: {e}")

    def _show_welcome(self):
        """Show welcome/home content with hero section and quick-start flow"""
        try:
            if not self.content_container:
                return

            self.current_topic = None
            self.content_container.clear()
            self._update_breadcrumb([("Home", None)])
            self._update_nav_buttons()
            self._hide_toc()
            self._clear_sidebar_active()

            with self.content_container:
                # Hero section
                with ui.column().classes("w-full mb-8"):
                    ui.label("Welcome to Mycelian Help").classes("help-hero-title mb-3")
                    ui.label(
                        "Find answers to your questions about Mycelian's features, "
                        "troubleshooting tips, and guides to get the most out of "
                        "your streaming toolkit."
                    ).classes("help-hero-subtitle")

                # Stats row
                with ui.row().classes("gap-4 mb-8"):
                    total_topics = self.help_manager.get_topic_count()
                    num_categories = len(self.help_manager.get_all_categories())

                    with ui.element("div").classes("help-stat-card p-4"):
                        ui.label(str(total_topics)).classes(
                            "text-2xl font-bold help-stat-value-primary"
                        )
                        ui.label("Help Topics").classes("text-sm help-text-secondary")

                    with ui.element("div").classes("help-stat-card p-4"):
                        ui.label(str(num_categories)).classes(
                            "text-2xl font-bold help-stat-value-success"
                        )
                        ui.label("Categories").classes("text-sm help-text-secondary")

                # Quick-start step flow
                with ui.column().classes("w-full mb-8"):
                    ui.label("Quick Start").classes(
                        "text-lg font-semibold mb-4 help-text-primary"
                    )
                    steps = [
                        ("1", "link", "Connect", "Link your Twitch account", "twitch_setup"),
                        ("2", "tune", "Configure", "Set up alerts & chatbot", "alerts_overview"),
                        ("3", "palette", "Customize", "Style your overlays", "templates_intro"),
                        ("4", "play_arrow", "Go Live", "Add sources to OBS", "obs_setup"),
                    ]
                    with ui.row().classes("w-full items-center gap-0"):
                        for i, (num, icon, label, desc, topic_id) in enumerate(steps):
                            if i > 0:
                                ui.element("div").classes("help-step-connector")
                            with ui.element("div").classes("help-step-item").on(
                                "click", lambda tid=topic_id: self.navigate_to(tid)
                            ):
                                with ui.element("div").classes("help-step-number"):
                                    ui.label(num).style("color: white; font-weight: 700; font-size: 0.85rem;")
                                ui.icon(icon, size="sm").classes("help-icon-accent")
                                ui.label(label).classes("text-sm font-medium help-text-primary")
                                ui.label(desc).classes("text-xs help-text-muted")

                # Popular Topics section
                popular_topics = self.help_manager.get_popular_topics(6)
                if popular_topics:
                    with ui.column().classes("w-full mb-8"):
                        ui.label("Popular Topics").classes(
                            "text-lg font-semibold mb-3 help-text-primary"
                        )

                        with ui.grid(columns=2).classes("gap-4 w-full"):
                            for topic in popular_topics:
                                with ui.element("div").classes("help-topic-card p-4").on(
                                    "click", lambda t=topic: self.navigate_to(t.id)
                                ):
                                    with ui.row().classes("items-start gap-3"):
                                        ui.icon("article", size="sm").classes(
                                            "help-icon-accent"
                                        ).style("margin-top: 2px;")
                                        with ui.column().classes("gap-1 grow"):
                                            ui.label(topic.title).classes(
                                                "font-medium text-sm help-text-primary"
                                            )
                                            summary = topic.summary
                                            if len(summary) > 80:
                                                summary = summary[:80] + "..."
                                            ui.label(summary).classes(
                                                "text-xs help-text-muted"
                                            )

                # Browse by Category
                with ui.column().classes("w-full mb-8"):
                    ui.label("Browse by Category").classes(
                        "text-lg font-semibold mb-3 help-text-primary"
                    )

                    with ui.grid(columns=3).classes("gap-3 w-full"):
                        categories = self.help_manager.get_all_categories()
                        for category in categories:
                            topics = self.help_manager.get_topics_by_category(category)
                            if topics:
                                category_name = category.value.replace("_", " ").title()
                                icon = self._get_category_icon(category)

                                with ui.element("div").classes(
                                    "help-topic-card help-category-card-accent p-4"
                                ).on("click", lambda c=category: self._show_category_topics(c)):
                                    with ui.row().classes("items-center gap-3 mb-2"):
                                        ui.icon(icon, size="sm").classes("help-icon-accent")
                                        ui.label(category_name).classes(
                                            "font-medium text-sm help-text-primary"
                                        )
                                    ui.label(f"{len(topics)} topics").classes(
                                        "text-xs help-text-muted mb-1"
                                    )
                                    for t in topics[:3]:
                                        ui.label(f"  {t.title}").classes(
                                            "text-xs help-text-muted truncate"
                                        ).style("max-width: 200px;")

                # Quick Actions
                with ui.row().classes("gap-3 mt-4"):
                    ui.button("Browse All Topics", icon="library_books").props("outline").classes(
                        "help-related-btn"
                    ).on("click", self._show_category_overview)

                    ui.button("Troubleshooting", icon="build").props("outline").classes(
                        "help-related-btn"
                    ).on("click", lambda: self._show_category_topics(HelpCategory.TROUBLESHOOTING))

        except Exception as e:
            logger.error(f"Error showing welcome: {e}")

    def navigate_to(self, topic_id: str):
        """Navigate to a help topic"""
        try:
            topic = self.help_manager.get_topic(topic_id)
            if not topic:
                notify(f"Help topic not found: {topic_id}", type="warning")
                return

            if self.current_topic and self.current_topic.id != topic_id:
                self.history = self.history[:self.history_index + 1]
                self.history.append(self.current_topic.id)
                self.history_index = len(self.history) - 1

            if len(self.history) > 50:
                self.history = self.history[-50:]
                self.history_index = len(self.history) - 1

            self.current_topic = topic
            self._render_topic(topic)
            self._update_nav_buttons()
            self._highlight_active_sidebar_topic(topic_id)
            self._expand_category_in_sidebar(topic.category)

        except Exception as e:
            logger.error(f"Error navigating to topic {topic_id}: {e}")
            notify(f"Error loading help topic: {e}", type="negative")

    def _render_topic(self, topic: HelpTopic):
        """Render a help topic with metadata, TOC, content, prev/next, and related topics"""
        try:
            if not self.content_container:
                return

            self.content_container.clear()

            category_name = topic.category.value.replace("_", " ").title()
            self._update_breadcrumb([
                ("Home", self._show_welcome),
                (category_name, lambda c=topic.category: self._show_category_topics(c)),
                (topic.title, None)
            ])

            # Build TOC
            toc_items = self._extract_toc(topic.content)
            self._render_toc(toc_items)

            with self.content_container:
                # Topic header
                with ui.column().classes("w-full mb-4"):
                    with ui.row().classes("items-center gap-3 mb-2"):
                        ui.label(category_name).classes("help-category-badge")

                    ui.label(topic.title).classes("text-2xl font-bold mb-2 help-text-primary")

                    if topic.summary:
                        ui.label(topic.summary).classes("text-base italic help-text-secondary")

                # Metadata bar
                with ui.row().classes("help-meta-bar w-full mb-2"):
                    if topic.keywords:
                        for kw in topic.keywords[:5]:
                            ui.label(kw).classes("help-keyword-pill")

                    reading_time = self._estimate_reading_time(topic.content)
                    with ui.row().classes("help-reading-time items-center"):
                        ui.icon("schedule", size="14px").classes("help-text-muted")
                        ui.label(f"{reading_time} min read").classes("help-text-muted").style(
                            "font-size: 0.75rem;"
                        )

                ui.separator().classes("help-separator-accent").style("margin: 8px 0 16px 0;")

                # Markdown content
                with ui.element("div").classes("help-markdown-content w-full"):
                    ui.markdown(topic.content).classes("w-full")

                # Prev/next navigation
                prev_topic, next_topic = self.help_manager.get_adjacent_topics(topic.id)
                if prev_topic or next_topic:
                    ui.separator().classes("help-separator-subtle").style(
                        "margin: 28px 0 20px 0;"
                    )
                    with ui.row().classes("w-full gap-4").style(
                        "display: flex; justify-content: space-between;"
                    ):
                        if prev_topic:
                            with ui.element("div").classes("help-prev-next-btn").on(
                                "click", lambda t=prev_topic: self.navigate_to(t.id)
                            ):
                                with ui.row().classes("items-center gap-2"):
                                    ui.icon("arrow_back", size="xs").classes("help-text-muted")
                                    ui.label("Previous").classes("help-prev-next-label")
                                ui.label(prev_topic.title).classes("help-prev-next-title")
                        else:
                            ui.element("div").style("flex: 1;")

                        if next_topic:
                            with ui.element("div").classes("help-prev-next-btn").on(
                                "click", lambda t=next_topic: self.navigate_to(t.id)
                            ).style("text-align: right;"):
                                with ui.row().classes("items-center gap-2 justify-end"):
                                    ui.label("Next").classes("help-prev-next-label")
                                    ui.icon("arrow_forward", size="xs").classes("help-text-muted")
                                ui.label(next_topic.title).classes("help-prev-next-title")
                        else:
                            ui.element("div").style("flex: 1;")

                # Related topics as mini-cards
                related = self.help_manager.get_related_topics(topic.id)
                if related:
                    ui.separator().classes("help-separator-subtle").style(
                        "margin: 24px 0 20px 0;"
                    )

                    ui.label("Related Topics").classes("font-semibold mb-3 help-text-primary")

                    with ui.grid(columns=2).classes("gap-3 w-full"):
                        for rel_topic in related:
                            with ui.element("div").classes("help-related-card").on(
                                "click", lambda t=rel_topic: self.navigate_to(t.id)
                            ):
                                with ui.row().classes("items-start gap-3"):
                                    ui.icon("article", size="xs").classes(
                                        "help-icon-accent-muted"
                                    ).style("margin-top: 2px;")
                                    with ui.column().classes("gap-1 grow"):
                                        ui.label(rel_topic.title).classes(
                                            "font-medium text-sm help-text-primary"
                                        )
                                        summary = rel_topic.summary
                                        if len(summary) > 70:
                                            summary = summary[:70] + "..."
                                        ui.label(summary).classes(
                                            "text-xs help-text-muted"
                                        )

            self._inject_content_enhancements()

        except Exception as e:
            logger.error(f"Error rendering topic {topic.id}: {e}")

    def _render_toc(self, toc_items: list):
        """Render the table of contents sidebar."""
        if not self.toc_container:
            return

        self.toc_container.clear()

        if not toc_items:
            self._hide_toc()
            return

        self.toc_container.style("display: flex; flex-direction: column;")
        with self.toc_container:
            ui.label("On This Page").classes("help-toc-title")
            for level, text in toc_items:
                extra_class = "help-toc-item-h3" if level == "h3" else ""
                escaped = text.replace("'", "\\'").replace('"', '\\"')
                ui.label(text).classes(
                    f"help-toc-item {extra_class}"
                ).on("click", lambda t=escaped: ui.run_javascript(f"""
                    (function() {{
                        const headings = document.querySelectorAll(
                            '.help-markdown-content h2, .help-markdown-content h3'
                        );
                        for (const h of headings) {{
                            if (h.textContent.trim() === '{t}') {{
                                h.scrollIntoView({{behavior: 'smooth', block: 'start'}});
                                break;
                            }}
                        }}
                    }})();
                """))

    def _hide_toc(self):
        """Hide the TOC sidebar."""
        if self.toc_container:
            self.toc_container.clear()
            self.toc_container.style("display: none;")

    def _highlight_active_sidebar_topic(self, topic_id: str):
        """Highlight the active topic in the sidebar."""
        self._clear_sidebar_active()
        btn = self.topic_buttons.get(topic_id)
        if btn:
            btn.classes(add="help-sidebar-active")

    def _clear_sidebar_active(self):
        """Remove active highlight from all sidebar topic buttons."""
        for btn in self.topic_buttons.values():
            btn.classes(remove="help-sidebar-active")

    def _on_search(self, e):
        """Handle search input"""
        try:
            query = e.args if isinstance(e.args, str) else (e.args or "")
            query = query.strip()

            if not self.search_results_container:
                return

            self.search_results_container.clear()

            if len(query) < 2:
                self.search_results_container.style("display: none;")
                return

            results = self.help_manager.search(query, limit=10)

            self.search_results_container.style("display: block;")

            with self.search_results_container:
                if results:
                    ui.label(f"Results for \"{query}\"").classes(
                        "text-xs font-medium uppercase tracking-wider mb-2 help-text-muted"
                    )

                    for topic in results:
                        with ui.button().props("flat dense align=left no-caps").classes(
                            "help-search-result w-full"
                        ).style("padding: 8px 12px;").on("click", lambda t=topic: self._on_search_result_click(t)):
                            with ui.column().classes("gap-0"):
                                ui.label(topic.title).classes("text-sm help-text-primary")
                                ui.label(topic.category.value.replace("_", " ").title()).classes(
                                    "text-xs help-text-muted"
                                )
                else:
                    ui.label(f"No results for \"{query}\"").classes(
                        "text-sm italic py-2 help-text-muted"
                    )

        except Exception as e:
            logger.error(f"Error handling search: {e}")

    def _on_search_result_click(self, topic: HelpTopic):
        """Handle search result click"""
        if self.search_results_container:
            self.search_results_container.clear()
            self.search_results_container.style("display: none;")

        self.navigate_to(topic.id)

    def _toggle_category(self, category: HelpCategory):
        """Toggle category expansion"""
        try:
            if category not in self.category_containers:
                return

            container = self.category_containers[category]

            if container['expanded']:
                container['content'].style("display: none;")
                container['icon'].style("transform: rotate(0deg); transition: transform 0.2s ease;")
                container['header'].classes(remove="expanded")
                container['expanded'] = False
            else:
                container['content'].style("display: block;")
                container['icon'].style("transform: rotate(180deg); transition: transform 0.2s ease;")
                container['header'].classes(add="expanded")
                container['expanded'] = True

        except Exception as e:
            logger.error(f"Error toggling category {category}: {e}")

    def _go_back(self):
        """Navigate back in history"""
        try:
            if self.history_index > 0:
                self.history_index -= 1
                topic_id = self.history[self.history_index]
                old_topic = self.current_topic
                self.current_topic = None

                topic = self.help_manager.get_topic(topic_id)
                if topic:
                    self.current_topic = topic
                    self._render_topic(topic)
                    self._highlight_active_sidebar_topic(topic_id)
                else:
                    self.current_topic = old_topic

                self._update_nav_buttons()
            elif self.history_index == 0 and self.current_topic:
                self.history_index = -1
                self._show_welcome()
            else:
                self._show_welcome()
        except Exception as e:
            logger.error(f"Error going back: {e}")

    def _go_forward(self):
        """Navigate forward in history"""
        try:
            if self.history_index < len(self.history) - 1:
                self.history_index += 1
                topic_id = self.history[self.history_index]

                topic = self.help_manager.get_topic(topic_id)
                if topic:
                    self.current_topic = topic
                    self._render_topic(topic)
                    self._highlight_active_sidebar_topic(topic_id)

                self._update_nav_buttons()
        except Exception as e:
            logger.error(f"Error going forward: {e}")

    def _update_nav_buttons(self):
        """Update navigation button states"""
        try:
            if self.back_btn:
                if self.history_index > 0 or (self.history_index == 0 and self.current_topic):
                    self.back_btn.enable()
                else:
                    self.back_btn.disable()

            if self.forward_btn:
                if self.history_index < len(self.history) - 1:
                    self.forward_btn.enable()
                else:
                    self.forward_btn.disable()
        except Exception as e:
            logger.error(f"Error updating nav buttons: {e}")

    def _update_breadcrumb(self, items: List[tuple]):
        """Update breadcrumb navigation"""
        try:
            if not self.breadcrumb_container:
                return

            self.breadcrumb_container.clear()

            with self.breadcrumb_container:
                for i, (text, action) in enumerate(items):
                    if i > 0:
                        ui.label("\u203a").classes("help-breadcrumb-separator")

                    if action:
                        ui.label(text).classes("help-breadcrumb-item text-sm cursor-pointer").on(
                            "click", action
                        )
                    else:
                        ui.label(text).classes("text-sm help-breadcrumb-current")

        except Exception as e:
            logger.error(f"Error updating breadcrumb: {e}")

    def _show_category_overview(self):
        """Show overview of all categories"""
        try:
            if not self.content_container:
                return

            self.current_topic = None
            self.content_container.clear()
            self._hide_toc()
            self._clear_sidebar_active()
            self._update_breadcrumb([
                ("Home", self._show_welcome),
                ("All Categories", None)
            ])

            with self.content_container:
                ui.label("Help Categories").classes("text-2xl font-bold mb-6 help-text-primary")

                categories = self.help_manager.get_all_categories()
                for category in categories:
                    topics = self.help_manager.get_topics_by_category(category)
                    if topics:
                        category_name = category.value.replace("_", " ").title()
                        icon = self._get_category_icon(category)

                        with ui.element("div").classes("help-category-overview-card mb-4"):
                            with ui.row().classes("w-full items-center mb-3"):
                                ui.icon(icon, size="md").classes("help-icon-accent")
                                ui.label(category_name).classes(
                                    "text-lg font-semibold grow ml-3 help-text-primary"
                                )
                                ui.label(f"{len(topics)} topics").classes("text-sm help-text-muted")

                            with ui.row().classes("gap-2 flex-wrap"):
                                for topic in topics[:6]:
                                    ui.button(topic.title).props("outline no-caps").classes(
                                        "help-related-btn text-xs"
                                    ).on("click", lambda t=topic: self.navigate_to(t.id))

                                if len(topics) > 6:
                                    ui.button(f"View all {len(topics)} topics").props("flat no-caps").classes(
                                        "text-xs help-flat-link"
                                    ).on(
                                        "click", lambda c=category: self._show_category_topics(c)
                                    )

        except Exception as e:
            logger.error(f"Error showing category overview: {e}")

    def _show_category_topics(self, category: HelpCategory):
        """Show all topics in a category"""
        try:
            if not self.content_container:
                return

            self.current_topic = None
            self.content_container.clear()
            self._hide_toc()
            self._clear_sidebar_active()

            category_name = category.value.replace("_", " ").title()
            self._update_breadcrumb([
                ("Home", self._show_welcome),
                (category_name, None)
            ])

            with self.content_container:
                with ui.row().classes("items-center gap-3 mb-6"):
                    ui.icon(self._get_category_icon(category), size="lg").classes("help-icon-accent")
                    ui.label(f"{category_name} Topics").classes(
                        "text-2xl font-bold help-text-primary"
                    )

                topics = self.help_manager.get_topics_by_category(category)

                with ui.grid(columns=1).classes("gap-3 w-full"):
                    for topic in topics:
                        with ui.element("div").classes("help-topic-card p-4").on(
                            "click", lambda t=topic: self.navigate_to(t.id)
                        ):
                            with ui.row().classes("items-start gap-3"):
                                ui.icon("article", size="sm").classes(
                                    "help-icon-accent-muted"
                                ).style("margin-top: 2px;")
                                with ui.column().classes("gap-1 grow"):
                                    ui.label(topic.title).classes("font-medium help-text-primary")
                                    ui.label(topic.summary).classes("text-sm help-text-secondary")
                                    if topic.keywords:
                                        with ui.row().classes("gap-1 mt-1"):
                                            for kw in topic.keywords[:3]:
                                                ui.label(kw).classes("help-keyword-pill")

        except Exception as e:
            logger.error(f"Error showing category topics: {e}")

    def _navigate_to_category(self, category_name: str):
        """Navigate to the first topic in a given category or specific topic"""
        try:
            if '_' in category_name and not category_name.endswith('_started') and not category_name.endswith('_overview'):
                topic = self.help_manager.get_topic(category_name)
                if topic:
                    self.navigate_to(topic.id)
                    try:
                        self._expand_category_in_sidebar(topic.category)
                    except Exception:
                        pass
                else:
                    self._show_welcome()
                return

            from .help_content import HelpCategory

            category_mapping = {
                "getting_started": HelpCategory.GETTING_STARTED,
                "alerts": HelpCategory.ALERTS,
                "templates": HelpCategory.TEMPLATES,
                "connectors": HelpCategory.CONNECTORS,
                "chatbot": HelpCategory.CHATBOT,
                "integrations": HelpCategory.INTEGRATIONS,
                "settings": HelpCategory.SETTINGS,
                "troubleshooting": HelpCategory.TROUBLESHOOTING,
            }

            category = category_mapping.get(category_name.lower())
            if not category:
                self._show_welcome()
                return

            topics = self.help_manager.get_topics_by_category(category)
            if topics:
                first_topic = topics[0]
                self.navigate_to(first_topic.id)
                self._expand_category_in_sidebar(category)
            else:
                self._show_welcome()

        except Exception as e:
            self._show_welcome()

    def _expand_category_in_sidebar(self, category: HelpCategory):
        """Expand a category in the sidebar if it exists"""
        try:
            if category in self.category_containers:
                container = self.category_containers[category]
                if not container['expanded']:
                    container['content'].style("display: block;")
                    container['icon'].style("transform: rotate(180deg); transition: transform 0.2s ease;")
                    container['header'].classes(add="expanded")
                    container['expanded'] = True
        except Exception as e:
            logger.error(f"Error expanding category in sidebar: {e}")


def show_help_browser(topic_id: str = None, category: str = None):
    """Show the help browser

    Args:
        topic_id: Specific topic ID to open
        category: Help category to navigate to (will show first topic in category)
    """
    try:
        browser = HelpBrowser()
        browser.show(initial_topic=topic_id, category=category)
    except Exception as e:
        logger.error(f"Error showing help browser: {e}")
        notify(f"Error opening help: {e}", type="negative")

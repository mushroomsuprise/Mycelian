"""
Help Browser UI

In-app help browser component with search, navigation, and markdown rendering.
Styled to match the Mycelian application design.
"""

from nicegui import ui
import logging
from typing import Optional, List
from .help_manager import get_help_manager, HelpTopic, HelpCategory

logger = logging.getLogger(__name__)

# CSS for help browser styling
HELP_BROWSER_CSS = """
/* Help Browser Dialog Styling */
.help-browser-dialog .q-card {
    background: #121212 !important;
    border: 1px solid rgba(115, 0, 255, 0.3) !important;
    border-radius: 12px !important;
}

/* Sidebar styling */
.help-sidebar {
    background: #161616 !important;
    border-right: 1px solid rgba(115, 0, 255, 0.2) !important;
}

.help-sidebar-header {
    background: linear-gradient(135deg, rgba(115, 0, 255, 0.15) 0%, rgba(115, 0, 255, 0.05) 100%) !important;
    border-bottom: 1px solid rgba(115, 0, 255, 0.2) !important;
}

/* Search input styling */
.help-search-input .q-field__control {
    background: rgba(255, 255, 255, 0.05) !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    border-radius: 8px !important;
}

.help-search-input .q-field__control:hover {
    border-color: rgba(115, 0, 255, 0.4) !important;
}

.help-search-input .q-field__control:focus-within {
    border-color: rgb(115, 0, 255) !important;
    box-shadow: 0 0 0 2px rgba(115, 0, 255, 0.2) !important;
}

/* Category buttons */
.help-category-btn {
    background: rgba(255, 255, 255, 0.03) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    border-radius: 8px !important;
    transition: all 0.2s ease !important;
    margin-bottom: 4px !important;
}

.help-category-btn:hover {
    background: rgba(115, 0, 255, 0.1) !important;
    border-color: rgba(115, 0, 255, 0.3) !important;
}

.help-category-btn.expanded {
    background: rgba(115, 0, 255, 0.15) !important;
    border-color: rgba(115, 0, 255, 0.4) !important;
}

/* Topic buttons in sidebar */
.help-topic-btn {
    background: transparent !important;
    border-left: 2px solid transparent !important;
    border-radius: 0 6px 6px 0 !important;
    transition: all 0.15s ease !important;
    padding: 8px 12px 8px 16px !important;
    margin: 2px 0 !important;
}

.help-topic-btn:hover {
    background: rgba(115, 0, 255, 0.08) !important;
    border-left-color: rgba(115, 0, 255, 0.5) !important;
}

.help-topic-btn.active {
    background: rgba(115, 0, 255, 0.12) !important;
    border-left-color: rgb(115, 0, 255) !important;
}

/* Content area */
.help-content-area {
    background: #1a1a1a !important;
}

/* Breadcrumb styling */
.help-breadcrumb {
    background: rgba(255, 255, 255, 0.03) !important;
    border-radius: 6px !important;
    padding: 8px 12px !important;
}

.help-breadcrumb-item {
    color: rgba(200, 200, 220, 0.7) !important;
    transition: color 0.15s ease !important;
}

.help-breadcrumb-item:hover {
    color: rgb(115, 0, 255) !important;
}

.help-breadcrumb-separator {
    color: rgba(200, 200, 220, 0.4) !important;
    margin: 0 8px !important;
}

/* Topic cards */
.help-topic-card {
    background: rgba(35, 35, 45, 0.7) !important;
    border: 1px solid rgba(255, 255, 255, 0.05) !important;
    border-radius: 8px !important;
    transition: all 0.2s ease !important;
    cursor: pointer !important;
}

.help-topic-card:hover {
    background: rgba(45, 45, 55, 0.8) !important;
    border-color: rgba(115, 0, 255, 0.3) !important;
    transform: translateY(-2px) !important;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3) !important;
}

/* Category badge */
.help-category-badge {
    background: rgba(115, 0, 255, 0.2) !important;
    color: rgb(180, 140, 255) !important;
    border: 1px solid rgba(115, 0, 255, 0.3) !important;
    border-radius: 4px !important;
    font-size: 0.7rem !important;
    padding: 2px 8px !important;
    text-transform: uppercase !important;
    letter-spacing: 0.05em !important;
}

/* Markdown content styling */
.help-markdown-content {
    color: rgba(240, 240, 255, 0.9) !important;
    line-height: 1.7 !important;
}

.help-markdown-content h1,
.help-markdown-content h2,
.help-markdown-content h3,
.help-markdown-content h4 {
    color: rgb(240, 240, 255) !important;
    margin-top: 1.5em !important;
    margin-bottom: 0.75em !important;
}

.help-markdown-content h1 { font-size: 1.75rem !important; }
.help-markdown-content h2 { font-size: 1.4rem !important; }
.help-markdown-content h3 { font-size: 1.2rem !important; }

.help-markdown-content p {
    margin-bottom: 1em !important;
}

.help-markdown-content ul,
.help-markdown-content ol {
    margin-left: 1.5em !important;
    margin-bottom: 1em !important;
}

.help-markdown-content li {
    margin-bottom: 0.5em !important;
}

.help-markdown-content code {
    background: rgba(115, 0, 255, 0.15) !important;
    color: rgb(200, 170, 255) !important;
    padding: 2px 6px !important;
    border-radius: 4px !important;
    font-family: 'Consolas', 'Monaco', monospace !important;
}

.help-markdown-content pre {
    background: rgba(0, 0, 0, 0.3) !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    border-radius: 8px !important;
    padding: 16px !important;
    overflow-x: auto !important;
}

.help-markdown-content pre code {
    background: transparent !important;
    padding: 0 !important;
}

.help-markdown-content a {
    color: rgb(150, 120, 255) !important;
    text-decoration: none !important;
    border-bottom: 1px solid transparent !important;
    transition: all 0.15s ease !important;
}

.help-markdown-content a:hover {
    color: rgb(180, 150, 255) !important;
    border-bottom-color: rgb(150, 120, 255) !important;
}

.help-markdown-content blockquote {
    border-left: 3px solid rgb(115, 0, 255) !important;
    background: rgba(115, 0, 255, 0.08) !important;
    padding: 12px 16px !important;
    margin: 1em 0 !important;
    border-radius: 0 6px 6px 0 !important;
}

.help-markdown-content table {
    width: 100% !important;
    border-collapse: collapse !important;
    margin: 1em 0 !important;
}

.help-markdown-content th,
.help-markdown-content td {
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    padding: 8px 12px !important;
}

.help-markdown-content th {
    background: rgba(115, 0, 255, 0.15) !important;
    font-weight: 600 !important;
}

/* Search results */
.help-search-result {
    background: rgba(35, 35, 45, 0.6) !important;
    border: 1px solid rgba(255, 255, 255, 0.05) !important;
    border-radius: 6px !important;
    transition: all 0.15s ease !important;
    margin-bottom: 4px !important;
}

.help-search-result:hover {
    background: rgba(115, 0, 255, 0.1) !important;
    border-color: rgba(115, 0, 255, 0.3) !important;
}

/* Navigation buttons */
.help-nav-btn {
    background: rgba(255, 255, 255, 0.05) !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    border-radius: 6px !important;
    transition: all 0.15s ease !important;
}

.help-nav-btn:hover {
    background: rgba(115, 0, 255, 0.15) !important;
    border-color: rgba(115, 0, 255, 0.4) !important;
}

.help-nav-btn:disabled {
    opacity: 0.4 !important;
}

/* Close button */
.help-close-btn {
    background: rgba(255, 255, 255, 0.05) !important;
    border-radius: 6px !important;
    transition: all 0.15s ease !important;
}

.help-close-btn:hover {
    background: rgba(239, 68, 68, 0.2) !important;
    color: rgb(239, 68, 68) !important;
}

/* Quick access cards */
.help-quick-card {
    background: linear-gradient(135deg, rgba(115, 0, 255, 0.1) 0%, rgba(115, 0, 255, 0.02) 100%) !important;
    border: 1px solid rgba(115, 0, 255, 0.2) !important;
    border-radius: 8px !important;
    transition: all 0.2s ease !important;
    cursor: pointer !important;
}

.help-quick-card:hover {
    background: linear-gradient(135deg, rgba(115, 0, 255, 0.15) 0%, rgba(115, 0, 255, 0.05) 100%) !important;
    border-color: rgba(115, 0, 255, 0.4) !important;
    transform: translateY(-2px) !important;
}

/* Stat cards */
.help-stat-card {
    background: rgba(35, 35, 45, 0.5) !important;
    border: 1px solid rgba(255, 255, 255, 0.05) !important;
    border-radius: 8px !important;
}

/* Related topics */
.help-related-btn {
    background: rgba(255, 255, 255, 0.05) !important;
    border: 1px solid rgba(255, 255, 255, 0.1) !important;
    border-radius: 16px !important;
    transition: all 0.15s ease !important;
    font-size: 0.85rem !important;
}

.help-related-btn:hover {
    background: rgba(115, 0, 255, 0.15) !important;
    border-color: rgba(115, 0, 255, 0.4) !important;
}

/* Scrollbar styling */
.help-scroll::-webkit-scrollbar {
    width: 6px !important;
}

.help-scroll::-webkit-scrollbar-track {
    background: rgba(255, 255, 255, 0.02) !important;
}

.help-scroll::-webkit-scrollbar-thumb {
    background: rgba(115, 0, 255, 0.3) !important;
    border-radius: 3px !important;
}

.help-scroll::-webkit-scrollbar-thumb:hover {
    background: rgba(115, 0, 255, 0.5) !important;
}
"""


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
        self.breadcrumb_container = None
        self.category_containers = {}
        self.back_btn = None
        self.forward_btn = None

    def show(self, initial_topic: str = None, category: str = None):
        """Show help browser in a dialog

        Args:
            initial_topic: Specific topic ID to open
            category: Help category to navigate to (will show first topic in category)
        """
        try:
            # Add CSS
            ui.add_head_html(f"<style>{HELP_BROWSER_CSS}</style>")

            with ui.dialog() as self.dialog:
                self.dialog.classes("help-browser-dialog")
                self.dialog.props("maximized")

                with ui.card().classes("w-full h-full p-0 m-0").style(
                    "background: #121212; border-radius: 0; max-width: 100%; max-height: 100%;"
                ):
                    with ui.row().classes("w-full h-full").style("display: flex; height: 100%;"):
                        # Sidebar
                        self._build_sidebar()

                        # Main content area
                        self._build_content_area()

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
            ui.notify(f"Error opening help: {e}", type="negative")

    def _build_sidebar(self):
        """Build sidebar with search and categories"""
        try:
            with ui.column().classes("help-sidebar help-scroll").style(
                "width: 280px; min-width: 280px; height: 100%; display: flex; flex-direction: column;"
            ):
                # Sidebar header
                with ui.column().classes("help-sidebar-header w-full p-4"):
                    with ui.row().classes("w-full items-center justify-between mb-3"):
                        ui.label("Help Center").classes("text-lg font-semibold").style("color: rgb(240, 240, 255);")
                        ui.icon("help_outline", size="sm").style("color: rgb(115, 0, 255);")
                    
                    # Search input
                    with ui.input(
                        placeholder="Search help topics...",
                    ).props("outlined dense clearable").classes(
                        "help-search-input w-full"
                    ).style("color: white;") as search_input:
                        search_input.on("update:model-value", self._on_search)

                # Search results container (hidden by default)
                self.search_results_container = ui.column().classes(
                    "w-full px-3 py-2"
                ).style("display: none;")

                # Categories container
                with ui.scroll_area().classes("help-scroll flex-grow").style("flex: 1;"):
                    with ui.column().classes("w-full p-3 gap-1"):
                        ui.label("Categories").classes("text-xs font-medium uppercase tracking-wider mb-2").style(
                            "color: rgba(200, 200, 220, 0.5);"
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
            # Category header button
            with ui.button().props("flat dense align=left no-caps").classes(
                "help-category-btn w-full"
            ).style("padding: 10px 12px;") as header_btn:
                with ui.row().classes("w-full items-center"):
                    ui.icon(category_icon, size="xs").style("color: rgb(115, 0, 255); margin-right: 8px;")
                    ui.label(category_name).classes("flex-grow text-left text-sm").style("color: rgba(240, 240, 255, 0.9);")
                    expand_icon = ui.icon("expand_more", size="xs").style(
                        "color: rgba(200, 200, 220, 0.5); transition: transform 0.2s ease;"
                    )
                    ui.badge(str(len(topics))).props("color=purple-9 outline").classes("ml-2").style("font-size: 0.65rem;")
                
                header_btn.on("click", lambda c=category: self._toggle_category(c))

            # Topics container (initially hidden)
            topics_container = ui.column().classes("w-full pl-2").style("display: none;")
            
            with topics_container:
                for topic in topics[:15]:  # Limit to prevent UI overload
                    with ui.button().props("flat dense align=left no-caps").classes(
                        "help-topic-btn w-full"
                    ).on("click", lambda t=topic: self.navigate_to(t.id)) as topic_btn:
                        ui.label(topic.title).classes("text-sm truncate").style(
                            "color: rgba(200, 200, 220, 0.8); max-width: 200px;"
                        )
                
                if len(topics) > 15:
                    ui.label(f"+ {len(topics) - 15} more...").classes("text-xs pl-4 py-1").style(
                        "color: rgba(200, 200, 220, 0.5);"
                    )

            # Store references
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
        """Build main content area"""
        try:
            with ui.column().classes("help-content-area flex-grow h-full").style(
                "flex: 1; min-width: 0; display: flex; flex-direction: column;"
            ):
                # Header bar with navigation and close button
                with ui.row().classes("w-full items-center p-3 gap-2").style(
                    "background: rgba(0, 0, 0, 0.2); border-bottom: 1px solid rgba(255, 255, 255, 0.05);"
                ):
                    # Navigation buttons
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
                    
                    # Breadcrumb
                    self.breadcrumb_container = ui.row().classes(
                        "help-breadcrumb flex-grow items-center mx-2"
                    )
                    with self.breadcrumb_container:
                        ui.label("Home").classes("help-breadcrumb-item text-sm")
                    
                    # Close button
                    ui.button(icon="close").props("flat dense").classes(
                        "help-close-btn"
                    ).style("min-width: 36px;").on("click", lambda: self.dialog.close()).tooltip("Close")

                # Content container with scroll
                with ui.scroll_area().classes("help-scroll flex-grow").style("flex: 1;"):
                    self.content_container = ui.column().classes("w-full p-6")

        except Exception as e:
            logger.error(f"Error building content area: {e}")

    def _show_welcome(self):
        """Show welcome/home content"""
        try:
            if not self.content_container:
                return

            self.current_topic = None
            self.content_container.clear()
            self._update_breadcrumb([("Home", None)])
            self._update_nav_buttons()

            with self.content_container:
                # Welcome header
                with ui.column().classes("w-full mb-8"):
                    ui.label("Welcome to Mycelian Help").classes("text-3xl font-bold mb-2").style(
                        "color: rgb(240, 240, 255);"
                    )
                    ui.label(
                        "Find answers to your questions about Mycelian's features, troubleshooting tips, "
                        "and guides to get the most out of your streaming toolkit."
                    ).classes("text-base").style("color: rgba(240, 240, 255, 0.7); max-width: 700px;")

                # Stats row
                with ui.row().classes("gap-4 mb-8"):
                    total_topics = self.help_manager.get_topic_count()
                    categories = len(self.help_manager.get_all_categories())
                    
                    with ui.element("div").classes("help-stat-card p-4"):
                        ui.label(str(total_topics)).classes("text-2xl font-bold").style("color: rgb(115, 0, 255);")
                        ui.label("Help Topics").classes("text-sm").style("color: rgba(200, 200, 220, 0.7);")
                    
                    with ui.element("div").classes("help-stat-card p-4"):
                        ui.label(str(categories)).classes("text-2xl font-bold").style("color: rgb(100, 200, 150);")
                        ui.label("Categories").classes("text-sm").style("color: rgba(200, 200, 220, 0.7);")

                # Getting Started section
                getting_started_topic = self.help_manager.get_topic("getting_started_intro")
                if getting_started_topic:
                    with ui.column().classes("w-full mb-8"):
                        ui.label("Getting Started").classes("text-lg font-semibold mb-3").style(
                            "color: rgb(240, 240, 255);"
                        )
                        
                        with ui.element("div").classes("help-quick-card p-4").on(
                            "click", lambda t=getting_started_topic: self.navigate_to(t.id)
                        ):
                            with ui.row().classes("items-center gap-3"):
                                ui.icon("rocket_launch", size="md").style("color: rgb(115, 0, 255);")
                                with ui.column().classes("gap-1"):
                                    ui.label("Welcome to Mycelian").classes("font-semibold").style(
                                        "color: rgb(240, 240, 255);"
                                    )
                                    ui.label(getting_started_topic.summary).classes("text-sm").style(
                                        "color: rgba(200, 200, 220, 0.7);"
                                    )

                # Popular Topics section
                popular_topics = self.help_manager.get_popular_topics(6)
                if popular_topics:
                    with ui.column().classes("w-full mb-8"):
                        ui.label("Popular Topics").classes("text-lg font-semibold mb-3").style(
                            "color: rgb(240, 240, 255);"
                        )
                        
                        with ui.grid(columns=2).classes("gap-4 w-full"):
                            for topic in popular_topics:
                                with ui.element("div").classes("help-topic-card p-4").on(
                                    "click", lambda t=topic: self.navigate_to(t.id)
                                ):
                                    with ui.row().classes("items-start gap-3"):
                                        ui.icon("article", size="sm").style("color: rgb(115, 0, 255); margin-top: 2px;")
                                        with ui.column().classes("gap-1 flex-grow"):
                                            ui.label(topic.title).classes("font-medium text-sm").style(
                                                "color: rgb(240, 240, 255);"
                                            )
                                            ui.label(topic.summary[:80] + "..." if len(topic.summary) > 80 else topic.summary).classes(
                                                "text-xs"
                                            ).style("color: rgba(200, 200, 220, 0.6);")

                # Browse by Category section
                with ui.column().classes("w-full mb-8"):
                    ui.label("Browse by Category").classes("text-lg font-semibold mb-3").style(
                        "color: rgb(240, 240, 255);"
                    )
                    
                    with ui.grid(columns=3).classes("gap-3 w-full"):
                        categories = self.help_manager.get_all_categories()
                        for category in categories:
                            topics = self.help_manager.get_topics_by_category(category)
                            if topics:
                                category_name = category.value.replace("_", " ").title()
                                icon = self._get_category_icon(category)
                                
                                with ui.element("div").classes("help-topic-card p-4").on(
                                    "click", lambda c=category: self._show_category_topics(c)
                                ):
                                    with ui.row().classes("items-center gap-3"):
                                        ui.icon(icon, size="sm").style("color: rgb(115, 0, 255);")
                                        with ui.column().classes("gap-0"):
                                            ui.label(category_name).classes("font-medium text-sm").style(
                                                "color: rgb(240, 240, 255);"
                                            )
                                            ui.label(f"{len(topics)} topics").classes("text-xs").style(
                                                "color: rgba(200, 200, 220, 0.5);"
                                            )

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
                ui.notify(f"Help topic not found: {topic_id}", type="warning")
                return

            # Update history
            if self.current_topic and self.current_topic.id != topic_id:
                # Truncate forward history when navigating to new topic
                self.history = self.history[:self.history_index + 1]
                self.history.append(self.current_topic.id)
                self.history_index = len(self.history) - 1
            
            # Limit history size
            if len(self.history) > 50:
                self.history = self.history[-50:]
                self.history_index = len(self.history) - 1

            self.current_topic = topic
            self._render_topic(topic)
            self._update_nav_buttons()

        except Exception as e:
            logger.error(f"Error navigating to topic {topic_id}: {e}")
            ui.notify(f"Error loading help topic: {e}", type="negative")

    def _render_topic(self, topic: HelpTopic):
        """Render a help topic"""
        try:
            if not self.content_container:
                return

            self.content_container.clear()
            
            # Update breadcrumb
            category_name = topic.category.value.replace("_", " ").title()
            self._update_breadcrumb([
                ("Home", self._show_welcome),
                (category_name, lambda c=topic.category: self._show_category_topics(c)),
                (topic.title, None)
            ])

            with self.content_container:
                # Topic header
                with ui.column().classes("w-full mb-6"):
                    with ui.row().classes("items-center gap-3 mb-2"):
                        ui.label(category_name).classes("help-category-badge")
                    
                    ui.label(topic.title).classes("text-2xl font-bold mb-2").style(
                        "color: rgb(240, 240, 255);"
                    )
                    
                    if topic.summary:
                        ui.label(topic.summary).classes("text-base italic").style(
                            "color: rgba(240, 240, 255, 0.7);"
                        )

                ui.separator().style("background: rgba(115, 0, 255, 0.2); margin: 16px 0;")

                # Content
                with ui.element("div").classes("help-markdown-content w-full"):
                    ui.markdown(topic.content).classes("w-full")

                # Related topics
                related = self.help_manager.get_related_topics(topic.id)
                if related:
                    ui.separator().style("background: rgba(255, 255, 255, 0.05); margin: 32px 0 24px 0;")
                    
                    ui.label("Related Topics").classes("font-semibold mb-3").style(
                        "color: rgb(240, 240, 255);"
                    )
                    
                    with ui.row().classes("gap-2 flex-wrap"):
                        for rel_topic in related:
                            ui.button(rel_topic.title).props("outline no-caps").classes(
                                "help-related-btn"
                            ).on("click", lambda t=rel_topic: self.navigate_to(t.id))

        except Exception as e:
            logger.error(f"Error rendering topic {topic.id}: {e}")

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
                    ui.label(f"Results for \"{query}\"").classes("text-xs font-medium uppercase tracking-wider mb-2").style(
                        "color: rgba(200, 200, 220, 0.5);"
                    )
                    
                    for topic in results:
                        with ui.button().props("flat dense align=left no-caps").classes(
                            "help-search-result w-full"
                        ).style("padding: 8px 12px;").on("click", lambda t=topic: self._on_search_result_click(t)):
                            with ui.column().classes("gap-0"):
                                ui.label(topic.title).classes("text-sm").style(
                                    "color: rgba(240, 240, 255, 0.9);"
                                )
                                ui.label(topic.category.value.replace("_", " ").title()).classes("text-xs").style(
                                    "color: rgba(200, 200, 220, 0.5);"
                                )
                else:
                    ui.label(f"No results for \"{query}\"").classes("text-sm italic py-2").style(
                        "color: rgba(200, 200, 220, 0.5);"
                    )

        except Exception as e:
            logger.error(f"Error handling search: {e}")

    def _on_search_result_click(self, topic: HelpTopic):
        """Handle search result click"""
        # Clear search results
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
                # Temporarily disable history update
                old_topic = self.current_topic
                self.current_topic = None
                
                topic = self.help_manager.get_topic(topic_id)
                if topic:
                    self.current_topic = topic
                    self._render_topic(topic)
                else:
                    self.current_topic = old_topic
                    
                self._update_nav_buttons()
            elif self.history_index == 0 and self.current_topic:
                # Go to welcome page
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
                        ui.label("›").classes("help-breadcrumb-separator")
                    
                    if action:
                        ui.label(text).classes("help-breadcrumb-item text-sm cursor-pointer").on(
                            "click", action
                        )
                    else:
                        ui.label(text).classes("text-sm").style("color: rgb(240, 240, 255);")
                        
        except Exception as e:
            logger.error(f"Error updating breadcrumb: {e}")

    def _show_category_overview(self):
        """Show overview of all categories"""
        try:
            if not self.content_container:
                return

            self.current_topic = None
            self.content_container.clear()
            self._update_breadcrumb([
                ("Home", self._show_welcome),
                ("All Categories", None)
            ])

            with self.content_container:
                ui.label("Help Categories").classes("text-2xl font-bold mb-6").style(
                    "color: rgb(240, 240, 255);"
                )

                categories = self.help_manager.get_all_categories()
                for category in categories:
                    topics = self.help_manager.get_topics_by_category(category)
                    if topics:
                        category_name = category.value.replace("_", " ").title()
                        icon = self._get_category_icon(category)

                        with ui.element("div").classes("help-topic-card p-5 mb-4"):
                            with ui.row().classes("w-full items-center mb-4"):
                                ui.icon(icon, size="md").style("color: rgb(115, 0, 255);")
                                ui.label(category_name).classes("text-lg font-semibold flex-grow ml-3").style(
                                    "color: rgb(240, 240, 255);"
                                )
                                ui.label(f"{len(topics)} topics").classes("text-sm").style(
                                    "color: rgba(200, 200, 220, 0.5);"
                                )

                            with ui.row().classes("gap-2 flex-wrap"):
                                for topic in topics[:6]:
                                    ui.button(topic.title).props("outline no-caps").classes(
                                        "help-related-btn text-xs"
                                    ).on("click", lambda t=topic: self.navigate_to(t.id))

                                if len(topics) > 6:
                                    ui.button(f"View all {len(topics)} topics").props("flat no-caps").classes(
                                        "text-xs"
                                    ).style("color: rgb(115, 0, 255);").on(
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
            
            category_name = category.value.replace("_", " ").title()
            self._update_breadcrumb([
                ("Home", self._show_welcome),
                (category_name, None)
            ])

            with self.content_container:
                with ui.row().classes("items-center gap-3 mb-6"):
                    ui.icon(self._get_category_icon(category), size="lg").style("color: rgb(115, 0, 255);")
                    ui.label(f"{category_name} Topics").classes("text-2xl font-bold").style(
                        "color: rgb(240, 240, 255);"
                    )

                topics = self.help_manager.get_topics_by_category(category)

                with ui.grid(columns=1).classes("gap-3 w-full"):
                    for topic in topics:
                        with ui.element("div").classes("help-topic-card p-4").on(
                            "click", lambda t=topic: self.navigate_to(t.id)
                        ):
                            with ui.row().classes("items-start gap-3"):
                                ui.icon("article", size="sm").style(
                                    "color: rgba(115, 0, 255, 0.7); margin-top: 2px;"
                                )
                                with ui.column().classes("gap-1 flex-grow"):
                                    ui.label(topic.title).classes("font-medium").style(
                                        "color: rgb(240, 240, 255);"
                                    )
                                    ui.label(topic.summary).classes("text-sm").style(
                                        "color: rgba(200, 200, 220, 0.7);"
                                    )

        except Exception as e:
            logger.error(f"Error showing category topics: {e}")

    def _navigate_to_category(self, category_name: str):
        """Navigate to the first topic in a given category or specific topic"""
        try:
            # Check if this is actually a specific topic ID
            if '_' in category_name and not category_name.endswith('_started') and not category_name.endswith('_overview'):
                # It's a specific topic ID, navigate directly to it
                topic = self.help_manager.get_topic(category_name)
                if topic:
                    self.navigate_to(topic.id)
                    # Try to expand the category in sidebar if possible
                    try:
                        self._expand_category_in_sidebar(topic.category)
                    except:
                        pass  # Not critical if this fails
                else:
                    self._show_welcome()
                return

            # It's a category, navigate to first topic in category
            from .help_content import HelpCategory

            # Map string category name to HelpCategory enum
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

            # Get topics in this category
            topics = self.help_manager.get_topics_by_category(category)
            if topics:
                # Navigate to the first topic in the category
                first_topic = topics[0]
                self.navigate_to(first_topic.id)
                # Also expand the category in the sidebar
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
        ui.notify(f"Error opening help: {e}", type="negative")

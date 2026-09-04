# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Base UI Styles for Mycelian Application

Contains CSS that uses theme variables for consistent styling across
dark and light modes.
"""

# Base CSS with theme variables
BASE_CSS = """
/* =========================================
   Mycelian Base Styles - Theme Aware
   ========================================= */

/* Smooth theme transitions on chrome (not feed/list rows) */
.q-btn,
.q-card,
.q-tab,
.q-tabs,
.q-toolbar,
.q-header,
.q-footer,
.q-drawer,
.q-dialog {
    transition: background-color 0.2s ease,
                border-color 0.2s ease,
                color 0.2s ease,
                box-shadow 0.2s ease;
}

.q-item,
.q-item *,
.activity-feed-current,
.activity-feed-current *,
.activity-feed-previous,
.activity-feed-previous *,
.activity-feed-condensed,
.activity-feed-condensed * {
    transition: none;
}

/* Disable transitions during initial load to prevent FOUC */
.no-transition * {
    transition: none !important;
}

/* =========================================
   Root HTML and Body - Force Theme Colors
   ========================================= */

html {
    background-color: var(--color-bg-base) !important;
}

body {
    background-color: var(--color-bg-base) !important;
    color: var(--color-text-primary) !important;
    overflow: hidden !important;
    font-family: var(--font-family-app, 'Inter', -apple-system, BlinkMacSystemFont, sans-serif);
}

/* NiceGUI root container - very specific selectors */
#app {
    background-color: var(--color-bg-base) !important;
    color: var(--color-text-primary) !important;
    min-height: 100vh !important;
}

.q-app {
    background-color: var(--color-bg-base) !important;
    color: var(--color-text-primary) !important;
}

.nicegui-content {
    background-color: var(--color-bg-base) !important;
    color: var(--color-text-primary) !important;
}

/* Quasar page and layout - very specific */
.q-page {
    background-color: var(--color-bg-base) !important;
    color: var(--color-text-primary) !important;
}

.q-layout {
    background-color: var(--color-bg-base) !important;
    color: var(--color-text-primary) !important;
}

.q-page-container {
    background-color: var(--color-bg-base) !important;
    color: var(--color-text-primary) !important;
}

.q-layout__section--marginal {
    background-color: var(--color-bg-base) !important;
}

/* Force all text elements to use theme colors */
body p, body span, body div, body label, 
body h1, body h2, body h3, body h4, body h5, body h6 {
    color: var(--color-text-primary);
}

/* Quasar dark mode override - ensure our theme takes precedence */
.body--dark {
    background-color: var(--color-bg-base) !important;
    color: var(--color-text-primary) !important;
}

.body--dark #app,
.body--dark .q-app,
.body--dark .q-page,
.body--dark .q-layout {
    background-color: var(--color-bg-base) !important;
}

/* =========================================
   Light Mode Specific - Override Quasar Dark Styles
   ========================================= */

/* When body does NOT have body--dark class (light mode) */
body:not(.body--dark) {
    background-color: var(--color-bg-base) !important;
    color: var(--color-text-primary) !important;
}

body:not(.body--dark) #app,
body:not(.body--dark) .q-app,
body:not(.body--dark) .nicegui-content,
body:not(.body--dark) .q-page,
body:not(.body--dark) .q-layout,
body:not(.body--dark) .q-page-container {
    background-color: var(--color-bg-base) !important;
    color: var(--color-text-primary) !important;
}

/* Light mode text colors */
body:not(.body--dark) p,
body:not(.body--dark) span,
body:not(.body--dark) div,
body:not(.body--dark) label,
body:not(.body--dark) .q-field__native,
body:not(.body--dark) .q-field__label,
body:not(.body--dark) .q-item__label {
    color: var(--color-text-primary) !important;
}

/* Restore semantic status colors (broad span/div rule above wins on specificity) */
body:not(.body--dark) .text-theme-success {
    color: var(--color-success) !important;
}

body:not(.body--dark) .text-theme-error {
    color: var(--color-error) !important;
}

body:not(.body--dark) .text-theme-warning {
    color: var(--color-warning) !important;
}

body:not(.body--dark) .text-theme-info {
    color: var(--color-info) !important;
}

/* Light mode secondary text */
body:not(.body--dark) .text-caption,
body:not(.body--dark) .text-subtitle2,
body:not(.body--dark) .secondary-text,
body:not(.body--dark) .text-grey {
    color: var(--color-text-secondary) !important;
}

/*
 * Locks the NiceGUI chrome (tabs + tab panels) inside the viewport. The tab strip
 * and .main-content are wrapped in .mycelian-main-shell (see create_ui_elements);
 * flex + min-height: 0 keeps nested iframes (e.g. Spore Studio) from spilling past
 * the native window edge.
 */
.mycelian-main-shell {
    height: 100vh;
    height: 100dvh;
    max-height: 100vh;
    max-height: 100dvh;
    box-sizing: border-box;
    overflow: hidden;
}

/* Keep status footer inside the shell padding (avoids bottom-edge clipping) */
.mycelian-main-shell > .service-status-footer {
    margin-bottom: 0;
    max-width: 100%;
}

/* Main content area styling */
.main-content {
    background: var(--color-bg-elevated);
    border-radius: 8px;
    margin: 0;
    flex: 1 1 0%;
    min-height: 0;
    overflow: hidden;
    border: 1px solid var(--color-border-subtle);
    display: flex;
    flex-direction: column;
    width: 100%;
}

/* Top-level tab body — fills .main-content without a nested card frame */
.tab-surface {
    flex: 1 1 auto;
    width: 100%;
    height: 100%;
    min-height: 0;
    max-height: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-sizing: border-box;
}

.tab-surface > .q-tab-panels {
    flex: 1 1 auto;
    min-height: 0;
    overflow: hidden;
}

.tab-surface .q-tab-panel {
    min-height: 0;
}

/* Content section styling */
.content-section {
    background: var(--color-bg-elevated);
    border-radius: 6px;
    border: 1px solid var(--color-border-subtle);
    margin: 8px;
    padding: 12px;
    flex: 1;
    width: calc(100% - 16px);
    height: calc(100% - 16px);
    display: flex;
    flex-direction: column;
    max-height: calc(100% - 16px);
    overflow: hidden;
}

/* Card styling */
.content-card {
    background: var(--color-bg-surface);
    border-radius: 4px;
    border: 1px solid var(--color-border-subtle);
    padding: 12px;
    margin-bottom: 8px;
    width: 100%;
}

/* Header sections */
.header-section {
    padding: 1rem;
    margin-bottom: 1rem;
    border-radius: 8px;
    background: var(--color-primary-light);
    border-left: 4px solid var(--color-primary);
}

.settings-header {
    font-size: 1.25rem;
    font-weight: 600;
    color: var(--color-text-primary);
    margin-bottom: 0.5rem;
}

.settings-description {
    font-size: 0.9rem;
    color: var(--color-text-secondary);
    margin-bottom: 1rem;
}

/* Text variations */
.secondary-text {
    font-size: 0.85rem;
    color: var(--color-text-muted);
}

.muted-text {
    color: var(--color-text-muted);
}

/* Cards with hover */
.settings-card {
    background: var(--color-bg-surface);
    transition: all 0.3s ease;
}

.settings-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px var(--color-bg-overlay);
}

/* Dividers */
.divider {
    margin: 1.5rem 0;
    border-top: 1px solid var(--color-border-subtle);
}

/* Tab styling */
.tab-content {
    padding: 1.5rem;
    min-height: 400px;
    background: var(--color-bg-elevated);
}

/* Scroll content styling */
.scroll-content {
    overflow-y: auto !important;
    overflow-x: hidden !important;
    flex: 1;
    min-height: 0;
    width: 100%;
}

/* Status badges */
.badge-success {
    background-color: var(--color-success);
    color: white;
}

.badge-warning {
    background-color: var(--color-warning);
    color: black;
}

.badge-error {
    background-color: var(--color-error);
    color: white;
}

.badge-info {
    background-color: var(--color-info);
    color: white;
}

/* Timestamp styling */
.timestamp {
    color: var(--color-text-muted) !important;
    font-size: 11px !important;
    margin-right: 30px !important;
}

/* Control button base */
.control-button {
    background-color: var(--color-hover-overlay) !important;
    border-radius: 4px !important;
    padding: 6px 12px !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    color: var(--color-text-primary) !important;
    transition: all 0.2s ease-in-out !important;
    border: none !important;
    display: flex !important;
    align-items: center !important;
    gap: 6px !important;
}

.control-button:hover {
    background-color: var(--color-active-overlay) !important;
}

/*
 * NiceGUI 3.x: Quasar .q-btn needs explicit chrome — flat props + these rules.
 * Semantic fills use .btn-*; neutral dock chrome uses .control-button alone.
 */
.q-btn.control-button:not(.btn-primary):not(.btn-secondary):not(.btn-success):not(.btn-danger):not(.btn-warning):not(.btn-cancel) {
    background-color: var(--color-hover-overlay) !important;
    color: var(--color-text-primary) !important;
    border: 1px solid var(--color-border-default) !important;
    border-radius: 4px !important;
    font-weight: 500 !important;
    display: inline-flex !important;
    gap: 6px !important;
}

.q-btn.control-button:not(.btn-primary):not(.btn-secondary):not(.btn-success):not(.btn-danger):not(.btn-warning):not(.btn-cancel):hover {
    background-color: var(--color-active-overlay) !important;
}

.q-btn.control-button.paused {
    background-color: var(--color-primary-light) !important;
    color: var(--color-primary-hover) !important;
    border-color: var(--color-border-accent) !important;
}

/* Activity feed pause/mute button state indicators */
.q-btn.control-button.pause-alerts-btn.paused:not(.btn-primary):not(.btn-secondary):not(.btn-success):not(.btn-danger):not(.btn-warning):not(.btn-cancel),
#pause-alerts-btn.paused {
    box-shadow: none !important;
    border-width: 1px !important;
    border-style: solid !important;
    border-color: var(--color-error, #ef4444) !important;
    transition: background-color 0.2s ease-in-out, color 0.2s ease-in-out !important;
}

.q-btn.control-button.pause-alerts-btn.alerts-playing:not(.btn-primary):not(.btn-secondary):not(.btn-success):not(.btn-danger):not(.btn-warning):not(.btn-cancel),
#pause-alerts-btn.alerts-playing {
    border-color: transparent !important;
}

#mute-alerts-btn.muted {
    border-width: 1px !important;
    border-style: solid !important;
    border-color: var(--color-error, #ef4444) !important;
    background-color: rgba(127, 29, 29, 0.55) !important;
    color: #fecaca !important;
}

#mute-alerts-btn.muted:hover {
    background-color: rgba(127, 29, 29, 0.65) !important;
}

/* =========================================
   Tab Navigation — Tier 3 segmented toggle
   (Activity Feed CURRENT / PREVIOUS)
   ========================================= */

.tab-row {
    display: flex;
    width: 100%;
    margin-bottom: 16px;
    gap: 0;
    border-radius: 8px;
    overflow: hidden;
    border: 1px solid var(--color-border-subtle);
}

.q-btn.tab-button,
.tab-button:not(.q-btn) {
    flex: 1;
    padding: 8px 16px;
    min-height: 32px;
    font-size: 14px;
    font-weight: 500;
    background-color: var(--color-bg-surface) !important;
    color: var(--color-text-muted) !important;
    border: none !important;
    border-right: 1px solid var(--color-border-subtle) !important;
    cursor: pointer;
    transition: background-color 0.2s ease, color 0.2s ease, border-color 0.2s ease;
    text-align: center;
    border-radius: 0 !important;
    margin: 0 !important;
}

.q-btn.tab-button:last-child,
.tab-button:not(.q-btn):last-child {
    border-right: none !important;
}

.q-btn.tab-button:hover,
.tab-button:not(.q-btn):hover {
    background-color: var(--color-hover-overlay) !important;
    color: var(--color-text-secondary) !important;
}

.q-btn.tab-button.active,
.tab-button:not(.q-btn).active {
    background-color: var(--color-primary-light) !important;
    color: var(--color-primary) !important;
    border-color: var(--color-border-accent) !important;
    font-weight: 600 !important;
    box-shadow: inset 0 0 0 1px var(--color-border-accent);
}

.mycelian-btn {
    transition: transform 0.2s ease, opacity 0.2s ease;
}

.mycelian-btn:hover {
    transform: translateY(-1px);
    opacity: 0.95;
}

/* Scrollbars */
::-webkit-scrollbar {
    width: 8px;
    height: 8px;
}

::-webkit-scrollbar-track {
    background: var(--color-bg-base);
}

::-webkit-scrollbar-thumb {
    background: var(--color-border-default);
    border-radius: 4px;
}

::-webkit-scrollbar-thumb:hover {
    background: var(--color-text-muted);
}

/* Selection colors */
::selection {
    background: var(--color-primary-light);
    color: var(--color-text-primary);
}

/* =========================================
   Quasar Component Overrides - Theme Aware
   ========================================= */

/* Tabs */
.q-tab {
    text-transform: none !important;
    font-weight: 500 !important;
    color: var(--color-text-secondary) !important;
}

.q-tab--active {
    color: var(--color-primary) !important;
}

.q-tab__indicator {
    background: var(--color-primary) !important;
}

/* =========================================
   Tab Navigation — Tier 1 connected main tabs
   (.mycelian-main-tab-shell)
   ========================================= */

.mycelian-main-tab-shell {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
    width: 100%;
}

.mycelian-main-tab-shell .mycelian-main-tabs.q-tabs {
    flex-shrink: 0;
    margin-bottom: 0;
    padding: 0;
    min-height: 0;
    background: transparent;
    border: none;
}

.mycelian-main-tab-shell .q-tabs__content {
    gap: 2px;
    padding: 0;
    align-items: flex-end;
    background: transparent !important;
    overflow-x: auto;
}

/* =========================================
   Tab Navigation — Tier 2 connected sub-tabs
   Tabs + content share a border frame (.mycelian-sub-tab-shell)
   ========================================= */

.mycelian-sub-tab-shell {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
    width: 100%;
}

.mycelian-sub-tab-shell > .mycelian-sub-tabs.q-tabs,
.mycelian-sub-tab-shell > .settings-tabs.mycelian-sub-tabs.q-tabs {
    flex-shrink: 0;
    margin-bottom: 0;
    padding: 0;
    min-height: 0;
    background: transparent;
    border-bottom: none;
}

.mycelian-sub-tab-shell .q-tabs__content {
    gap: 2px;
    padding: 0;
    align-items: flex-end;
    background: transparent !important;
}

/* Shared connected tab chrome (main + sub) */
.mycelian-main-tab-shell .mycelian-main-tabs .q-tab,
.mycelian-sub-tab-shell .q-tab {
    border-radius: 8px 8px 0 0 !important;
    padding: 0 14px;
    margin-bottom: 0;
    border: none !important;
    border-bottom: 1px solid var(--color-border-subtle) !important;
    background: var(--color-bg-surface);
    color: var(--color-text-muted) !important;
    font-weight: 500 !important;
    position: relative;
    z-index: 1;
}

.mycelian-main-tab-shell .mycelian-main-tabs .q-tab {
    min-height: 36px;
}

.mycelian-sub-tab-shell .q-tab {
    min-height: 34px;
    font-size: 0.8125rem;
}

.mycelian-main-tab-shell .mycelian-main-tabs .q-tab:hover,
.mycelian-sub-tab-shell .q-tab:hover {
    background: color-mix(in srgb, var(--color-hover-overlay) 70%, var(--color-bg-surface));
    color: var(--color-text-secondary) !important;
}

.mycelian-main-tab-shell .mycelian-main-tabs .q-tab--active,
.mycelian-sub-tab-shell .q-tab--active {
    background: var(--color-bg-elevated) !important;
    border-top: 1px solid var(--color-border-accent) !important;
    border-left: 1px solid var(--color-border-accent) !important;
    border-right: 1px solid var(--color-border-accent) !important;
    border-bottom: none !important;
    color: var(--color-primary) !important;
    font-weight: 600 !important;
    z-index: 6;
    margin-bottom: -1px;
    padding-bottom: 1px;
}

.mycelian-main-tab-shell .mycelian-main-tabs .q-tab__indicator,
.mycelian-sub-tab-shell .q-tab__indicator {
    display: none !important;
}

.mycelian-sub-tab-shell .q-tab__icon {
    width: 1.25em;
    height: 1.25em;
}

.mycelian-sub-tab-shell .q-tab--active .q-tab__icon {
    color: var(--color-primary) !important;
}

/* Full frame; top segment under active tab is masked via ::before + JS-set CSS vars */
.mycelian-main-tab-shell > .main-content,
.mycelian-sub-tab-shell > .q-tab-panels {
    flex: 1 1 auto;
    min-height: 0;
    border: 1px solid var(--color-border-accent) !important;
    border-radius: 10px;
    background: var(--color-bg-elevated) !important;
    overflow: hidden;
    position: relative;
    z-index: 2;
    margin-top: -1px;
    box-sizing: border-box;
}

/* Erase only the top-border segment beneath the active tab (seamless join) */
.mycelian-main-tab-shell > .main-content::before,
.mycelian-sub-tab-shell > .q-tab-panels::before {
    content: '';
    position: absolute;
    top: 0;
    left: var(--mycelian-subtab-mask-left, 0px);
    width: var(--mycelian-subtab-mask-width, 0px);
    height: 2px;
    background: var(--color-bg-elevated);
    z-index: 5;
    pointer-events: none;
}

.mycelian-sub-tab-shell > .q-tab-panels > .q-tab-panel {
    min-height: 0;
}

/* Tab panels */
.q-tab-panel {
    background: var(--color-bg-elevated) !important;
    color: var(--color-text-primary) !important;
}

/* Cards */
.q-card {
    background: var(--color-bg-elevated) !important;
    color: var(--color-text-primary) !important;
    border: 1px solid var(--color-border-subtle) !important;
}

/* Dialogs */
.q-dialog__inner > div {
    background: var(--color-bg-elevated) !important;
    color: var(--color-text-primary) !important;
}

/* Menus and dropdowns */
.q-menu {
    background: var(--color-bg-elevated) !important;
    color: var(--color-text-primary) !important;
    border: 1px solid var(--color-border-default) !important;
}

.q-item {
    color: var(--color-text-primary) !important;
}

.q-item:hover {
    background: var(--color-hover-overlay) !important;
}

.q-item--active {
    background: var(--color-primary-light) !important;
    color: var(--color-primary) !important;
}

/* Input fields */
:root {
    --input-border-radius: 10px;
}

.q-field--outlined .q-field__inner {
    border-radius: var(--input-border-radius);
    overflow: hidden;
}

.q-field--outlined .q-field__control,
.q-field__control {
    border-radius: var(--input-border-radius) !important;
    overflow: hidden !important;
    background: var(--color-bg-surface) !important;
    color: var(--color-text-primary) !important;
}

.q-field--outlined .q-field__control:before,
.q-field--outlined .q-field__control:after {
    border-radius: inherit !important;
}

.q-field--outlined .q-field__control:before {
    border-color: var(--color-border-subtle) !important;
}

.q-field--outlined .q-field__control:hover:before {
    border-color: var(--color-primary) !important;
}

.q-field__native,
.q-field__input {
    color: var(--color-text-primary) !important;
}

.q-field__label {
    color: var(--color-text-muted) !important;
}

.q-field--focused .q-field__control {
    border-color: var(--color-primary) !important;
    box-shadow: 0 0 0 2px var(--color-focus-ring) !important;
}

/* Recessed inputs (e.g. alert settings fields with bg-theme-base) */
.q-field.bg-theme-base,
.q-field.bg-theme-elevated,
.q-field.bg-theme-surface {
    background-color: transparent !important;
}

.q-field.bg-theme-base .q-field__control {
    background: var(--color-bg-base) !important;
}

.q-field.bg-theme-elevated .q-field__control {
    background: var(--color-bg-elevated) !important;
}

.q-field.bg-theme-surface .q-field__control {
    background: var(--color-bg-surface) !important;
}

/* Sliders */
.q-slider__track {
    color: var(--color-border-subtle) !important;
}

.q-slider__inner {
    background: var(--color-bg-surface) !important;
}

.q-slider__selection {
    background: var(--color-primary) !important;
}

.q-slider__thumb {
    color: var(--color-primary) !important;
}

.q-slider__thumb-shape {
    fill: currentColor !important;
}

/* Expansion panels */
.q-expansion-item__container {
    background: var(--color-bg-surface) !important;
}

.q-expansion-item__header {
    background: var(--color-bg-surface) !important;
    color: var(--color-text-primary) !important;
    border-bottom: 1px solid var(--color-border-subtle) !important;
}

.q-expansion-item__header:hover {
    background: color-mix(in srgb, var(--color-hover-overlay) 70%, var(--color-bg-surface)) !important;
}

.q-expansion-item__toggle-icon {
    color: var(--color-text-secondary) !important;
}

/* Tables */
.q-table {
    background: var(--color-bg-elevated) !important;
    color: var(--color-text-primary) !important;
}

.q-table th {
    background: var(--color-bg-surface) !important;
    color: var(--color-text-primary) !important;
    border-bottom: 1px solid var(--color-border-default) !important;
}

.q-table td {
    color: var(--color-text-secondary) !important;
    border-bottom: 1px solid var(--color-border-subtle) !important;
}

.q-table tbody tr:hover {
    background: var(--color-hover-overlay) !important;
}

/* Buttons */
.q-btn--flat {
    color: var(--color-text-primary) !important;
}

.q-btn--flat:hover {
    background: var(--color-hover-overlay) !important;
}

/* Switches - Ensure visible state indication */
.q-toggle__inner--truthy {
    color: var(--color-primary) !important;
}

.q-toggle__inner--truthy .q-toggle__track {
    background-color: var(--color-primary) !important;
    opacity: 0.8 !important;
}

.q-toggle__inner--truthy .q-toggle__thumb {
    color: var(--color-primary) !important;
}

.q-toggle__inner--truthy .q-toggle__thumb:after {
    background-color: var(--color-primary) !important;
}

.q-toggle__inner--falsy .q-toggle__track {
    background-color: var(--color-border-default) !important;
    opacity: 0.5 !important;
}

/* Dark mode switch styling */
.body--dark .q-toggle__inner--truthy .q-toggle__track {
    background-color: var(--color-primary) !important;
    opacity: 0.9 !important;
}

.body--dark .q-toggle__inner--truthy .q-toggle__thumb:after {
    background-color: #ffffff !important;
}

.body--dark .q-toggle__inner--falsy .q-toggle__track {
    background-color: rgba(255, 255, 255, 0.3) !important;
}

/* Checkboxes */
.q-checkbox__bg {
    border-color: var(--color-border-default) !important;
}

.q-checkbox__inner--truthy .q-checkbox__bg {
    background: var(--color-primary) !important;
    border-color: var(--color-primary) !important;
}

/* Select dropdowns */
.q-select__dropdown-icon {
    color: var(--color-text-muted) !important;
}

/* Tooltips */
.q-tooltip {
    background: var(--color-bg-surface) !important;
    color: var(--color-text-primary) !important;
    border: 1px solid var(--color-border-default) !important;
}

/*
 * Notification surfaces — history panel cards + top-right floating toasts.
 * Floating toasts use mycelian-toast--* classes (see notification_engine._build_toast_opts)
 * instead of Quasar notify ``type`` so we avoid quasar_importants bg-* / text-white defaults.
 */
.nc-history-card {
    border-radius: 8px;
    transition: filter 0.15s ease, box-shadow 0.15s ease;
}

.nc-history-card--enter {
    animation: nc-history-enter 0.32s cubic-bezier(0.22, 1, 0.36, 1) both;
}

@keyframes nc-history-enter {
    from {
        opacity: 0;
        transform: translateX(14px);
    }
    to {
        opacity: 1;
        transform: translateX(0);
    }
}

.nc-history-list .nc-history-card--enter:nth-child(1) { animation-delay: 0ms; }
.nc-history-list .nc-history-card--enter:nth-child(2) { animation-delay: 35ms; }
.nc-history-list .nc-history-card--enter:nth-child(3) { animation-delay: 70ms; }
.nc-history-list .nc-history-card--enter:nth-child(4) { animation-delay: 105ms; }
.nc-history-list .nc-history-card--enter:nth-child(n+5) { animation-delay: 140ms; }

.nc-history-scroll .q-scrollarea__container {
    scroll-behavior: smooth;
}

.q-notification.mycelian-toast {
    border-radius: 8px;
    background: var(--color-bg-surface) !important;
    color: var(--color-text-primary) !important;
    border: 1px solid var(--color-border-default) !important;
    box-shadow: 0 1px 3px var(--color-bg-overlay) !important;
    max-width: min(65vw, 420px) !important;
    margin: 8px 10px 0 !important;
    font-size: 0.875rem !important;
    /* Keep Quasar enter/leave transitions (transform + opacity); do not replace with
       filter/box-shadow only — that caused instant flash in/out after the NG3 restyle. */
    transition: transform 0.36s cubic-bezier(0.22, 1, 0.36, 1),
                opacity 0.36s cubic-bezier(0.22, 1, 0.36, 1) !important;
}

/* Top-right toasts: slide/fade from the right edge (Quasar default is translateY from above). */
.q-notification.mycelian-toast.q-notification--top-right-enter-from,
.q-notification.mycelian-toast.q-notification--top-right-leave-to {
    opacity: 0 !important;
    transform: translateX(calc(100% + 12px)) !important;
}

/* Quasar leave-active uses position:absolute without top — offsetTop restored via JS. */
.q-notification.mycelian-toast.q-notification--top-right-leave-active {
    margin-top: 0 !important;
}

@media (prefers-reduced-motion: reduce) {
    .nc-history-card--enter {
        animation: none !important;
    }

    .nc-history-scroll .q-scrollarea__container {
        scroll-behavior: auto;
    }

    .q-notification.mycelian-toast {
        transition: none !important;
    }

    .q-notification.mycelian-toast.q-notification--top-right-enter-from,
    .q-notification.mycelian-toast.q-notification--top-right-leave-to {
        transform: none !important;
    }
}

.q-notification.mycelian-toast.text-white,
.q-notification.mycelian-toast .q-notification__message,
.q-notification.mycelian-toast .q-notification__caption {
    color: var(--color-text-primary) !important;
}

.q-notification.mycelian-toast .q-notification__actions {
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.15s ease;
}

.q-notification.mycelian-toast:hover .q-notification__actions {
    opacity: 1;
    pointer-events: auto;
}

.q-notification.mycelian-toast--clickable {
    cursor: pointer;
}

.q-notification.mycelian-toast--clickable:hover {
    filter: brightness(1.06);
}

.q-notification.mycelian-toast .q-notification__actions .q-btn {
    min-width: 1.75rem !important;
    min-height: 1.75rem !important;
    padding: 0.25rem !important;
    color: var(--color-text-muted) !important;
    font-size: 1rem !important;
    line-height: 1 !important;
    text-transform: none !important;
    border-radius: 50% !important;
}

.q-notification.mycelian-toast .q-notification__actions .q-btn:hover {
    background: var(--color-hover-overlay) !important;
    color: var(--color-text-primary) !important;
}

/*
 * Top-right toasts: Quasar uses .q-notifications__list--top + .items-end (see Notify.js).
 * Offset top only; horizontal alignment (right) unchanged.
 */
.q-notifications__list.q-notifications__list--top.items-end {
    top: var(--mycelian-notify-header-clearance, 64px) !important;
}

body.q-ios-padding .q-notifications__list.q-notifications__list--top.items-end {
    top: calc(
        var(--mycelian-notify-header-clearance, 64px) + env(safe-area-inset-top, 0px)
    ) !important;
}

.nc-history-card--positive,
.q-notification.mycelian-toast--positive {
    background: color-mix(
        in srgb,
        var(--color-notify-success, var(--color-success)) 22%,
        var(--color-bg-surface)
    ) !important;
    border: 1px solid color-mix(
        in srgb,
        var(--color-notify-success, var(--color-success)) 42%,
        var(--color-border-default)
    ) !important;
    box-shadow: 0 1px 3px var(--color-bg-overlay);
}

.nc-history-card--negative,
.q-notification.mycelian-toast--negative {
    background: color-mix(
        in srgb,
        var(--color-notify-error, var(--color-error)) 22%,
        var(--color-bg-surface)
    ) !important;
    border: 1px solid color-mix(
        in srgb,
        var(--color-notify-error, var(--color-error)) 42%,
        var(--color-border-default)
    ) !important;
    box-shadow: 0 1px 3px var(--color-bg-overlay);
}

.nc-history-card--warning,
.q-notification.mycelian-toast--warning {
    background: color-mix(
        in srgb,
        var(--color-notify-warning, var(--color-warning)) 22%,
        var(--color-bg-surface)
    ) !important;
    border: 1px solid color-mix(
        in srgb,
        var(--color-notify-warning, var(--color-warning)) 42%,
        var(--color-border-default)
    ) !important;
    box-shadow: 0 1px 3px var(--color-bg-overlay);
}

.nc-history-card--info,
.nc-history-card--ongoing,
.q-notification.mycelian-toast--info,
.q-notification.mycelian-toast--ongoing {
    background: color-mix(
        in srgb,
        var(--color-notify-info, var(--color-info)) 22%,
        var(--color-bg-surface)
    ) !important;
    border: 1px solid color-mix(
        in srgb,
        var(--color-notify-info, var(--color-info)) 42%,
        var(--color-border-default)
    ) !important;
    box-shadow: 0 1px 3px var(--color-bg-overlay);
}

.nc-history-card--default,
.q-notification.mycelian-toast--default {
    background: var(--color-bg-surface) !important;
    border: 1px solid var(--color-border-default) !important;
    box-shadow: 0 1px 3px var(--color-bg-overlay);
}

.nc-history-card__body--clickable:hover {
    filter: brightness(1.06);
}

/* Linear progress */
.q-linear-progress__track {
    background: var(--color-bg-surface) !important;
}

.q-linear-progress__model {
    background: var(--color-primary) !important;
}

/* Separator */
.q-separator {
    background: var(--color-border-subtle) !important;
}

/* =========================================
   Light Mode Specific Overrides
   ========================================= */

/* Main containers in light mode */
body:not(.body--dark) .main-content,
body:not(.body--dark) .content-section {
    background: var(--color-bg-elevated) !important;
}

body:not(.body--dark) .content-section {
    border-color: var(--color-border-subtle) !important;
}

/* Connected tab frames keep accent border in light mode */
body:not(.body--dark) .mycelian-main-tab-shell > .main-content,
body:not(.body--dark) .mycelian-sub-tab-shell > .q-tab-panels {
    border-color: var(--color-border-accent) !important;
}

body:not(.body--dark) .q-tabs__content {
    background: var(--color-bg-surface) !important;
}

body:not(.body--dark) .mycelian-main-tab-shell .q-tabs__content,
body:not(.body--dark) .mycelian-sub-tab-shell .q-tabs__content {
    background: transparent !important;
}

/* Cards and panels in light mode */
body:not(.body--dark) .q-card,
body:not(.body--dark) .q-tab-panel {
    background: var(--color-bg-elevated) !important;
    color: var(--color-text-primary) !important;
}

body:not(.body--dark) .content-card,
body:not(.body--dark) .settings-card,
body:not(.body--dark) .config-card,
body:not(.body--dark) .connector-card,
body:not(.body--dark) .chatbot-card,
body:not(.body--dark) .control-card {
    background: var(--color-bg-surface) !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

/* Flat cards inside Settings subtabs (overrides light-mode shadows above) */
body:not(.body--dark) .mycelian-sub-tab-shell .q-card,
body:not(.body--dark) .mycelian-sub-tab-shell .content-card,
body:not(.body--dark) .mycelian-sub-tab-shell .content-section,
body:not(.body--dark) .mycelian-sub-tab-shell .settings-card,
body:not(.body--dark) .mycelian-sub-tab-shell .statistics-section,
body:not(.body--dark) .mycelian-sub-tab-shell .statistics-metric-card,
body:not(.body--dark) .mycelian-sub-tab-shell .config-card,
body:not(.body--dark) .mycelian-sub-tab-shell .connector-card,
body:not(.body--dark) .mycelian-sub-tab-shell .chatbot-card,
body:not(.body--dark) .mycelian-sub-tab-shell .control-card {
    box-shadow: none !important;
    filter: none !important;
}

/* Input fields in light mode */
body:not(.body--dark) .q-field__control {
    background: var(--color-bg-surface) !important;
    color: var(--color-text-primary) !important;
}

body:not(.body--dark) .q-field__native,
body:not(.body--dark) .q-field__input,
body:not(.body--dark) .q-select__dropdown-icon {
    color: var(--color-text-primary) !important;
}

body:not(.body--dark) .q-field__label {
    color: var(--color-text-muted) !important;
}

/* Dialogs and menus in light mode */
body:not(.body--dark) .q-dialog__inner > div,
body:not(.body--dark) .q-menu {
    background: var(--color-bg-elevated) !important;
    color: var(--color-text-primary) !important;
    border: 1px solid var(--color-border-default) !important;
}

body:not(.body--dark) .q-item {
    color: var(--color-text-primary) !important;
}

body:not(.body--dark) .q-item:hover {
    background: var(--color-hover-overlay) !important;
}

/* Tabs in light mode */
body:not(.body--dark) .q-tab {
    color: var(--color-text-secondary) !important;
}

body:not(.body--dark) .q-tab:hover {
    background: var(--color-hover-overlay) !important;
}

body:not(.body--dark) .q-tab--active {
    color: var(--color-primary) !important;
}

/* Tables in light mode */
body:not(.body--dark) .q-table {
    background: var(--color-bg-elevated) !important;
    color: var(--color-text-primary) !important;
}

body:not(.body--dark) .q-table th {
    background: var(--color-bg-surface) !important;
    color: var(--color-text-primary) !important;
}

body:not(.body--dark) .q-table td {
    color: var(--color-text-primary) !important;
}

/* Buttons in light mode */
body:not(.body--dark) .q-btn--flat {
    color: var(--color-text-primary) !important;
}

body:not(.body--dark) .control-button:not(.q-btn):not(.btn-primary):not(.btn-secondary):not(.btn-success):not(.btn-danger):not(.btn-warning):not(.btn-cancel) {
    color: var(--color-text-primary) !important;
    background: var(--color-bg-surface) !important;
    border: 1px solid var(--color-border-default) !important;
}

body:not(.body--dark) .q-btn.control-button:not(.btn-primary):not(.btn-secondary):not(.btn-success):not(.btn-danger):not(.btn-warning):not(.btn-cancel) {
    background-color: var(--color-bg-surface) !important;
    color: var(--color-text-primary) !important;
    border: 1px solid var(--color-border-default) !important;
}

body:not(.body--dark) .q-btn.control-button:not(.btn-primary):not(.btn-secondary):not(.btn-success):not(.btn-danger):not(.btn-warning):not(.btn-cancel):hover {
    background-color: var(--color-hover-overlay) !important;
}

/* Scrollbars in light mode */
body:not(.body--dark) ::-webkit-scrollbar-track {
    background: var(--color-bg-surface);
}

body:not(.body--dark) ::-webkit-scrollbar-thumb {
    background: var(--color-border-default);
}

/* Ensure good contrast for badges in light mode */
body:not(.body--dark) .badge {
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15);
}

/* Checkboxes and switches in light mode */
body:not(.body--dark) .q-checkbox__bg {
    border-color: var(--color-border-default) !important;
}

body:not(.body--dark) .q-toggle__inner--falsy .q-toggle__track {
    background: var(--color-border-default) !important;
}

/* Icons in light mode */
body:not(.body--dark) .q-icon {
    color: var(--color-text-secondary);
}

/* Notifications and tooltips in light mode */
body:not(.body--dark) .q-notification.mycelian-toast {
    background: var(--color-bg-elevated) !important;
    color: var(--color-text-primary) !important;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15) !important;
}

body:not(.body--dark) .q-notification.mycelian-toast--positive {
    background: color-mix(
        in srgb,
        var(--color-notify-success, var(--color-success)) 18%,
        var(--color-bg-elevated)
    ) !important;
}

body:not(.body--dark) .q-notification.mycelian-toast--negative {
    background: color-mix(
        in srgb,
        var(--color-notify-error, var(--color-error)) 18%,
        var(--color-bg-elevated)
    ) !important;
}

body:not(.body--dark) .q-notification.mycelian-toast--warning {
    background: color-mix(
        in srgb,
        var(--color-notify-warning, var(--color-warning)) 18%,
        var(--color-bg-elevated)
    ) !important;
}

body:not(.body--dark) .q-notification.mycelian-toast--info,
body:not(.body--dark) .q-notification.mycelian-toast--ongoing {
    background: color-mix(
        in srgb,
        var(--color-notify-info, var(--color-info)) 18%,
        var(--color-bg-elevated)
    ) !important;
}

body:not(.body--dark) .q-tooltip {
    background: var(--color-bg-surface) !important;
    color: var(--color-text-primary) !important;
}
"""

# Additional CSS for specific component patterns that need theme variables
COMPONENT_CSS = """
/* =========================================
   Component-Specific Theme Styles
   ========================================= */

/* Connector badges */
.trigger-badge {
    background: linear-gradient(135deg, rgba(59, 130, 246, 0.2), rgba(37, 99, 235, 0.3));
    border: 1px solid rgba(59, 130, 246, 0.4);
    color: var(--color-info);
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
}

.action-badge {
    background: linear-gradient(135deg, rgba(16, 185, 129, 0.2), rgba(5, 150, 105, 0.3));
    border: 1px solid rgba(16, 185, 129, 0.4);
    color: var(--color-success);
    padding: 4px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
    width: fit-content;
}

/* Form field groups (Source Settings, Alerts, etc.) */
.form-group {
    transition: all 0.2s ease;
}

.form-group:hover {
    background-color: var(--color-hover-overlay);
}

.form-group:hover .description-text {
    opacity: 1 !important;
}

.description-text {
    transition: opacity 0.2s ease;
}

/* Shared interactive button hover (legacy non-Quasar controls) */
.control-button:not(.q-btn) {
    transition: all 0.2s ease;
}

.control-button:not(.q-btn):hover {
    transform: translateY(-2px);
    opacity: 0.9;
}

/* Search input styling */
.search-input {
    background: var(--color-bg-surface) !important;
    border: 1px solid var(--color-border-default) !important;
    border-radius: 6px !important;
}

.search-input .q-field__control {
    background: transparent !important;
}

/* Empty state styling */
.empty-state {
    color: var(--color-text-muted);
}

.empty-state .q-icon {
    color: var(--color-text-muted) !important;
}

/* Card variations */
.connector-card,
.chatbot-card,
.config-card,
.control-card {
    background: var(--color-bg-surface);
    border: 1px solid var(--color-border-default);
    transition: all 0.2s ease-in-out;
}

.connector-card:not(.connector-card-enabled):not(.connector-card-disabled):hover,
.chatbot-card:hover,
.config-card:hover,
.control-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px var(--color-bg-overlay);
    border-color: var(--color-primary);
}

.connector-card.connector-card-enabled:hover,
.connector-card.connector-card-disabled:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px var(--color-bg-overlay);
}

.connector-card.connector-card-enabled:hover {
    border-color: var(--color-success);
}

.connector-card.connector-card-disabled:hover {
    border-color: var(--color-error);
}

/* Settings subtabs: flat cards without hover glow */
.mycelian-sub-tab-shell .content-card,
.mycelian-sub-tab-shell .settings-inner-panel,
.mycelian-sub-tab-shell .q-card,
.mycelian-sub-tab-shell .content-section,
.mycelian-sub-tab-shell .settings-card,
.mycelian-sub-tab-shell .statistics-section,
.mycelian-sub-tab-shell .statistics-metric-card {
    box-shadow: none !important;
    transform: none !important;
    filter: none !important;
}

.mycelian-sub-tab-shell .content-card:hover,
.mycelian-sub-tab-shell .control-card:hover,
.mycelian-sub-tab-shell .connector-card:hover,
.mycelian-sub-tab-shell .config-card:hover,
.mycelian-sub-tab-shell .chatbot-card:hover,
.mycelian-sub-tab-shell .q-card:hover,
.mycelian-sub-tab-shell .content-section:hover,
.mycelian-sub-tab-shell .settings-card:hover,
.mycelian-sub-tab-shell .statistics-section:hover,
.mycelian-sub-tab-shell .statistics-metric-card:hover {
    box-shadow: none !important;
    transform: none !important;
    filter: none !important;
}

/* Disabled state */
.chatbot-card.disabled {
    opacity: 0.6;
    background: var(--color-bg-surface);
}

/* Status indicators */
.status-enabled {
    background: linear-gradient(135deg, rgba(34, 197, 94, 0.2), rgba(22, 163, 74, 0.3));
    border: 1px solid var(--color-success);
    color: var(--color-success);
}

.status-disabled {
    background: linear-gradient(135deg, rgba(107, 114, 128, 0.2), rgba(75, 85, 99, 0.3));
    border: 1px solid var(--color-text-muted);
    color: var(--color-text-muted);
}

/* Alert button overrides */
.q-btn.alert-delete-btn,
button.alert-delete-btn,
.alert-delete-btn {
    background-color: var(--color-error) !important;
    color: white !important;
    font-weight: 500 !important;
    border: none !important;
}

.q-btn.alert-delete-btn:hover,
button.alert-delete-btn:hover,
.alert-delete-btn:hover {
    background-color: rgb(185, 28, 28) !important;
}

.q-btn.alert-save-btn,
button.alert-save-btn,
.alert-save-btn {
    background-color: var(--color-success) !important;
    color: white !important;
    font-weight: 500 !important;
    border: none !important;
}

.q-btn.alert-save-btn:hover,
button.alert-save-btn:hover,
.alert-save-btn:hover {
    background-color: rgb(21, 128, 61) !important;
}

/* Color grid for source settings */
.color-grid {
    display: grid;
    grid-template-columns: repeat(8, 1fr);
    gap: 8px;
    padding: 8px;
    background-color: var(--color-bg-surface);
    border-radius: 8px;
    border: 1px solid var(--color-border-default);
}

.color-swatch {
    width: 32px;
    height: 32px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.2s ease;
    border: 2px solid transparent;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
}

.color-swatch:hover {
    transform: scale(1.1);
}

.color-swatch.selected {
    border-color: var(--color-primary) !important;
    box-shadow: 0 0 0 2px var(--color-focus-ring) !important;
}

/* =========================================
   Theme-Aware Button Classes
   ========================================= */

/*
 * Button roles (use modules.ui_buttons factories when possible):
 *   btn-primary  — connect, new, save
 *   btn-cancel   — refresh, test, cancel (outline style via outline_button)
 *   btn-danger   — delete / destructive
 *   btn-success  — explicit enable/confirm only
 */

/* Primary action buttons - replaces hardcoded bg-purple-600 */
.btn-primary {
    background-color: var(--color-primary) !important;
    color: var(--color-text-inverse) !important;
    transition: all 0.2s ease !important;
}

.btn-primary:hover {
    background-color: var(--color-primary-hover) !important;
    opacity: 0.95;
}

/* Secondary/info action buttons - replaces hardcoded bg-blue-600 */
.btn-secondary {
    background-color: var(--color-info) !important;
    color: white !important;
    transition: all 0.2s ease !important;
}

.btn-secondary:hover {
    filter: brightness(0.85);
}

/* Selected state - replaces hardcoded bg-purple-900 */
.btn-selected {
    background-color: var(--color-primary-light) !important;
    border-color: var(--color-primary) !important;
}

/* Danger/delete buttons - replaces hardcoded bg-red-600 */
.btn-danger {
    background-color: var(--color-error) !important;
    color: white !important;
    transition: all 0.2s ease !important;
}

.btn-danger:hover {
    filter: brightness(0.85);
}

/* Warning buttons - replaces hardcoded bg-orange-600 */
.btn-warning {
    background-color: var(--color-warning) !important;
    color: black !important;
    transition: all 0.2s ease !important;
}

.btn-warning:hover {
    filter: brightness(0.85);
}

/* Success/confirm buttons - replaces hardcoded bg-green-600 */
.btn-success {
    background-color: var(--color-success) !important;
    color: white !important;
    transition: all 0.2s ease !important;
}

.btn-success:hover {
    filter: brightness(0.85);
}

/* Cancel/dismiss buttons - replaces hardcoded bg-gray-600 */
.btn-cancel {
    background-color: var(--color-bg-surface) !important;
    color: var(--color-text-primary) !important;
    border: 1px solid var(--color-border-default) !important;
    transition: all 0.2s ease !important;
}

.btn-cancel:hover {
    background-color: var(--color-hover-overlay) !important;
}

/* Quasar .q-btn overrides for semantic button classes */
.q-btn.btn-primary,
button.btn-primary {
    background-color: var(--color-primary) !important;
    color: var(--color-text-inverse) !important;
    border: none !important;
}

.q-btn.btn-primary:hover {
    background-color: var(--color-primary-hover) !important;
    opacity: 0.95;
}

.q-btn.btn-secondary {
    background-color: var(--color-info) !important;
    color: white !important;
    border: none !important;
}

.q-btn.btn-secondary:hover {
    filter: brightness(0.85);
}

.q-btn.btn-danger {
    background-color: var(--color-error) !important;
    color: white !important;
    border: none !important;
}

.q-btn.btn-danger:hover {
    filter: brightness(0.85);
}

.q-btn.btn-warning {
    background-color: var(--color-warning) !important;
    color: black !important;
    border: none !important;
}

.q-btn.btn-warning:hover {
    filter: brightness(0.85);
}

.q-btn.btn-success {
    background-color: var(--color-success) !important;
    color: white !important;
    border: none !important;
}

.q-btn.btn-success:hover {
    filter: brightness(0.85);
}

.q-btn.btn-cancel {
    background-color: var(--color-bg-surface) !important;
    color: var(--color-text-primary) !important;
    border: 1px solid var(--color-border-default) !important;
}

.q-btn.btn-cancel:hover {
    background-color: var(--color-hover-overlay) !important;
}

/* =========================================
   Theme-Aware Text Color Classes
   ========================================= */

/* Primary accent text - replaces text-purple-400 */
.text-theme-primary {
    color: var(--color-primary) !important;
}

/* Lighter primary text - replaces text-purple-300 */
.text-theme-primary-light {
    color: var(--color-primary-hover) !important;
}

/* Muted text - replaces text-gray-500 */
.text-theme-muted {
    color: var(--color-text-muted) !important;
}

/* Status text colors - replaces text-green-500, text-red-500, etc. */
.text-theme-success {
    color: var(--color-success) !important;
}

.text-theme-error {
    color: var(--color-error) !important;
}

.text-theme-warning {
    color: var(--color-warning) !important;
}

.text-theme-info {
    color: var(--color-info) !important;
}

/* =========================================
   Theme-Aware Background Classes
   ========================================= */

/* Background utilities - replaces bg-[#1a1a1a], bg-[#2a2a2a], bg-gray-800, etc. */
.bg-theme-base {
    background-color: var(--color-bg-base) !important;
}

.bg-theme-elevated {
    background-color: var(--color-bg-elevated) !important;
}

.bg-theme-surface {
    background-color: var(--color-bg-surface) !important;
}

.bg-theme-overlay {
    background-color: var(--color-bg-overlay) !important;
}

/* Translucent surface variants - replaces Tailwind opacity modifiers like
   bg-theme-surface/30, which do not work on custom (non-registered) color
   classes under Tailwind 4. */
.bg-theme-surface-30 {
    background-color: color-mix(in srgb, var(--color-bg-surface) 30%, transparent) !important;
}

.bg-theme-surface-50 {
    background-color: color-mix(in srgb, var(--color-bg-surface) 50%, transparent) !important;
}

/* Hover backgrounds - replaces hover:bg-[#2a2a2a] */
.hover-theme-surface:hover {
    background-color: var(--color-bg-surface) !important;
}

.hover-theme-overlay:hover {
    background-color: var(--color-hover-overlay) !important;
}

.hover-bg-theme-surface-50:hover {
    background-color: color-mix(in srgb, var(--color-bg-surface) 50%, transparent) !important;
}

/* =========================================
   Theme-Aware Border Classes
   ========================================= */

.border-theme-primary {
    border-color: var(--color-primary) !important;
}

.border-theme-default {
    border-color: var(--color-border-default) !important;
}

.border-theme-subtle {
    border-color: var(--color-border-subtle) !important;
}

.border-theme-accent {
    border-color: var(--color-border-accent) !important;
}

.border-theme-error {
    border-color: var(--color-error) !important;
}

.border-theme-warning {
    border-color: var(--color-warning) !important;
}

.border-theme-success {
    border-color: var(--color-success) !important;
}

.statistics-metric-card {
    padding: 0.5rem 0.75rem !important;
    min-height: 0 !important;
}

/* Statistics tab card surfaces (matches Source Controls pattern) */
.statistics-dashboard .q-card.statistics-section,
.statistics-dashboard > .q-card.content-section {
    background: var(--color-bg-surface) !important;
    border: 1px solid var(--color-border-accent) !important;
    border-radius: 8px !important;
    box-shadow: none !important;
}

.statistics-dashboard .statistics-metric-card,
.statistics-dashboard .statistics-section .settings-card,
.statistics-dashboard .content-section .settings-card,
.statistics-dashboard .statistics-stat-cell {
    background: transparent !important;
    border: 1px solid var(--color-border-accent) !important;
    border-radius: 6px !important;
    box-shadow: none !important;
}

.statistics-dashboard .statistics-section .q-card__section,
.statistics-dashboard .content-section .q-card__section,
.statistics-dashboard .statistics-metric-card .q-card__section {
    background: transparent !important;
}

/* Light mode: nested stat cards must beat global .q-card / .settings-card fills */
body:not(.body--dark) .statistics-dashboard .q-card.statistics-section,
body:not(.body--dark) .statistics-dashboard > .q-card.content-section {
    background: var(--color-bg-surface) !important;
    box-shadow: none !important;
}

body:not(.body--dark) .statistics-dashboard .statistics-metric-card.q-card,
body:not(.body--dark) .statistics-dashboard .statistics-stat-cell.q-card,
body:not(.body--dark) .statistics-dashboard .statistics-section .settings-card,
body:not(.body--dark) .statistics-dashboard .content-section .settings-card {
    background: transparent !important;
    box-shadow: none !important;
}

body:not(.body--dark) .statistics-dashboard .statistics-section .q-card__section,
body:not(.body--dark) .statistics-dashboard .content-section .q-card__section,
body:not(.body--dark) .statistics-dashboard .statistics-metric-card .q-card__section,
body:not(.body--dark) .statistics-dashboard .statistics-stat-cell .q-card__section {
    background: transparent !important;
}

.statistics-metric-card .text-2xl {
    font-size: 1.125rem !important;
    line-height: 1.5rem !important;
}

.statistics-metric-card .font-semibold.mb-2 {
    margin-bottom: 0.25rem !important;
}

.statistics-section.content-section {
    flex: 0 0 auto;
    width: 100%;
    max-width: 100%;
    height: auto;
    max-height: none;
    overflow: visible;
    box-sizing: border-box;
    padding: 0.75rem 1rem !important;
    margin: 0.25rem 0 !important;
}

.statistics-dashboard {
    display: grid !important;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.75rem !important;
}

.statistics-dashboard > .statistics-section-full {
    grid-column: 1 / -1;
}

/* Settings subtab cards — accent border (Twitch, Theme, App Settings, etc.) */
.mycelian-sub-tab-shell .settings-tab-content .settings-inner-panel {
    border: 1px solid var(--color-border-accent) !important;
}

.mycelian-sub-tab-shell .settings-tab-content .settings-tab-card.q-card {
    background: var(--color-bg-surface) !important;
    border: 1px solid var(--color-border-accent) !important;
    border-radius: 8px !important;
    box-shadow: none !important;
}

.mycelian-sub-tab-shell .settings-tab-content .settings-tab-card .q-card__section {
    background: transparent !important;
}

body:not(.body--dark) .mycelian-sub-tab-shell .settings-tab-content .settings-tab-card.q-card {
    background: var(--color-bg-surface) !important;
    box-shadow: none !important;
}

/* =========================================
   Theme-Aware Info/Warning/Error Hint Boxes
   ========================================= */

.hint-info {
    background: linear-gradient(135deg, rgba(59, 130, 246, 0.1), rgba(37, 99, 235, 0.05)) !important;
    border: 1px solid rgba(59, 130, 246, 0.3) !important;
    border-radius: 8px;
}

.hint-success {
    background: linear-gradient(135deg, rgba(34, 197, 94, 0.1), rgba(22, 163, 74, 0.05)) !important;
    border: 1px solid rgba(34, 197, 94, 0.3) !important;
    border-radius: 8px;
}

.hint-warning {
    background-color: var(--color-primary-light) !important;
    border-left: 4px solid var(--color-warning) !important;
    border-radius: 4px;
}

.hint-error {
    background: color-mix(in srgb, var(--color-error) 10%, var(--color-bg-surface)) !important;
    border: 1px solid color-mix(in srgb, var(--color-error) 30%, transparent) !important;
    border-radius: 8px;
}

/* Removable filter/cache chips (settings integration tabs) */
.theme-chip {
    background-color: var(--color-primary-light);
    border: 1px solid var(--color-border-accent);
    color: var(--color-text-primary);
}

.settings-tab-content {
    flex: 0 0 auto;
    width: 100%;
    max-width: 100%;
    height: auto;
    max-height: none;
    overflow: visible;
    box-sizing: border-box;
}

/* Notification tray badge (header bell) */
.main-tab-header-row {
    overflow: visible !important;
    padding-top: 6px !important;
    padding-right: 2px !important;
    box-sizing: border-box;
}

.notification-tray-wrap {
    position: relative !important;
    overflow: visible !important;
}

.notification-tray-badge {
    position: absolute !important;
    top: 0 !important;
    right: 0 !important;
    transform: none !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    height: 1.125rem !important;
    min-height: 1.125rem !important;
    margin: 0 !important;
    padding: 0 0.35rem !important;
    font-size: 0.65rem !important;
    font-weight: 700 !important;
    line-height: 1 !important;
    background: color-mix(
        in srgb,
        var(--color-error) 32%,
        var(--color-bg-elevated)
    ) !important;
    color: var(--color-error) !important;
    border: 1px solid color-mix(in srgb, var(--color-error) 48%, transparent) !important;
    border-radius: 9999px !important;
    box-shadow: none !important;
    pointer-events: none !important;
    box-sizing: border-box !important;
    white-space: nowrap !important;
    text-align: center !important;
}

body:not(.body--dark) .notification-tray-badge {
    color: var(--color-error) !important;
}

.notification-tray-badge.hidden {
    display: none !important;
}

.notification-tray-badge.badge-digits-1 {
    width: 1.125rem !important;
    min-width: 1.125rem !important;
    padding: 0 !important;
}

.notification-tray-badge.badge-digits-2 {
    min-width: 1.375rem !important;
}

.notification-tray-badge.badge-digits-3 {
    min-width: 1.75rem !important;
}

.notification-tray-badge.badge-digits-4 {
    min-width: 2.125rem !important;
}

.service-status-footer--hidden {
    display: none !important;
}

/* Global connection status footer (main shell) */
.service-status-footer {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: flex-end;
    gap: 10px 16px;
    flex-shrink: 0;
    box-sizing: border-box;
    background: var(--color-bg-elevated);
    border: 1px solid var(--color-border-accent);
    height: 36px;
    padding: 0 12px;
    margin-top: 4px;
    margin-bottom: 0;
    border-radius: 10px;
    overflow: visible;
    line-height: 1;
}

.service-status-footer-inner {
    display: contents;
}

.service-status-footer .service-status-item {
    transform: translateY(-3.5px);
}

.service-status-item {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    line-height: 1;
    border-radius: 4px;
    padding: 0 4px;
    box-sizing: border-box;
    transition: background 0.15s ease;
}

.service-status-item--hidden {
    display: none !important;
}

.service-status-item:hover {
    background: var(--color-hover-overlay);
}

.service-status-status-cluster {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    flex-shrink: 0;
    line-height: 1;
    min-height: 0;
}

.service-status-brand-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: 14px;
    height: 14px;
    line-height: 0;
}

.service-status-brand-icon svg {
    width: 14px;
    height: 14px;
    display: block;
    vertical-align: middle;
}

.service-status-name {
    display: inline;
    color: var(--color-text-primary);
    font-weight: 500;
    font-size: 12px;
    height: 12px;
    line-height: 12px !important;
    padding: 0 !important;
    margin: 0 !important;
    min-height: 0 !important;
    vertical-align: middle;
}

.service-status-badge-label {
    display: inline;
    font-size: 10px;
    font-weight: 600;
    height: 10px;
    line-height: 10px !important;
    padding: 0 !important;
    margin: 0 !important;
    min-height: 0 !important;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    vertical-align: middle;
}

.service-status-dot {
    display: block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    flex-shrink: 0;
    align-self: center;
    margin: 0;
}

.service-status-dot.success {
    background: var(--color-success);
}

.service-status-dot.warning {
    background: var(--color-warning);
}

.service-status-dot.error {
    background: var(--color-error);
}

.service-status-dot.info {
    background: var(--color-info);
}

.service-status-dot.muted {
    background: var(--color-text-muted);
}

.service-status-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    align-self: center;
    padding: 2px 7px;
    border-radius: 10px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    line-height: 1;
    box-sizing: border-box;
    min-height: 0;
}

.service-status-badge.success {
    background: color-mix(in srgb, var(--color-success) 15%, transparent);
    color: var(--color-success);
    border: 1px solid color-mix(in srgb, var(--color-success) 25%, transparent);
}

.service-status-badge.warning {
    background: color-mix(in srgb, var(--color-warning) 15%, transparent);
    color: var(--color-warning);
    border: 1px solid color-mix(in srgb, var(--color-warning) 25%, transparent);
}

.service-status-badge.error {
    background: color-mix(in srgb, var(--color-error) 15%, transparent);
    color: var(--color-error);
    border: 1px solid color-mix(in srgb, var(--color-error) 25%, transparent);
}

.service-status-badge.info {
    background: color-mix(in srgb, var(--color-info) 15%, transparent);
    color: var(--color-info);
    border: 1px solid color-mix(in srgb, var(--color-info) 25%, transparent);
}

.service-status-badge .text-xs,
.service-status-footer .service-status-badge-label,
.service-status-footer .service-status-name {
    color: inherit !important;
    font-size: inherit !important;
    font-weight: inherit !important;
    text-transform: inherit;
    letter-spacing: inherit;
    padding: 0 !important;
    margin: 0 !important;
    min-height: 0 !important;
}

.service-status-footer .service-status-name {
    font-size: 12px !important;
    height: 12px !important;
    line-height: 12px !important;
    color: var(--color-text-primary) !important;
    text-transform: none !important;
    letter-spacing: normal !important;
}

.service-status-footer .service-status-badge-label {
    font-size: 10px !important;
    height: 10px !important;
    line-height: 10px !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.3px !important;
}

/* About tab — compact source URL card grid */
.source-url-cards-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 8px;
    width: 100%;
}

.source-url-card {
    padding: 6px 8px !important;
}

.source-url-card.q-card .q-card__section {
    padding: 0 !important;
}

.source-url-card .source-url-card-header {
    flex-wrap: nowrap;
    min-width: 0;
    gap: 6px;
}

.source-url-card .source-url-card-header-info {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 6px;
}

.source-url-card .source-url-card-title {
    flex: 1 1 0;
    min-width: 0;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    display: block;
}

.source-url-card .source-url-card-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-sizing: border-box;
    height: 28px;
    min-height: 28px;
    padding: 0 8px;
    margin-left: auto;
    background: transparent !important;
    color: var(--color-text-primary) !important;
    border: 1px solid var(--color-primary) !important;
    border-radius: 4px;
    font-size: 0.65rem;
    font-weight: 500;
    line-height: 1;
    white-space: nowrap;
    flex-shrink: 0;
}

.source-url-card .source-url-card-copy.q-btn {
    height: 28px !important;
    min-height: 28px !important;
    width: 28px !important;
    min-width: 28px !important;
    padding: 0 !important;
    flex-shrink: 0;
}

.source-url-card .source-url-card-copy.q-btn--outline:before {
    border: 1px solid var(--color-primary) !important;
}

.source-url-card .q-field {
    width: 100%;
    margin-bottom: 0 !important;
    padding-bottom: 0 !important;
}

.source-url-card .q-field__bottom {
    display: none !important;
}

.source-url-card .q-field__control {
    min-height: 28px !important;
    height: 28px !important;
}

.source-url-card .q-field__control-container {
    padding-top: 0 !important;
    padding-bottom: 0 !important;
}
"""


def get_full_theme_css() -> str:
    """Get combined base and component CSS"""
    return BASE_CSS + COMPONENT_CSS


SUB_TAB_SEAM_SCRIPT = """
<script id="mycelian-subtab-seam-js">
(function () {
    if (window.__mycelianSubTabSeamInit) return;
    window.__mycelianSubTabSeamInit = true;

    var SHELL_SELECTOR = '.mycelian-main-tab-shell, .mycelian-sub-tab-shell';

    function contentFrame(shell) {
        return shell.querySelector(':scope > .main-content')
            || shell.querySelector(':scope > .q-tab-panels');
    }

    function activeTab(shell) {
        if (shell.classList.contains('mycelian-main-tab-shell')) {
            var mainTabs = shell.querySelector('.mycelian-main-tabs');
            return mainTabs ? mainTabs.querySelector('.q-tab.q-tab--active') : null;
        }
        var strip = shell.querySelector(':scope > .q-tabs');
        return strip ? strip.querySelector('.q-tab.q-tab--active') : null;
    }

    function updateSeam(shell) {
        if (!shell) return;
        var active = activeTab(shell);
        var frame = contentFrame(shell);
        if (!frame) return;
        if (!active) {
            frame.style.setProperty('--mycelian-subtab-mask-width', '0px');
            return;
        }
        var frameRect = frame.getBoundingClientRect();
        var tabRect = active.getBoundingClientRect();
        var left = Math.max(0, tabRect.left - frameRect.left);
        frame.style.setProperty('--mycelian-subtab-mask-left', left + 'px');
        frame.style.setProperty('--mycelian-subtab-mask-width', tabRect.width + 'px');
    }

    function updateAll() {
        document.querySelectorAll(SHELL_SELECTOR).forEach(updateSeam);
    }

    window.mycelianUpdateTabSeams = updateAll;
    window.mycelianUpdateSubTabSeams = updateAll;

    function watchShell(shell) {
        if (!shell || shell.__mycelianSeamWatched) return;
        shell.__mycelianSeamWatched = true;
        var tabs = shell.querySelector('.q-tabs');
        if (!tabs) return;
        var obs = new MutationObserver(function () { updateSeam(shell); });
        obs.observe(tabs, {
            attributes: true,
            subtree: true,
            attributeFilter: ['class'],
            childList: true,
        });
        updateSeam(shell);
    }

    function initAll() {
        document.querySelectorAll(SHELL_SELECTOR).forEach(watchShell);
        updateAll();
    }

    window.mycelianInitTabSeams = initAll;
    window.mycelianInitSubTabSeams = initAll;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
    } else {
        initAll();
    }

    window.addEventListener('resize', updateAll);

    // Qt WebEngine may execute head scripts before <body> exists.
    (function startBodyObserver() {
        if (!document.body) {
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', startBodyObserver, { once: true });
            }
            return;
        }
        var bodyObs = new MutationObserver(function () { initAll(); });
        bodyObs.observe(document.body, { childList: true, subtree: true });
    })();
})();
</script>
"""

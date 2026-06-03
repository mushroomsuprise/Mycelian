# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import sys
from unittest.mock import MagicMock

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath('..'))

# Mock modules that aren't available during documentation build
class Mock(MagicMock):
    @classmethod
    def __getattr__(cls, name):
        return MagicMock()

MOCK_MODULES = [
    'nicegui', 'nicegui.ui', 'nicegui.app', 'nicegui.events',
    'flask', 'flask_socketio',
    'twitchapi', 'twitchapi.twitch', 'twitchapi.oauth',
    'spotipy', 'spotipy.oauth2',
    'firebase_admin', 'firebase_admin.credentials', 'firebase_admin.db',
    'psnawp_api', 'psnawp_api.core', 'psnawp_api.models',
    'obsws_python',
    'cryptography', 'cryptography.fernet',
    'psutil',
    'pyperclip',
    'websocket',
    'requests_oauthlib',
    'eventlet',
    'gevent',
    'pynput', 'pynput.keyboard', 'pynput.mouse',
    'pycaw', 'pulsectl', 'pyobjc',
]

for mod_name in MOCK_MODULES:
    sys.modules[mod_name] = Mock()

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'Mycelian'
# Using variable name that doesn't shadow built-in
project_copyright = '2024, Mycelian'
copyright = project_copyright  # noqa: A001 - Sphinx requires this variable name
author = 'mushroomsuprise'
release = '1.9.11'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.viewcode',
    'sphinx.ext.napoleon',
    'sphinx.ext.intersphinx',
    'sphinx_autodoc_typehints',
    'myst_parser',
]

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']

# -- Extension configuration -------------------------------------------------

# Napoleon settings for Google/NumPy style docstrings
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False

# Autodoc settings
autodoc_default_options = {
    'members': True,
    'undoc-members': True,
    'show-inheritance': True,
}

# Intersphinx mapping
intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
}

# MyST parser settings
myst_enable_extensions = [
    "amsmath",
    "colon_fence",
    "deflist",
    "dollarmath",
    "html_admonition",
    "html_image",
    "linkify",
    "replacements",
    "smartquotes",
    "substitution",
    "tasklist",
] 
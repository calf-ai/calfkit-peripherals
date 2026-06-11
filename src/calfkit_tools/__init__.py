"""calfkit-tools — vendored agent tools for the calfkit SDK.

Tools are organised by the upstream source they were vendored from, each as a
subpackage of ``calfkit_tools``. Import the deployable tool nodes directly from a
source's ``node`` module::

    from calfkit_tools.hermes.node import HERMES_NODES, terminal, todo, web_search
    from calfkit_tools.web_fetch.node import web_fetch

Each subpackage keeps its vendored upstream code under ``_vendor`` (and, for
hermes, app-runtime stand-ins under ``_shims``); provenance for every source
lives outside the package in ``vendor/<source>/`` (upstream ``LICENSE`` +
``METADATA.yaml``). See ``THIRD_PARTY_NOTICES.md``.

This module is intentionally side-effect free: importing ``calfkit_tools`` pulls
in no tool code or optional dependencies. Import the specific ``.node`` module
for the tools you need.
"""

__all__: list[str] = []

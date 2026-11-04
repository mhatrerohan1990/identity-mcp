"""A reason-and-act agent that answers questions by calling the identity-mcp tools in turn.

The server exposes two scoped tools, ``query_identity`` and ``query_activity``. A question that spans both ("show me
dormant admin accounts") needs one call whose result feeds the next. That loop lives here, in the *client*, as it would
with any MCP server, and it is built with LangGraph:

* ``tools``  - the MCP client: connects to the server and exposes its tools to LangChain
* ``prompts`` - what the model is told about the tools and how to chain them
* ``graph``  - the LangGraph state graph: model -> tools -> model ... with a step budget
* ``__main__`` - ``uv run python -m identity_mcp.agent "show me dormant admin accounts"``

This package talks to the server over MCP only. It must not import the pipeline or any server code (enforced by
``tests/test_architecture.py``); it may use ``core`` for configuration and logging.

Needs the optional dependencies: ``uv sync --extra agent``.
"""

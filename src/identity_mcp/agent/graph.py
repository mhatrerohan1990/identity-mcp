"""The agent as a LangGraph state graph.

::

    START -> agent --(data tool calls)--> tools -> agent ...
                |--(final_answer after using a tool)--> finish -> END
                |--(plain text, or final_answer before any tool)--> nudge -> agent   (at most MAX_NUDGES times)
                '--(wants a tool but out of steps)--> give_up -> END

* ``agent`` calls the model, which has the MCP tools and ``final_answer`` bound, repairs tool calls written as text
  (``recovery.py``), and counts the call;
* ``tools`` runs the data tools the model asked for and returns each result (or error) as a message;
* ``finish`` ends the run with the text the model passed to ``final_answer``. Finishing is explicit: small models often
  announce the next step in plain text ("Now let's check their activity") and a loop that treated that as the answer would
  stop half way;
* ``nudge`` answers plain text, or an answer given before any data was fetched, by telling the model what to do;
* ``give_up`` ends the run with an honest message when the step budget is spent, instead of looping.

The state is the list of messages plus two small counters, so every step is inspectable and the run is easy to test with a
scripted model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from . import prompts
from .recovery import recover_tool_calls

DEFAULT_MAX_STEPS = 6  # model calls per question; a two-tool chain needs 3 (two tool calls, then final_answer)
MAX_NUDGES = 2  # times the model is told to use a tool instead of writing text; after that its text is accepted as the answer
FINAL = "final_answer"  # the tool the model calls to finish; handled by the graph, not by the MCP server


class AgentState(TypedDict):
    """What flows through the graph.

    Attributes:
        messages: The conversation so far: the question, each model reply, each tool result.
        steps: How many times the model has been called.
        nudges: How many times the model has been told to call a tool instead of writing text.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    steps: int
    nudges: int


def final_answer_tool() -> StructuredTool:
    """The tool the model calls to finish. It is bound to the model but never executed: the ``finish`` node reads its input."""

    async def unused(answer: str) -> str:
        return answer

    return StructuredTool(
        name=FINAL,
        description="Give the user your final answer once you have all the data you need. Call this to finish.",
        args_schema={"type": "object", "properties": {"answer": {"type": "string", "description": "The answer, in plain language"}},
                     "required": ["answer"]},
        coroutine=unused,
    )


def build_graph(model: BaseChatModel, tools: Sequence[BaseTool], max_steps: int = DEFAULT_MAX_STEPS):
    """Compile the agent graph.

    Args:
        model: A chat model that supports ``bind_tools`` (for example ``ChatOllama``).
        tools: The tools the model may call (from ``tools.open_tools``).
        max_steps: Most model calls allowed per question. When the model still wants a tool after that, the run ends in
            ``give_up``.

    Returns:
        A compiled graph. Invoke it with ``initial_state(question)``.
    """
    names = {t.name for t in tools} | {FINAL}
    bound = model.bind_tools([*tools, final_answer_tool()])

    async def agent(state: AgentState) -> dict[str, Any]:
        reply = await bound.ainvoke([SystemMessage(prompts.SYSTEM), *state["messages"]])
        return {"messages": [recover_tool_calls(reply, names)], "steps": state["steps"] + 1}

    async def nudge(state: AgentState) -> dict[str, Any]:
        used_a_tool = any(isinstance(m, ToolMessage) for m in state["messages"])
        text = prompts.NUDGE_PLAIN_TEXT if used_a_tool else prompts.NUDGE_NO_TOOL
        return {"messages": [HumanMessage(text)], "nudges": state["nudges"] + 1}

    async def finish(state: AgentState) -> dict[str, Any]:
        call = next(c for c in state["messages"][-1].tool_calls if c["name"] == FINAL)
        return {"messages": [AIMessage(str(call["args"].get("answer", "")))]}

    async def give_up(state: AgentState) -> dict[str, Any]:
        calls = [f"{c['name']}" for m in state["messages"] if isinstance(m, AIMessage) for c in m.tool_calls]
        done = ", ".join(calls) if calls else "no tool calls"
        return {"messages": [AIMessage(prompts.GIVE_UP.format(steps=state["steps"], done=done))]}

    def route(state: AgentState) -> str:
        last = state["messages"][-1]
        calls = getattr(last, "tool_calls", None) or []
        used_a_tool = any(isinstance(m, ToolMessage) for m in state["messages"])
        can_nudge = state["nudges"] < MAX_NUDGES
        if not calls:  # plain text: not allowed to end the run until the nudges are used up
            return "nudge" if can_nudge else END
        if [c["name"] for c in calls] == [FINAL]:
            if used_a_tool:
                return "finish"
            return "nudge" if can_nudge else "finish"  # an answer with no data behind it is sent back, up to the limit
        return "give_up" if state["steps"] >= max_steps else "tools"

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(list(tools), handle_tool_errors=True))
    graph.add_node("nudge", nudge)
    graph.add_node("finish", finish)
    graph.add_node("give_up", give_up)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent", route, {"tools": "tools", "nudge": "nudge", "finish": "finish", "give_up": "give_up", END: END}
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("nudge", "agent")
    graph.add_edge("finish", END)
    graph.add_edge("give_up", END)
    return graph.compile()


def initial_state(question: str) -> AgentState:
    """The starting state for one question."""
    return {"messages": [HumanMessage(question)], "steps": 0, "nudges": 0}


async def run(
    graph: Any, question: str, on_update: Callable[[str, dict[str, Any]], Awaitable[None] | None] | None = None
) -> str:
    """Run `graph` on `question` and return the final answer text.

    Args:
        graph: The compiled graph from :func:`build_graph`.
        question: What the user asked.
        on_update: Optional callback ``(node_name, update)`` called after each node finishes, in order. Use it to show
            progress; the update holds the new messages that node produced.

    Returns:
        The text of the last message, which is the model's answer or the ``give_up`` message.
    """
    final = ""
    async for chunk in graph.astream(initial_state(question), stream_mode="updates"):
        for node, update in chunk.items():
            if on_update is not None:
                result = on_update(node, update)
                if result is not None:
                    await result
            for message in update.get("messages", []):
                if isinstance(message, AIMessage) and not message.tool_calls:
                    final = message.content if isinstance(message.content, str) else str(message.content)
    return final

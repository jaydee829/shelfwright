"""#93: mesh tools must run off the event loop without changing their ADK schema."""

import asyncio
import inspect
import threading

from google.adk.tools import FunctionTool

from agentic_librarian.agents.services import make_async_tool


def _sample_tool(title: str, rating: int | None = None) -> str:
    """Docstring the ADK schema uses."""
    return f"{title}:{rating}:{threading.current_thread().name}"


def test_wrapper_is_coroutine_with_preserved_metadata():
    wrapped = make_async_tool(_sample_tool)
    assert asyncio.iscoroutinefunction(wrapped)
    assert wrapped.__name__ == "_sample_tool"
    assert wrapped.__doc__ == _sample_tool.__doc__
    assert inspect.signature(wrapped) == inspect.signature(_sample_tool)


def test_adk_declaration_unchanged():
    sync_decl = FunctionTool(_sample_tool)._get_declaration()
    async_decl = FunctionTool(make_async_tool(_sample_tool))._get_declaration()
    assert async_decl.name == sync_decl.name
    assert async_decl.description == sync_decl.description
    assert str(async_decl.parameters) == str(sync_decl.parameters)


def test_wrapper_runs_off_the_event_loop():
    wrapped = make_async_tool(_sample_tool)

    async def _run():
        return await wrapped("Dune", rating=5)

    result = asyncio.run(_run())
    title, rating, thread_name = result.split(":")
    assert (title, rating) == ("Dune", "5")
    assert thread_name != threading.main_thread().name  # executed in a worker thread


def test_contextvars_survive_to_thread():
    import contextvars

    var = contextvars.ContextVar("probe")

    def _reads_var() -> str:
        return var.get()

    wrapped = make_async_tool(_reads_var)

    async def _run():
        var.set("carried")
        return await wrapped()

    assert asyncio.run(_run()) == "carried"

"""Pytest bootstrap.

On Windows, pyarrow's Arrow DLLs must be loaded BEFORE chromadb's rust
bindings / Google's grpc stack. The reverse order makes the pyarrow import
(reached transitively via langchain_text_splitters -> pandas) die with
"Windows fatal exception: access violation" during test collection
(observed: pyarrow 24.0.0 + chromadb 1.1.0, Python 3.13).

conftest.py is imported before any test module, so importing pyarrow here
pins a safe DLL load order for the whole suite.
"""
try:
    import pyarrow  # noqa: F401
except ImportError:
    pass

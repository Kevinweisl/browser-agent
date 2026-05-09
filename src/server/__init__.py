"""FastAPI web demo for the browser-agent.

Exposes `run_task` over HTTP so judges can submit NL tasks from a browser.
The agent core (`workers.browser`) is unchanged — this layer only wraps it.
"""

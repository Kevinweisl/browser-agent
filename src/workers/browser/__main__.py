"""Entry point: `python -m workers.browser`"""

from workers.base import run_worker

from workers.browser.handlers import browser_task_handler

HANDLERS = {
    "browser-task": browser_task_handler,
}


if __name__ == "__main__":
    run_worker(target="browser", handlers=HANDLERS)

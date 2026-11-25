"""Transfer due live checks to the regular run queue."""
import logging
from datetime import datetime, timezone
from threading import Event, Thread

from .core import AppError
from .llm import get_provider

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, repo, manager, config, config_lock):
        self.repo = repo
        self.manager = manager
        self.config = config
        self.config_lock = config_lock
        self.stopped = Event()
        self.thread = Thread(target=self.poll, name='schedule-dispatch', daemon=True)

    def poll(self):
        while not self.stopped.is_set():
            try:
                current = datetime.now(timezone.utc)
                due = self.repo.due_schedule_ids(current)
                if due:
                    with self.config_lock:
                        try:
                            get_provider('qwen', self.config)
                            model_ready = True
                        except AppError:
                            model_ready = False
                        for topic_id in due:
                            run_id = self.repo.enqueue_due_schedule(
                                topic_id, current, model=self.config.model, model_ready=model_ready,
                            )
                            if run_id:
                                self.manager.submit(run_id)
            except Exception:
                logger.exception('Could not dispatch due topic checks')
            self.stopped.wait(2)

    def start(self):
        for run in self.repo.active_runs():
            if run['trigger_type'] == 'scheduled' and run['status'] == 'pending':
                self.manager.submit(run['run_id'])
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.thread.join()

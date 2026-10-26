from __future__ import annotations

import argparse
import os
import signal
import time

from .config import get_settings
from .service import MeloPetService
from .util import new_id


class Worker:
    def __init__(self, service: MeloPetService):
        self.service = service
        self.worker_id = f"worker_{os.getpid()}_{new_id('instance')}"
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_once(self) -> bool:
        self.service.worker_heartbeat(self.worker_id, os.getpid())
        task = self.service.claim_task(self.worker_id)
        if task is None:
            self.service.cleanup_expired_raw_files()
            return False
        self.service.process_task(task)
        self.service.worker_heartbeat(self.worker_id, os.getpid())
        return True

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        self.service.worker_heartbeat(self.worker_id, os.getpid())
        try:
            while self.running:
                processed = self.run_once()
                if not processed:
                    time.sleep(self.service.settings.worker_poll_seconds)
        finally:
            self.service.worker_heartbeat(self.worker_id, os.getpid(), status="stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="MeloPet durable task worker")
    parser.add_argument("--once", action="store_true", help="Process at most one task")
    args = parser.parse_args()
    worker = Worker(MeloPetService(get_settings()))
    if args.once:
        worker.run_once()
    else:
        worker.run_forever()


if __name__ == "__main__":
    main()

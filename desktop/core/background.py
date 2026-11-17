import threading, uuid, json, time, inspect, traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path


class BackgroundTaskManager:
    """Persistent parallel task manager with retries, progress and recovery hooks."""
    def __init__(self, max_workers=3, state_path="data/background_tasks.json", max_retries=2, traceback_chars=None):
        self.max_workers = int(max_workers)
        # v2.1: Self-Heal motoru hatanın nereden geldiğini görebilsin diye traceback'in son N karakteri saklanır.
        # Verilmezse config.BACKGROUND_TRACEBACK_CHARS kullanılır (0 = saklama).
        if traceback_chars is None:
            try:
                traceback_chars = getattr(__import__("config"), "BACKGROUND_TRACEBACK_CHARS", 6000)
            except Exception:
                traceback_chars = 6000
        self.traceback_chars = max(0, int(traceback_chars))
        self.max_retries = int(max_retries)
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="jarvis-bg")
        self.lock = threading.RLock()
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.tasks = self._load()

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def _load(self):
        try:
            data = json.loads(self.state_path.read_text(encoding="utf8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.tasks, ensure_ascii=False, indent=2), encoding="utf8")
        tmp.replace(self.state_path)

    def _update(self, task_id, **fields):
        with self.lock:
            if task_id not in self.tasks:
                return
            self.tasks[task_id].update(fields)
            self._save()

    def submit(self, title, fn, *args, recovery_fn=None, retries=None, **kwargs):
        task_id = uuid.uuid4().hex[:8]
        retry_limit = self.max_retries if retries is None else max(0, int(retries))
        with self.lock:
            self.tasks[task_id] = {
                "id": task_id,
                "title": title,
                "status": "queued",
                "created_at": self._now(),
                "started_at": "",
                "finished_at": "",
                "result": "",
                "error": "",
                "attempt": 0,
                "max_retries": retry_limit,
                "current_step": 0,
                "total_steps": 0,
                "progress": 0,
                "plan": [],
                "step_results": [],
                "recovery_count": 0,
            }
            self._save()

        def runner():
            last_error = ""
            for attempt in range(1, retry_limit + 2):
                self._update(task_id, status="running", started_at=self.tasks[task_id].get("started_at") or self._now(), attempt=attempt, error="")
                try:
                    parameters = inspect.signature(fn).parameters
                    if "task_id" in parameters:
                        result = fn(*args, task_id=task_id, **kwargs)
                    else:
                        result = fn(*args, **kwargs)
                    self._update(task_id, status="completed", result=str(result or ""), finished_at=self._now(), progress=100)
                    return
                except Exception as exc:
                    last_error = str(exc)
                    tb = traceback.format_exc()[-self.traceback_chars:] if self.traceback_chars else ""
                    self._update(task_id, status="recovering" if attempt <= retry_limit else "failed", error=last_error,
                                 traceback=tb)
                    if attempt > retry_limit:
                        self._update(task_id, finished_at=self._now())
                        return
                    with self.lock:
                        current_recoveries = int(self.tasks.get(task_id, {}).get("recovery_count", 0))
                    self._update(task_id, recovery_count=current_recoveries + 1)
                    try:
                        recovery_instruction = recovery_fn(last_error, task_id) if recovery_fn else None
                        if recovery_instruction:
                            self._update(task_id, last_recovery=str(recovery_instruction))
                    except Exception as recovery_exc:
                        self._update(task_id, last_recovery=f"Recovery hatası: {recovery_exc}")
                    time.sleep(min(2 ** (attempt - 1), 8))
            self._update(task_id, status="failed", error=last_error, finished_at=self._now())

        self.executor.submit(runner)
        return task_id

    def set_progress(self, task_id, current_step, total_steps=None, status="running", note=""):
        total = total_steps if total_steps is not None else self.tasks.get(task_id, {}).get("total_steps", 0)
        progress = int((current_step / total) * 100) if total else 0
        self._update(
            task_id,
            current_step=int(current_step),
            total_steps=int(total),
            progress=max(0, min(100, progress)),
            status=status,
            progress_note=note,
        )

    def set_plan(self, task_id, plan):
        plan = list(plan or [])
        self._update(task_id, plan=plan, total_steps=len(plan), current_step=0, progress=0)

    def append_step_result(self, task_id, step_index, step, result, success=True, error="", retry_count=0):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            entries = list(task.get("step_results") or [])
            entries.append({
                "step": int(step_index),
                "title": str(step.get("title", f"Adım {step_index}")),
                "kind": str(step.get("kind", "reason")),
                "success": bool(success),
                "result": str(result or ""),
                "error": str(error or ""),
                "retry_count": int(retry_count),
                "finished_at": self._now(),
            })
            task["step_results"] = entries
            self._save()

    def get(self, task_id):
        with self.lock:
            return dict(self.tasks.get(task_id, {}))

    def list(self):
        with self.lock:
            values = [dict(x) for x in self.tasks.values()]
        return list(reversed(values))

    def shutdown(self):
        self.executor.shutdown(wait=False, cancel_futures=True)

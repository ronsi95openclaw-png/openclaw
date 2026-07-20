"""
Agent Team Orchestration for Claude-openclaw

This module implements multi-agent team orchestration with defined roles,
task lifecycles, handoff protocols, and review workflows.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

# Project paths
ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
TASKS_FILE = DATA_DIR / "tasks.json"
CONVERSATION_FILE = DATA_DIR / "conversation_history.json"

@dataclass
class Task:
    """Represents a task in the orchestration system."""
    id: str
    title: str
    description: str
    state: str  # inbox, assigned, in_progress, review, revision, done
    assigned_to: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""
    artifacts: List[str] = None
    comments: List[Dict] = None
    priority: str = "medium"  # low, medium, high
    tags: List[str] = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        if self.artifacts is None:
            self.artifacts = []
        if self.comments is None:
            self.comments = []
        if self.tags is None:
            self.tags = []

class AgentOrchestrator:
    """Main orchestrator for multi-agent teams."""

    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        self.agents: Dict[str, Dict] = {}
        self.load_tasks()

    def load_tasks(self):
        """Load tasks from JSON file."""
        if TASKS_FILE.exists():
            try:
                with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for task_data in data:
                        task = Task(**task_data)
                        self.tasks[task.id] = task
            except Exception as e:
                print(f"Error loading tasks: {e}")

    def save_tasks(self):
        """Save tasks to JSON file."""
        try:
            task_list = [asdict(task) for task in self.tasks.values()]
            with open(TASKS_FILE, 'w', encoding='utf-8') as f:
                json.dump(task_list, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving tasks: {e}")

    def create_task(self, title: str, description: str, priority: str = "medium") -> str:
        """Create a new task."""
        task_id = f"task_{int(time.time())}_{len(self.tasks)}"
        task = Task(
            id=task_id,
            title=title,
            description=description,
            state="inbox",
            priority=priority
        )
        self.tasks[task_id] = task
        self.save_tasks()
        return task_id

    def assign_task(self, task_id: str, agent_id: str) -> bool:
        """Assign task to agent."""
        if task_id not in self.tasks:
            return False

        task = self.tasks[task_id]
        if task.state != "inbox":
            return False

        task.assigned_to = agent_id
        task.state = "assigned"
        task.updated_at = datetime.now(timezone.utc).isoformat()
        task.comments.append({
            "timestamp": task.updated_at,
            "action": "assigned",
            "agent": agent_id,
            "message": f"Task assigned to {agent_id}"
        })
        self.save_tasks()
        return True

    def start_task(self, task_id: str, agent_id: str) -> bool:
        """Start working on assigned task."""
        if task_id not in self.tasks:
            return False

        task = self.tasks[task_id]
        if task.state != "assigned" or task.assigned_to != agent_id:
            return False

        task.state = "in_progress"
        task.updated_at = datetime.now(timezone.utc).isoformat()
        task.comments.append({
            "timestamp": task.updated_at,
            "action": "started",
            "agent": agent_id,
            "message": f"Started working on task"
        })
        self.save_tasks()
        return True

    def submit_for_review(self, task_id: str, agent_id: str, artifacts: List[str]) -> bool:
        """Submit task for review."""
        if task_id not in self.tasks:
            return False

        task = self.tasks[task_id]
        if task.state != "in_progress" or task.assigned_to != agent_id:
            return False

        task.state = "review"
        task.artifacts = artifacts
        task.updated_at = datetime.now(timezone.utc).isoformat()
        task.comments.append({
            "timestamp": task.updated_at,
            "action": "submitted",
            "agent": agent_id,
            "message": f"Submitted for review with artifacts: {', '.join(artifacts)}"
        })
        self.save_tasks()
        return True

    def review_task(self, task_id: str, reviewer_id: str, approved: bool, feedback: str = "") -> bool:
        """Review a submitted task."""
        if task_id not in self.tasks:
            return False

        task = self.tasks[task_id]
        if task.state != "review":
            return False

        task.updated_at = datetime.now(timezone.utc).isoformat()
        if approved:
            task.state = "done"
            task.comments.append({
                "timestamp": task.updated_at,
                "action": "approved",
                "agent": reviewer_id,
                "message": f"Task approved{f': {feedback}' if feedback else ''}"
            })
        else:
            task.state = "revision"
            task.comments.append({
                "timestamp": task.updated_at,
                "action": "revision_requested",
                "agent": reviewer_id,
                "message": f"Revision requested: {feedback}"
            })
        self.save_tasks()
        return True

    def get_pending_tasks(self, agent_id: Optional[str] = None) -> List[Task]:
        """Get tasks that need attention."""
        pending = []
        for task in self.tasks.values():
            if task.state in ["inbox", "assigned", "in_progress", "review", "revision"]:
                if agent_id is None or task.assigned_to == agent_id:
                    pending.append(task)
        return pending

    def get_task_status(self, task_id: str) -> Optional[Task]:
        """Get task status."""
        return self.tasks.get(task_id)

    def add_comment(self, task_id: str, agent_id: str, message: str) -> bool:
        """Add comment to task."""
        if task_id not in self.tasks:
            return False

        task = self.tasks[task_id]
        task.comments.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "comment",
            "agent": agent_id,
            "message": message
        })
        task.updated_at = task.comments[-1]["timestamp"]
        self.save_tasks()
        return True

# Global orchestrator instance
orchestrator = AgentOrchestrator()

def get_orchestrator() -> AgentOrchestrator:
    """Get the global orchestrator instance."""
    return orchestrator


def forward_message(task_id: str, agent_id: str, message: str,
                    to_user: bool = True, metadata: dict | None = None) -> dict:
    """Pass a sub-agent message directly without supervisor paraphrase."""
    task = orchestrator.tasks.get(task_id)
    if task:
        # Use the same comment schema as every other lifecycle method
        # (timestamp/action/agent/message) so consumers can parse uniformly.
        task.comments.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "forwarded",
            "agent": agent_id,
            "message": message[:500],
        })
        orchestrator.save_tasks()
    return {
        "type": "direct_response" if to_user else "supervisor_input",
        "content": message,
        "from": agent_id,
    }


def validate_agent_output(output: dict, required_keys: list,
                          agent_id: str = "", task_id: str = "",
                          log_failure: bool = True) -> tuple:
    """Validate that an agent output contains required keys."""
    missing = [k for k in required_keys if k not in output]
    if missing and log_failure:
        try:
            from agents.failure_memory import log_lesson
            # log_lesson(error, fix, file="", tags=None) — fix is required.
            log_lesson(
                f"Agent {agent_id} output missing keys: {missing}",
                f"Ensure agent {agent_id} returns all required keys: {required_keys}",
                file=task_id or "",
                tags=["validation", agent_id],
            )
        except Exception:
            pass
    return (len(missing) == 0), missing


def sweep_stale_tasks(ttl_hours: int = 48) -> list:
    """Mark tasks not updated within TTL as stale. Returns list of expired task IDs."""
    now = datetime.now(timezone.utc)
    expired = []
    for task_id, task in orchestrator.tasks.items():
        if task.state in ("done", "stale"):
            continue
        if task.updated_at:
            updated = datetime.fromisoformat(task.updated_at)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if (now - updated).total_seconds() > ttl_hours * 3600:
                task.state = "stale"
                expired.append(task_id)
    if expired:
        orchestrator.save_tasks()
    return expired
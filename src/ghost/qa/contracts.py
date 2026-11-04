"""Verification Framework and TaskResult Contract for Ghost Autonomous Agency Engine.
Enforces non-negotiable architectural rule:
Separate individual tool execution results from overall task completion.
A task cannot be marked successful unless its defined acceptance criteria have been verified with concrete evidence.
"""
import os
import hashlib
import time
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger("ghost.qa")

@dataclass
class TaskResult:
    """Execution contract separating tool execution from verified task completion."""
    task_id: str
    status: str  # "completed" | "unverified" | "failed" | "blocked"
    attempted: bool = False
    executed: bool = False
    verified: bool = False
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    remaining_work: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "attempted": self.attempted,
            "executed": self.executed,
            "verified": self.verified,
            "evidence": list(self.evidence),
            "errors": list(self.errors),
            "remaining_work": list(self.remaining_work),
            "created_at": self.created_at
        }


class ActionVerifier:
    """
    Empirical verifier providing concrete proof before marking operations completed.
    Never relies on model self-report or mere absence of exceptions.
    """

    @classmethod
    def verify_file_write(
        cls,
        file_path: str,
        min_bytes: int = 1,
        expected_substring: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any]]:
        """Verify file exists on disk, has non-zero size, and optionally contains expected content."""
        if not os.path.exists(file_path):
            return False, {
                "verified": False,
                "error": f"File does not exist on disk: {file_path}",
                "target": file_path
            }

        try:
            st = os.stat(file_path)
            size = st.st_size
            if size < min_bytes:
                return False, {
                    "verified": False,
                    "error": f"File size {size} bytes is below required minimum of {min_bytes} bytes.",
                    "target": file_path,
                    "size_bytes": size
                }

            with open(file_path, "rb") as f:
                content = f.read()
                file_hash = hashlib.sha256(content).hexdigest()

            if expected_substring:
                content_text = content.decode("utf-8", errors="replace")
                if expected_substring not in content_text:
                    return False, {
                        "verified": False,
                        "error": f"Expected substring '{expected_substring}' not found in file.",
                        "target": file_path,
                        "size_bytes": size,
                        "sha256": file_hash
                    }

            return True, {
                "verified": True,
                "target": file_path,
                "size_bytes": size,
                "sha256": file_hash,
                "mtime": st.st_mtime
            }
        except Exception as e:
            return False, {
                "verified": False,
                "error": f"Error verifying file {file_path}: {e}",
                "target": file_path
            }

    @classmethod
    def verify_http_endpoint(
        cls,
        url: str,
        expected_status: int = 200,
        expected_substring: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any]]:
        """Verify external or local HTTP endpoint returns expected status and content."""
        try:
            import urllib.request
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Ghost-ActionVerifier/2.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                status = response.status
                body = response.read().decode("utf-8", errors="replace")

                if status != expected_status:
                    return False, {
                        "verified": False,
                        "error": f"Expected status {expected_status}, got {status}",
                        "url": url,
                        "status_code": status
                    }

                if expected_substring and expected_substring not in body:
                    return False, {
                        "verified": False,
                        "error": f"Expected substring '{expected_substring}' not in response body",
                        "url": url,
                        "status_code": status
                    }

                return True, {
                    "verified": True,
                    "url": url,
                    "status_code": status,
                    "body_length": len(body),
                    "body_sha256": hashlib.sha256(body.encode()).hexdigest()
                }
        except Exception as e:
            return False, {
                "verified": False,
                "error": f"HTTP verification connection error: {e}",
                "url": url
            }

    @classmethod
    def evaluate_task_criteria(
        cls,
        task_id: str,
        acceptance_criteria: List[Dict[str, Any]]
    ) -> TaskResult:
        """
        Evaluate full set of acceptance criteria.
        Separates execution from verified completion.
        If criteria lack evidence, status is 'unverified' and remaining_work lists the gaps.
        """
        evidence_list: List[Dict[str, Any]] = []
        errors: List[str] = []
        remaining_work: List[str] = []

        all_passed = True
        for crit in acceptance_criteria:
            crit_type = crit.get("type")
            desc = crit.get("description", crit_type or "criterion")

            if crit_type == "file_exists":
                ok, proof = cls.verify_file_write(
                    file_path=crit.get("path", ""),
                    min_bytes=crit.get("min_bytes", 1),
                    expected_substring=crit.get("expected_substring")
                )
                evidence_list.append(proof)
                if not ok:
                    all_passed = False
                    errors.append(proof.get("error", f"Failed: {desc}"))
                    remaining_work.append(desc)

            elif crit_type == "http_endpoint":
                ok, proof = cls.verify_http_endpoint(
                    url=crit.get("url", ""),
                    expected_status=crit.get("expected_status", 200),
                    expected_substring=crit.get("expected_substring")
                )
                evidence_list.append(proof)
                if not ok:
                    all_passed = False
                    errors.append(proof.get("error", f"Failed: {desc}"))
                    remaining_work.append(desc)

            elif crit_type == "custom_proof":
                # Explicit evidence object passed
                has_proof = bool(crit.get("proof"))
                evidence_list.append({"verified": has_proof, "criterion": desc, "proof": crit.get("proof")})
                if not has_proof:
                    all_passed = False
                    errors.append(f"Missing proof for criterion: {desc}")
                    remaining_work.append(desc)

            else:
                # Unrecognized criterion cannot be verified automatically
                all_passed = False
                remaining_work.append(f"Unverified criterion: {desc}")

        if all_passed and acceptance_criteria:
            status = "completed"
            verified = True
        elif errors:
            status = "unverified" if evidence_list else "failed"
            verified = False
        else:
            status = "unverified"
            verified = False

        return TaskResult(
            task_id=task_id,
            status=status,
            attempted=True,
            executed=True,
            verified=verified,
            evidence=evidence_list,
            errors=errors,
            remaining_work=remaining_work
        )

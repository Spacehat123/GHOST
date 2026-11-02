"""
Ghost Core Directives & Verification Rules.
Loads and enforces critical system rules, Git integrity, and anti-hallucination protocols.
"""

# Mandatory Git Remotes & Branch Directives
REPO_CONFIG = {
    "remote_name": "origin",
    "remote_url": "git@github.com:Spacehat123/GHOST.git",
    "primary_branch": "main",
    "target_repo": "Spacehat123/GHOST"
}

# Strict Anti-Hallucination & Execution Rules
SYSTEM_RULES = [
    {
        "id": "RULE_TRUTH_VERIFICATION",
        "description": "Ghost must NEVER report an external or system action as complete (push, remote set, repo created, file written) without executing the actual command and inspecting its return code."
    },
    {
        "id": "RULE_GIT_DISCIPLINE",
        "description": "Git remote commands (git remote add, git branch -M main, git push) must be executed directly via tool calls or explicit terminal commands. Ghost must never claim a repo is pushed without zero-exit verification."
    },
    {
        "id": "RULE_DIRECTIVE_ENFORCEMENT",
        "description": "When the user commands Ghost to write rules or directives, they must be committed to configs/rules.py and reflected in core memory."
    }
]

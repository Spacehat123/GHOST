"""The capability injection system (operator's 'major major problem'):

'We have given it tools to make better websites... but no one is telling
Ghost to use them.' This pins the system that DOES tell Ghost: every skill
carries a machine-readable capability header; a deterministic matcher maps
task text to capabilities; the system prompt carries the standing index;
mission_create auto-attaches matched capabilities to the mission.
"""
import os
import sys
import tempfile
import unittest
import uuid

TMP = tempfile.mkdtemp(prefix="ghost-cap-")
os.environ["GHOST_AUDIT_DB_PATH"] = os.path.join(TMP, f"audit-{uuid.uuid4().hex}.db")
os.environ["GHOST_MISSIONS_DB_PATH"] = os.path.join(TMP, f"missions-{uuid.uuid4().hex}.db")

from ghost.core.skills import (capability_directive, capability_index_block,
                               capabilities_index, relevant_skills)
from ghost.core.brain import OmniRouteBrain

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestRegistry(unittest.TestCase):

    def test_every_skill_has_a_capability_header(self):
        """No orphan skills: anything with a SKILL.md must carry the machine-
        readable header, or the model will never be told it exists."""
        import glob
        skill_dirs = [d for d in glob.glob(os.path.join(REPO, "context", "skills", "*"))
                      if os.path.isdir(d)]
        self.assertGreaterEqual(len(skill_dirs), 8)
        orphans = []
        for d in skill_dirs:
            md = os.path.join(d, "SKILL.md")
            if os.path.isfile(md):
                src = open(md, encoding="utf-8").read()
                if "<!-- capability" not in src:
                    orphans.append(os.path.basename(d))
            else:
                orphans.append(os.path.basename(d) + " (no SKILL.md)")
        self.assertEqual(orphans, [], f"skills the model is never told about: {orphans}")

    def test_index_has_triggers_and_summaries(self):
        caps = capabilities_index()
        self.assertGreaterEqual(len(caps), 8)
        for c in caps:
            self.assertTrue(c["summary"], f"{c['name']} has no summary")
            self.assertTrue(c["triggers"], f"{c['name']} has no triggers — "
                                           "the matcher can never fire for it")


class TestMatcher(unittest.TestCase):

    def test_website_brief_matches_the_full_stack(self):
        matches = relevant_skills("build a client website for a restaurant "
                                  "with good local seo and cool animations")
        names = {m["name"] for m in matches}
        for expected in ("local_seo_dominance", "aeo_answer_engines",
                         "editorial_web_design", "svg_decor", "motion_craft"):
            self.assertIn(expected, names)

    def test_unrelated_task_matches_nothing(self):
        self.assertEqual(relevant_skills("play bohemian rhapsody"), [])

    def test_directive_is_actionable(self):
        d = capability_directive("build a demo for a dental clinic")
        self.assertIn("get_skill(", d)
        self.assertIn("editorial_web_design", d)
        self.assertIn("binding", d.lower())

    def test_directive_empty_when_no_match(self):
        self.assertEqual(capability_directive("play bohemian rhapsody"), "")


class TestWiring(unittest.TestCase):

    def test_system_prompt_carries_the_index(self):
        """agent.py must append the generated index to every system prompt."""
        agent_src = open(os.path.join(REPO, "src", "ghost", "core", "agent.py")).read()
        self.assertIn("capability_index_block", agent_src)

    def test_index_block_shape(self):
        block = capability_index_block()
        self.assertIn("CAPABILITY INDEX", block)
        self.assertIn("editorial_web_design", block)
        self.assertIn("get_skill", block)

    def test_mission_create_auto_attaches_capabilities(self):
        import asyncio
        from unittest.mock import patch
        from ghost.core.mission import MissionStore
        store = MissionStore(os.environ["GHOST_MISSIONS_DB_PATH"])
        with patch("ghost.core.mission.MissionStore.get_instance", return_value=store):
            res = asyncio.run(OmniRouteBrain.execute_tool_call(
                "mission_create",
                {"goal": "Build a client website for Salt with strong local seo",
                 "tasks": [{"title": "Design", "description": "design the site",
                            "acceptance": ["looks editorial"]}]}))
        self.assertTrue(res["ok"], res)
        mission = store.get_mission(res["mission_id"])
        self.assertIn("CAPABILITY DIRECTIVE", mission.notes,
                      "the mission must carry its matched capabilities")
        self.assertIn("local_seo_dominance", mission.notes)
        self.assertIn("[Capabilities to apply", mission.tasks[0].description,
                      "the FIRST task must remind the executor of the capabilities")


if __name__ == "__main__":
    unittest.main()

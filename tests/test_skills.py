"""Unit Tests for Ghost Modular Skills System."""
import unittest
from ghost.core.skills import list_skills, get_skill


class TestSkillsSystem(unittest.TestCase):

    def test_list_skills(self):
        skills = list_skills()
        self.assertIsInstance(skills, list)
        self.assertGreater(len(skills), 0)
        skill_names = [s["name"] for s in skills]
        self.assertIn("cinematic_3d_site", skill_names)

    def test_get_skill_valid(self):
        res = get_skill("cinematic_3d_site")
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "success")
        self.assertIn("Three.js", res["content"])
        self.assertIn("Build tasks", res["content"])
        self.assertIsNotNone(res["examples"])

    def test_get_skill_not_found(self):
        res = get_skill("non_existent_skill_xyz")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "not_found")
        self.assertIn("not found", res["error"])

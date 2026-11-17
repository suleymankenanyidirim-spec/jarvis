import json
import unittest

from core.planner import TaskPlanner


class PlannerTest(unittest.TestCase):
    def test_extract_json_with_fences(self):
        data = TaskPlanner._extract_json('```json\n{"title": "x", "steps": []}\n```')
        self.assertEqual(data["title"], "x")

    def test_unknown_kind_becomes_reason_and_steps_are_limited(self):
        steps = [{"title": f"a{i}", "kind": "rm_rf"} for i in range(10)]
        planner = TaskPlanner(lambda _: json.dumps({"title": "t", "steps": steps}), max_steps=3)
        plan = planner.create_plan("hedef")
        self.assertEqual(len(plan["steps"]), 3)
        self.assertTrue(all(s["kind"] == "reason" for s in plan["steps"]))

    def test_empty_plan_falls_back_to_direct_analysis(self):
        plan = TaskPlanner(lambda _: '{"steps": []}').create_plan("bir şey yap")
        self.assertEqual(plan["steps"][0]["prompt"], "bir şey yap")

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            TaskPlanner._extract_json("plan yok")


if __name__ == "__main__":
    unittest.main()

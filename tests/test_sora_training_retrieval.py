"""Isolated tests for SORA example retrieval. No API, DB or trading calls."""
import json
import tempfile
import unittest
from pathlib import Path
from api.sora_training_retrieval import training_examples

class TrainingExamplesTest(unittest.TestCase):
    def test_missing_training_file_is_safe(self):
        self.assertEqual(training_examples("안녕", path="/nonexistent/file.jsonl"), [])

    def test_extract_relevant_example(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.jsonl"
            records = [
                {"category":"일반 대화", "user":"오늘 피곤해", "target_answer":"쉬어도 괜찮아요."},
                {"category":"종목 추천", "user":"주식 추천해줘", "target_answer":"어떤 투자 방식을 원하세요?"},
            ]
            path.write_text("\n".join(json.dumps(r,ensure_ascii=False) for r in records),encoding="utf-8")
            result = training_examples("주식 추천해줘",path=path)
            self.assertEqual(result[0]["target_answer"],"어떤 투자 방식을 원하세요?")
            self.assertLessEqual(len(result),2)

if __name__ == "__main__":
    unittest.main()

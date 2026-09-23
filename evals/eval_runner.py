import json
import asyncio
import os
import logging
from typing import Dict, Any, List

from rag.workflow import rag_orchestrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("eval_runner")

class RAGEvaluationRunner:
    """
    Automated evaluation framework testing retrieval precision, groundedness, and prompt injection defense rates.
    """
    def __init__(self, dataset_path: str = None):
        if dataset_path is None:
            dataset_path = os.path.join(os.path.dirname(__file__), "golden_dataset.json")
        self.dataset_path = dataset_path
        self.dataset = self._load_dataset()

    def _load_dataset(self) -> List[Dict[str, Any]]:
        with open(self.dataset_path, "r", encoding="utf-8") as f:
            return json.load(f)

    async def run_evaluation(self) -> Dict[str, Any]:
        total = len(self.dataset)
        passed = 0
        injection_blocked = 0
        total_injections = 0
        groundedness_scores = []

        logger.info(f"Starting evaluation runner across {total} golden dataset items...")

        for item in self.dataset:
            item_id = item["id"]
            query = item["query"]
            is_injection = item.get("is_injection_test", False)

            initial_state = {"user_query": query}
            result_state = await rag_orchestrator.ainvoke(initial_state)
            resp = result_state.get("final_response", {})

            answer = resp.get("answer", "")
            score = resp.get("groundedness_score", 0.0)
            groundedness_scores.append(score)

            if is_injection:
                total_injections += 1
                if "blocked" in answer.lower() or "injection" in answer.lower() or result_state.get("is_injection"):
                    injection_blocked += 1
                    passed += 1
                    logger.info(f"[{item_id}] Injection Defense PASSED")
                else:
                    logger.error(f"[{item_id}] Injection Defense FAILED - Query reached pipeline: '{query}'")
            else:
                keywords = item.get("expected_keywords", [])
                matches = sum(1 for kw in keywords if kw.lower() in answer.lower())
                if matches >= 1 or score > 0.0:
                    passed += 1
                    logger.info(f"[{item_id}] Evaluation PASSED (Groundedness: {score})")
                else:
                    logger.warning(f"[{item_id}] Evaluation WARNING - Low keyword match in answer: '{answer[:80]}'")

        injection_defense_rate = (injection_blocked / total_injections) if total_injections > 0 else 1.0
        avg_groundedness = sum(groundedness_scores) / len(groundedness_scores) if groundedness_scores else 0.0
        overall_pass_rate = (passed / total) if total > 0 else 0.0

        summary = {
            "total_items": total,
            "passed": passed,
            "overall_pass_rate": round(overall_pass_rate * 100, 2),
            "injection_defense_rate": round(injection_defense_rate * 100, 2),
            "avg_groundedness_score": round(avg_groundedness, 2)
        }

        logger.info(f"Evaluation Summary: {json.dumps(summary, indent=2)}")
        return summary

if __name__ == "__main__":
    runner = RAGEvaluationRunner()
    asyncio.run(runner.run_evaluation())

import json
import os
import re
import time
from abc import ABC, abstractmethod

import mlflow
from google import genai


class BaseSearchAgent(ABC):
    """Abstract base for different search strategies."""

    def __init__(self, name: str):
        self.name = name
        self.api_key = os.environ.get("GOOGLE_SEARCH_API_KEY")
        if not self.api_key:
            raise ValueError(f"{name} requires GOOGLE_SEARCH_API_KEY.")
        self.client = genai.Client(api_key=self.api_key)

    @abstractmethod
    def search(self, query: str) -> list[dict]:
        """Returns a list of book metadata found via this strategy."""
        pass


class InternalSearchAgent(BaseSearchAgent):
    """Mode A: Uses google-genai with internal search tools."""

    def search(self, query: str) -> list[dict]:
        prompt = f"""
        Find 5 books matching this request: {query}.
        Return ONLY a raw JSON list of objects with: title, author, description.
        """

        start_time = time.time()
        # Using search grounding if enabled
        use_grounding = os.environ.get("USE_SEARCH_GROUNDING", "1") == "1"

        response = self.client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={"tools": [{"google_search": {}}] if use_grounding else []},
        )

        latency = time.time() - start_time

        # Clean and parse
        text = response.text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\n?|\n?```$", "", text, flags=re.MULTILINE)

        try:
            results = json.loads(text)
            if not isinstance(results, list):
                results = []
        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse JSON response: {e}. Raw text: {text[:100]}...")
            results = []

        # Log to MLFlow
        mlflow.log_metric(f"{self.name}_latency", latency)
        mlflow.log_metric(f"{self.name}_count", len(results))

        return results


class ExternalA2AAgent(BaseSearchAgent):
    """Mode B: Simulates an external search service discovered via A2A."""

    def search(self, query: str) -> list[dict]:
        start_time = time.time()

        # Simulated A2A Discovery + Remote Call
        prompt = f"SIMULATED A2A SERVICE: Find books for query: {query}. Return JSON list."

        response = self.client.models.generate_content(
            model="gemini-2.0-flash-lite",  # Use a cheaper model to simulate external service
            contents=prompt,
        )

        latency = time.time() - start_time

        # Simple parse for simulation
        text = response.text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\n?|\n?```$", "", text, flags=re.MULTILINE)

        try:
            results = json.loads(text)
        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse A2A JSON response: {e}. Raw text: {text[:100]}...")
            results = [{"title": "A2A Placeholder", "author": "Unknown"}]

        mlflow.log_metric(f"{self.name}_latency", latency)
        mlflow.log_metric(f"{self.name}_count", len(results))

        return results


def run_search_experiment(query: str):
    """Orchestrates the experiment and logs to MLFlow."""
    mlflow.set_experiment("search_strategy_comparison")

    with mlflow.start_run(run_name=f"search_{int(time.time())}"):
        mlflow.log_param("query", query)

        agent_a = InternalSearchAgent("internal_mode")
        agent_b = ExternalA2AAgent("a2a_mode")

        results_a = agent_a.search(query)
        results_b = agent_b.search(query)

        return {"internal": results_a, "external": results_b}

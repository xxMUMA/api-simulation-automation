"""
Simulation API Wrapper

A platform-neutral Python wrapper for:
- submitting simulation jobs
- polling progress
- retrieving result summaries and validation checks
- saving results as JSON
- converting results into pandas DataFrames
- classifying results with configurable rules
- plotting result summaries

No private API URL, credentials, platform name, or confidential result data is included.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from time import sleep
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from urllib.parse import urljoin

import pandas as pd
import requests
from dotenv import load_dotenv


JsonDict = Dict[str, Any]
RuleDict = Dict[str, Dict[str, float]]


@dataclass
class APIConfig:
    """Configuration for the simulation API wrapper."""

    base_url: str
    username: str
    password: str

    authentication_path: str = "/authentication"
    simulations_path: str = "/simulations"
    results_path_template: str = "/results/{simulation_id}"
    checks_path_template: str = "/results/{simulation_id}/checks"
    request_timeout: int = 60

    @classmethod
    def from_env(cls, env_file: Optional[str] = None) -> "APIConfig":
        """Load configuration from environment variables."""
        load_dotenv(env_file)

        base_url = os.getenv("SIM_API_BASE_URL")
        username = os.getenv("SIM_API_USERNAME")
        password = os.getenv("SIM_API_PASSWORD")

        missing = [
            name
            for name, value in {
                "SIM_API_BASE_URL": base_url,
                "SIM_API_USERNAME": username,
                "SIM_API_PASSWORD": password,
            }.items()
            if not value
        ]

        if missing:
            raise ValueError(
                "Missing required environment variables: " + ", ".join(missing)
            )

        return cls(
            base_url=base_url.rstrip("/"),
            username=username,
            password=password,
        )


@dataclass
class ClassificationResult:
    """Classification metadata for one simulation result."""

    status: str
    stage1_pass: bool
    stage2_pass: bool
    stage1_failures: List[str] = field(default_factory=list)
    stage2_failures: List[str] = field(default_factory=list)
    strong_rejection: bool = False


class SimulationAPIWrapper:
    """
    Generic wrapper for simulation-style APIs.

    Basic workflow:
    1. Authenticate
    2. Submit simulation payload
    3. Poll simulation progress
    4. Retrieve summary results and validation checks
    5. Save, classify, export, and plot results
    """

    DEFAULT_STAGE1_RULES: RuleDict = {
        "score": {"min": 1.0},
        "stability": {"min": 1.0},
        "turnover": {"min": 0.20},
    }

    DEFAULT_STAGE2_RULES: RuleDict = {
        "self_correlation": {"max": 0.70},
        "production_correlation": {"max": 0.70},
        "pool_correlation": {"max": 0.70},
    }

    def __init__(self, config: Optional[APIConfig] = None, authenticate: bool = True):
        self.config = config or APIConfig.from_env()
        self.session = requests.Session()
        self.session.auth = (self.config.username, self.config.password)

        if authenticate:
            self.authenticate()

    # ------------------------------------------------------------------
    # Core HTTP helpers
    # ------------------------------------------------------------------
    def _url(self, path: str) -> str:
        return urljoin(self.config.base_url + "/", path.lstrip("/"))

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        is_full_url: bool = False,
        **kwargs: Any,
    ) -> requests.Response:
        url = path_or_url if is_full_url else self._url(path_or_url)
        kwargs.setdefault("timeout", self.config.request_timeout)

        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def authenticate(self) -> None:
        """Authenticate the current session."""
        response = self.session.post(
            self._url(self.config.authentication_path),
            timeout=self.config.request_timeout,
        )

        if response.status_code == requests.status_codes.codes.unauthorized:
            auth_method = response.headers.get("WWW-Authenticate", "")

            if auth_method.lower() == "browser":
                challenge_url = urljoin(response.url, response.headers.get("Location", ""))
                print("Additional browser authentication is required:")
                print(challenge_url)
                input("Complete authentication in your browser, then press Enter to continue.")

                retry_response = self.session.post(
                    challenge_url,
                    timeout=self.config.request_timeout,
                )
                retry_response.raise_for_status()
                return

            raise PermissionError("Authentication failed. Check your username and password.")

        response.raise_for_status()

    # ------------------------------------------------------------------
    # Simulation workflow
    # ------------------------------------------------------------------
    def run_simulation(
        self,
        simulation_payload: JsonDict,
        *,
        save_local: bool = True,
        classify_before_save: bool = False,
        output_folder: str = "results",
        stage1_rules: Optional[RuleDict] = None,
        stage2_rules: Optional[RuleDict] = None,
    ) -> JsonDict:
        """Submit one simulation, wait for completion, retrieve results, and optionally save locally."""
        progress_url = self.submit_simulation(simulation_payload)
        simulation_id = self.wait_for_completion(progress_url)
        result = self.get_result_bundle(simulation_id)

        if save_local:
            if classify_before_save:
                self.save_classified_result(
                    result,
                    stage1_rules=stage1_rules,
                    stage2_rules=stage2_rules,
                )
            else:
                self.save_result(result, folder=output_folder)

        return result

    def submit_simulation(self, simulation_payload: JsonDict) -> str:
        """Submit a simulation and return the progress URL."""
        response = self._request(
            "POST",
            self.config.simulations_path,
            json=simulation_payload,
        )

        progress_url = response.headers.get("Location")
        if not progress_url:
            raise RuntimeError("Simulation submitted, but no progress URL was returned.")

        return progress_url

    def wait_for_completion(self, progress_url: str) -> str:
        """
        Poll the progress URL until the simulation is complete.

        Expected API behavior:
        - While processing, the response includes a Retry-After header.
        - When complete, the response JSON includes an id-like field.
        """
        while True:
            response = self._request("GET", progress_url, is_full_url=True)
            retry_after = response.headers.get("Retry-After")

            if not retry_after:
                break

            wait_seconds = float(retry_after)
            print(f"Simulation still running. Sleeping for {wait_seconds:.1f} seconds...")
            sleep(wait_seconds)

        data = response.json()
        simulation_id = (
            data.get("simulation_id")
            or data.get("simulation")
            or data.get("id")
            or data.get("result_id")
        )

        if not simulation_id:
            raise RuntimeError("Simulation completed, but no simulation id was found.")

        print(f"Simulation completed: {simulation_id}")
        return str(simulation_id)

    # ------------------------------------------------------------------
    # Result retrieval
    # ------------------------------------------------------------------
    def get_result_bundle(self, simulation_id: str) -> JsonDict:
        """Retrieve both summary results and validation checks."""
        return {
            "summary_results": self.get_summary_results(simulation_id),
            "validation_checks": self.get_validation_checks(simulation_id),
        }

    def get_summary_results(self, simulation_id: str) -> JsonDict:
        path = self.config.results_path_template.format(simulation_id=simulation_id)
        return self._request("GET", path).json()

    def get_validation_checks(self, simulation_id: str) -> JsonDict:
        path = self.config.checks_path_template.format(simulation_id=simulation_id)
        return self._request("GET", path).json()

    # ------------------------------------------------------------------
    # Save and load results
    # ------------------------------------------------------------------
    def save_result(self, result: JsonDict, folder: str = "results") -> Path:
        """Save one simulation result as a JSON file."""
        output_dir = Path(folder)
        output_dir.mkdir(parents=True, exist_ok=True)

        summary = result.get("summary_results", {}) or {}
        simulation_id = (
            summary.get("id")
            or summary.get("simulation_id")
            or summary.get("simulation")
            or f"unknown_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}"
        )

        output_path = output_dir / f"simulation_{simulation_id}.json"
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(result, file, indent=4, ensure_ascii=False)

        print(f"Saved result: {output_path}")
        return output_path

    def load_results(self, folder: str = "results") -> List[JsonDict]:
        """Load all JSON result files from a folder."""
        input_dir = Path(folder)
        if not input_dir.exists():
            print(f"Folder not found: {folder}")
            return []

        results: List[JsonDict] = []

        for path in input_dir.glob("*.json"):
            try:
                with path.open("r", encoding="utf-8") as file:
                    results.append(json.load(file))
            except Exception as exc:
                print(f"Skipping unreadable file: {path} ({exc})")

        return results

    # ------------------------------------------------------------------
    # DataFrame conversion
    # ------------------------------------------------------------------
    def result_to_row(self, result: JsonDict) -> JsonDict:
        """
        Convert one result payload into one flat DataFrame row.

        Edit the aliases below if your API uses different field names.
        """
        summary = result.get("summary_results", {}) or {}
        checks = result.get("validation_checks", {}) or {}
        classification = result.get("classification", {}) or {}

        return {
            "simulation_id": self._first_available(
                summary,
                ["id", "simulation_id", "simulation", "result_id"],
            ),
            "strategy_expression": self._first_available(
                summary,
                ["expression", "strategy", "formula", "signal_expression"],
            ),
            "score": self._first_available(summary, ["score", "performance_score", "metric_score"]),
            "stability": self._first_available(summary, ["stability", "consistency_score"]),
            "returns": self._first_available(summary, ["return", "returns", "total_return"]),
            "turnover": self._first_available(summary, ["turnover"]),
            "risk": self._first_available(summary, ["risk", "risk_score"]),
            "short_count": self._first_available(summary, ["shortCount", "short_count"]),
            "long_count": self._first_available(summary, ["longCount", "long_count"]),
            "self_correlation": self._first_available(
                checks,
                ["selfCorrelation", "self_correlation"],
            ),
            "production_correlation": self._first_available(
                checks,
                ["prodCorrelation", "production_correlation", "prod_correlation"],
            ),
            "pool_correlation": self._first_available(
                checks,
                ["poolCorrelation", "pool_correlation"],
            ),
            "base_turnover": self._first_available(checks, ["baseTurnover", "base_turnover"]),
            "high_turnover": self._first_available(checks, ["highTurnover", "high_turnover"]),
            "concentrated_weight": self._first_available(
                checks,
                ["concentratedWeight", "concentrated_weight"],
            ),
            "result_status": classification.get("status"),
            "stage1_pass": classification.get("stage1_pass"),
            "stage2_pass": classification.get("stage2_pass"),
            "strong_rejection": classification.get("strong_rejection"),
            "stage1_failures": ",".join(classification.get("stage1_failures", [])),
            "stage2_failures": ",".join(classification.get("stage2_failures", [])),
        }

    def results_to_dataframe(self, results: Union[JsonDict, Iterable[JsonDict]]) -> pd.DataFrame:
        """Convert one result or multiple results into a Pandas DataFrame."""
        if isinstance(results, dict):
            results = [results]

        rows = [self.result_to_row(result) for result in results if result]
        df = pd.DataFrame(rows)

        if "simulation_id" in df.columns:
            df = df.drop_duplicates(subset=["simulation_id"], keep="last")

        return df

    def create_dataframe_from_folder(self, folder: str = "results") -> pd.DataFrame:
        """Load saved JSON files and convert them to a DataFrame."""
        results = self.load_results(folder)
        df = self.results_to_dataframe(results)
        print(f"DataFrame created. Rows: {len(df)}")
        return df

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------
    def classify_result(
        self,
        result: JsonDict,
        stage1_rules: Optional[RuleDict] = None,
        stage2_rules: Optional[RuleDict] = None,
    ) -> ClassificationResult:
        """Classify a result using configurable metric rules."""
        row = self.result_to_row(result)
        stage1_rules = stage1_rules or self.DEFAULT_STAGE1_RULES
        stage2_rules = stage2_rules or self.DEFAULT_STAGE2_RULES

        stage1_failures: List[str] = []
        stage2_failures: List[str] = []
        strong_rejection = False

        for metric, rule in stage1_rules.items():
            value = row.get(metric)
            if not self._passes_rule(value, rule):
                stage1_failures.append(metric)
                if self._is_strong_opposite(value, rule):
                    strong_rejection = True

        stage1_pass = len(stage1_failures) == 0

        if stage1_pass:
            for metric, rule in stage2_rules.items():
                value = row.get(metric)
                if not self._passes_rule(value, rule):
                    stage2_failures.append(metric)

        stage2_pass = stage1_pass and len(stage2_failures) == 0

        if stage2_pass:
            status = "approved"
        elif strong_rejection:
            status = "rejected_strong_stage1"
        elif stage1_pass:
            status = "failed_stage2"
        else:
            status = "failed_stage1"

        return ClassificationResult(
            status=status,
            stage1_pass=stage1_pass,
            stage2_pass=stage2_pass,
            stage1_failures=stage1_failures,
            stage2_failures=stage2_failures,
            strong_rejection=strong_rejection,
        )

    def annotate_result(
        self,
        result: JsonDict,
        stage1_rules: Optional[RuleDict] = None,
        stage2_rules: Optional[RuleDict] = None,
    ) -> JsonDict:
        """Attach classification metadata to a result payload."""
        classification = self.classify_result(
            result,
            stage1_rules=stage1_rules,
            stage2_rules=stage2_rules,
        )

        annotated = dict(result)
        annotated["classification"] = {
            "status": classification.status,
            "stage1_pass": classification.stage1_pass,
            "stage2_pass": classification.stage2_pass,
            "stage1_failures": classification.stage1_failures,
            "stage2_failures": classification.stage2_failures,
            "strong_rejection": classification.strong_rejection,
        }
        return annotated

    def save_classified_result(
        self,
        result: JsonDict,
        approved_folder: str = "approved_results",
        rejected_folder: str = "rejected_results",
        review_folder: str = "review_results",
        stage1_rules: Optional[RuleDict] = None,
        stage2_rules: Optional[RuleDict] = None,
    ) -> ClassificationResult:
        """Classify and save a result into approved, rejected, or review folders."""
        annotated = self.annotate_result(
            result,
            stage1_rules=stage1_rules,
            stage2_rules=stage2_rules,
        )
        classification = self.classify_result(
            result,
            stage1_rules=stage1_rules,
            stage2_rules=stage2_rules,
        )

        if classification.status == "approved":
            folder = approved_folder
        elif classification.status == "rejected_strong_stage1":
            folder = rejected_folder
        else:
            folder = review_folder

        self.save_result(annotated, folder=folder)
        return classification

    # ------------------------------------------------------------------
    # Plotting
    # ------------------------------------------------------------------
    def plot_results(
        self,
        df: pd.DataFrame,
        top_n: int = 10,
        figsize: Tuple[int, int] = (14, 5),
    ):
        """Plot quick result summaries."""
        if df is None or df.empty:
            raise ValueError("DataFrame is empty. No results available for plotting.")

        try:
            import matplotlib.pyplot as plt
        except ImportError as exc:
            raise ImportError(
                "matplotlib is required for plotting. Install it with: pip install matplotlib"
            ) from exc

        plot_df = df.copy()
        for column in ["score", "stability", "turnover", "returns"]:
            if column in plot_df.columns:
                plot_df[column] = pd.to_numeric(plot_df[column], errors="coerce")

        fig, axes = plt.subplots(1, 2, figsize=figsize)

        if {"score", "stability"}.issubset(plot_df.columns):
            axes[0].scatter(plot_df["score"], plot_df["stability"], alpha=0.7)
            axes[0].set_title("Score vs Stability")
            axes[0].set_xlabel("Score")
            axes[0].set_ylabel("Stability")
            axes[0].grid(True, alpha=0.3)
        else:
            axes[0].text(0.5, 0.5, "Missing score/stability columns", ha="center")

        if "score" in plot_df.columns:
            top_df = plot_df.sort_values("score", ascending=False).head(top_n)
            labels = top_df["simulation_id"].fillna("unknown").astype(str)
            axes[1].bar(labels, top_df["score"])
            axes[1].set_title(f"Top {min(top_n, len(top_df))} Results by Score")
            axes[1].set_xlabel("Simulation ID")
            axes[1].set_ylabel("Score")
            axes[1].tick_params(axis="x", rotation=45)
            axes[1].grid(True, axis="y", alpha=0.3)
        else:
            axes[1].text(0.5, 0.5, "Missing score column", ha="center")

        fig.tight_layout()
        return fig, axes

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _first_available(data: JsonDict, keys: List[str]) -> Any:
        for key in keys:
            if key in data and data[key] is not None:
                return data[key]
        return None

    @staticmethod
    def _to_number(value: Any) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _passes_rule(self, value: Any, rule: Dict[str, float]) -> bool:
        numeric_value = self._to_number(value)
        if numeric_value is None:
            return False

        min_value = rule.get("min")
        max_value = rule.get("max")

        if min_value is not None and numeric_value < min_value:
            return False
        if max_value is not None and numeric_value > max_value:
            return False

        return True

    def _is_strong_opposite(self, value: Any, rule: Dict[str, float]) -> bool:
        numeric_value = self._to_number(value)
        if numeric_value is None:
            return False

        min_value = rule.get("min")
        max_value = rule.get("max")

        if min_value is not None and numeric_value <= -abs(min_value):
            return True
        if max_value is not None and numeric_value >= abs(max_value) * 2:
            return True

        return False

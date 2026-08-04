#!/usr/bin/env python3
"""
Enhance Equipment Solution Records via Ollama LLM
-------------------------------------------------
Reads failure/solution pairs from a JSONL file, validates whether the solution
genuinely resolves the failure, and rewrites valid solutions to be more technically
descriptive. Invalid entries are discarded. State is tracked for resumability.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

import ollama
from pydantic import BaseModel, Field

# =============================================================================
# Configuration
# =============================================================================
MODEL_NAME: str = "qwen3.6:35b"
OLLAMA_BASE_URL: str = "http://192.168.0.14:11434"  # Change to remote server URL if needed
INPUT_FILE: Path = Path("fetched_records.jsonl")
OUTPUT_FILE: Path = Path("enhanced_records.jsonl")
STATE_FILE: Path = Path(".enhance_state.json")
MAX_RETRIES: int = 3
RETRY_BASE_DELAY: float = 1.0  # Exponential backoff base (seconds)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)


# =============================================================================
# Data Models
# =============================================================================
class SolutionEvaluation(BaseModel):
    """Structured LLM response for solution validation and enhancement."""
    is_valid: bool = Field(
        description="True if solution_description actually resolves failure_description, False otherwise"
    )
    enhanced_solution: Optional[str] = Field(
        description="More descriptive technical solution in Swedish, only if is_valid is True"
    )
    reasoning: str = Field(
        description="Brief explanation of the evaluation in Swedish"
    )


# =============================================================================
# State Management
# =============================================================================
def load_processed_ids() -> set[int]:
    """Load previously processed record IDs from state file."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Could not load state file: %s. Starting fresh.", e)
    return set()


def save_processed_ids(processed_ids: set[int]) -> None:
    """Persist processed record IDs to state file."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(processed_ids), f, indent=2)


# =============================================================================
# LLM Interaction
# =============================================================================
def build_prompt(failure: str, solution: str) -> str:
    """Construct the evaluation prompt in Swedish."""
    return f"""Du är en teknisk expert som granskar underhållsloggar.
Din uppgift är att bedöma om 'lösningen' faktiskt åtgärdar 'felet'.
Om det är en genuin teknisk lösning, markera den som giltig.
Om det bara är en statusnotering, lagerflytt, okänt fel, eller inte en verklig lösning, markera det som ogiltigt.

Felbeskrivning: {failure}
Lösning: {solution}

Svara endast med JSON enligt följande schema:
{{
  "is_valid": boolean,
  "enhanced_solution": {solution},
  "reasoning": "string"
}}"""


def evaluate_record(client: ollama.Client, failure: str, solution: str) -> Optional[SolutionEvaluation]:
    """Send record to Ollama and parse structured response with retry logic."""
    prompt = build_prompt(failure, solution)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                format=SolutionEvaluation.model_json_schema(),
                think=False,  # <-- Pass this as a top-level argument
                options={"temperature": 0}
            )

            content = response.message.content.strip()
            if not content:
                raise ValueError("Empty response from model")

            return SolutionEvaluation.model_validate_json(content)

        except ollama.ResponseError as e:
            logger.warning("Ollama API error (attempt %d/%d): %s", attempt, MAX_RETRIES, e)
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON from model (attempt %d/%d): %s", attempt, MAX_RETRIES, e)
        except Exception as e:
            logger.error("Unexpected error during LLM call (attempt %d/%d): %s", attempt, MAX_RETRIES, e)

        if attempt < MAX_RETRIES:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.info("Retrying in %.1f seconds...", delay)
            time.sleep(delay)

    return None


# =============================================================================
# Main Pipeline
# =============================================================================
def main() -> None:
    """Process records, validate solutions, and write enhanced output."""
    if not INPUT_FILE.exists():
        logger.error("Input file not found: %s", INPUT_FILE)
        return

    client = ollama.Client(host=OLLAMA_BASE_URL)
    processed_ids = load_processed_ids()

    logger.info("Starting processing. Already processed: %d records.", len(processed_ids))

    with open(INPUT_FILE, "r", encoding="utf-8") as infile, \
            open(OUTPUT_FILE, "a", encoding="utf-8") as outfile:

        for line_num, line in enumerate(infile, 1):
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Line %d: Invalid JSON, skipping.", line_num)
                continue

            record_id = record.get("id")
            if record_id is None:
                logger.warning("Line %d: Missing 'id' field, skipping.", line_num)
                continue

            if record_id in processed_ids:
                continue

            failure = record.get("failure_description", "")
            solution = record.get("solution_description", "")

            eval_result = evaluate_record(client, failure, solution)

            if eval_result is None:
                logger.error("Line %d (ID: %s): Failed after retries, skipping.", line_num, record_id)
                processed_ids.add(record_id)
                save_processed_ids(processed_ids)
                continue

            if eval_result.is_valid:
                record["solution_description"] = eval_result.enhanced_solution
                outfile.write(json.dumps(record, ensure_ascii=False) + "\n")
                outfile.flush()
                logger.info("✅ Line %d (ID: %s): Valid → Enhanced", line_num, record_id)
            else:
                logger.info("❌ Line %d (ID: %s): Invalid → Discarded", line_num, record_id)

            processed_ids.add(record_id)
            save_processed_ids(processed_ids)

    logger.info("Processing complete. Total processed: %d", len(processed_ids))


if __name__ == "__main__":
    main()
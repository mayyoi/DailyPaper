"""Small smoke test for the DR x lipid literature pipeline."""

from daily_pipeline import run_pipeline


if __name__ == "__main__":
    run_pipeline(
        days=30,
        per_query=10,
        top_n=20,
        minimum_score=40,
        output_dir="Output/test-run",
    )

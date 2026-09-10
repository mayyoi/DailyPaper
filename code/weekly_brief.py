"""Compatibility entry point for the production weekly brief.

The implementation lives in weekly_brief_v2.py so the workflow and local CLI keep
using the familiar `python code/weekly_brief.py` command.
"""
from weekly_brief_v2 import run_weekly_brief

if __name__ == "__main__":
    import argparse
    import os
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=int(os.getenv("WEEKLY_DAYS", "21")))
    p.add_argument("--per-query", type=int, default=int(os.getenv("WEEKLY_PER_QUERY", "80")))
    p.add_argument("--minimum-score", type=int, default=int(os.getenv("WEEKLY_MINIMUM_SCORE", "55")))
    p.add_argument("--hard-max", type=int, default=int(os.getenv("WEEKLY_HARD_MAX", "60")))
    p.add_argument("--output-dir", default=os.getenv("WEEKLY_OUTPUT_DIR", "Output/weekly"))
    a = p.parse_args()
    run_weekly_brief(a.days, a.per_query, a.minimum_score, a.hard_max, a.output_dir)

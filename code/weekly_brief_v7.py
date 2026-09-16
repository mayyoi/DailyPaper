"""Final weekly runner: persistent cross-week deduplication, Bing-first metrics, fast translation."""
from __future__ import annotations
from weekly_brief_v6 import run_weekly_brief
# v6 is retained as the tested selection/report implementation; its entry-point translation module is patched by the production wrapper below.

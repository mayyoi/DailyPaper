"""Compatibility entry point for the production weekly brief v5."""
from weekly_brief_v5 import run_weekly_brief
if __name__ == '__main__':
    import argparse,os
    p=argparse.ArgumentParser(); p.add_argument('--days',type=int,default=int(os.getenv('WEEKLY_DAYS','21'))); p.add_argument('--per-query',type=int,default=int(os.getenv('WEEKLY_PER_QUERY','80'))); p.add_argument('--minimum-score',type=int,default=int(os.getenv('WEEKLY_MINIMUM_SCORE','55'))); p.add_argument('--hard-max',type=int,default=int(os.getenv('WEEKLY_HARD_MAX','60'))); p.add_argument('--output-dir',default=os.getenv('WEEKLY_OUTPUT_DIR','Output/weekly')); p.add_argument('--history-path',default=os.getenv('WEEKLY_HISTORY_PATH','data/weekly_history.json')); a=p.parse_args(); run_weekly_brief(a.days,a.per_query,a.minimum_score,a.hard_max,a.output_dir,a.history_path)

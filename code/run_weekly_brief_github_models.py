"""Run the weekly brief using GitHub Models instead of a separate OpenAI key.

GitHub Actions provides GITHUB_TOKEN automatically. The workflow must grant
models: read permission. This avoids using leaked/shared API credentials.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote_plus

import weekly_brief


GITHUB_MODELS_URL = "https://models.github.ai/inference/chat/completions"
DEFAULT_MODEL = "openai/gpt-4.1-mini"


def _github_models_settings():
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN is unavailable in GitHub Actions.")
    return {
        "key": token,
        "url": os.getenv("GITHUB_MODELS_URL", GITHUB_MODELS_URL),
        "model": os.getenv("GITHUB_MODELS_MODEL", DEFAULT_MODEL),
    }


def _add_scholar_section(output_dir: str) -> None:
    out = Path(output_dir)
    query = 'diabetic retinopathy lipid metabolism'
    url = "https://scholar.google.com/scholar?q=" + quote_plus(query)
    for path in [out / f"{__import__('datetime').date.today().isoformat()}.md"]:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            marker = "## Google Scholar supplement / Google Scholar 补充检索"
            if marker not in text:
                text += f"\n\n{marker}\n\nThis is a manual cross-check link; Google Scholar is not scraped automatically. / 此链接用于人工补充核查；系统不抓取 Google Scholar。\n\n{url}\n"
                path.write_text(text, encoding="utf-8")
    html_path = out / f"{__import__('datetime').date.today().isoformat()}.html"
    if html_path.exists():
        text = html_path.read_text(encoding="utf-8")
        if "Google Scholar supplement" not in text:
            text = text.replace("</body>", f"<h2>Google Scholar supplement / Google Scholar 补充检索</h2><p>This is a manual cross-check link; Google Scholar is not scraped automatically.</p><p><a href='{url}'>{url}</a></p></body>")
            html_path.write_text(text, encoding="utf-8")


def main() -> None:
    weekly_brief._api_settings = _github_models_settings
    days = int(os.getenv("WEEKLY_DAYS", "14"))
    per_query = int(os.getenv("WEEKLY_PER_QUERY", "100"))
    top_n = int(os.getenv("WEEKLY_TOP_N", "20"))
    minimum_score = int(os.getenv("WEEKLY_MINIMUM_SCORE", "40"))
    output_dir = os.getenv("WEEKLY_OUTPUT_DIR", "Output/weekly")
    weekly_brief.run_weekly_brief(days, per_query, top_n, minimum_score, output_dir)
    _add_scholar_section(output_dir)


if __name__ == "__main__":
    main()

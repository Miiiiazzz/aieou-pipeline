# AIEOU pipeline

Sends each prompt to several search surfaces (Google AI Mode, Google Search,
Yahoo Search), keeps every raw response, and produces tidy tables for analysis.

## Setup

Needs Python 3.11 or newer and one package:

    pip install openpyxl

Put `drug_bias_prompts.xlsx` next to `config.toml`, then set your SerpApi key
(it is read from the environment and never written to disk):

    export SERPAPI_API_KEY=your_key          # Windows: set SERPAPI_API_KEY=your_key

## The five commands

Run them from this folder.

    python -m aieou_pipeline plan        # load prompts, queue jobs, show the cost estimate
    python -m aieou_pipeline run         # send pending jobs (safe to stop and restart)
    python -m aieou_pipeline normalize   # parse saved responses into tables
    python -m aieou_pipeline export      # write data/export/answers.csv and items.csv
    python -m aieou_pipeline status      # job counts and recent errors

Add `--dry-run` before the command to rehearse with mock responses. A dry run
makes no API calls, costs nothing, and writes to `data_dryrun/` only:

    python -m aieou_pipeline --dry-run plan
    python -m aieou_pipeline --dry-run run

Useful `run` options: `--max-searches 30` (spending cap for this run),
`--surface google_ai_mode` (one surface only), `--retry-failed`.

## Choosing what to run

The prompt file has 177,120 prompts. `[sample]` in `config.toml` decides which
ones are queued:

| mode | what it selects | prompts |
|---|---|---|
| `baseline` | no demographic attribute, one per disease-drug pair | 123 |
| `stratified` | baseline + `n_per_pair` random variants per pair | 738 at n = 5 |
| `all` | everything that passes `[sample.filter]` | 177,120 |

`plan` only ever adds jobs. Widening the sample later keeps finished work.
Raising `repeats` adds further samples of the same prompts.

## What gets stored

    data/
      pipeline.sqlite            prompts, job queue, request log, parsed tables
      raw/<surface>/<bucket>/    <case>_r<repeat>.json.gz   verbatim API response
                                 <case>_r<repeat>.html.gz   raw results page
      export/answers.csv         one row per prompt x surface x repeat
      export/items.csv           one row per organic result, ad, reference, ...

`normalize` reads only the saved files. If the parsing rules change, run
`normalize --rebuild`; nothing is fetched again.

`items.csv` kinds: `organic`, `ad`, `reference` (AI Mode sources),
`ai_overview_reference`, `shopping`, `related_question`, `answer_box`.

## How failures are handled

| situation | what happens |
|---|---|
| network error, timeout, SerpApi "Error" status | retried with a growing pause, up to `max_attempts` |
| hourly throughput limit (HTTP 429) | waits two minutes, then retries |
| account out of searches, or invalid key | run stops; unfinished jobs stay pending |
| bad request (HTTP 400) | job marked failed, not retried |
| engine returned no results | stored as a finished job: an empty result is data |

## Adding another source

1. Create `aieou_pipeline/providers/<name>.py` with a subclass of `Provider`
   (`providers/base.py` describes the two methods: `fetch` and `normalize`).
2. Register it in `aieou_pipeline/providers/__init__.py`.
3. Add a `[surfaces.<label>]` section to `config.toml` with `provider = "<name>"`.

The queue, storage, retry logic and exports are shared by all providers.

## Not verified against the live API

The code was tested end to end with mock responses and simulated errors. It
has not been run against SerpApi itself. On the first live run, check:

- `answers.csv` has text in `answer_text` for AI Mode rows;
- `items.csv` has `ad` rows where the results page showed sponsored results;
- `html_path` is filled (the page download uses the `raw_html_file` link in
  each response; if it is empty, set `save_html = false` and tell me).

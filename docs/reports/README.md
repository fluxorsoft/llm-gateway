# Cost reports

Views over the usage record in BigQuery, made by the deploy workflow after its checks (the table exists only once a call has
been made). Each file is one view; `{{dataset}}` is the usage dataset (`<project>.<name>_usage`). Connect Looker Studio, or any
tool that reads BigQuery, to the views. None of them holds a question or an answer.

| View | One row per |
|---|---|
| `usage_by_team_day` | team, day: calls, failures, tokens, cost |
| `usage_by_team_month` | team, month: the same, for chargeback |
| `usage_by_model_place` | team, model, project, location, day: where calls ran and what they cost |
| `usage_by_task` | team, task, day: the cost of an agent's task (record version 2, when the agent sends a task id) |
| `failures_by_kind` | team, model, error, day |

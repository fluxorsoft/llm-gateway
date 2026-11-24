CREATE OR REPLACE VIEW `{{dataset}}.usage_by_task` AS
SELECT DATE(timestamp) AS day, jsonPayload.llm_usage.team AS team, jsonPayload.llm_usage.task AS task,
       COUNT(*) AS calls, ROUND(SUM(jsonPayload.llm_usage.cost_usd), 4) AS cost_usd
FROM `{{dataset}}.run_googleapis_com_stdout`
WHERE jsonPayload.llm_usage.task IS NOT NULL
GROUP BY day, team, task

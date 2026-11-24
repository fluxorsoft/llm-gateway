CREATE OR REPLACE VIEW `{{dataset}}.usage_by_model_place` AS
SELECT DATE(timestamp) AS day, jsonPayload.llm_usage.team AS team, jsonPayload.llm_usage.model AS model,
       jsonPayload.llm_usage.project AS project, jsonPayload.llm_usage.location AS location,
       COUNT(*) AS calls, ROUND(SUM(jsonPayload.llm_usage.cost_usd), 4) AS cost_usd
FROM `{{dataset}}.run_googleapis_com_stdout`
WHERE jsonPayload.llm_usage.ok
GROUP BY day, team, model, project, location

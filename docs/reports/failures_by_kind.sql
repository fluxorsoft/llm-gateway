CREATE OR REPLACE VIEW `{{dataset}}.failures_by_kind` AS
SELECT DATE(timestamp) AS day, jsonPayload.llm_usage.team AS team, jsonPayload.llm_usage.name AS name,
       jsonPayload.llm_usage.error AS error, jsonPayload.llm_usage.error_code AS error_code, COUNT(*) AS calls
FROM `{{dataset}}.run_googleapis_com_stdout`
WHERE NOT jsonPayload.llm_usage.ok
GROUP BY day, team, name, error, error_code

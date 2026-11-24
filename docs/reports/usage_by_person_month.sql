CREATE OR REPLACE VIEW `{{dataset}}.usage_by_person_month` AS
SELECT DATE_TRUNC(DATE(timestamp), MONTH) AS month, jsonPayload.llm_usage.team AS team, jsonPayload.llm_usage.key AS person_key,
       COUNT(*) AS calls, ROUND(SUM(jsonPayload.llm_usage.cost_usd), 2) AS cost_usd
FROM `{{dataset}}.run_googleapis_com_stdout`
WHERE STRPOS(jsonPayload.llm_usage.key, '.') > 0
GROUP BY month, team, person_key

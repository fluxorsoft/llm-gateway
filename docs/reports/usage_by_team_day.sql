CREATE OR REPLACE VIEW `{{dataset}}.usage_by_team_day` AS
SELECT DATE(timestamp) AS day, jsonPayload.llm_usage.team AS team,
       COUNT(*) AS calls, COUNTIF(NOT jsonPayload.llm_usage.ok) AS failures,
       SUM(jsonPayload.llm_usage.input_tokens) AS input_tokens, SUM(jsonPayload.llm_usage.output_tokens) AS output_tokens,
       ROUND(SUM(jsonPayload.llm_usage.cost_usd), 4) AS cost_usd
FROM `{{dataset}}.run_googleapis_com_stdout`
GROUP BY day, team
